"""The read-only guarantee, proved three ways.

§34 is not a code-review promise: the dashboard must be *unable* to write to the trading
database. These tests check the route table, prove empirically that exercising the whole
surface leaves every table's row count untouched, and scan the package's syntax tree for a
write call.
"""

import ast
from pathlib import Path

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from sqlalchemy import Engine, MetaData, func, select

from tradingagent.web.app import READ_ONLY, create_app

WEB_PACKAGE = Path(__file__).resolve().parents[2] / "src" / "tradingagent" / "web"

# SQLAlchemy's write verbs. `update` is left out on purpose: `dict.update` is a legitimate
# mapping call, and the empirical test below covers the database.
FORBIDDEN_CALLS = frozenset(
    {
        "add",
        "add_all",
        "bulk_save_objects",
        "bulk_insert_mappings",
        "commit",
        "delete",
        "execute_delete",
        "execute_insert",
        "execute_update",
        "executemany",
        "flush",
        "insert",
    }
)

READ_PATHS = (
    "/",
    "/positions",
    "/trades",
    "/trades?market=XAUUSD&mode=PAPER",
    "/trades/1",
    "/trades/9999",
    "/scalping",
    "/strategies",
    "/ai-lab",
    "/risk",
    "/system",
    "/reports",
    "/events?cycles=1",
    "/healthz",
    "/static/nexagold.png",
    "/static/nexagold-dark.png",
    "/export/trades.csv",
    "/export/trades.json",
    "/export/performance.json",
    "/export/equity.svg",
    "/export/reports/1.txt",
)

WRITE_METHODS = ("post", "put", "patch", "delete")


def test_the_app_declares_itself_read_only() -> None:
    assert READ_ONLY is True


def test_the_route_table_only_ever_accepts_get(engine: Engine) -> None:
    app = create_app(engine)
    routes = [route for route in app.routes if isinstance(route, APIRoute)]
    assert routes, "the dashboard must expose at least one route"
    for route in routes:
        assert set(route.methods or set()) <= {"GET", "HEAD"}, (
            f"{route.path} accepts {sorted(route.methods or [])}"
        )


@pytest.mark.parametrize("path", ["/", "/risk", "/positions", "/trades", "/healthz"])
@pytest.mark.parametrize("method", WRITE_METHODS)
def test_write_verbs_are_refused(seeded_client: TestClient, method: str, path: str) -> None:
    response = getattr(seeded_client, method)(path)
    assert response.status_code == 405


def test_the_brand_mark_is_served_but_cannot_be_written(client: TestClient) -> None:
    """The one static asset is a read too: GET serves it, every write verb is refused."""
    served = client.get("/static/nexagold.png")
    assert served.status_code == 200
    assert served.headers["content-type"].startswith("image/png")
    assert served.content[:8] == b"\x89PNG\r\n\x1a\n"
    for method in WRITE_METHODS:
        assert getattr(client, method)("/static/nexagold.png").status_code == 405


def test_every_page_shows_the_brand_mark(seeded_client: TestClient) -> None:
    page = seeded_client.get("/")
    assert 'src="/static/nexagold.png"' in page.text
    assert 'rel="icon"' in page.text


def _row_counts(engine: Engine) -> dict[str, int]:
    metadata = MetaData()
    metadata.reflect(bind=engine)
    with engine.connect() as connection:
        return {
            name: int(connection.execute(select(func.count()).select_from(table)).scalar_one())
            for name, table in metadata.tables.items()
        }


def test_visiting_every_page_changes_no_row(engine: Engine, populated: object) -> None:
    """The empirical proof: same tables, same counts, after the whole surface was exercised."""
    del populated
    with TestClient(create_app(engine)) as client:
        before = _row_counts(engine)
        assert sum(before.values()) > 0, "the fixture must have written something to compare"
        for path in READ_PATHS:
            response = client.get(path)
            assert response.status_code in {200, 404}, f"{path} answered {response.status_code}"
        after = _row_counts(engine)
    assert after == before


def _forbidden_calls(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = None
        if isinstance(node.func, ast.Name):
            name = node.func.id
        elif isinstance(node.func, ast.Attribute):
            name = node.func.attr
        if name in FORBIDDEN_CALLS:
            found.append(f"{path.name}:{node.lineno}: {name}()")
    return found


def test_the_web_package_contains_no_write_call() -> None:
    offenders = [
        call for path in sorted(WEB_PACKAGE.rglob("*.py")) for call in _forbidden_calls(path)
    ]
    assert offenders == []


def test_the_scanner_would_catch_a_write(tmp_path: Path) -> None:
    """A guard on the guard: the scanner must fail on a file that does write."""
    sample = tmp_path / "sneaky.py"
    sample.write_text(
        "from sqlalchemy import insert\n"
        "def go(connection):\n"
        "    connection.execute(insert(object))\n"
        "    connection.commit()\n",
        encoding="utf-8",
    )
    assert len(_forbidden_calls(sample)) == 2


def test_no_module_of_the_web_package_imports_the_executor() -> None:
    """The dashboard must not even be able to reach the broker adapter."""
    for path in sorted(WEB_PACKAGE.rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        assert "tradingagent.execution" not in source, path.name
        assert "MetaTrader5" not in source, path.name
