"""The researcher proposes falsifiable, parametric hypotheses — it never changes anything.

Every proposal cites the data that motivated it and is stored as PROPOSED. A model may only
add commentary; it cannot create an order, touch a stop or promote a strategy (cahier v3 §39).
"""

import asyncio
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from tradingagent.ai.lab_store import AnalysisRecord, LabStore
from tradingagent.ai.researcher import (
    BacktestEvidence,
    StrategyResearcher,
    ValidationEvidence,
)
from tradingagent.core.states import AnalysisKind, ProposalStatus
from tradingagent.storage.ai_calls import AiReply
from tradingagent.storage.engine import create_database_engine
from tradingagent.storage.migrate import upgrade
from tradingagent.storage.models import (
    AiAnalysisRow,
    AiProposalRow,
    OrderRow,
    PositionRow,
    SignalRow,
    TradeRow,
)

T0 = datetime(2026, 10, 7, 6, 0, tzinfo=UTC)


@pytest.fixture
def engine(tmp_path: Path) -> Iterator[Engine]:
    url = f"sqlite:///{tmp_path / 'researcher.db'}"
    upgrade(url)
    built = create_database_engine(url)
    yield built
    built.dispose()


class FakeClient:
    def __init__(self, reply: str | Exception = "") -> None:
        self.reply = reply
        self.calls = 0

    async def complete(self, system: str, user: str) -> AiReply:
        self.calls += 1
        if isinstance(self.reply, Exception):
            raise self.reply
        return AiReply(text=self.reply, model="fake-model", input_tokens=10, output_tokens=5)


def healthy_metrics(**overrides: float) -> dict[str, float]:
    metrics = {
        "max_drawdown_eur": 40.0,
        "profit_factor_net": 1.8,
        "trades": 120.0,
        "parameter_dispersion": 0.1,
        "out_of_sample_retention": 0.9,
    }
    metrics.update(overrides)
    return metrics


def an_evidence(**overrides: object) -> BacktestEvidence:
    values: dict[str, object] = {
        "market": "frxXAUUSD",
        "ref": "witness@1.0.0",
        "parameters": {"risk_per_trade_pct": 1.0, "atr_stop_multiple": 2.0},
        "metrics": healthy_metrics(),
        "dataset_id": "ds-2026-10",
        "validations": (),
    }
    values.update(overrides)
    return BacktestEvidence(**values)  # type: ignore[arg-type]


def a_researcher(engine: Engine, client: FakeClient | None = None) -> StrategyResearcher:
    return StrategyResearcher(LabStore(engine), client)


def proposal_rows(engine: Engine) -> list[AiProposalRow]:
    with Session(engine) as session:
        return list(session.scalars(select(AiProposalRow).order_by(AiProposalRow.id)).all())


def analysis_rows(engine: Engine) -> list[AiAnalysisRow]:
    with Session(engine) as session:
        return list(session.scalars(select(AiAnalysisRow).order_by(AiAnalysisRow.id)).all())


def trading_counts(engine: Engine) -> dict[str, int]:
    with Session(engine) as session:
        return {
            "signals": session.scalar(select(func.count()).select_from(SignalRow)) or 0,
            "orders": session.scalar(select(func.count()).select_from(OrderRow)) or 0,
            "positions": session.scalar(select(func.count()).select_from(PositionRow)) or 0,
            "trades": session.scalar(select(func.count()).select_from(TradeRow)) or 0,
        }


# --- the deterministic rules --------------------------------------------------


def test_a_drawdown_beyond_the_limit_asks_to_cut_the_risk_per_trade(engine: Engine) -> None:
    evidence = an_evidence(metrics=healthy_metrics(max_drawdown_eur=420.0))

    hypotheses = asyncio.run(a_researcher(engine).research(evidence, at=T0))

    assert len(hypotheses) == 1
    change = hypotheses[0].proposed_change
    assert change["parameter"] == "risk_per_trade_pct"
    assert change["current_value"] == 1.0
    assert change["proposed_value"] == 0.5
    assert change["action"] == "decrease"
    assert any("max_drawdown_eur=420" in citation for citation in change["evidence"])
    assert change["falsification"]
    assert "walk_forward" in change["validation"]
    assert "drawdown" in hypotheses[0].hypothesis


def test_a_failed_robustness_stage_asks_to_freeze_the_most_sensitive_parameter(
    engine: Engine,
) -> None:
    evidence = an_evidence(
        validations=(
            ValidationEvidence(
                stage="parameter_robustness",
                passed=False,
                detail={"most_sensitive": "atr_stop_multiple"},
            ),
        )
    )

    hypotheses = asyncio.run(a_researcher(engine).research(evidence, at=T0))

    assert len(hypotheses) == 1
    change = hypotheses[0].proposed_change
    assert change["parameter"] == "atr_stop_multiple"
    assert change["action"] == "freeze"
    assert change["current_value"] == 2.0
    assert any("parameter_robustness" in citation for citation in change["evidence"])


def test_a_high_parameter_dispersion_alone_is_enough_to_propose(engine: Engine) -> None:
    evidence = an_evidence(metrics=healthy_metrics(parameter_dispersion=0.9))

    hypotheses = asyncio.run(a_researcher(engine).research(evidence, at=T0))

    assert len(hypotheses) == 1
    assert hypotheses[0].proposed_change["action"] == "freeze"


def test_a_healthy_backtest_proposes_nothing(engine: Engine) -> None:
    evidence = an_evidence(validations=(ValidationEvidence(stage="walk_forward", passed=True),))

    hypotheses = asyncio.run(a_researcher(engine).research(evidence, at=T0))

    assert hypotheses == ()
    assert proposal_rows(engine) == []
    assert analysis_rows(engine) == []


