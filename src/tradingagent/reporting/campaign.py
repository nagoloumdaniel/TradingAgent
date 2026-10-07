"""Where the paper-trading campaign stands (cahier v3 §44, §53; Q-15; TASK-071).

Phase 11 stops being a promise and becomes a measurement here. The campaign has three
frozen exit criteria, none of them renegotiable once the run has started:

* **duration** — thirty calendar days between the first paper trade and the reading date
  (Q-15);
* **volume** — thirty closed paper trades per strategy (Q-15);
* **drawdown** — the maximum peak-to-trough fall of the realized P&L stays at or below the
  campaign ceiling. The default ceiling is 5 % of the paper starting capital (1 000 €,
  decision D-07), the same 5 % the live ceiling of RM-005 allows, expressed in euros
  because `analytics` already reports `max_drawdown` in euros.

Two rules hold the report honest.

*Every figure comes from `analytics`.* Operations, net P&L and drawdown are
`compute_performance` on the closed `PAPER` trades, the same code a backtest runs, so a
campaign and its reference are comparable by construction (C-001).

*Nothing is recomputed by hand and no clock is read.* `progress` takes the reference
instant `at` as an argument, and the day calendar comes from the `daily_performance`
aggregates the runtime already writes. A missing criterion is never silent: it is printed
with the value observed and the value targeted.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from typing import Any

from sqlalchemy import Engine

from tradingagent.analytics import Axis, Performance, Trade, compute_performance, group
from tradingagent.core.mode import TradingMode
from tradingagent.storage.account import ReportData
from tradingagent.storage.daily import DailyPerformance, DailyPerformanceStore, day_floor

# Only the simulated mode belongs to the campaign: demo and live are separate runs (R-14).
CAMPAIGN_MODE = TradingMode.PAPER

# Q-15, cahier des charges v3 (tableau des questions ouvertes) : durée du paper trading.
Q15_MIN_DAYS = 30
Q15_MIN_TRADES = 30
Q15_RULE = (
    "Q-15 — la campagne de paper trading dure au moins trente jours calendaires et "
    "produit au moins trente opérations par stratégie."
)

# 5 % of PAPER_STARTING_CAPITAL (1 000 €, décision D-07), the same ceiling as RM-005.
CAMPAIGN_MAX_DRAWDOWN_EUR = Decimal("50")

# The campaign has no configured start date: its first paper trade is one. Reading from a
# fixed epoch keeps the query bounded without inventing a starting date.
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


@dataclass(frozen=True)
class CampaignPlan:
    """The frozen exit criteria. Widening one after the fact would be a promotion by
    default, which Q-15 does not allow."""

    min_days: int = Q15_MIN_DAYS
    min_trades: int = Q15_MIN_TRADES
    max_drawdown_eur: Decimal = CAMPAIGN_MAX_DRAWDOWN_EUR
    rule: str = Q15_RULE

    def __post_init__(self) -> None:
        if self.min_days < 1:
            raise ValueError("the campaign needs at least one calendar day")
        if self.min_trades < 1:
            raise ValueError("the campaign needs at least one operation per strategy")
        if self.max_drawdown_eur < 0:
            raise ValueError("the tolerated drawdown must not be negative")


PLAN = CampaignPlan()


class Verdict(StrEnum):
    """EN COURS: still running. PRÊT: every criterion met. ÉCHEC: one criterion lost."""

    EN_COURS = "EN COURS"
    PRET = "PRÊT"
    ECHEC = "ÉCHEC"


@dataclass(frozen=True)
class Criterion:
    """One exit condition, satisfied or not, always with its observed value and target."""

    key: str
    label: str
    observed: str
    target: str
    satisfied: bool

    def describe(self) -> str:
        return f"{self.label} : observé {self.observed}, cible {self.target}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "observed": self.observed,
            "target": self.target,
            "satisfied": self.satisfied,
        }


@dataclass(frozen=True)
class StrategyProgress:
    """The campaign measured for one (market, strategy) pair."""

    market: str
    strategy_ref: str
    first_trade_day: datetime | None
    last_trade_day: datetime | None
    elapsed_days: int
    remaining_days: int
    trades: int
    net_profit: Decimal
    max_drawdown: Decimal
    criteria: tuple[Criterion, ...]
    verdict: Verdict

    @property
    def satisfied(self) -> tuple[Criterion, ...]:
        return tuple(criterion for criterion in self.criteria if criterion.satisfied)

    @property
    def missing(self) -> tuple[Criterion, ...]:
        return tuple(criterion for criterion in self.criteria if not criterion.satisfied)

    def to_dict(self) -> dict[str, Any]:
        return {
            "market": self.market,
            "strategy_ref": self.strategy_ref,
            "verdict": self.verdict.value,
            "first_trade_day": _iso(self.first_trade_day),
            "last_trade_day": _iso(self.last_trade_day),
            "elapsed_days": self.elapsed_days,
            "remaining_days": self.remaining_days,
            "trades": self.trades,
            "net_profit": _money(self.net_profit),
            "max_drawdown": _money(self.max_drawdown),
            "criteria": [criterion.to_dict() for criterion in self.criteria],
        }


@dataclass(frozen=True)
class CampaignProgress:
    """One reading of the campaign: every strategy, and the overall verdict."""

    at: datetime
    market: str | None
    plan: CampaignPlan
    strategies: tuple[StrategyProgress, ...]
    verdict: Verdict

    @property
    def markets(self) -> tuple[str, ...]:
        return tuple(sorted({strategy.market for strategy in self.strategies}))

    def to_dict(self) -> dict[str, Any]:
        return {
            "at": self.at.isoformat(),
            "market": self.market,
            "verdict": self.verdict.value,
            "plan": {
                "min_days": self.plan.min_days,
                "min_trades": self.plan.min_trades,
                "max_drawdown_eur": _money(self.plan.max_drawdown_eur),
                "rule": self.plan.rule,
            },
            "strategies": [strategy.to_dict() for strategy in self.strategies],
        }


def evaluate(
    *,
    market: str,
    strategy_ref: str,
    first_trade_day: datetime | None,
    last_trade_day: datetime | None,
    performance: Performance,
    at: datetime,
    plan: CampaignPlan = PLAN,
) -> StrategyProgress:
    """One verdict from aggregates already computed: pure, no clock and no I/O."""
    elapsed, remaining = calendar_days(first_trade_day, at, plan)
    duration = Criterion(
        key="duree",
        label="durée de la campagne (Q-15)",
        observed=f"{elapsed} jour(s)",
        target=f"{plan.min_days} jour(s) calendaires",
        satisfied=elapsed >= plan.min_days,
    )
    operations = Criterion(
        key="operations",
        label="opérations cumulées (Q-15)",
        observed=f"{performance.trades}",
        target=f"{plan.min_trades} par stratégie",
        satisfied=performance.trades >= plan.min_trades,
    )
    drawdown = Criterion(
        key="drawdown",
        label="drawdown maximal",
        observed=f"{_money(performance.max_drawdown)} EUR",
        target=f"<= {_money(plan.max_drawdown_eur)} EUR",
        satisfied=performance.max_drawdown <= plan.max_drawdown_eur,
    )
    return StrategyProgress(
        market=market,
        strategy_ref=strategy_ref,
        first_trade_day=first_trade_day,
        last_trade_day=last_trade_day,
        elapsed_days=elapsed,
        remaining_days=remaining,
        trades=performance.trades,
        net_profit=performance.net_profit,
        max_drawdown=performance.max_drawdown,
        criteria=(duration, operations, drawdown),
        verdict=_verdict(duration, operations, drawdown),
    )


def calendar_days(
    first_trade_day: datetime | None, at: datetime, plan: CampaignPlan
) -> tuple[int, int]:
    """Elapsed calendar days since the first paper trade, and days still to run.

    Both are calendar days, never trading days: Q-15 counts the campaign's duration, and a
    weekend the market was closed is still a day the campaign ran.
    """
    if first_trade_day is None:
        return 0, plan.min_days
    elapsed = max(0, (day_floor(at) - day_floor(first_trade_day)).days)
    return elapsed, max(0, plan.min_days - elapsed)


def campaign_verdict(strategies: Iterable[StrategyProgress]) -> Verdict:
    """A campaign is ready only when every strategy is; one failure fails the campaign."""
    verdicts = [strategy.verdict for strategy in strategies]
    if Verdict.ECHEC in verdicts:
        return Verdict.ECHEC
    if verdicts and all(verdict is Verdict.PRET for verdict in verdicts):
        return Verdict.PRET
    return Verdict.EN_COURS


def _verdict(duration: Criterion, operations: Criterion, drawdown: Criterion) -> Verdict:
    if not drawdown.satisfied:
        # A breach is final: the run stops rather than being extended until it passes.
        return Verdict.ECHEC
    if not duration.satisfied:
        return Verdict.EN_COURS
    if not operations.satisfied:
        # The whole window ran and the sample never reached the minimum: the campaign
        # cannot support a comparison, so it fails instead of lingering.
        return Verdict.ECHEC
    return Verdict.PRET


def progress(
    engine: Engine,
    *,
    at: datetime,
    market: str | None = None,
    plan: CampaignPlan = PLAN,
) -> CampaignProgress:
    """Read the paper campaign up to `at`: the day calendar from `daily_performance`, the
    figures from the closed `trades` through `analytics`. Read-only, never writes."""
    _require_aware(at)
    days = _paper_days(engine, at=at, market=market)
    trades = _paper_trades(engine, at=at, market=market, start=_first_day(days))
    by_strategy = _by_market_and_strategy(trades)
    calendar = _calendar(days, trades)

    strategies: list[StrategyProgress] = []
    for key in sorted(set(calendar) | set(by_strategy)):
        bucket = by_strategy.get(key, [])
        first_day, last_day = calendar.get(key, (None, None))
        strategies.append(
            evaluate(
                market=key[0],
                strategy_ref=key[1],
                first_trade_day=first_day,
                last_trade_day=last_day,
                performance=compute_performance(bucket),
                at=at,
                plan=plan,
            )
        )
    ordered = tuple(strategies)
    return CampaignProgress(
        at=at,
        market=market,
        plan=plan,
        strategies=ordered,
        verdict=campaign_verdict(ordered),
    )


def render(state: CampaignProgress) -> str:
    """The report, in French, ready for Telegram or for an operator's screen."""
    scope = "tous marchés" if state.market is None else state.market
    lines = [
        f"Campagne de paper trading — état au {_stamp(state.at)} ({scope})",
        f"Règle Q-15 : {state.plan.rule}",
        (
            f"Critères figés : {state.plan.min_days} jours calendaires minimum, "
            f"{state.plan.min_trades} opérations par stratégie, drawdown maximal toléré "
            f"{_money(state.plan.max_drawdown_eur)} EUR."
        ),
        f"Verdict global : {state.verdict.value}",
    ]
    if not state.strategies:
        lines.append("")
        lines.append(
            "Aucune opération de paper trading enregistrée : la campagne n'a pas commencé."
        )
        return "\n".join(lines)
    for strategy in state.strategies:
        lines.append("")
        lines.append(f"{strategy.market} / {strategy.strategy_ref} — {strategy.verdict.value}")
        lines.append(
            f"  jours écoulés depuis le premier trade : {strategy.elapsed_days} / "
            f"{state.plan.min_days} ({strategy.remaining_days} restant(s))"
        )
        lines.append(f"  opérations cumulées : {strategy.trades} / {state.plan.min_trades}")
        lines.append(f"  résultat net : {_signed(strategy.net_profit)} EUR")
        lines.append(
            f"  drawdown maximal observé : {_money(strategy.max_drawdown)} EUR "
            f"(toléré {_money(state.plan.max_drawdown_eur)} EUR)"
        )
        if strategy.first_trade_day is not None:
            lines.append(f"  premier trade paper : {_stamp(strategy.first_trade_day)}")
        if strategy.satisfied:
            lines.append(
                "  critères satisfaits : "
                + ", ".join(criterion.label for criterion in strategy.satisfied)
            )
        if strategy.missing:
            lines.append("  critères manquants :")
            lines.extend(f"    - {criterion.describe()}" for criterion in strategy.missing)
        if strategy.verdict is Verdict.ECHEC:
            lines.append("  -> campagne en échec : retour à TASK-064, aucune promotion par dépit.")
    return "\n".join(lines)


