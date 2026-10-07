"""Serve the dashboard against a disposable, seeded database — for design review only.

It never touches `.env` or the production database: it builds a temporary SQLite file,
applies the migrations, fills it with the same fixtures the web tests use, and serves the
read-only app on a local port.

    uv run python scripts/preview_dashboard.py [--port 8799] [--host 127.0.0.1]
"""

import argparse
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import uvicorn  # noqa: E402
from tests.web.seed import seed  # noqa: E402

from tradingagent.storage.engine import create_database_engine  # noqa: E402
from tradingagent.storage.migrate import upgrade  # noqa: E402
from tradingagent.web.app import create_app  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="preview_dashboard", description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8799)
    args = parser.parse_args(argv)

    database = Path(tempfile.mkdtemp(prefix="tradingagent-preview-")) / "preview.db"
    url = f"sqlite:///{database}"
    upgrade(url)
    engine = create_database_engine(url)
    seed(engine)
    print(f"preview database: {database}")
    print(f"dashboard: http://{args.host}:{args.port}/")
    uvicorn.run(create_app(engine), host=args.host, port=args.port, log_level="warning")
    engine.dispose()
    return 0


if __name__ == "__main__":
    sys.exit(main())
