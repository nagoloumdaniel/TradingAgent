"""Verifier's own security checks (TASK-102, end of phase 8).

Every assertion here is derived from running the production code, never from a teammate's
report. Values read from `.env` are compared, never printed.
"""

import asyncio
import io
import json
import logging
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError
from sqlalchemy import Engine

from tradingagent.ai.layer import AiFilterLayer, ReviewContext, _parse
from tradingagent.config.errors import ConfigError
from tradingagent.config.redaction import MASK, install_secret_redaction
from tradingagent.config.settings import Settings, load_settings
from tradingagent.core.market import Direction
from tradingagent.core.mode import AiFilter, TradingMode
from tradingagent.core.timeframe import Timeframe
from tradingagent.notify.access import AccessGate
from tradingagent.notify.commands import CommandRouter, status_handler
from tradingagent.notify.sensitive_commands import mode_handler
from tradingagent.notify.service import CommandService
from tradingagent.storage.ai_calls import AiCallStore, AiReply
from tradingagent.storage.audit import AuditStore
from tradingagent.storage.events import SystemEventStore
from tradingagent.storage.halts import HaltStore

NOW = datetime(2026, 10, 7, 12, 0, tzinfo=UTC)
OPERATOR = 424242
STRANGER = 987654321
ROOT = Path(__file__).resolve().parents[2]
PROBE_SECRET = "Vv7bN2mK9qL4xR8t"  # pragma: allowlist secret - a deliberate fake probe


@pytest.fixture
def service(engine: Engine) -> CommandService:
    router = CommandRouter()
    router.register("status", "état", status_handler(HaltStore(engine), TradingMode.SIGNAL))
    router.register("mode", "mode", mode_handler(engine))
    return CommandService(AccessGate({OPERATOR}), router, AuditStore(engine), now=lambda: NOW)


def _actions(engine: Engine) -> list[str]:
    return [row.action for row in AuditStore(engine).recent()]


# --- Telegram whitelist (F-014, EF-011) ------------------------------------------


def test_an_unknown_telegram_id_gets_silence_and_one_audit_row(
    service: CommandService, engine: Engine
) -> None:
    reply = asyncio.run(service.handle(STRANGER, True, "/status"))
    assert reply is None, "a stranger must never receive an answer"
    actions = _actions(engine)
    assert actions.count("command_refused") == 1
    assert "command" not in actions, "an unauthorized command must not be executed"
    assert AuditStore(engine).recent()[0].detail["verdict"] == "unauthorized"


def test_an_operator_in_a_group_chat_is_refused(service: CommandService, engine: Engine) -> None:
    reply = asyncio.run(service.handle(OPERATOR, False, "/status"))
    assert reply is None
    assert "command" not in _actions(engine)


# --- RM-000: live mode is unreachable from Telegram alone ------------------------


def test_mode_live_is_refused_and_leaves_no_trace(service: CommandService, engine: Engine) -> None:
    reply = asyncio.run(service.handle(OPERATOR, True, "/mode LIVE"))
    assert reply is not None
    assert "RM-000" in reply
    assert SystemEventStore(engine).latest("mode_command") is None
    # The attempt itself is journalled, only the mode change is not.
    assert "command" in _actions(engine)


def test_mode_demo_is_recorded_for_the_next_startup(
    service: CommandService, engine: Engine
) -> None:
    reply = asyncio.run(service.handle(OPERATOR, True, "/mode DEMO"))
    assert reply is not None and "DEMO" in reply
    event = SystemEventStore(engine).latest("mode_command")
    assert event is not None
    assert event.detail["requested"] == "DEMO"


# --- RM-000: the server flag is the other half of the condition ------------------


