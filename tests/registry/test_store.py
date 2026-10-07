"""The versioned strategy registry: lifecycle, gates, immutability and restart (§14, §49)."""

from datetime import UTC, datetime

import pytest
from sqlalchemy import Engine

from tradingagent.core.states import StrategyStatus, ValidationStage
from tradingagent.registry.store import (
    DuplicateStrategyRef,
    IllegalStrategyTransition,
    ImmutableLiveStrategy,
    MarketAlreadyLive,
    MissingValidationGates,
    StrategyRegistry,
    UnknownStrategyRef,
    parse_ref,
)
from tradingagent.storage.audit import AuditStore
from tradingagent.storage.engine import create_database_engine

GOLD = "XAUUSD"
BITCOIN = "BTCUSD"
REF = "witness@1.1.0"
PARAMETERS = {"ema_fast": 20, "ema_slow": 50}
NOW = datetime(2026, 10, 7, 6, 30, tzinfo=UTC)
LATER = datetime(2026, 10, 7, 9, 0, tzinfo=UTC)
CANDIDATE_PATH = (
    StrategyStatus.EXPERIMENTAL,
    StrategyStatus.BACKTESTING,
    StrategyStatus.VALIDATING,
    StrategyStatus.PAPER,
    StrategyStatus.CANDIDATE,
)


def registry(engine: Engine) -> StrategyRegistry:
    return StrategyRegistry(engine, clock=lambda: NOW)


def register(
    store: StrategyRegistry,
    ref: str = REF,
    market: str = GOLD,
    parent_ref: str | None = None,
) -> int:
    return store.register(market, ref, "human", PARAMETERS, parent_ref=parent_ref)


def walk_to_candidate(store: StrategyRegistry, ref: str = REF, market: str = GOLD) -> None:
    for target in CANDIDATE_PATH:
        store.transition(market, ref, target, "operator", f"move to {target.value}", NOW)


def pass_every_gate(
    store: StrategyRegistry,
    ref: str = REF,
    market: str = GOLD,
    *,
    skip: ValidationStage | None = None,
) -> None:
    for stage in ValidationStage:
        if stage is skip:
            continue
        store.record_validation(ref, market, stage, True, {"evidence": "measured"}, NOW)


def promote(store: StrategyRegistry, ref: str = REF, market: str = GOLD) -> None:
    store.promote(market, ref, "operator", "nine gates cleared", LATER)


# ---------------------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------------------


def test_register_creates_a_discovered_entry(engine: Engine) -> None:
    store = registry(engine)
    row_id = register(store, parent_ref=None)

    assert isinstance(row_id, int)
    row = store.get(GOLD, REF)
    assert row.id == row_id
    assert row.status is StrategyStatus.DISCOVERED
    assert row.strategy_id == "witness"
    assert row.version == "1.1.0"
    assert row.origin == "human"
    assert row.parameters == PARAMETERS
    assert row.created_at == NOW
    assert row.updated_at == NOW
    assert row.promoted_at is None


def test_register_refuses_a_duplicate_market_and_ref(engine: Engine) -> None:
    store = registry(engine)
    register(store)
    with pytest.raises(DuplicateStrategyRef, match=r"witness@1\.1\.0"):
        register(store)


def test_the_same_ref_lives_independently_on_another_market(engine: Engine) -> None:
    store = registry(engine)
    first = register(store, market=GOLD)
    second = register(store, market=BITCOIN)
    assert first != second
    assert store.get(BITCOIN, REF).market == BITCOIN


def test_register_refuses_an_unknown_parent(engine: Engine) -> None:
    store = registry(engine)
    with pytest.raises(UnknownStrategyRef, match=r"ghost@9\.9\.9"):
        register(store, parent_ref="ghost@9.9.9")


def test_a_known_parent_is_recorded(engine: Engine) -> None:
    store = registry(engine)
    register(store, ref="witness@1.0.0")
    register(store, ref="witness@1.1.0", parent_ref="witness@1.0.0")
    assert store.get(GOLD, "witness@1.1.0").parent_ref == "witness@1.0.0"


def test_a_parent_from_another_market_is_refused(engine: Engine) -> None:
    """Lineage is a version history of one market; a parent elsewhere is a different lineage."""
    store = registry(engine)
    register(store, market=BITCOIN, ref="witness@1.0.0")
    with pytest.raises(UnknownStrategyRef):
        register(store, market=GOLD, parent_ref="witness@1.0.0")


