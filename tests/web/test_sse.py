"""The SSE feed: frame format, polling behaviour, and the endpoint itself."""

import json
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, insert
from tests.web import seed

from tradingagent.core.states import Severity
from tradingagent.storage.models import SystemEventRow
from tradingagent.web.sse import RETRY_HINT, EventStream, Frame


def test_a_frame_is_rendered_in_the_wire_format() -> None:
    assert Frame("positions", "{}").render() == "event: positions\ndata: {}\n\n"


def test_the_interval_must_be_positive(engine: Engine) -> None:
    with pytest.raises(ValueError, match="strictly positive"):
        EventStream(engine, interval_seconds=0)


def test_poll_reports_the_open_positions(populated: seed.Seeded, engine: Engine) -> None:
    stream = EventStream(engine, now=lambda: seed.NOW)
    positions, alerts = stream.poll(seed.NOW, None)
    assert positions.event == "positions"
    assert alerts.event == "alerts"
    payload = json.loads(positions.data)
    assert payload["columns"][0] == "Marché"
    assert [row[0] for row in payload["rows"]] == [seed.XAU, seed.BTC]
    assert payload["rows"][0][4] == "53.00 €"  # already formatted: the browser computes nothing
    assert payload["rows"][0][7] == "02:00:00"  # age, computed server-side
    assert payload["at"] == seed.NOW.isoformat()


def test_poll_only_reports_alerts_recorded_after_the_last_cycle(
    populated: seed.Seeded, engine: Engine
) -> None:
    stream = EventStream(engine, now=lambda: seed.NOW)
    _, initial = stream.poll(seed.NOW, None)
    assert len(json.loads(initial.data)["rows"]) == 2  # warning and critical, not info

    with engine.begin() as connection:
        connection.execute(
            insert(SystemEventRow).values(
                kind="halted",
                severity=Severity.CRITICAL,
                detail={"scope": "global"},
                occurred_at=seed.NOW + timedelta(seconds=1),
            )
        )
    _, later = stream.poll(seed.NOW + timedelta(seconds=2), seed.NOW)
    rows = json.loads(later.data)["rows"]
    assert len(rows) == 1
    assert rows[0][2] == "halted"
    assert "global" in rows[0][3]


def test_frames_are_bounded_when_a_cycle_count_is_given(
    populated: seed.Seeded, engine: Engine
) -> None:
    slept: list[float] = []
    stream = EventStream(engine, now=lambda: seed.NOW, sleep=slept.append, interval_seconds=3.0)
    body = list(stream.frames(cycles=1))
    assert body[0] == RETRY_HINT
    assert len(body) == 3  # retry hint + positions + alerts
    assert body[1].startswith("event: positions")
    assert body[2].startswith("event: alerts")
    assert slept == []


def test_frames_wait_between_cycles(populated: seed.Seeded, engine: Engine) -> None:
    slept: list[float] = []
    stream = EventStream(engine, now=lambda: seed.NOW, sleep=slept.append, interval_seconds=1.5)
    body = list(stream.frames(cycles=2))
    assert len(body) == 1 + 2 * 2
    assert slept == [1.5]


def test_the_stream_repeats_the_interval_it_was_built_with(engine: Engine) -> None:
    assert EventStream(engine, interval_seconds=4.0).interval_seconds == 4.0


def test_the_events_endpoint_streams_a_bounded_response(seeded_client: TestClient) -> None:
    response = seeded_client.get("/events", params={"cycles": 1})
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["cache-control"] == "no-cache, no-transform"
    body = response.text
    assert body.startswith(RETRY_HINT)
    assert "event: positions" in body
    assert "event: alerts" in body
    payload = json.loads(body.split("event: positions\ndata: ", 1)[1].split("\n\n", 1)[0])
    assert [row[0] for row in payload["rows"]] == [seed.XAU, seed.BTC]


def test_the_events_endpoint_works_on_an_empty_database(client: TestClient) -> None:
    response = client.get("/events", params={"cycles": 1})
    assert response.status_code == 200
    payload = json.loads(response.text.split("data: ", 1)[1].split("\n\n", 1)[0])
    assert payload["rows"] == []
    assert payload["columns"][0] == "Marché"


@pytest.mark.parametrize("cycles", [0, -1, 101])
def test_an_out_of_range_cycle_count_is_refused(seeded_client: TestClient, cycles: int) -> None:
    assert seeded_client.get("/events", params={"cycles": cycles}).status_code == 422
