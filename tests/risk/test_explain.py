from tradingagent.risk.explain import explain_refusal, refusal_causes


def test_a_spread_refusal_is_explicit_french() -> None:
    text = explain_refusal("spread: spread 0.8, limit 0.5")
    assert text.startswith("Spread trop large — ")
    assert "0.8" in text and "0.5" in text


def test_several_causes_are_all_named_once() -> None:
    reason = "spread: spread 0.8, limit 0.5; trading_hours: market closed"
    assert refusal_causes(reason) == ["Spread trop large", "Marché fermé"]
    assert explain_refusal(reason).count("—") == 2


def test_the_daily_streak_message_stays_as_written() -> None:
    reason = "daily_loss_streak: 4 pertes consécutives aujourd'hui sur M5, arrêt"
    assert refusal_causes(reason) == ["Série de pertes du jour atteinte"]


def test_an_unknown_or_empty_reason_is_never_invented() -> None:
    assert explain_refusal(None) == "Motif non enregistré"
    assert explain_refusal("raison inconnue") == "raison inconnue"