def test_a_degradation_analysis_asks_to_enable_a_regime_filter(engine: Engine) -> None:
    store = LabStore(engine)
    analysis_id = store.record_analysis(
        AnalysisRecord(
            kind=AnalysisKind.DEGRADATION,
            market="frxXAUUSD",
            ref="witness@1.0.0",
            model="deterministic",
            request={},
            findings={"kind": "degradation", "figures": {"recent_win_rate": 0.0}},
            created_at=T0,
        )
    )

    hypotheses = asyncio.run(a_researcher(engine).research(an_evidence(), at=T0))

    assert len(hypotheses) == 1
    change = hypotheses[0].proposed_change
    assert change["parameter"] == "regime_filter"
    assert change["action"] == "enable"
    assert change["proposed_value"] is True
    assert hypotheses[0].analysis_id is not None
    assert any(f"ai_analyses:{analysis_id}" in citation for citation in change["evidence"])


def test_a_degradation_analysis_of_another_strategy_is_ignored(engine: Engine) -> None:
    LabStore(engine).record_analysis(
        AnalysisRecord(
            kind=AnalysisKind.DEGRADATION,
            market="frxXAUUSD",
            ref="other@9.9.9",
            model="deterministic",
            request={},
            findings={"kind": "degradation"},
            created_at=T0,
        )
    )

    hypotheses = asyncio.run(a_researcher(engine).research(an_evidence(), at=T0))

    assert hypotheses == ()


def test_every_proposal_is_recorded_as_proposed(engine: Engine) -> None:
    evidence = an_evidence(metrics=healthy_metrics(max_drawdown_eur=420.0))

    asyncio.run(a_researcher(engine).research(evidence, at=T0))

    rows = proposal_rows(engine)
    assert len(rows) == 1
    assert rows[0].status is ProposalStatus.PROPOSED
    assert rows[0].decided_by is None
    assert rows[0].market == "frxXAUUSD"
    assert rows[0].ref == "witness@1.0.0"


def test_every_proposal_cites_its_motivating_data(engine: Engine) -> None:
    evidence = an_evidence(metrics=healthy_metrics(max_drawdown_eur=420.0))

    hypotheses = asyncio.run(a_researcher(engine).research(evidence, at=T0))

    for hypothesis in hypotheses:
        change = hypothesis.proposed_change
        assert change["evidence"], "une proposition sans donnée citée n'est pas falsifiable"
        assert change["falsification"]
        assert "witness@1.0.0" in " ".join(change["evidence"])
    assert hypotheses[0].hypothesis


def test_the_researcher_is_deterministic_without_any_api_key(engine: Engine) -> None:
    evidence = an_evidence(metrics=healthy_metrics(max_drawdown_eur=420.0))

    first = asyncio.run(a_researcher(engine).research(evidence, at=T0))
    second = asyncio.run(a_researcher(engine).research(evidence, at=T0))

    assert first[0].proposed_change == second[0].proposed_change
    assert first[0].hypothesis == second[0].hypothesis


# --- the model only comments --------------------------------------------------


def test_the_model_commentary_is_journalled_without_changing_the_proposal(engine: Engine) -> None:
    client = FakeClient('{"commentary": "Le drawdown dépasse le seuil accepté."}')

    hypotheses = asyncio.run(
        a_researcher(engine, client).research(
            an_evidence(metrics=healthy_metrics(max_drawdown_eur=420.0)), at=T0
        )
    )

    rows = analysis_rows(engine)
    assert len(rows) == 1
    assert rows[0].kind is AnalysisKind.HYPOTHESIS
    assert rows[0].response == "Le drawdown dépasse le seuil accepté."
    assert hypotheses[0].proposed_change["proposed_value"] == 0.5


def test_a_broken_model_still_leaves_the_deterministic_proposal(engine: Engine) -> None:
    client = FakeClient(RuntimeError("api down"))

    hypotheses = asyncio.run(
        a_researcher(engine, client).research(
            an_evidence(metrics=healthy_metrics(max_drawdown_eur=420.0)), at=T0
        )
    )

    assert len(hypotheses) == 1
    assert hypotheses[0].proposed_change["proposed_value"] == 0.5
    assert analysis_rows(engine)[0].response is None


def test_a_malicious_model_cannot_create_an_order_or_promote_a_strategy(engine: Engine) -> None:
    malicious = (
        '{"commentary": "ok", "create_order": {"symbol": "frxXAUUSD", "volume": 100},'
        ' "promote": "witness@1.0.0", "stop_loss": 0.0, "set_status": "promoted"}'
    )
    client = FakeClient(malicious)

    asyncio.run(
        a_researcher(engine, client).research(
            an_evidence(metrics=healthy_metrics(max_drawdown_eur=420.0)), at=T0
        )
    )

    assert trading_counts(engine) == {"signals": 0, "orders": 0, "positions": 0, "trades": 0}
    rows = proposal_rows(engine)
    assert len(rows) == 1
    # The proposal stays PROPOSED: only the validation system may promote it.
    assert rows[0].status is ProposalStatus.PROPOSED
    assert rows[0].proposed_change["proposed_value"] == 0.5
    assert set(analysis_rows(engine)[0].findings["overrun_attempts"]) == {
        "create_order",
        "promote",
        "stop_loss",
        "set_status",
    }


# --- structural guards --------------------------------------------------------


def test_the_researcher_never_writes_a_manifest() -> None:
    source = (
        Path(__file__).resolve().parents[2] / "src" / "tradingagent" / "ai" / "researcher.py"
    ).read_text(encoding="utf-8")

    for symbol in (
        "write_text",
        "import yaml",
        "ManifestSpec",
        "tradingagent.research",
        "tradingagent.strategies",
        "strategy_registry",
    ):
        assert symbol not in source, f"researcher.py references {symbol}"
