import pytest

from tradingagent.config.errors import ConfigError
from tradingagent.config.settings import Settings, load_settings
from tradingagent.core.mode import TradingMode

DERIV_TOKEN = "Zq8wR3tY6uI9oP2"  # pragma: allowlist secret
TELEGRAM_TOKEN = "9876543210:BBHdqTcvCH1vGWJxfSeofSAs0K5PALDsawZ"  # pragma: allowlist secret
ANTHROPIC_KEY = "sk-ant-api03-Xk9pQ2rT7vLm4nB8wZ1cY6hJ3dF5gS0aE"  # pragma: allowlist secret

VALID_ENV = {
    "DERIV_APP_ID": "12345",
    "DERIV_API_TOKEN": DERIV_TOKEN,
    "TELEGRAM_BOT_TOKEN": TELEGRAM_TOKEN,
    "TELEGRAM_ALLOWED_USER_IDS": "111, 222",
    "ANTHROPIC_API_KEY": ANTHROPIC_KEY,
}
MANAGED = (
    *VALID_ENV,
    "DATABASE_URL",
    "TRADING_MODE",
    "LIVE_TRADING_ENABLED",
)


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    for name in MANAGED:
        monkeypatch.delenv(name, raising=False)
    for name, value in VALID_ENV.items():
        monkeypatch.setenv(name, value)
    return monkeypatch


def error_of(env: pytest.MonkeyPatch) -> str:
    with pytest.raises(ConfigError) as caught:
        load_settings()
    return str(caught.value)


def test_valid_environment_loads(env: pytest.MonkeyPatch) -> None:
    settings = load_settings()
    assert settings.deriv_app_id == 12345
    assert settings.telegram_allowed_user_ids == (111, 222)
    assert settings.deriv_api_token.get_secret_value() == DERIV_TOKEN


def test_default_mode_is_signal_and_live_is_off(env: pytest.MonkeyPatch) -> None:
    settings = load_settings()
    assert settings.trading_mode is TradingMode.SIGNAL
    assert settings.live_trading_enabled is False


@pytest.mark.parametrize(
    "name", ["DERIV_APP_ID", "DERIV_API_TOKEN", "TELEGRAM_BOT_TOKEN", "ANTHROPIC_API_KEY"]
)
def test_missing_variable_blocks_startup_and_is_named(env: pytest.MonkeyPatch, name: str) -> None:
    env.delenv(name)
    assert name in error_of(env)


@pytest.mark.parametrize("blank", ["", "   "])
@pytest.mark.parametrize("name", ["DERIV_API_TOKEN", "TELEGRAM_BOT_TOKEN", "ANTHROPIC_API_KEY"])
def test_blank_secret_copied_from_template_blocks_startup(
    env: pytest.MonkeyPatch, name: str, blank: str
) -> None:
    env.setenv(name, blank)
    assert name in error_of(env)


@pytest.mark.parametrize("ids", ["", " , ", "111,abc"])
def test_invalid_telegram_whitelist_blocks_startup(env: pytest.MonkeyPatch, ids: str) -> None:
    env.setenv("TELEGRAM_ALLOWED_USER_IDS", ids)
    assert "TELEGRAM_ALLOWED_USER_IDS" in error_of(env)


def test_unknown_mode_blocks_startup(env: pytest.MonkeyPatch) -> None:
    env.setenv("TRADING_MODE", "YOLO")
    assert "TRADING_MODE" in error_of(env)


def test_live_mode_requires_server_flag(env: pytest.MonkeyPatch) -> None:
    env.setenv("TRADING_MODE", "LIVE")
    assert "LIVE_TRADING_ENABLED" in error_of(env)


def test_live_mode_with_server_flag_loads(env: pytest.MonkeyPatch) -> None:
    env.setenv("TRADING_MODE", "LIVE")
    env.setenv("LIVE_TRADING_ENABLED", "true")
    assert load_settings().trading_mode is TradingMode.LIVE


def test_error_message_never_contains_a_secret(env: pytest.MonkeyPatch) -> None:
    env.setenv("DERIV_APP_ID", "not-a-number")
    env.setenv("TRADING_MODE", "LIVE")
    message = error_of(env)
    for secret in (DERIV_TOKEN, TELEGRAM_TOKEN, ANTHROPIC_KEY):
        assert secret not in message


def test_settings_repr_never_contains_a_secret(env: pytest.MonkeyPatch) -> None:
    settings = load_settings()
    rendered = f"{settings!r} {settings!s}"
    for secret in (DERIV_TOKEN, TELEGRAM_TOKEN, ANTHROPIC_KEY):
        assert secret not in rendered


def test_secret_values_exposes_every_secret_for_redaction(env: pytest.MonkeyPatch) -> None:
    assert set(load_settings().secret_values()) == {DERIV_TOKEN, TELEGRAM_TOKEN, ANTHROPIC_KEY}


def test_settings_are_immutable(env: pytest.MonkeyPatch) -> None:
    settings: Settings = load_settings()
    with pytest.raises(ValueError, match="frozen"):
        settings.trading_mode = TradingMode.LIVE  # type: ignore[misc]
