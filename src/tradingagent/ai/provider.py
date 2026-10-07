"""Which model answers, and where it lives.

Two providers, one protocol. DeepSeek publishes an **Anthropic-compatible** base URL
(`https://api.deepseek.com/anthropic`), so the same client and the same request shape reach
both — only the base URL, the model name and the price differ. Writing an OpenAI-shaped
client would have been a second code path to keep correct for no benefit.

This module is a pure decision: given the configured keys and an explicit preference, say
which provider wins. Nothing here reads the environment, opens a connection or imports a
provider SDK, so `tradingagent doctor` and the tests can ask the question cheaply.

`AUTO` exists because the common case is "I have one key and I do not want to think about
it". An explicit choice still wins, so an operator holding both keys is never surprised.
"""

from dataclasses import dataclass, field
from enum import StrEnum

DEEPSEEK_BASE_URL = "https://api.deepseek.com/anthropic"
# The current DeepSeek model names. `deepseek-chat` is no longer listed by the provider.
DEFAULT_DEEPSEEK_MODEL = "deepseek-flash"
DEFAULT_ANTHROPIC_MODEL = "claude-sonnet-4-5"


class AiProvider(StrEnum):
    AUTO = "auto"
    DEEPSEEK = "deepseek"
    ANTHROPIC = "anthropic"
    NONE = "none"


@dataclass(frozen=True)
class ModelTarget:
    """The resolved provider: where to send the request, with which key and which model.

    ``repr=False`` on the key keeps it out of a traceback or a log line that dumps the
    object, exactly like the dashboard's access token.
    """

    provider: AiProvider
    api_key: str = field(repr=False)
    model: str
    base_url: str | None = None


def resolve_target(
    *,
    provider: AiProvider = AiProvider.AUTO,
    deepseek_key: str | None = None,
    anthropic_key: str | None = None,
    deepseek_model: str = DEFAULT_DEEPSEEK_MODEL,
    anthropic_model: str = DEFAULT_ANTHROPIC_MODEL,
) -> ModelTarget | None:
    """The provider to use, or ``None`` when the agent must run without a model.

    A blank key counts as absent, so an empty variable never produces a client that fails
    on its first call. `NONE` disables the model explicitly even when keys are present —
    the deterministic analysis is a complete mode, not a degraded one (RM-011).
    """
    deepseek = (deepseek_key or "").strip()
    anthropic = (anthropic_key or "").strip()

    if provider is AiProvider.NONE:
        return None
    if provider is AiProvider.DEEPSEEK:
        if not deepseek:
            return None
        return ModelTarget(AiProvider.DEEPSEEK, deepseek, deepseek_model, DEEPSEEK_BASE_URL)
    if provider is AiProvider.ANTHROPIC:
        if not anthropic:
            return None
        return ModelTarget(AiProvider.ANTHROPIC, anthropic, anthropic_model, None)

    # AUTO: one key means no decision to make. DeepSeek first, because it is the cheaper of
    # the two and the one this project is configured for.
    if deepseek:
        return ModelTarget(AiProvider.DEEPSEEK, deepseek, deepseek_model, DEEPSEEK_BASE_URL)
    if anthropic:
        return ModelTarget(AiProvider.ANTHROPIC, anthropic, anthropic_model, None)
    return None


__all__ = [
    "DEEPSEEK_BASE_URL",
    "DEFAULT_ANTHROPIC_MODEL",
    "DEFAULT_DEEPSEEK_MODEL",
    "AiProvider",
    "ModelTarget",
    "resolve_target",
]