def test_a_ref_needs_a_version(engine: Engine) -> None:
    store = registry(engine)
    with pytest.raises(ValueError, match="id@version"):
        register(store, ref="witness")
    with pytest.raises(ValueError, match="id@version"):
        register(store, ref="witness@")


def test_parse_ref_splits_on_the_last_at_sign() -> None:
    assert parse_ref("witness@1.1.0") == ("witness", "1.1.0")
    assert parse_ref("trend_breakout@1.0.0") == ("trend_breakout", "1.0.0")


def test_register_refuses_blank_market_or_ref(engine: Engine) -> None:
    store = registry(engine)
    with pytest.raises(ValueError, match="market"):
        store.register("  ", REF, "human", PARAMETERS)


def test_parameter_dictionary_is_copied_not_aliased(engine: Engine) -> None:
    store = registry(engine)
    parameters = {"ema_fast": 20}
    store.register(GOLD, REF, "human", parameters)
    parameters["ema_fast"] = 999
    assert store.get(GOLD, REF).parameters == {"ema_fast": 20}


# ---------------------------------------------------------------------------------------
# Transitions
# ---------------------------------------------------------------------------------------


def test_an_illegal_transition_is_refused(engine: Engine) -> None:
    store = registry(engine)
    register(store)
    with pytest.raises(IllegalStrategyTransition) as refusal:
        store.transition(GOLD, REF, StrategyStatus.LIVE, "operator", "too fast", NOW)
    assert "discovered" in str(refusal.value)
    assert store.get(GOLD, REF).status is StrategyStatus.DISCOVERED


def test_a_backward_transition_is_refused(engine: Engine) -> None:
    store = registry(engine)
    register(store)
    walk_to_candidate(store)
    with pytest.raises(IllegalStrategyTransition, match="candidate"):
        store.transition(GOLD, REF, StrategyStatus.EXPERIMENTAL, "operator", "rewind", NOW)


def test_the_lifecycle_walks_from_discovered_to_candidate(engine: Engine) -> None:
    store = registry(engine)
    register(store)
    walk_to_candidate(store)
    assert store.get(GOLD, REF).status is StrategyStatus.CANDIDATE
    assert store.get(GOLD, REF).updated_at == NOW


def test_every_transition_is_audited(engine: Engine) -> None:
    store = registry(engine)
    register(store)
    walk_to_candidate(store)

    actions = [row.action for row in AuditStore(engine).recent(limit=50)]
    assert actions.count("strategy.transition") == len(CANDIDATE_PATH)
    assert "strategy.registered" in actions
    recent = AuditStore(engine).recent(limit=50)
    latest = next(row for row in recent if row.action == "strategy.transition")
    assert latest.actor == "operator"
    assert latest.detail["market"] == GOLD
    assert latest.detail["ref"] == REF
    assert latest.detail["from"] == "paper"
    assert latest.detail["to"] == "candidate"
    assert latest.detail["reason"] == "move to candidate"
    assert latest.occurred_at == NOW


def test_a_transition_needs_a_utc_timestamp(engine: Engine) -> None:
    store = registry(engine)
    register(store)
    with pytest.raises(ValueError, match="UTC"):
        store.transition(
            GOLD,
            REF,
            StrategyStatus.EXPERIMENTAL,
            "operator",
            "clock drift",
            datetime(2026, 1, 1),  # noqa: DTZ001
        )


def test_a_transition_needs_an_actor_and_a_reason(engine: Engine) -> None:
    store = registry(engine)
    register(store)
    with pytest.raises(ValueError, match="actor"):
        store.transition(GOLD, REF, StrategyStatus.EXPERIMENTAL, "  ", "no one", NOW)
    with pytest.raises(ValueError, match="reason"):
        store.transition(GOLD, REF, StrategyStatus.EXPERIMENTAL, "operator", "  ", NOW)


def test_an_unknown_ref_cannot_transition(engine: Engine) -> None:
    with pytest.raises(UnknownStrategyRef):
        registry(engine).transition(GOLD, REF, StrategyStatus.EXPERIMENTAL, "op", "why", NOW)


