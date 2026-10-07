"""Hand rationale for the nine promotion gates (cahier v3 §10, §49).

The protocol is not a score: every stage is a hurdle the candidate must clear on its own.
A candidate that cleared backtest, costs and the eight other stages is promotable. One that
missed a single stage is not, and the refusal must name the missing stages so the operator
knows exactly what evidence to produce next. The order of `ValidationStage` is the order of
the protocol, so a refusal is always reported in the same, auditable order.
"""

from tradingagent.core.states import ValidationStage
from tradingagent.registry.gates import PROMOTION_GATES, can_promote, missing_gates


def test_the_protocol_holds_the_nine_stages_of_the_spec() -> None:
    assert tuple(ValidationStage) == PROMOTION_GATES
    assert len(PROMOTION_GATES) == 9


def test_no_evidence_leaves_every_gate_missing() -> None:
    assert missing_gates(()) == PROMOTION_GATES
    assert missing_gates([]) == PROMOTION_GATES
    assert not can_promote(())


def test_missing_gates_are_reported_in_protocol_order() -> None:
    passed = {ValidationStage.COSTS, ValidationStage.BACKTEST}
    assert missing_gates(passed) == (
        ValidationStage.WALK_FORWARD,
        ValidationStage.OUT_OF_SAMPLE,
        ValidationStage.MONTE_CARLO,
        ValidationStage.STRESS,
        ValidationStage.PARAMETER_ROBUSTNESS,
        ValidationStage.PAPER,
        ValidationStage.RISK,
    )


def test_one_missing_gate_is_enough_to_refuse() -> None:
    passed = [stage for stage in ValidationStage if stage is not ValidationStage.RISK]
    assert missing_gates(passed) == (ValidationStage.RISK,)
    assert not can_promote(passed)


def test_all_nine_gates_clear_the_protocol() -> None:
    assert missing_gates(ValidationStage) == ()
    assert can_promote(ValidationStage)


def test_repeated_stages_are_tolerated() -> None:
    """A stage may be recorded several times (a rerun); it is still one gate."""
    assert can_promote([*ValidationStage, ValidationStage.RISK, ValidationStage.BACKTEST])
