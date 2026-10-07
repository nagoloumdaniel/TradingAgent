"""The trade replay page (§28): one signal, and everything that followed it.

The fixtures are the shared ones: ``seeded_client`` renders the page against the
representative dataset, and ``replay_telemetry`` adds the measurement hops the runtime
records — latencies included — for the trade that has them.

Two questions the page must answer, in the words of the brief: *why was this trade
executed?*, and *which version of the strategy executed it?* (§27). A third case matters
just as much: a trade with no fill and no AI analysis must state the empty sections, never
fail.
"""

from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from tests.web import seed

from tradingagent.web import queries
from tradingagent.web.queries import _elapsed_ms
from tradingagent.web.views import replay_timeline

# State the seed writes and the page must show verbatim.
SIGNAL_REASON = "croisement de moyennes confirmé par le volume"
RISK_REASON = "dans les limites"
REFUSED_REASON = "plafond de positions atteint"
STRATEGY_HASH = "a" * 64


def _appended_at() -> datetime:
    """A moment no seeded chain uses: ``signals.idempotency_key`` is unique per timestamp."""
    return seed.NOW - timedelta(days=3, minutes=7)


def test_the_trades_list_links_to_the_replay(
    seeded_client: TestClient, seeded: seed.Seeded
) -> None:
    body = seeded_client.get("/trades").text
    assert f'href="/trades/{seeded.losing_signal_id}"' in body
    assert "rejouer" in body


def test_the_replay_renders_the_whole_chain(seeded_client: TestClient, seeded: seed.Seeded) -> None:
    body = seeded_client.get(f"/trades/{seeded.losing_signal_id}").text
    assert f"signal #{seeded.losing_signal_id}" in body
    # The signal, with the indicators that produced it.
    assert SIGNAL_REASON in body
    assert "ema_fast" in body
    assert "2649.50000" in body
    # The risk verdict, control by control, with its reason.
    assert RISK_REASON in body
    assert "daily_loss" in body
    assert "vérifié" in body
    assert "12.00" in body  # the margin the verdict recorded
    # The order: requested price, state and the broker's return code.
    assert "10009" in body
    assert "2650.00000" in body
    # The execution, the position and the close.
    assert str(900000 + seeded.losing_signal_id) in body
    assert "take_profit" in body
    assert "2655.00000" in body
    assert "-0.80 R" in body  # -4.00 profit / 5.00 risk, the storage layer's own ratio
    # The identity of the version that executed it (§27).
    assert seed.WITNESS in body
    assert "1.0.0" in body
    assert STRATEGY_HASH in body
    assert "validé sur 12 mois" in body


def test_the_replay_answers_why_it_was_executed(
    seeded_client: TestClient, seeded: seed.Seeded
) -> None:
    body = seeded_client.get(f"/trades/{seeded.losing_signal_id}").text
    assert "Pourquoi ce trade a-t-il été exécuté ?" in body
    assert "Autorisé" in body
    assert "Version de la stratégie qui a exécuté (§27)" in body


def test_the_timeline_is_chronological_and_carries_every_step(
    engine: Engine, seeded: seed.Seeded
) -> None:
    replay = queries.trade_replay(engine, seeded.losing_signal_id)
    assert replay is not None
    entries = replay_timeline(replay)
    moments = [entry.at for entry in entries]
    assert moments == sorted(moments)
    kinds = [entry.kind for entry in entries]
    assert kinds[0] == "signal"
    assert {"signal", "risk", "order", "execution", "position", "close"} <= set(kinds)
    assert entries[-1].kind == "close"


def test_the_timeline_orders_simultaneous_steps_by_their_nature(
    engine: Engine, seeded: seed.Seeded
) -> None:
    """The signal, the verdict and the order share one timestamp in this dataset."""
    replay = queries.trade_replay(engine, seeded.losing_signal_id)
    assert replay is not None
    same_second = [
        entry.kind for entry in replay_timeline(replay) if entry.at == replay.signal.generated_at
    ]
    assert same_second[:3] == ["signal", "risk", "order"]


def test_the_page_shows_the_chronology_it_built(
    seeded_client: TestClient, seeded: seed.Seeded
) -> None:
    body = seeded_client.get(f"/trades/{seeded.losing_signal_id}").text
    assert "Chronologie" in body
    assert "Signal généré" in body
    assert "Position clôturée" in body