def _paper_days(engine: Engine, *, at: datetime, market: str | None) -> list[DailyPerformance]:
    """The daily paper buckets up to `at`; the day of `at` itself is included."""
    rows = DailyPerformanceStore(engine).between(_EPOCH, at + timedelta(days=1))
    return [
        row
        for row in rows
        if row.mode is CAMPAIGN_MODE and (market is None or row.market == market)
    ]


def _first_day(days: Iterable[DailyPerformance]) -> datetime | None:
    first: datetime | None = None
    for row in days:
        day = day_floor(row.day)
        if first is None or day < first:
            first = day
    return first


def _paper_trades(
    engine: Engine, *, at: datetime, market: str | None, start: datetime | None
) -> list[Trade]:
    """Closed paper trades in [start, at); the first paper day bounds the read."""
    window_start = start if start is not None else _EPOCH
    read = ReportData(engine).trades_between(window_start, at)
    return [
        trade
        for _, trade in read
        if trade.mode is CAMPAIGN_MODE and (market is None or trade.symbol == market)
    ]


def _by_market_and_strategy(trades: list[Trade]) -> dict[tuple[str, str], list[Trade]]:
    """The same two axes the rest of reporting uses, from the shared `analytics` package."""
    grouped: dict[tuple[str, str], list[Trade]] = {}
    for market, bucket in group(trades, Axis.MARKET).items():
        for ref, strategy_trades in group(bucket, Axis.STRATEGY).items():
            grouped[(market, ref)] = strategy_trades
    return grouped


