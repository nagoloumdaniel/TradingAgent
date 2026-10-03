from alembic import context

from tradingagent.storage.engine import create_database_engine
from tradingagent.storage.models import Base

config = context.config
url = config.get_main_option("sqlalchemy.url")
if not url:
    raise RuntimeError("sqlalchemy.url is not set: run migrations through storage.migrate")

engine = create_database_engine(url)
with engine.connect() as connection:
    # render_as_batch: SQLite cannot ALTER most constraints, so Alembic rebuilds tables.
    context.configure(
        connection=connection,
        target_metadata=Base.metadata,
        render_as_batch=True,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()
engine.dispose()
