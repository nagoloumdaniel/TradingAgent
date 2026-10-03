from tradingagent.core.mode import AiFilter, TradingMode, mode_rank


def test_modes_are_ranked_from_least_to_most_exposed() -> None:
    ordered = [
        TradingMode.OBSERVATION,
        TradingMode.SIGNAL,
        TradingMode.PAPER,
        TradingMode.DEMO,
        TradingMode.LIVE,
    ]
    ranks = [mode_rank(mode) for mode in ordered]
    assert ranks == sorted(set(ranks))


def test_every_mode_has_a_rank() -> None:
    assert len({mode_rank(mode) for mode in TradingMode}) == len(TradingMode)


def test_ai_filter_values_match_the_specification() -> None:
    assert {member.value for member in AiFilter} == {"shadow", "advisory", "required"}
