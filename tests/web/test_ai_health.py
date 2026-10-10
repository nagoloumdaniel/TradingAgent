"""The AI lab must say whether the model actually answered.

Established on 2026-10-10 against the production database: ``ai_calls`` held 22 rows — 15
answered ("approved", never a rejection) and **7 failures**, the last six of them consecutive
and all of them ``Error code: 402 … Insufficient Balance`` — while ``/ai-lab`` displayed
"Analyses 0 observations enregistrées". An empty table reads like a quiet week; the truth was
a filter that had stopped answering a day earlier and had nobody to tell. A monitoring page
that cannot tell "nothing happened" from "everything failed" hides exactly the failure it
exists to show.

The panel therefore reads ``ai_calls`` — the table the runtime already writes — and states the
provider's own message verbatim. It covers every market on purpose: a refused key or an empty
balance is not an instrument.
"""

from datetime import timedelta
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy import Engine
from tests.web.seed import (
    INSUFFICIENT_BALANCE,
    NOW,
    AiCallSpec,
    Seeded,
    a_failed_call,
    add_ai_calls,
)

from tradingagent.core.mode import AiFilter
from tradingagent.web import format as display
from tradingagent.web import queries
from tradingagent.web.app import create_app

# --- the query --------------------------------------------------------------------------


def test_a_database_that_never_called_the_model_says_so(engine: Engine) -> None:
    health = queries.ai_call_health(engine)

    assert health.calls == 0
    assert health.failed == 0
    assert health.never_called is True
    assert health.healthy is False  # nothing was verified, so nothing is declared healthy
    assert health.cost_eur == Decimal(0)
    assert health.last_called_at is None
    assert health.failures == ()


def test_the_verdicts_the_failures_and_the_cost_are_counted(
    engine: Engine, populated: Seeded
) -> None:
    add_ai_calls(
        engine,
        (
            AiCallSpec(called_at=NOW - timedelta(hours=3), verdict="approved"),
            AiCallSpec(called_at=NOW - timedelta(hours=2), verdict="rejected"),
            a_failed_call(NOW - timedelta(hours=1)),
        ),
    )

    health = queries.ai_call_health(engine)

    assert health.calls == 3
    assert health.answered == 2
    assert health.approved == 1
    assert health.rejected == 1
    assert health.failed == 1
    # A refusal is not a failure: the model answered and its verdict was journalled.
    assert health.healthy is False
    assert health.cost_eur == Decimal("0.000246")  # only the two answered calls were billed
    assert health.last_called_at == NOW - timedelta(hours=1)
    assert health.models == ("deepseek-flash",)
    assert health.filters == ("shadow",)


def test_the_provider_message_is_kept_verbatim(engine: Engine, populated: Seeded) -> None:
    add_ai_calls(engine, (a_failed_call(NOW - timedelta(minutes=30)),))

    health = queries.ai_call_health(engine)

    assert health.failures[0].error == INSUFFICIENT_BALANCE
    assert health.failures[0].latency_ms == 612
    assert health.failures[0].ai_filter == "shadow"


def test_only_the_most_recent_failures_are_returned_and_the_total_is_kept(
    engine: Engine, populated: Seeded
) -> None:
    add_ai_calls(
        engine,
        tuple(a_failed_call(NOW - timedelta(minutes=minute)) for minute in range(1, 9)),
    )

    health = queries.ai_call_health(engine, failures_shown=5)

    assert len(health.failures) == 5
    assert health.failed == 8  # the total is never bounded, only the list is
    assert health.failures[0].called_at == NOW - timedelta(minutes=1)  # newest first
    assert health.failures[-1].called_at == NOW - timedelta(minutes=5)


def test_the_modes_actually_recorded_are_reported_not_a_configured_default(
    engine: Engine, populated: Seeded
) -> None:
    add_ai_calls(
        engine,
        (
            AiCallSpec(called_at=NOW - timedelta(hours=2), ai_filter=AiFilter.SHADOW),
            AiCallSpec(called_at=NOW - timedelta(hours=1), ai_filter=AiFilter.ADVISORY),
        ),
    )

    assert queries.ai_call_health(engine).filters == ("advisory", "shadow")


def test_a_healthy_filter_is_declared_healthy_when_every_call_answered(
    engine: Engine, populated: Seeded
) -> None:
    add_ai_calls(
        engine,
        (
            AiCallSpec(called_at=NOW - timedelta(hours=2), verdict="approved"),
            AiCallSpec(called_at=NOW - timedelta(hours=1), verdict="rejected"),
        ),
    )

    health = queries.ai_call_health(engine)

    assert health.healthy is True
    assert health.failed == 0
    assert health.never_called is False


# --- the page ---------------------------------------------------------------------------


def test_the_page_states_that_no_call_was_recorded(seeded_client: TestClient) -> None:
    body = seeded_client.get("/ai-lab").text

    assert "Appels au modèle" in body
    assert "Aucun appel au modèle" in body


def test_the_page_reports_the_failures_with_the_provider_message(
    engine: Engine, populated: Seeded
) -> None:
    add_ai_calls(
        engine,
        (
            AiCallSpec(called_at=NOW - timedelta(hours=2), verdict="approved"),
            a_failed_call(NOW - timedelta(hours=1)),
        ),
    )

    with TestClient(create_app(engine, now=lambda: NOW)) as client:
        body = client.get("/ai-lab").text

    assert "1 appel(s) sur 2 n'ont pas abouti" in body
    assert "Insufficient Balance" in body
    assert "402" in body
    # The reader must be told what the failure did NOT do: the deterministic rules decided.
    assert "règles déterministes" in body


def test_the_page_shows_the_mode_that_was_really_used(engine: Engine, populated: Seeded) -> None:
    add_ai_calls(engine, (AiCallSpec(called_at=NOW - timedelta(hours=1)),))

    with TestClient(create_app(engine, now=lambda: NOW)) as client:
        body = client.get("/ai-lab").text

    assert "Mode effectif" in body
    assert "shadow" in body


def test_the_page_shows_the_cumulated_cost(engine: Engine, populated: Seeded) -> None:
    add_ai_calls(
        engine,
        (
            AiCallSpec(called_at=NOW - timedelta(hours=2), cost_eur=Decimal("0.012")),
            AiCallSpec(called_at=NOW - timedelta(hours=1), cost_eur=Decimal("0.003")),
        ),
    )

    with TestClient(create_app(engine, now=lambda: NOW)) as client:
        body = client.get("/ai-lab").text

    # 0.015 EUR, rendered by the dashboard's own money format rather than by a literal.
    assert display.money(Decimal("0.015")) in body


def test_the_panel_says_it_covers_every_market(engine: Engine, populated: Seeded) -> None:
    add_ai_calls(engine, (AiCallSpec(called_at=NOW - timedelta(hours=1)),))

    with TestClient(create_app(engine, now=lambda: NOW)) as client:
        body = client.get("/ai-lab", params={"market": "XAUUSD"}).text

    assert "Tous marchés confondus" in body


def test_no_failure_is_shown_as_a_success(engine: Engine, populated: Seeded) -> None:
    add_ai_calls(engine, (a_failed_call(NOW - timedelta(minutes=5)),))

    with TestClient(create_app(engine, now=lambda: NOW)) as client:
        body = client.get("/ai-lab").text

    assert "1 appel(s) sur 1 n'ont pas abouti" in body
    assert "Aucune réponse exploitable" in body
