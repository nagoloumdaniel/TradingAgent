from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory

MIGRATIONS = Path(__file__).resolve().parent / "migrations"


def alembic_config(url: str) -> Config:
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS))
    # Alembic stores options through configparser, which treats "%" as interpolation:
    # URL-encoded passwords contain it.
    config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    return config


def upgrade(url: str, revision: str = "head") -> None:
    command.upgrade(alembic_config(url), revision)


def downgrade(url: str, revision: str = "base") -> None:
    command.downgrade(alembic_config(url), revision)


def head_revision() -> str:
    """The revision the code expects, read from the migration scripts themselves.

    Used by the diagnostic to tell an operator whether their database is behind the code,
    which is otherwise only discovered by a failing query.
    """
    return str(ScriptDirectory.from_config(alembic_config("sqlite://")).get_current_head())
