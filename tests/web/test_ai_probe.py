import asyncio
from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from tradingagent.ai.probe import run_probe
from tradingagent.storage.ai_calls import AiReply
from tradingagent.web.app import create_app


class _Refused(Exception):
    status_code = 402


class _Client:
    def __init__(self, error: Exception | None = None, text: str = "ok") -> None:
        self._error, self._text = error, text

    async def complete(self, system: str, user: str) -> AiReply:
        if self._error is not None:
            raise self._error
        return AiReply(text=self._text, model="deepseek-flash", input_tokens=1, output_tokens=1)


def test_probe_without_a_key_says_so() -> None:
    result = asyncio.run(run_probe(None))
    assert not result.ok
    assert "DEEPSEEK_API_KEY" in result.detail


def test_probe_reports_the_provider_refusal_with_a_hint() -> None:
    result = asyncio.run(run_probe(_Client(_Refused("Insufficient Balance"))))
    assert not result.ok
    assert "solde" in result.detail
    assert "Insufficient Balance" in result.detail


def test_probe_succeeds_on_a_reply() -> None:
    result = asyncio.run(run_probe(_Client()))
    assert result.ok
    assert result.model == "deepseek-flash"


@pytest.fixture
def client(engine: Engine) -> Iterator[TestClient]:
    app = create_app(
        engine,
        now=lambda: datetime(2026, 11, 3, 12, 0, tzinfo=UTC),
        ai_client=_Client(_Refused("Insufficient Balance")),
    )
    with TestClient(app) as test_client:
        yield test_client


def test_the_test_button_route_returns_the_failure_as_json(client: TestClient) -> None:
    body = client.post("/ai-lab/test").json()
    assert body["ok"] is False
    assert "Insufficient Balance" in body["detail"]


def test_the_ai_lab_page_has_the_test_button(client: TestClient) -> None:
    assert 'id="ai-test-btn"' in client.get("/ai-lab").text
