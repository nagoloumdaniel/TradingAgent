"""Signal generation on candle close (F-009, F-012, RM-009, RM-016, TASK-034).

A strategy runs only on the close of its primary timeframe, and only when every series it
reads is healthy: a closed market or a doubtful series means no signal, with the reason
stored. Each (strategy, market) pair is isolated: a crash is contained, counted, and three
in a row quarantine that pair until an operator re-arms it.
"""

import logging
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Protocol

from tradingagent.config.strategy_catalog import LoadedStrategy
from tradingagent.core.market import Candle
from tradingagent.core.mode import TradingMode, mode_rank
from tradingagent.core.states import Severity
from tradingagent.data.market_calendar import MarketCalendar
from tradingagent.data.quality import assess_series
from tradingagent.storage.candles import CandleStore
from tradingagent.storage.signals import SignalRecord, SignalRepository, idempotency_key
from tradingagent.strategies.evaluation import OutcomeKind, evaluate

log = logging.getLogger(__name__)

DEFAULT_QUARANTINE_AFTER = 3


class GenerationStatus(StrEnum):
    RECORDED = "recorded"
    DUPLICATE = "duplicate"
    NO_SIGNAL = "no_signal"
    SKIPPED = "skipped"
    INSUFFICIENT_HISTORY = "insufficient_history"
    STRATEGY_ERROR = "strategy_error"
    QUARANTINED = "quarantined"
    FAILED = "failed"


@dataclass(frozen=True)
class Generation:
    ref: str
    symbol: str
    status: GenerationStatus
    signal_id: int | None = None
    detail: str = ""


Pair = tuple[str, str]  # (strategy reference, symbol)


class QuarantineBook(Protocol):
    """Where quarantines live. The agent uses the database-backed one, so a restart does
    not silently re-arm a faulty strategy."""

    def is_quarantined(self, ref: str, symbol: str) -> bool: ...
    def quarantined(self) -> set[Pair]: ...
    def quarantine(self, ref: str, symbol: str, reason: str, at: datetime) -> None: ...
    def rearm(self, ref: str, symbol: str, actor: str) -> None: ...


class InMemoryQuarantine:
    def __init__(self) -> None:
        self._pairs: set[Pair] = set()

    def is_quarantined(self, ref: str, symbol: str) -> bool:
        return (ref, symbol) in self._pairs

    def quarantined(self) -> set[Pair]:
        return set(self._pairs)

    def quarantine(self, ref: str, symbol: str, reason: str, at: datetime) -> None:
        self._pairs.add((ref, symbol))

    def rearm(self, ref: str, symbol: str, actor: str) -> None:
        self._pairs.discard((ref, symbol))


