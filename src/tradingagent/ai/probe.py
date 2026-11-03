"""One real round trip to the model, to answer "does the key work?" without waiting for a signal.

The dashboard's test button calls this. Nothing is written to `ai_calls`: a probe is not a
review of a signal, and counting it would skew the failure rate it exists to explain.
"""

import asyncio
import time
from dataclasses import dataclass
from typing import Protocol

from tradingagent.resilience import one_line
from tradingagent.storage.ai_calls import AiReply

PROBE_SYSTEM = "Tu es un test de connexion. Réponds uniquement par le mot : ok"
PROBE_USER = "ping"
PROBE_TIMEOUT_SECONDS = 15.0
HINTS = {
    401: "clé refusée : vérifiez DEEPSEEK_API_KEY",
    402: "solde insuffisant : rechargez le compte DeepSeek",
    403: "accès refusé par le fournisseur",
    404: "modèle introuvable : vérifiez DEEPSEEK_MODEL",
    429: "trop de requêtes : réessayez dans un moment",
}


class ProbeClient(Protocol):
    """Anything that can complete a prompt. Deliberately not the review layer's own type: the
    dashboard may test the key, never reach a verdict."""

    async def complete(self, system: str, user: str) -> AiReply: ...


@dataclass(frozen=True)
class ProbeResult:
    ok: bool
    detail: str
    model: str | None = None
    latency_ms: int | None = None


async def run_probe(client: ProbeClient | None) -> ProbeResult:
    if client is None:
        return ProbeResult(False, "Aucune clé de modèle configurée (DEEPSEEK_API_KEY absente).")
    started = time.monotonic()
    try:
        reply = await asyncio.wait_for(
            client.complete(PROBE_SYSTEM, PROBE_USER), PROBE_TIMEOUT_SECONDS
        )
    except Exception as error:
        elapsed = int((time.monotonic() - started) * 1000)
        status = getattr(error, "status_code", None)
        hint = HINTS.get(status) if isinstance(status, int) else None
        detail = one_line(error) if hint is None else f"{hint} ({one_line(error)})"
        return ProbeResult(False, detail, latency_ms=elapsed)
    elapsed = int((time.monotonic() - started) * 1000)
    if not reply.text.strip():
        return ProbeResult(False, "Réponse vide du modèle.", reply.model, elapsed)
    return ProbeResult(True, "Le modèle répond.", reply.model, elapsed)