def _calendar(
    days: list[DailyPerformance], trades: list[Trade]
) -> dict[tuple[str, str], tuple[datetime | None, datetime | None]]:
    """First and last paper day per pair, from the daily aggregates.

    A pair known only through `trades` (an aggregate that was never written) still gets its
    dates from the trades themselves: the campaign is reported, not silently dropped.
    """
    calendar: dict[tuple[str, str], tuple[datetime | None, datetime | None]] = {}
    for row in days:
        key = (row.market, row.ref)
        day = day_floor(row.day)
        first, last = calendar.get(key, (None, None))
        calendar[key] = (
            day if first is None or day < first else first,
            day if last is None or day > last else last,
        )
    for key, bucket in _by_market_and_strategy(trades).items():
        if key in calendar:
            continue
        moments = [day_floor(trade.closed_at) for trade in bucket]
        calendar[key] = (min(moments), max(moments))
    return calendar


def _require_aware(at: datetime) -> None:
    if at.tzinfo is None or at.utcoffset() is None:
        raise ValueError("`at` must be timezone-aware: every instant is UTC")


def _iso(moment: datetime | None) -> str | None:
    return None if moment is None else moment.isoformat()


def _money(value: Decimal) -> str:
    """Money travels as a two-decimal string: JSON has no decimal, and a float rounds."""
    return f"{value:.2f}"


def _signed(value: Decimal) -> str:
    return f"{value:+.2f}"


def _stamp(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%d %H:%M UTC")
