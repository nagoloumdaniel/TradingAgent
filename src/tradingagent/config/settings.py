from pathlib import Path
from typing import Annotated, Any

from pydantic import Field, SecretStr, ValidationError, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict
from sqlalchemy import make_url

from tradingagent.config.errors import ConfigError
from tradingagent.core.mode import TradingMode


class Settings(BaseSettings):
    model_config = SettingsConfigDict(frozen=True, extra="ignore")

    mt5_login: int = Field(gt=0)
    mt5_server: str
    # Investor (read-only) password until execution starts in phase 8.
    mt5_password: SecretStr
    mt5_terminal_path: Path | None = None
    telegram_bot_token: SecretStr
    telegram_allowed_user_ids: Annotated[tuple[int, ...], NoDecode] = Field(min_length=1)
    anthropic_api_key: SecretStr
    # Hosted PostgreSQL, required: no silent fallback to a local file. The URL carries the
    # database password, so it is kept secret and redacted from logs.
    database_url: SecretStr
    trading_mode: TradingMode = TradingMode.SIGNAL
    live_trading_enabled: bool = False

    @field_validator("mt5_password", "telegram_bot_token", "anthropic_api_key", "database_url")
    @classmethod
    def _secret_not_blank(cls, value: SecretStr) -> SecretStr:
        if not value.get_secret_value().strip():
            raise ValueError("must not be blank")
        return value

    @field_validator("mt5_server")
    @classmethod
    def _server_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be blank")
        return value.strip()

    @field_validator("mt5_terminal_path", mode="before")
    @classmethod
    def _blank_path_is_unset(cls, value: Any) -> Any:
        return None if isinstance(value, str) and not value.strip() else value

    @field_validator("telegram_allowed_user_ids", mode="before")
    @classmethod
    def _split_user_ids(cls, value: Any) -> Any:
        if isinstance(value, str):
            return tuple(part.strip() for part in value.split(",") if part.strip())
        return value

    @model_validator(mode="after")
    def _live_requires_server_flag(self) -> "Settings":
        if self.trading_mode is TradingMode.LIVE and not self.live_trading_enabled:
            raise ValueError(
                "TRADING_MODE=LIVE requires LIVE_TRADING_ENABLED=true on the server (RM-000)"
            )
        return self

    def secret_values(self) -> tuple[str, ...]:
        secrets = [
            secret.get_secret_value()
            for secret in (self.mt5_password, self.telegram_bot_token, self.anthropic_api_key)
        ]
        database_password = make_url(self.database_url.get_secret_value()).password
        if database_password:
            secrets.append(str(database_password))
        return tuple(secrets)


class DatabaseSettings(BaseSettings):
    """Only the database, for operator tools that must work while the agent is down."""

    model_config = SettingsConfigDict(frozen=True, extra="ignore")

    database_url: SecretStr

    @field_validator("database_url")
    @classmethod
    def _not_blank(cls, value: SecretStr) -> SecretStr:
        if not value.get_secret_value().strip():
            raise ValueError("must not be blank")
        return value


class BotSettings(DatabaseSettings):
    """What the Telegram bot needs, and nothing else: it can start before the AI key exists."""

    telegram_bot_token: SecretStr
    telegram_allowed_user_ids: Annotated[tuple[int, ...], NoDecode] = Field(min_length=1)
    trading_mode: TradingMode = TradingMode.SIGNAL

    @field_validator("telegram_bot_token")
    @classmethod
    def _token_not_blank(cls, value: SecretStr) -> SecretStr:
        if not value.get_secret_value().strip():
            raise ValueError("must not be blank")
        return value

    @field_validator("telegram_allowed_user_ids", mode="before")
    @classmethod
    def _split_ids(cls, value: Any) -> Any:
        if isinstance(value, str):
            return tuple(part.strip() for part in value.split(",") if part.strip())
        return value


def load_bot_settings(env_file: Path | None = None) -> BotSettings:
    try:
        return BotSettings(_env_file=env_file)
    except ValidationError as error:
        raise ConfigError(_describe(error)) from None


def load_database_settings(env_file: Path | None = None) -> DatabaseSettings:
    try:
        return DatabaseSettings(_env_file=env_file)
    except ValidationError as error:
        raise ConfigError(_describe(error)) from None


def load_settings(env_file: Path | None = None) -> Settings:
    try:
        return Settings(_env_file=env_file)
    except ValidationError as error:
        raise ConfigError(_describe(error)) from None


def _describe(error: ValidationError) -> str:
    # Only locations and messages, never the offending input: it may be a secret.
    lines = []
    for detail in error.errors(include_input=False, include_url=False):
        location = str(detail["loc"][0]).upper() if detail["loc"] else "SETTINGS"
        lines.append(f"  {location}: {detail['msg']}")
    return "Invalid environment configuration:\n" + "\n".join(lines)
