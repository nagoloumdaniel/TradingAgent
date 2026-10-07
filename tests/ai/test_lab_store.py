"""The AI Lab store persists observations and proposals, and nothing else (cahier v3 §39)."""

from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from tradingagent.ai.lab_store import (
    AnalysisRecord,
    LabStore,
    ProposalRecord,
)
from tradingagent.core.states import AnalysisKind, ProposalStatus
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
    url = f"sqlite:///{tmp_path / 'lab.db'}"
    upgrade(url)
    built = create_database_engine(url)
    yield built
    built.dispose()


def an_analysis(**overrides: object) -> AnalysisRecord:
    values: dict[str, object] = {
        "kind": AnalysisKind.LOSS_ANALYSIS,
        "market": "frxXAUUSD",
        "model": "deterministic",
        "request": {"trade": "t-1"},
        "findings": {"kind": "normal", "reason": "perte conforme au risque"},
        "created_at": T0,
        "ref": "witness@1.0.0",
    }
    values.update(overrides)
    return AnalysisRecord(**values)  # type: ignore[arg-type]


def a_proposal(**overrides: object) -> ProposalRecord:
    values: dict[str, object] = {
        "market": "frxXAUUSD",
        "hypothesis": "réduire le risque par trade divise le drawdown par deux",
        "proposed_change": {"parameter": "risk_per_trade_pct", "from": 1.0, "to": 0.5},
        "created_at": T0,
        "ref": "witness@1.0.0",
    }
    values.update(overrides)
    return ProposalRecord(**values)  # type: ignore[arg-type]


def counts(engine: Engine) -> dict[str, int]:
    with Session(engine) as session:
        return {
            "signals": session.scalar(select(func.count()).select_from(SignalRow)) or 0,
            "orders": session.scalar(select(func.count()).select_from(OrderRow)) or 0,
            "positions": session.scalar(select(func.count()).select_from(PositionRow)) or 0,
            "trades": session.scalar(select(func.count()).select_from(TradeRow)) or 0,
        }


# --- analyses -----------------------------------------------------------------


def test_an_analysis_is_persisted_with_its_findings(engine: Engine) -> None:
    store = LabStore(engine)

    analysis_id = store.record_analysis(an_analysis())

    rows = store.recent_analyses(kind=AnalysisKind.LOSS_ANALYSIS, market="frxXAUUSD")
    assert analysis_id > 0
    assert len(rows) == 1
    assert rows[0].id == analysis_id
    assert rows[0].kind is AnalysisKind.LOSS_ANALYSIS
    assert rows[0].ref == "witness@1.0.0"
    assert rows[0].findings["kind"] == "normal"
    assert rows[0].response is None
    assert rows[0].cost_eur is None


def test_an_analysis_cost_is_kept(engine: Engine) -> None:
    store = LabStore(engine)
    store.record_analysis(an_analysis(cost_eur=Decimal("0.000123"), response="commentaire"))

    row = store.recent_analyses(kind=AnalysisKind.LOSS_ANALYSIS, market="frxXAUUSD")[0]
    assert row.cost_eur == Decimal("0.000123")
    assert row.response == "commentaire"


def test_recent_analyses_filters_and_returns_the_most_recent_first(engine: Engine) -> None:
    store = LabStore(engine)
    store.record_analysis(an_analysis(market="frxXAUUSD"))
    store.record_analysis(an_analysis(market="BTCUSD"))
    newest = store.record_analysis(
        an_analysis(
            market="frxXAUUSD",
            kind=AnalysisKind.DEGRADATION,
            created_at=T0.replace(hour=7),
        )
    )

    by_kind = store.recent_analyses(kind=AnalysisKind.DEGRADATION)
    by_market = store.recent_analyses(market="frxXAUUSD", limit=1)

    assert [row.id for row in by_kind] == [newest]
    assert len(by_market) == 1
    assert by_market[0].id == newest
    assert len(store.recent_analyses()) == 3


# --- proposals ----------------------------------------------------------------


def test_a_proposal_is_always_recorded_as_proposed(engine: Engine) -> None:
    store = LabStore(engine)

    proposal_id = store.record_proposal(a_proposal())

    open_ids = [row.id for row in store.open_proposals()]
    assert open_ids == [proposal_id]
    with Session(engine) as session:
        row = session.get(AiProposalRow, proposal_id)
        assert row is not None
        assert row.status is ProposalStatus.PROPOSED
        assert row.decided_by is None
        assert row.decided_at is None