def test_the_replay_shows_the_measured_hop_latencies(
    seeded_client: TestClient, seeded: seed.Seeded, replay_telemetry: seed.Seeded
) -> None:
    body = seeded_client.get(f"/trades/{seeded.xau_signal_id}").text
    assert "12 ms" in body
    assert "148 ms" in body
    assert "Ordre envoyé" in body
    assert "Exécuté" in body


def test_the_replay_shows_the_attached_ai_analysis(
    seeded_client: TestClient, seeded: seed.Seeded
) -> None:
    body = seeded_client.get(f"/trades/{seeded.xau_signal_id}").text
    assert "Analyse IA" in body
    assert "Analyse de perte" in body
    assert "claude-sonnet-4-5" in body
    assert "Le régime de marché a changé." in body
    assert "range" in body
    assert "0.01" in body  # the recorded cost
    assert "elle ne peut ni créer un signal" in body


def test_the_r_multiple_follows_the_stored_ratio(engine: Engine, seeded: seed.Seeded) -> None:
    replay = queries.trade_replay(engine, seeded.xau_signal_id)
    assert replay is not None
    assert replay.r_multiple is not None
    assert str(replay.r_multiple) == "2.5"  # 12.50 profit / 5.00 risk
    assert replay.total_duration is not None


def test_a_trade_without_execution_or_analysis_renders_every_empty_section(
    seeded_client: TestClient, engine: Engine, seeded: seed.Seeded
) -> None:
    """The brief's hard case: a closed trade with no fill and no AI analysis.

    Every table is append-only, so the fixture appends a chain rather than deleting a fill
    from one that has it.
    """
    signal_id = seed.add_chain(engine, generated_at=_appended_at(), with_execution=False)
    response = seeded_client.get(f"/trades/{signal_id}")
    assert response.status_code == 200
    body = response.text
    assert "Aucune exécution enregistrée pour cet ordre." in body
    assert "Aucune analyse IA rattachée à ce signal." in body
    assert "Aucun événement d'exécution enregistré" in body
    assert SIGNAL_REASON in body  # the rest of the replay is still there
    assert "Position ouverte" in body
    assert "take_profit" in body  # the close is recorded, only the fill is missing


def test_a_trade_never_closed_still_replays_its_chain(
    seeded_client: TestClient, engine: Engine, seeded: seed.Seeded
) -> None:
    """A position that never closed has no trade row: the page says so, the chain stays."""
    signal_id = seed.add_chain(engine, generated_at=_appended_at(), closed=False)

    response = seeded_client.get(f"/trades/{signal_id}")
    assert response.status_code == 200
    assert "Aucune clôture enregistrée : la position est ouverte ou n'a jamais existé." in (
        response.text
    )
    assert "Position ouverte" in response.text
    assert "take_profit" not in response.text.split("<h2>Clôture</h2>", 1)[1].split("<h2>", 1)[0]


def test_a_signal_refused_by_risk_replays_without_order_or_position(
    seeded_client: TestClient, seeded: seed.Seeded
) -> None:
    response = seeded_client.get(f"/trades/{seeded.refused_signal_id}")
    assert response.status_code == 200
    body = response.text
    assert REFUSED_REASON in body
    assert "Refusé" in body
    assert "max_open_positions" in body
    assert "6.00" in body  # the risk the refusal avoided, as recorded
    assert "Aucun ordre enregistré : le signal s'est arrêté avant l'envoi au courtier." in body
    assert "Aucune position enregistrée : le signal n'a pas été exécuté." in body
    assert "Aucune clôture enregistrée" in body


def test_an_unknown_signal_is_a_404_without_dashboard_figures(seeded_client: TestClient) -> None:
    response = seeded_client.get("/trades/9999")
    assert response.status_code == 404
    assert "Trade inconnu" in response.text
    assert "Aucun signal 9999 en base." in response.text
    assert "16.75" not in response.text  # no figure of the dashboard leaks through


def test_the_replay_module_returns_none_for_an_unknown_signal(empty_engine: Engine) -> None:
    assert queries.trade_replay(empty_engine, 4242) is None


def test_the_replay_of_an_empty_database_is_a_404(client: TestClient) -> None:
    assert client.get("/trades/1").status_code == 404


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ({"elapsed_ms": 148}, 148),
        ({"elapsed_ms": "12"}, 12),
        ({"elapsed_ms": True}, None),
        ({"elapsed_ms": "fast"}, None),
        ({"elapsed_ms": None}, None),
        ({}, None),
    ],
)
def test_a_stored_elapsed_ms_is_read_but_never_invented(
    payload: dict[str, object], expected: int | None
) -> None:
    assert _elapsed_ms(payload) == expected