# ---------------------------------------------------------------------------------------
# Validation evidence
# ---------------------------------------------------------------------------------------


def test_recorded_gates_are_persisted_with_their_detail(engine: Engine) -> None:
    store = registry(engine)
    register(store)
    run_id = store.record_validation(
        REF, GOLD, ValidationStage.BACKTEST, True, {"sharpe": 1.4}, NOW
    )
    assert isinstance(run_id, int)
    history = store.history(REF, GOLD)
    assert [row.stage for row in history.validations] == [ValidationStage.BACKTEST]
    assert history.validations[0].passed is True
    assert history.validations[0].detail == {"sharpe": 1.4}
    assert history.validations[0].created_at == NOW


def test_a_failed_gate_does_not_count(engine: Engine) -> None:
    store = registry(engine)
    register(store)
    store.record_validation(
        REF, GOLD, ValidationStage.RISK, False, {"reason": "too much risk"}, NOW
    )
    assert store.passed_stages(REF, GOLD) == frozenset()


def test_a_later_failure_revokes_an_earlier_pass(engine: Engine) -> None:
    store = registry(engine)
    register(store)
    store.record_validation(REF, GOLD, ValidationStage.RISK, True, {}, NOW)
    store.record_validation(
        REF, GOLD, ValidationStage.RISK, False, {"reason": "regime shift"}, LATER
    )
    assert store.passed_stages(REF, GOLD) == frozenset()


def test_validation_of_an_unknown_ref_is_refused(engine: Engine) -> None:
    with pytest.raises(UnknownStrategyRef):
        registry(engine).record_validation(REF, GOLD, ValidationStage.RISK, True, {}, NOW)


def test_record_backtest_keeps_the_dataset_and_the_numbers(engine: Engine) -> None:
    store = registry(engine)
    register(store)
    run_id = store.record_backtest(
        REF,
        GOLD,
        dataset_id="gold-m15-2026",
        fingerprint="a" * 64,
        window_start=NOW,
        window_end=LATER,
        metrics={"net_profit": 42.0},
        costs={"spread": 0.3},
        report_path="reports/backtest/witness.html",
        at=NOW,
    )
    assert isinstance(run_id, int)
    history = store.history(REF, GOLD)
    assert len(history.backtests) == 1
    assert history.backtests[0].dataset_id == "gold-m15-2026"
    assert history.backtests[0].metrics == {"net_profit": 42.0}
    assert history.backtests[0].costs == {"spread": 0.3}


def test_backtest_of_an_unknown_ref_is_refused(engine: Engine) -> None:
    store = registry(engine)
    with pytest.raises(UnknownStrategyRef):
        store.record_backtest(
            REF,
            GOLD,
            dataset_id="gold",
            fingerprint="b" * 64,
            window_start=NOW,
            window_end=LATER,
            metrics={},
            costs={},
            at=NOW,
        )


# ---------------------------------------------------------------------------------------
# Promotion
# ---------------------------------------------------------------------------------------


def test_promotion_is_refused_while_a_gate_is_missing(engine: Engine) -> None:
    store = registry(engine)
    register(store)
    walk_to_candidate(store)
    pass_every_gate(store, skip=ValidationStage.RISK)

    with pytest.raises(MissingValidationGates, match="risk") as refusal:
        promote(store)
    assert store.get(GOLD, REF).status is StrategyStatus.CANDIDATE
    assert "monte_carlo" not in str(refusal.value)
    assert "risk" in str(refusal.value)


def test_promotion_lists_every_missing_gate(engine: Engine) -> None:
    store = registry(engine)
    register(store)
    walk_to_candidate(store)
    store.record_validation(REF, GOLD, ValidationStage.BACKTEST, True, {}, NOW)

    with pytest.raises(MissingValidationGates) as refusal:
        promote(store)
    message = str(refusal.value)
    for stage in ValidationStage:
        if stage is not ValidationStage.BACKTEST:
            assert stage.value in message


def test_promotion_succeeds_once_the_nine_gates_pass(engine: Engine) -> None:
    store = registry(engine)
    register(store)
    walk_to_candidate(store)
    pass_every_gate(store)
    promote(store)

    row = store.get(GOLD, REF)
    assert row.status is StrategyStatus.LIVE
    assert row.promoted_at == LATER
    assert row.promotion_reason == "nine gates cleared"
    assert row.updated_at == LATER
    assert store.active(GOLD) == REF


