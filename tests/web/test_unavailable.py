"""When the database is unreachable, the dashboard states it instead of raising.

An operator watching a monitoring host must see *why* the page is empty; a stack trace in a
browser is not an answer. The failure stays a failure: HTTP 503, and still no write.
"""

from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import create_engine

from tradingagent.web.app import create_app


def test_an_unmigrated_database_yields_a_503_page(tmp_path: Path) -> None:
    """An empty SQLite file has no tables: every read fails, every page says so politely."""
    engine = create_engine(f"sqlite:///{tmp_path / 'empty.db'}")
    with TestClient(create_app(engine), raise_server_exceptions=False) as client:
        response = client.get("/")
        assert response.status_code == 503
        assert "Base de données indisponible" in response.text
        assert "lecture seule" in response.text
        assert "/healthz" in response.text
    engine.dispose()


def test_the_failure_page_is_not_reached_on_a_healthy_database(seeded_client: TestClient) -> None:
    assert "Base de données indisponible" not in seeded_client.get("/").text