class SignalGenerator:
    def __init__(
        self,
        strategies: Iterable[LoadedStrategy],
        candles: CandleStore,
        signals: SignalRepository,
        agent_mode: TradingMode,
        quarantine_after: int = DEFAULT_QUARANTINE_AFTER,
        quarantine: QuarantineBook | None = None,
    ) -> None:
        self._strategies = tuple(strategies)
        self._candles = candles
        self._signals = signals
        self._agent_mode = agent_mode
        self._quarantine_after = quarantine_after
        self._failures: dict[Pair, int] = {}
        self._quarantine = quarantine if quarantine is not None else InMemoryQuarantine()

    @property
    def quarantined(self) -> set[Pair]:
        return self._quarantine.quarantined()

    @property
    def strategies(self) -> tuple[LoadedStrategy, ...]:
        return self._strategies

    def rearm(self, ref: str, symbol: str, actor: str = "operator") -> None:
        self._quarantine.rearm(ref, symbol, actor)
        self._failures.pop((ref, symbol), None)
        log.info("%s re-armed on %s", ref, symbol)

    def on_candle_closed(
        self,
        symbol: str,
        trigger: Candle,
        calendar: MarketCalendar,
        now: datetime,
        last_tick_at: datetime | None,
    ) -> list[Generation]:
        """Run every strategy listening to this market and timeframe. Never raises."""
        results = []
        for loaded in self._strategies:
            manifest = loaded.manifest
            if symbol not in manifest.allowed_symbols:
                continue
            if manifest.primary_timeframe is not trigger.timeframe:
                continue
            try:
                result = self._run(loaded, symbol, trigger, calendar, now, last_tick_at)
            except Exception as error:
                # Infrastructure failure (database, data): contained so other markets go on.
                log.exception("%s on %s failed", manifest.ref, symbol)
                result = Generation(
                    manifest.ref, symbol, GenerationStatus.FAILED, detail=repr(error)
                )
                self._event(
                    "signal_generation_failed",
                    Severity.CRITICAL,
                    manifest.ref,
                    symbol,
                    trigger,
                    now,
                    result.detail,
                )
            results.append(result)
        return results

    def _run(
        self,
        loaded: LoadedStrategy,
        symbol: str,
        trigger: Candle,
        calendar: MarketCalendar,
        now: datetime,
        last_tick_at: datetime | None,
    ) -> Generation:
        manifest = loaded.manifest
        ref = manifest.ref
        if self._quarantine.is_quarantined(ref, symbol):
            return Generation(ref, symbol, GenerationStatus.QUARANTINED)

        evaluated_at = trigger.close_time
        windows = {
            timeframe: self._candles.latest(
                symbol, timeframe, manifest.history_bars, closed_by=evaluated_at
            )
            for timeframe in manifest.timeframes
        }
        for timeframe, window in windows.items():
            health = assess_series(window, timeframe, calendar, now, last_tick_at)
            if not health.usable:
                detail = f"{timeframe} {health.status}: {health.detail}"
                severity = Severity.WARNING if health.is_anomaly else Severity.INFO
                self._event("evaluation_skipped", severity, ref, symbol, trigger, now, detail)
                return Generation(ref, symbol, GenerationStatus.SKIPPED, detail=detail)

        outcome = evaluate(loaded.strategy, manifest, symbol, windows, evaluated_at)
        if outcome.kind is OutcomeKind.INSUFFICIENT_HISTORY:
            self._event(
                "insufficient_history", Severity.WARNING, ref, symbol, trigger, now, outcome.detail
            )
            return Generation(
                ref, symbol, GenerationStatus.INSUFFICIENT_HISTORY, detail=outcome.detail
            )
        if outcome.kind.is_strategy_error:
            return self._strategy_failed(ref, symbol, trigger, now, outcome.detail)

        self._failures.pop((ref, symbol), None)
        if outcome.candidate is None:
            return Generation(ref, symbol, GenerationStatus.NO_SIGNAL)

        candidate = outcome.candidate
        step = timedelta(seconds=trigger.timeframe.seconds)
        signal_id = self._signals.record(
            SignalRecord(
                idempotency_key=idempotency_key(ref, symbol, trigger.timeframe, evaluated_at),
                manifest=manifest,
                symbol=symbol,
                timeframe=trigger.timeframe,
                direction=candidate.direction,
                mode=self._capped_mode(manifest.max_mode),
                observed_price=trigger.close,
                entry_low=candidate.entry_low,
                entry_high=candidate.entry_high,
                stop_loss=candidate.stop_loss,
                take_profits=candidate.take_profits,
                reason=candidate.reason,
                indicators=candidate.indicators,
                generated_at=evaluated_at,
                expires_at=evaluated_at + step * manifest.expiry_bars,
            )
        )
        if signal_id is None:
            return Generation(ref, symbol, GenerationStatus.DUPLICATE)
        log.info("%s %s signal on %s recorded (#%d)", ref, candidate.direction, symbol, signal_id)
        return Generation(ref, symbol, GenerationStatus.RECORDED, signal_id=signal_id)

    def _capped_mode(self, max_mode: TradingMode) -> TradingMode:
        """RM-016: a signal never carries more exposure than its strategy was promoted to."""
        return min(self._agent_mode, max_mode, key=mode_rank)

    def _strategy_failed(
        self, ref: str, symbol: str, trigger: Candle, now: datetime, detail: str
    ) -> Generation:
        pair = (ref, symbol)
        self._failures[pair] = self._failures.get(pair, 0) + 1
        self._event("strategy_error", Severity.WARNING, ref, symbol, trigger, now, detail)
        if self._failures[pair] >= self._quarantine_after:
            reason = f"{self._failures[pair]} consecutive failures, last: {detail}"
            self._quarantine.quarantine(ref, symbol, reason, now)
            self._event(
                "strategy_quarantined", Severity.CRITICAL, ref, symbol, trigger, now, reason
            )
            log.error("%s quarantined on %s: %s", ref, symbol, detail)
        return Generation(ref, symbol, GenerationStatus.STRATEGY_ERROR, detail=detail)

    def _event(
        self,
        kind: str,
        severity: Severity,
        ref: str,
        symbol: str,
        trigger: Candle,
        now: datetime,
        detail: str,
    ) -> None:
        self._signals.record_system_event(
            kind,
            severity,
            {
                "strategy": ref,
                "symbol": symbol,
                "timeframe": str(trigger.timeframe),
                "candle_close": trigger.close_time.isoformat(),
                "detail": detail,
            },
            now,
        )