def test_a_proposal_keeps_its_parametric_change_and_reference(engine: Engine) -> None:
    store = LabStore(engine)
    analysis_id = store.record_analysis(an_analysis())
    proposal_id = store.record_proposal(a_proposal(analysis_id=analysis_id))

    with Session(engine) as session:
        row = session.get(AiProposalRow, proposal_id)
        assert row is not None
        assert row.analysis_id == analysis_id
        assert row.proposed_change["parameter"] == "risk_per_trade_pct"
        assert row.market == "frxXAUUSD"


def test_decide_proposal_records_the_actor_the_reason_and_the_moment(engine: Engine) -> None:
    store = LabStore(engine)
    proposal_id = store.record_proposal(a_proposal())

    store.decide_proposal(
        proposal_id,
        ProposalStatus.REJECTED,
        "operator",
        "l'échantillon est trop court",
        T0.replace(hour=8),
    )

    with Session(engine) as session:
        row = session.get(AiProposalRow, proposal_id)
        assert row is not None
        assert row.status is ProposalStatus.REJECTED
        assert row.decided_by == "operator"
        assert row.decision_reason == "l'échantillon est trop court"
        assert row.decided_at == T0.replace(hour=8)
    assert store.open_proposals() == ()


def test_a_validation_in_progress_stays_an_open_proposal(engine: Engine) -> None:
    store = LabStore(engine)
    validating = store.record_proposal(a_proposal())
    rejected = store.record_proposal(a_proposal())
    store.decide_proposal(validating, ProposalStatus.VALIDATING, "operator", "gates lancés", T0)
    store.decide_proposal(rejected, ProposalStatus.REJECTED, "operator", "hors sujet", T0)

    assert [row.id for row in store.open_proposals()] == [validating]


def test_a_decided_proposal_cannot_be_decided_again(engine: Engine) -> None:
    store = LabStore(engine)
    proposal_id = store.record_proposal(a_proposal())
    store.decide_proposal(proposal_id, ProposalStatus.REJECTED, "operator", "non", T0)

    with pytest.raises(ValueError, match="already decided"):
        store.decide_proposal(
            proposal_id, ProposalStatus.PROMOTED, "operator", "finalement oui", T0
        )


def test_the_store_cannot_reopen_a_proposal_by_setting_it_back_to_proposed(engine: Engine) -> None:
    store = LabStore(engine)
    proposal_id = store.record_proposal(a_proposal())

    with pytest.raises(ValueError, match="proposed"):
        store.decide_proposal(proposal_id, ProposalStatus.PROPOSED, "operator", "?", T0)


def test_deciding_an_unknown_proposal_is_refused(engine: Engine) -> None:
    store = LabStore(engine)

    with pytest.raises(ValueError, match="no proposal"):
        store.decide_proposal(999, ProposalStatus.REJECTED, "operator", "non", T0)


@pytest.mark.parametrize(
    ("actor", "reason"),
    [("", "motif"), ("operator", ""), ("   ", "motif")],
)
def test_a_decision_requires_an_actor_and_a_reason(engine: Engine, actor: str, reason: str) -> None:
    store = LabStore(engine)
    proposal_id = store.record_proposal(a_proposal())

    with pytest.raises(ValueError):
        store.decide_proposal(proposal_id, ProposalStatus.REJECTED, actor, reason, T0)


def test_a_decision_moment_must_be_utc(engine: Engine) -> None:
    store = LabStore(engine)
    proposal_id = store.record_proposal(a_proposal())

    with pytest.raises(ValueError, match="UTC"):
        store.decide_proposal(
            proposal_id,
            ProposalStatus.REJECTED,
            "operator",
            "non",
            datetime(2026, 10, 7, 6, 0, tzinfo=UTC).replace(tzinfo=None),
        )


# --- the structural guard -----------------------------------------------------


def test_the_lab_store_writes_no_trading_row(engine: Engine) -> None:
    store = LabStore(engine)
    analysis_id = store.record_analysis(an_analysis())
    proposal_id = store.record_proposal(a_proposal(analysis_id=analysis_id))
    store.decide_proposal(proposal_id, ProposalStatus.PROMOTED, "validation", "gates verts", T0)

    assert counts(engine) == {"signals": 0, "orders": 0, "positions": 0, "trades": 0}
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(AiAnalysisRow)) == 1


def test_the_lab_sources_never_reference_a_trading_table() -> None:
    root = Path(__file__).resolve().parents[2] / "src" / "tradingagent" / "ai"
    forbidden = (
        "OrderRow",
        "PositionRow",
        "SignalRow",
        "TradeRow",
        "ExecutionRow",
        "TradingMode",
        "tradingagent.execution",
        "tradingagent.storage.repository",
    )
    for name in ("lab_store.py", "analyst.py", "researcher.py"):
        source = (root / name).read_text(encoding="utf-8")
        for symbol in forbidden:
            assert symbol not in source, f"{name} references {symbol}"
