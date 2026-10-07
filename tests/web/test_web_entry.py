"""The web entry point must read the access token from the same `.env` as everything else.

This guards a real defect. `TRADINGAGENT_WEB_TOKEN` was documented, honoured by
`create_app`, and verified by tests that injected it — but `uv run tradingagent-web` never
passed it. `pydantic-settings` parses `.env` into a model and does not export it to the
process, so `configured_token()` read an `os.environ` that never held it.

The failure mode was the worst kind: the operator sets the token, believes the dashboard is
protected, and the port stays open to anyone who can reach it.
"""

from pathlib import Path

from fastapi.testclient import TestClient

from tradingagent.web import app as web_app

# A test value, not a credential: it only ever exists inside this file and a temporary
# `.env`. The scanner flags the shape, hence the allowlist.
TOKEN = "un-jeton-de-test-suffisamment-long"  # pragma: allowlist secret


def _write_env(tmp_path: Path, *, with_token: bool) -> Path:
    lines = [f"DATABASE_URL=sqlite:///{tmp_path / 'agent.db'}"]
    if with_token:
        lines.append(f"TRADINGAGENT_WEB_TOKEN={TOKEN}")
    path = tmp_path / ".env"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _app_from_entry_point(monkeypatch, env_path: Path):
    """Run the real `main()`, capturing the application uvicorn was handed."""
    captured: dict[str, object] = {}
    monkeypatch.setattr(web_app, "ENV_FILE", env_path)

    def fake_run(application, **kwargs: object) -> None:
        captured["app"] = application

    monkeypatch.setattr(web_app.uvicorn, "run", fake_run)
    assert web_app.main([]) == 0
    assert "app" in captured, "uvicorn.run was never called"
    return captured["app"]


def test_the_entry_point_protects_the_dashboard_when_env_declares_a_token(
    tmp_path: Path, monkeypatch
) -> None:
    application = _app_from_entry_point(monkeypatch, _write_env(tmp_path, with_token=True))
    with TestClient(application) as client:
        assert client.get("/").status_code == 401
        # A valid bearer gets past the middleware; the empty database then answers 503,
        # which is a different failure and proof that the credential was accepted.
        granted = client.get("/", headers={"Authorization": f"Bearer {TOKEN}"})
        assert granted.status_code != 401


def test_the_entry_point_leaves_the_dashboard_open_without_a_token(
    tmp_path: Path, monkeypatch
) -> None:
    """The documented default on 127.0.0.1: reaching the port is enough."""
    application = _app_from_entry_point(monkeypatch, _write_env(tmp_path, with_token=False))
    with TestClient(application) as client:
        assert client.get("/").status_code != 401


def test_a_blank_token_counts_as_absent(tmp_path: Path, monkeypatch) -> None:
    """`KEY=` must not lock the dashboard behind an empty credential."""
    path = _write_env(tmp_path, with_token=False)
    path.write_text(
        path.read_text(encoding="utf-8") + "TRADINGAGENT_WEB_TOKEN=\n", encoding="utf-8"
    )
    application = _app_from_entry_point(monkeypatch, path)
    with TestClient(application) as client:
        assert client.get("/").status_code != 401


def test_the_environment_wins_over_the_file_like_every_other_setting(
    tmp_path: Path, monkeypatch
) -> None:
    """Precedence is the ordinary one: process environment, then `.env`, then nothing.

    `pydantic-settings` resolves every key that way, so the token behaves like `DATABASE_URL`
    rather than inventing a rule of its own. The regression this file guards is not about
    which one wins — it is that before the fix the file was not consulted at all.
    """
    monkeypatch.setenv("TRADINGAGENT_WEB_TOKEN", "un-jeton-d-ambiance-tres-long-mais-vrai")
    application = _app_from_entry_point(monkeypatch, _write_env(tmp_path, with_token=True))
    with TestClient(application) as client:
        # The ambient variable is the effective one…
        ambient = "Bearer un-jeton-d-ambiance-tres-long-mais-vrai"
        assert client.get("/", headers={"Authorization": ambient}).status_code != 401
        # …and the file's value does not also grant access.
        assert client.get("/", headers={"Authorization": f"Bearer {TOKEN}"}).status_code == 401


def test_an_ambient_variable_still_applies_when_the_file_is_silent(
    tmp_path: Path, monkeypatch
) -> None:
    """The documented escape hatch: a supervisor can export the token rather than edit the
    file. Silence in `.env` therefore means "look at the environment", not "no protection".
    """
    monkeypatch.setenv("TRADINGAGENT_WEB_TOKEN", "un-jeton-d-ambiance-tres-long-mais-vrai")
    application = _app_from_entry_point(monkeypatch, _write_env(tmp_path, with_token=False))
    with TestClient(application) as client:
        assert client.get("/").status_code == 401
        ambient = "Bearer un-jeton-d-ambiance-tres-long-mais-vrai"
        assert client.get("/", headers={"Authorization": ambient}).status_code != 401
