"""Which provider answers, and what it costs.

The decision is a pure function, so it is tested without a key, a network or a client. The
two things that would be expensive to get wrong are here: a key must never leak through a
repr or a traceback, and the cost table must not quietly fall back to a default rate for a
model whose price is known — the figure exists to stop spending, so understating it is the
one error that matters.
"""

from decimal import Decimal

import pytest

from tradingagent.ai.layer import (
    DEFAULT_INPUT_PRICE,
    DEFAULT_OUTPUT_PRICE,
    PRICES_EUR_PER_MTOK,
    USD_TO_EUR,
)
from tradingagent.ai.provider import (
    DEEPSEEK_BASE_URL,
    DEFAULT_DEEPSEEK_MODEL,
    AiProvider,
    resolve_target,
)
from tradingagent.storage.ai_calls import AiReply

DEEPSEEK_KEY = "sk-deepseek-key-value"
ANTHROPIC_KEY = "sk-ant-key-value"


def test_auto_prefers_deepseek_when_both_keys_exist() -> None:
    target = resolve_target(deepseek_key=DEEPSEEK_KEY, anthropic_key=ANTHROPIC_KEY)
    assert target is not None
    assert target.provider is AiProvider.DEEPSEEK
    assert target.base_url == DEEPSEEK_BASE_URL
    assert target.model == DEFAULT_DEEPSEEK_MODEL


def test_auto_uses_whichever_key_is_present() -> None:
    only_deepseek = resolve_target(anthropic_key=None, deepseek_key=DEEPSEEK_KEY)
    only_anthropic = resolve_target(deepseek_key=None, anthropic_key=ANTHROPIC_KEY)
    assert only_deepseek is not None and only_deepseek.provider is AiProvider.DEEPSEEK
    assert only_anthropic is not None and only_anthropic.provider is AiProvider.ANTHROPIC
    # Anthropic is the native protocol: no base URL override.
    assert only_anthropic.base_url is None


def test_auto_without_any_key_means_no_model() -> None:
    assert resolve_target() is None


def test_a_blank_key_counts_as_absent() -> None:
    """`KEY=` is what a half-filled template looks like; it must not build a client."""
    assert resolve_target(deepseek_key="   ", anthropic_key="") is None


def test_an_explicit_provider_without_its_key_does_not_fall_back() -> None:
    """Choosing DeepSeek and having no DeepSeek key is a configuration mistake, not an
    invitation to quietly spend on the other provider."""
    assert resolve_target(provider=AiProvider.DEEPSEEK, anthropic_key=ANTHROPIC_KEY) is None
    assert resolve_target(provider=AiProvider.ANTHROPIC, deepseek_key=DEEPSEEK_KEY) is None


def test_none_disables_the_model_even_with_keys() -> None:
    target = resolve_target(
        provider=AiProvider.NONE, deepseek_key=DEEPSEEK_KEY, anthropic_key=ANTHROPIC_KEY
    )
    assert target is None


def test_model_names_come_from_configuration() -> None:
    target = resolve_target(deepseek_key=DEEPSEEK_KEY, deepseek_model="un-modele-plus-recent")
    assert target is not None
    assert target.model == "un-modele-plus-recent"


def test_the_key_never_appears_in_a_repr() -> None:
    """A traceback that dumps the target must not print the credential.

    `repr=False` removes the field from the repr entirely rather than printing a
    placeholder: there is nothing left to redact, so nothing can be forgotten.
    """
    target = resolve_target(deepseek_key=DEEPSEEK_KEY)
    assert target is not None
    rendered = repr(target)
    assert DEEPSEEK_KEY not in rendered
    assert "api_key" not in rendered
    assert "deepseek-flash" in rendered  # the harmless parts are still visible


def test_the_deepseek_prices_are_declared() -> None:
    assert "deepseek-flash" in PRICES_EUR_PER_MTOK
    assert "deepseek-v4-pro" in PRICES_EUR_PER_MTOK


def test_the_deepseek_prices_beat_the_default_rate() -> None:
    """The reason the project switched provider: the cost figure must reflect it."""
    flash = PRICES_EUR_PER_MTOK["deepseek-flash"]
    assert flash[0] < DEFAULT_INPUT_PRICE
    assert flash[1] < DEFAULT_OUTPUT_PRICE


def test_the_declared_prices_match_the_providers_dollars() -> None:
    """Peak rates, converted at the documented rate — recomputed here, not trusted."""
    # deepseek-flash: $0.30 input (cache miss), $1.20 output.
    assert (Decimal("0.30") * USD_TO_EUR).quantize(Decimal("0.01")) == Decimal("0.28")
    assert (Decimal("1.20") * USD_TO_EUR).quantize(Decimal("0.01")) == Decimal("1.10")
    # deepseek-v4-pro: $1.32 input, $3.96 output.
    assert (Decimal("1.32") * USD_TO_EUR).quantize(Decimal("0.01")) == Decimal("1.21")
    assert (Decimal("3.96") * USD_TO_EUR).quantize(Decimal("0.01")) == Decimal("3.64")


def test_an_unknown_model_still_gets_a_rate() -> None:
    """A model someone forgot to price must not cost nothing."""
    from tradingagent.ai.layer import _prices

    assert _prices("un-modele-inconnu") == (DEFAULT_INPUT_PRICE, DEFAULT_OUTPUT_PRICE)


def test_a_priced_model_is_not_billed_at_the_default() -> None:
    from tradingagent.ai.layer import _cost_eur, _prices

    reply = AiReply(text="{}", model="deepseek-flash", input_tokens=1_000_000, output_tokens=0)
    input_price, _ = _prices("deepseek-flash")
    assert _cost_eur(reply) == input_price.quantize(Decimal("0.000001"))


def test_the_cost_is_zero_for_no_tokens() -> None:
    from tradingagent.ai.layer import _cost_eur

    reply = AiReply(text="{}", model="deepseek-flash", input_tokens=0, output_tokens=0)
    assert _cost_eur(reply) == Decimal("0.000000")


@pytest.mark.parametrize("model", ["deepseek-flash", "deepseek-v4-pro"])
def test_every_declared_price_is_a_positive_pair(model: str) -> None:
    input_price, output_price = PRICES_EUR_PER_MTOK[model]
    assert input_price > 0
    assert output_price > input_price  # generation costs more than reading, everywhere
