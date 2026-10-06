"""The real model client (TASK-037). Blocking SDK calls are confined to a thread, like
every other blocking boundary; the key comes from the environment via Settings and is
never logged."""

import asyncio
import logging
from typing import Any

from tradingagent.storage.ai_calls import AiReply

log = logging.getLogger(__name__)


class AnthropicClient:
    def __init__(self, api_key: str, model: str) -> None:
        self._model = model
        # Imported lazily: tests and offline runs never pay for the import.
        import anthropic

        self._client = anthropic.Anthropic(api_key=api_key)

    async def complete(self, system: str, user: str) -> AiReply:

        def create() -> object:
            message = self._client.messages.create(
                model=self._model,
                max_tokens=1024,
                system=system,
                messages=[{"role": "user", "content": user}],
            )
            return message

        response: Any = await asyncio.to_thread(create)
        usage = response.usage
        text = "".join(block.text for block in response.content if block.type == "text")
        return AiReply(
            text=text,
            model=response.model,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
        )
