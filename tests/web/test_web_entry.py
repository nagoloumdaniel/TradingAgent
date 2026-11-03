"""The web entry point must always put the PIN page in front of the dashboard.

`create_app` is open when it is given no `pin_file` — that default keeps every embedder and
page test simple. The price is that forgetting to pass `PIN_FILE` in `main()` would publish the
dashboard unprotected, with nothing failing. This file is the guard: it runs the real `main()`
and checks what uvicorn was handed.
"""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tradingagent.web import app as web_app


def _app_from_entry_point(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> object:
    """Run the real `main()`, capturing the application uvicorn was handed."""
    env_path = tmp_path / ".env"
    env_path.write_text(f"DATABASE_URL=sqlite:///{tmp_path / 'agent.db'}\n", encoding="utf-8")
    captured: dict[str, object] = {}
    monkeypatch.setattr(web_app, "ENV_FILE", env_path)
    monkeypatch.setattr(web_app, "PIN_FILE", tmp_path / "web_pin.json")

    def fake_run(application: object, **kwargs: object) -> None:
        captured["app"] = application

    monkeypatch.setattr(web_app.uvicorn, "run", fake_run)
    assert web_app.main([]) == 0
    assert "app" in captured, "uvicorn.run was never called"
    return captured["app"]


def test_the_entry_point_protects_the_dashboard(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    application = _app_from_entry_point(monkeypatch, tmp_path)

    assert application.state.access_protected is True  # type: ignore[attr-defined]
    with TestClient(application, follow_redirects=False) as client:  # type: ignore[arg-type]
        response = client.get("/")
        assert response.status_code == 303
        assert response.headers["location"] == "/login"


def test_the_entry_point_offers_to_choose_a_pin_on_first_visit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    application = _app_from_entry_point(monkeypatch, tmp_path)

    with TestClient(application) as client:  # type: ignore[arg-type]
        page = client.get("/login")
        assert page.status_code == 200
        assert 'name="confirm"' in page.text
        assert not (tmp_path / "web_pin.json").exists()  # nothing written until a PIN is chosen


def test_the_pin_file_lives_under_the_ignored_data_directory() -> None:
    assert web_app.PIN_FILE.parent.name == "data"
    assert web_app.PIN_FILE.name == "web_pin.json"