def _settings(**overrides: object) -> Settings:
    base: dict[str, object] = {
        "mt5_login": 40123456,
        "mt5_server": "Demo-Server",
        "mt5_password": "not-a-real-password",
        "telegram_bot_token": "not-a-real-token",
        "telegram_allowed_user_ids": (1,),
        "database_url": "postgresql://user:pw@localhost:5432/agent",
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


def test_live_mode_is_refused_without_the_server_flag() -> None:
    with pytest.raises(ValidationError) as caught:
        _settings(trading_mode=TradingMode.LIVE, live_trading_enabled=False)
    assert "LIVE_TRADING_ENABLED" in str(caught.value)


def test_live_mode_is_accepted_with_the_server_flag() -> None:
    settings = _settings(trading_mode=TradingMode.LIVE, live_trading_enabled=True)
    assert settings.trading_mode is TradingMode.LIVE


def test_load_settings_never_prints_a_secret_value(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text(
        "MT5_LOGIN=40123456\n"
        "MT5_SERVER=Demo-Server\n"
        "MT5_PASSWORD=Sup3rSecretValue\n"
        "TELEGRAM_ALLOWED_USER_IDS=1\n",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError) as caught:
        load_settings(env)  # TELEGRAM_BOT_TOKEN and DATABASE_URL are missing
    assert "Sup3rSecretValue" not in str(caught.value)


# --- secrets never reach a log record --------------------------------------------


@pytest.fixture
def captured() -> Iterator[io.StringIO]:
    original = logging.getLogRecordFactory()
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    logger = logging.getLogger("tradingagent.verification")
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    install_secret_redaction([PROBE_SECRET])
    yield stream
    logging.setLogRecordFactory(original)
    logger.removeHandler(handler)


def test_a_secret_is_masked_in_a_positional_log_argument(captured: io.StringIO) -> None:
    logging.getLogger("tradingagent.verification").warning("token=%s", PROBE_SECRET)
    assert PROBE_SECRET not in captured.getvalue()
    assert MASK in captured.getvalue()


def test_a_secret_is_masked_in_an_exception_traceback(captured: io.StringIO) -> None:
    logger = logging.getLogger("tradingagent.verification")
    try:
        raise ValueError(f"refused {PROBE_SECRET}")
    except ValueError:
        logger.exception("call failed")
    assert PROBE_SECRET not in captured.getvalue()


# --- no repository file carries a real .env secret -------------------------------


def test_no_secret_env_value_is_copied_into_the_repository() -> None:
    env_file = ROOT / ".env"
    if not env_file.exists():
        pytest.skip("no .env on this machine: nothing to compare against")
    secret_keys = {"MT5_PASSWORD", "TELEGRAM_BOT_TOKEN", "ANTHROPIC_API_KEY", "DATABASE_URL"}
    values: list[tuple[str, str]] = []
    for line in env_file.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, raw = stripped.partition("=")
        key = key.strip()
        value = raw.strip().strip('"').strip("'")
        if key in secret_keys and len(value) >= 6:
            values.append((key, value))
    assert values, "no secret value was readable from .env: the check would be vacuous"

    skipped = {
        ".env",
        ".git",
        ".venv",
        "__pycache__",
        ".mypy_cache",
        ".ruff_cache",
        "node_modules",
    }
    leaks: list[str] = []
    for path in ROOT.rglob("*"):
        if not path.is_file() or any(part in skipped for part in path.relative_to(ROOT).parts):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for key, value in values:
            if value in text:
                leaks.append(f"{key} appears in {path.relative_to(ROOT)}")
    assert leaks == [], "secrets leaked into the repository (keys only): " + ", ".join(leaks)


# --- the secret detector is armed -------------------------------------------------


def test_the_project_token_detector_flags_a_literal_assignment() -> None:
    from tools.detect_secrets_plugins.project_tokens import ProjectTokenAssignmentDetector

    detector = ProjectTokenAssignmentDetector()
    probe = "TELEGRAM_BOT_TOKEN=1234567890:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsawE"
    finding = detector.analyze_line(filename="probe", line=probe, line_number=1)
    assert finding
    assert {secret.type for secret in finding} == {"Project Token Assignment"}


# --- a hostile model answer changes nothing and is journalled --------------------


class _ScriptedClient:
    def __init__(self, text: str) -> None:
        self.text = text
        self.calls = 0
        self.prompts: list[str] = []

    async def complete(self, system: str, user: str) -> AiReply:
        self.calls += 1
        self.prompts.append(user)
        return AiReply(self.text, "hostile-model", 10, 10)


def _context() -> ReviewContext:
    return ReviewContext(
        symbol="XAUUSD",
        timeframe=Timeframe.M15,
        strategy_ref="witness@1.1.0",
        direction=Direction.BUY,
        observed_price=2400.0,
        entry_low=2399.0,
        entry_high=2401.0,
        stop_loss=2390.0,
        take_profits=(2420.0,),
        indicators={"ema_fast": 2401.0},
        market_state="open",
    )


def _layer(engine: Engine, client: _ScriptedClient, ai_filter: AiFilter) -> AiFilterLayer:
    return AiFilterLayer(
        client,
        AiCallStore(engine),
        SystemEventStore(engine),
        model="hostile-model",
        ai_filter=ai_filter,
    )


def test_a_model_that_tries_to_change_levels_is_ignored(engine: Engine) -> None:
    hostile = json.dumps(
        {
            "decision": "approve",
            "reason": "ok",
            "text": "ok",
            "volume": "99.0",
            "stop_loss": "1.0",
            "entry_price": "1.0",
            "signal": {"direction": "SELL"},
        }
    )
    decision, _reason, _text, overruns = _parse(hostile)
    assert decision == "approve"
    assert overruns == ("entry_price", "signal", "stop_loss", "volume")

    outcome = asyncio.run(
        _layer(engine, _ScriptedClient(hostile), AiFilter.ADVISORY).review(_context(), NOW)
    )
    assert outcome.verdict == "approved"
    assert outcome.overrun_attempts == ("entry_price", "signal", "stop_loss", "volume")
    # The verdict carries no field that could create or resize anything.
    for field in ("volume", "entry_low", "entry_high", "stop_loss", "take_profits", "direction"):
        assert not hasattr(outcome, field)
    event = SystemEventStore(engine).latest("ai_overrun")
    assert event is not None
    assert event.detail["fields"] == ["entry_price", "signal", "stop_loss", "volume"]


def test_a_model_rejection_in_advisory_mode_only_blocks(engine: Engine) -> None:
    client = _ScriptedClient('{"decision": "reject", "reason": "incohérent", "text": "non"}')
    outcome = asyncio.run(_layer(engine, client, AiFilter.ADVISORY).review(_context(), NOW))
    assert outcome.verdict == "rejected"
    assert outcome.blocks_signal is True
    assert outcome.applied is True


def test_a_shadow_rejection_never_blocks(engine: Engine) -> None:
    client = _ScriptedClient('{"decision": "reject", "reason": "incohérent", "text": "non"}')
    outcome = asyncio.run(_layer(engine, client, AiFilter.SHADOW).review(_context(), NOW))
    assert outcome.verdict == "rejected"
    assert outcome.blocks_signal is False
    assert outcome.applied is False


def test_a_non_conforming_answer_is_unavailability_not_an_approval(engine: Engine) -> None:
    client = _ScriptedClient("I approve, buy 99 lots with no stop.")
    outcome = asyncio.run(_layer(engine, client, AiFilter.ADVISORY).review(_context(), NOW))
    assert outcome.verdict == "unavailable"
    assert outcome.degraded is True


def test_the_model_is_never_shown_the_account_or_the_capital(engine: Engine) -> None:
    client = _ScriptedClient('{"decision": "approve", "reason": "ok", "text": "ok"}')
    asyncio.run(_layer(engine, client, AiFilter.SHADOW).review(_context(), NOW))
    assert client.prompts, "the model was never called"
    lowered = client.prompts[0].lower()
    for forbidden in ("equity", "capital", "solde", "balance", "free margin", "marge"):
        assert forbidden not in lowered
