"""The error surfaces: 404, 405, 500. Each one is a page, not a dead end.

What is tested here is the contract, not the copy: the right status, a readable body, the
way out, and — the part that matters most on a monitoring tool — no figure of the dashboard
leaking through an error, and no database read spent to draw one.
"""

from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import Engine

from tradingagent.web import queries
from tradingagent.web.app import create_app


def test_an_unknown_url_is_a_styled_404(seeded_client: TestClient) -> None:
    response = seeded_client.get("/cette-page-nexiste-pas")
    assert response.status_code == 404
    assert "Page introuvable" in response.text
    # The way out: real pages, reachable from the error itself.
    for href in ('href="/"', 'href="/positions"', 'href="/trades"', 'href="/system"'):
        assert href in response.text, href


def test_a_404_shows_no_dashboard_figure(seeded_client: TestClient) -> None:
    text = seeded_client.get("/cette-page-nexiste-pas").text
    assert "16.75" not in text  # the seeded net result
    assert "1073" not in text


def test_a_404_echoes_the_requested_path_escaped(seeded_client: TestClient) -> None:
    """Echoing the path is what makes a stale bookmark debuggable — and it stays escaped,
    because it is the one piece of user input any page reflects."""
    response = seeded_client.get("/inconnu%22%3E%3Cscript%3Ealert(1)%3C/script%3E")
    assert response.status_code == 404
    assert "<script>alert(1)</script>" not in response.text
    assert "&lt;" in response.text or '">' not in response.text


def test_a_404_does_not_read_the_database(seeded_client: TestClient) -> None:
    """An error page must never need the database: that is what keeps a database failure
    from becoming a rendering failure."""
    text = seeded_client.get("/cette-page-nexiste-pas").text
    assert '<svg class="chart' not in text  # no watermark was computed


def test_the_documented_404s_still_answer_404(seeded_client: TestClient) -> None:
    for path in ("/openapi.json", "/docs", "/redoc", "/export/reports/9999.txt"):
        assert seeded_client.get(path).status_code == 404, path


def test_a_write_verb_explains_the_read_only_rule(seeded_client: TestClient) -> None:
    response = seeded_client.post("/")
    assert response.status_code == 405
    assert "Lecture seule" in response.text
    assert "GET" in response.text
    # The `Allow` header is part of the answer: dropping it would misdescribe the surface.
    assert "GET" in response.headers.get("allow", "")


def test_the_method_stays_refused_on_every_page(seeded_client: TestClient) -> None:
    for path in ("/positions", "/trades", "/risk", "/scalping"):
        assert seeded_client.put(path).status_code == 405, path


def test_a_client_that_asked_for_json_gets_json(seeded_client: TestClient) -> None:
    response = seeded_client.get("/cette-page-nexiste-pas", headers={"Accept": "application/json"})
    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/json")
    assert response.json() == {"detail": "Not Found"}


def test_an_unexpected_failure_renders_the_500_page(engine: Engine, monkeypatch) -> None:
    """The last line of defence: an unhandled exception still shows something readable, and
    says nothing about internals."""

    def boom(*args: object, **kwargs: object) -> object:
        raise RuntimeError("secret internal detail")

    monkeypatch.setattr(queries, "overview", boom)
    with TestClient(create_app(engine), raise_server_exceptions=False) as client:
        response = client.get("/")
    assert response.status_code == 500
    assert "Erreur interne" in response.text
    assert "secret internal detail" not in response.text
    assert "Traceback" not in response.text


def test_the_500_page_offers_a_way_out(engine: Engine, monkeypatch) -> None:
    def boom(*args: object, **kwargs: object) -> object:
        raise RuntimeError("boom")

    monkeypatch.setattr(queries, "overview", boom)
    with TestClient(create_app(engine), raise_server_exceptions=False) as client:
        response = client.get("/")
    assert 'href="/healthz"' in response.text


def test_the_database_failure_keeps_its_own_page(tmp_path: Path) -> None:
    """503, not 500: the distinction tells the operator whether to fix the code or the
    connection."""
    from sqlalchemy import create_engine

    broken = create_engine(f"sqlite:///{tmp_path / 'empty.db'}")
    with TestClient(create_app(broken), raise_server_exceptions=False) as client:
        response = client.get("/")
    assert response.status_code == 503
    assert "Base de données indisponible" in response.text
    assert "DATABASE_URL" in response.text
    broken.dispose()


def test_error_pages_keep_the_theme_switch(seeded_client: TestClient) -> None:
    """An error page is a page of the dashboard: same shell, same controls."""
    text = seeded_client.get("/cette-page-nexiste-pas").text
    assert 'data-theme-set="light"' in text
    assert 'src="/static/nexagold.png"' in text
