"""Où le dashboard trouve les rapports EA quand personne ne le lui dit.

Le tableau de bord lit le pont des Experts Advisors. Jusqu'au 2026-10-08 cette information
n'arrivait que par `--ea-reports-dir` ou `TRADINGAGENT_EA_REPORTS_DIR`, alors que l'agent la
tient déjà de `EA_FILES_DIR` dans `.env` : la même donnée demandée deux fois, donc oubliée une
fois. Observé en vrai — le dashboard affichait « le pont n'est pas configuré » pendant que
l'EA portait un arrêt local, et l'opérateur ne pouvait pas le voir.

Ces tests fixent l'ordre de résolution : l'argument explicite, puis la variable
d'environnement, puis `.env`. Et ils vérifient le seul point qui compte : une valeur qui ne
mène nulle part rend `None` au lieu de faire croire à un pont configuré.
"""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from tests.web import seed

from tradingagent.web.app import (
    EA_REPORTS_ENV_VAR,
    create_app,
    resolve_ea_reports_dir,
)


def bridge(tmp_path: Path, *, with_reports: bool = True) -> Path:
    """Un pont EA minimal : la racine porte les sous-répertoires du protocole."""
    root = tmp_path / "TradingAgent"
    (root / "state").mkdir(parents=True)
    if with_reports:
        (root / "reports").mkdir()
    return root


def test_the_explicit_argument_wins(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = bridge(tmp_path, with_reports=True)
    monkeypatch.setenv(EA_REPORTS_ENV_VAR, str(root))

    resolved = resolve_ea_reports_dir(root / "reports", env_file=tmp_path / "absent.env")

    assert resolved == root / "reports"


def test_the_environment_variable_is_used_when_no_argument_is_given(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    reports = bridge(tmp_path, with_reports=True) / "reports"
    monkeypatch.setenv(EA_REPORTS_ENV_VAR, str(reports))

    assert resolve_ea_reports_dir(None, env_file=tmp_path / "absent.env") == reports


def test_a_declared_bridge_root_resolves_to_its_reports_subdirectory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`EA_FILES_DIR` désigne la racine du pont, pas `reports/` : c'est le pont qui est stocké."""
    root = bridge(tmp_path, with_reports=True)
    env_file = tmp_path / ".env"
    env_file.write_text(f"EA_FILES_DIR={root}\n", encoding="utf-8")
    monkeypatch.delenv(EA_REPORTS_ENV_VAR, raising=False)

    assert resolve_ea_reports_dir(None, env_file=env_file) == root / "reports"


def test_the_env_file_is_read_without_mt5_credentials(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Le dashboard démarre sans identifiant de courtier : lire un chemin n'est pas trader."""
    root = bridge(tmp_path, with_reports=True)
    env_file = tmp_path / ".env"
    env_file.write_text(f"EA_FILES_DIR={root}\n", encoding="utf-8")
    monkeypatch.delenv(EA_REPORTS_ENV_VAR, raising=False)

    # Aucun MT5_LOGIN ni MT5_PASSWORD dans ce fichier, et la résolution aboutit quand même.
    assert resolve_ea_reports_dir(None, env_file=env_file) is not None


def test_nothing_declared_means_no_bridge_instead_of_an_invented_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("DATABASE_URL=postgresql://example\n", encoding="utf-8")
    monkeypatch.delenv(EA_REPORTS_ENV_VAR, raising=False)

    assert resolve_ea_reports_dir(None, env_file=env_file) is None


def test_a_path_that_leads_nowhere_is_refused_rather_than_shown_as_a_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Un chemin mort doit rester « pas de pont » : sinon l'opérateur lit un tableau vide
    comme s'il était un état, alors qu'il ne mesure rien."""
    env_file = tmp_path / ".env"
    env_file.write_text(f"EA_FILES_DIR={tmp_path / 'inexistant'}\n", encoding="utf-8")
    monkeypatch.delenv(EA_REPORTS_ENV_VAR, raising=False)

    assert resolve_ea_reports_dir(None, env_file=env_file) is None


def test_an_empty_declaration_is_not_a_bridge(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("EA_FILES_DIR=\n", encoding="utf-8")
    monkeypatch.delenv(EA_REPORTS_ENV_VAR, raising=False)

    assert resolve_ea_reports_dir(None, env_file=env_file) is None


def test_the_system_page_shows_the_halt_the_bridge_declares(
    populated: seed.Seeded, engine: Engine, ea_halted_reports_dir: Path
) -> None:
    """Le cas réel du 2026-10-08 : un EA en arrêt local doit être lisible, pas invisible."""
    with TestClient(
        create_app(engine, now=lambda: seed.NOW, ea_reports_dir=ea_halted_reports_dir)
    ) as client:
        page = client.get("/system")

    assert page.status_code == 200
    assert "Arrêt local" in page.text
    assert "actif" in page.text