def test_promotion_needs_the_candidate_status(engine: Engine) -> None:
    store = registry(engine)
    register(store)
    for target in CANDIDATE_PATH[:-1]:
        store.transition(GOLD, REF, target, "operator", "advance", NOW)
    pass_every_gate(store)
    with pytest.raises(IllegalStrategyTransition):
        promote(store)


def test_a_transition_straight_to_live_still_needs_the_gates(engine: Engine) -> None:
    """`transition` is the low-level door: it must not become a way around §49."""
    store = registry(engine)
    register(store)
    walk_to_candidate(store)
    with pytest.raises(MissingValidationGates):
        store.transition(GOLD, REF, StrategyStatus.LIVE, "operator", "shortcut", LATER)
    assert store.get(GOLD, REF).status is StrategyStatus.CANDIDATE


def test_promotion_requires_the_allowed_transition(engine: Engine) -> None:
    store = registry(engine)
    register(store)
    pass_every_gate(store)
    with pytest.raises(IllegalStrategyTransition):
        promote(store)


def test_a_second_live_ref_on_the_same_market_is_refused(engine: Engine) -> None:
    store = registry(engine)
    register(store, ref="witness@1.0.0")
    walk_to_candidate(store, ref="witness@1.0.0")
    pass_every_gate(store, ref="witness@1.0.0")
    promote(store, ref="witness@1.0.0")

    register(store, ref="witness@1.1.0")
    walk_to_candidate(store, ref="witness@1.1.0")
    pass_every_gate(store, ref="witness@1.1.0")
    with pytest.raises(MarketAlreadyLive, match=r"witness@1\.0\.0"):
        promote(store, ref="witness@1.1.0")
    assert store.active(GOLD) == "witness@1.0.0"


# ---------------------------------------------------------------------------------------
# Live immutability
# ---------------------------------------------------------------------------------------


def live_registry(engine: Engine, market: str = GOLD, ref: str = REF) -> StrategyRegistry:
    store = registry(engine)
    register(store, ref=ref, market=market)
    walk_to_candidate(store, ref=ref, market=market)
    pass_every_gate(store, ref=ref, market=market)
    promote(store, ref=ref, market=market)
    return store


def test_a_live_ref_cannot_be_promoted_again(engine: Engine) -> None:
    store = live_registry(engine)
    with pytest.raises(ImmutableLiveStrategy, match="new ref"):
        promote(store)


def test_a_live_ref_cannot_be_rewritten_by_registering_it_again(engine: Engine) -> None:
    store = live_registry(engine)
    with pytest.raises(DuplicateStrategyRef):
        register(store)
    assert store.get(GOLD, REF).status is StrategyStatus.LIVE


def test_a_live_ref_cannot_leave_live_except_to_deprecated(engine: Engine) -> None:
    store = live_registry(engine)
    with pytest.raises(IllegalStrategyTransition):
        store.transition(GOLD, REF, StrategyStatus.CANDIDATE, "operator", "rollback", LATER)
    store.transition(GOLD, REF, StrategyStatus.DEPRECATED, "operator", "retired", LATER)
    assert store.get(GOLD, REF).status is StrategyStatus.DEPRECATED
    assert store.active(GOLD) is None


def test_a_deprecated_ref_can_never_move_again(engine: Engine) -> None:
    store = live_registry(engine)
    store.transition(GOLD, REF, StrategyStatus.DEPRECATED, "operator", "retired", LATER)
    with pytest.raises(IllegalStrategyTransition):
        store.transition(GOLD, REF, StrategyStatus.EXPERIMENTAL, "operator", "revive", LATER)


def test_a_new_ref_can_replace_a_deprecated_live(engine: Engine) -> None:
    """Any change to a LIVE version produces a new ref: that is how the registry evolves."""
    store = live_registry(engine, ref="witness@1.0.0")
    store.transition(GOLD, "witness@1.0.0", StrategyStatus.DEPRECATED, "operator", "retired", LATER)

    register(store, ref="witness@1.1.0")
    walk_to_candidate(store, ref="witness@1.1.0")
    pass_every_gate(store, ref="witness@1.1.0")
    promote(store, ref="witness@1.1.0")
    assert store.active(GOLD) == "witness@1.1.0"
    assert store.get(GOLD, "witness@1.0.0").status is StrategyStatus.DEPRECATED


# ---------------------------------------------------------------------------------------
# Restart, active market, two markets
# ---------------------------------------------------------------------------------------


def test_a_new_instance_reads_the_same_state(database_url: str, engine: Engine) -> None:
    live_registry(engine)
    engine.dispose()

    restarted = create_database_engine(database_url)
    try:
        store = StrategyRegistry(restarted)
        assert store.get(GOLD, REF).status is StrategyStatus.LIVE
        assert store.active(GOLD) == REF
        assert len(store.history(REF, GOLD).validations) == 9
    finally:
        restarted.dispose()


def test_active_is_none_without_a_live_ref(engine: Engine) -> None:
    store = registry(engine)
    register(store)
    assert store.active(GOLD) is None
    assert store.active(BITCOIN) is None


def test_two_markets_are_independent(engine: Engine) -> None:
    store = registry(engine)
    register(store, market=GOLD, ref="witness@1.1.0")
    register(store, market=BITCOIN, ref="trend_breakout@1.0.0")

    walk_to_candidate(store, market=GOLD, ref="witness@1.1.0")
    pass_every_gate(store, market=GOLD, ref="witness@1.1.0")
    promote(store, market=GOLD, ref="witness@1.1.0")

    assert store.active(GOLD) == "witness@1.1.0"
    assert store.active(BITCOIN) is None
    assert store.get(BITCOIN, "trend_breakout@1.0.0").status is StrategyStatus.DISCOVERED

    walk_to_candidate(store, market=BITCOIN, ref="trend_breakout@1.0.0")
    pass_every_gate(store, market=BITCOIN, ref="trend_breakout@1.0.0")
    promote(store, market=BITCOIN, ref="trend_breakout@1.0.0")

    assert store.active(GOLD) == "witness@1.1.0"
    assert store.active(BITCOIN) == "trend_breakout@1.0.0"


def test_history_carries_the_whole_life_of_a_ref(engine: Engine) -> None:
    store = live_registry(engine)
    history = store.history(REF, GOLD)

    assert history.ref == REF
    assert history.market == GOLD
    assert history.status is StrategyStatus.LIVE
    assert [row.stage for row in history.validations] == list(ValidationStage)
    assert len(history.transitions) >= len(CANDIDATE_PATH) + 2  # registered, walk, promoted
    assert [row.detail["to"] for row in history.transitions if "to" in row.detail] == [
        *[target.value for target in CANDIDATE_PATH],
        "live",
    ]
    assert history.backtests == ()


def test_history_of_an_unknown_ref_is_refused(engine: Engine) -> None:
    with pytest.raises(UnknownStrategyRef):
        registry(engine).history(REF, GOLD)


def test_history_is_scoped_to_one_market(engine: Engine) -> None:
    store = registry(engine)
    register(store, market=GOLD, ref=REF)
    register(store, market=BITCOIN, ref=REF)
    store.transition(BITCOIN, REF, StrategyStatus.EXPERIMENTAL, "operator", "btc only", NOW)
    assert store.history(REF, GOLD).status is StrategyStatus.DISCOVERED
    assert store.history(REF, BITCOIN).status is StrategyStatus.EXPERIMENTAL


def test_list_market_returns_every_ref_of_one_market(engine: Engine) -> None:
    store = registry(engine)
    register(store, market=GOLD, ref="witness@1.0.0")
    register(store, market=GOLD, ref="witness@1.1.0")
    register(store, market=BITCOIN, ref="trend_breakout@1.0.0")
    assert [row.ref for row in store.list_market(GOLD)] == ["witness@1.0.0", "witness@1.1.0"]
    assert [row.ref for row in store.list_market(BITCOIN)] == ["trend_breakout@1.0.0"]


def test_utc_is_the_only_timezone_accepted(engine: Engine) -> None:
    store = registry(engine)
    register(store)
    with pytest.raises(ValueError, match="UTC"):
        store.record_validation(
            REF,
            GOLD,
            ValidationStage.BACKTEST,
            True,
            {},
            datetime(2026, 1, 1),  # noqa: DTZ001
        )
