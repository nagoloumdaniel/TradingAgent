"""Optional access protection for the dashboard (cahier v3 §43).

The dashboard listens on ``127.0.0.1`` and, by default, has no notion of identity: an
operator who can reach the port can read every page. When ``TRADINGAGENT_WEB_TOKEN`` is set
in the environment, that stops being true — every page *and* the SSE feed then answer 401
until a valid credential is presented.

Two transports are accepted, and neither ever carries the token in a URL:

``Authorization: Bearer <token>``
    The exact configured value, compared in constant time. This is the transport for
    scripts, ``curl`` and a supervisor's health probe.

``Cookie: tradingagent_session=<value>``
    A browser cannot set a header on a top-level navigation, and ``EventSource`` cannot set
    one either, so the session cookie exists for the two flows that need it. Its value is
    ``HMAC-SHA256(token, SESSION_MESSAGE)``: a *derived* credential, not the token itself.
    ``GET /session`` mints it from a valid bearer credential. Rotating
    ``TRADINGAGENT_WEB_TOKEN`` invalidates every cookie ever issued.

The token is read from the environment once, at application build time. It is never written
to a template, never echoed in a response, never put in a URL and never logged — not even in
the warning emitted for a short value.
"""

import hmac
import logging
import os
from dataclasses import dataclass, field
from hashlib import sha256

from fastapi import Request
from fastapi.responses import HTMLResponse

# The variable the operator sets. Declared here and nowhere else: the value itself never
# exists in the source tree.
ACCESS_ENV_VAR = "TRADINGAGENT_WEB_TOKEN"
# The cookie a browser presents so that `EventSource` keeps working behind the token.
SESSION_COOKIE = "tradingagent_session"
# Domain-separation string for the HMAC. Not a secret: it only makes the derived value
# unusable as anything but this dashboard's session cookie.
SESSION_MESSAGE = b"tradingagent-web-session-v1"
# Below this, the warning is worth printing; the value is never printed.
MINIMUM_RECOMMENDED_LENGTH = 16

# The 401 body: a standalone document with no navigation, no figure and no echo of the
# request. A denied request must learn nothing about what it failed to reach.
_DENIED = """<!doctype html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="dark">
<meta name="robots" content="noindex">
<title>401 — Acces refuse</title>
<style>
  html { background: #0f1013; color: #e7e9ee; }
  body { margin: 0; min-height: 100dvh; display: grid; place-items: center;
         font: 15px/1.5 ui-sans-serif, "Segoe UI Variable Text", "Segoe UI", sans-serif; }
  main { max-width: 46ch; padding: 24px; border: 1px solid #262a32; border-radius: 12px;
         background: #16181d; }
  h1 { font-size: 14px; margin: 0 0 8px; letter-spacing: 0.02em; text-transform: uppercase;
       color: #9aa1ad; font-weight: 600; }
  p { margin: 0 0 8px; }
  code { font-family: ui-monospace, Consolas, monospace; font-size: 12.5px; color: #9aa1ad; }
</style>
</head>
<body>
<main>
  <h1>401 — Acces refuse</h1>
  <p>Un jeton d'acces est requis. La requete n'a pas presente de justificatif valide.</p>
  <p><code>Authorization: Bearer &lt;jeton&gt;</code> ou le cookie de session
     <code>tradingagent_session</code>.</p>
</main>
</body>
</html>
"""

logger = logging.getLogger(__name__)


def configured_token(value: str | None = None) -> str | None:
    """The token the operator declared, or ``None`` when protection is off.

    ``value`` lets an embedder (a preview script, a test) hand the token in directly; when
    it is ``None`` the environment is read. A blank value counts as absent, so an empty
    variable never silently locks the dashboard behind an empty token.
    """
    raw = os.environ.get(ACCESS_ENV_VAR) if value is None else value
    if raw is None:
        return None
    candidate = raw.strip()
    if not candidate:
        return None
    if len(candidate) < MINIMUM_RECOMMENDED_LENGTH:
        logger.warning(
            "%s is shorter than %d characters; a long random value is expected",
            ACCESS_ENV_VAR,
            MINIMUM_RECOMMENDED_LENGTH,
        )
    return candidate


@dataclass(frozen=True)
class AccessControl:
    """The one credential this process accepts, and the two ways it may be presented.

    ``repr=False`` on the field is deliberate: the token must not reach a traceback, a log
    record or an error message that dumps the object.
    """

    token: str = field(repr=False)

    @property
    def session_value(self) -> str:
        """The cookie value handed to a browser: an HMAC of the token, never the token."""
        return hmac.new(self.token.encode("utf-8"), SESSION_MESSAGE, sha256).hexdigest()

    def bearer_granted(self, presented: str | None) -> bool:
        if presented is None:
            return False
        prefix = "bearer "
        if not presented.lower().startswith(prefix):
            return False
        candidate = presented[len(prefix) :].strip()
        return self._same(candidate)

    def cookie_granted(self, presented: str | None) -> bool:
        if presented is None:
            return False
        return self._same(presented.strip(), expected=self.session_value)

    def granted(self, request: Request) -> bool:
        """True when the request carries a valid bearer token or a valid session cookie."""
        if self.bearer_granted(request.headers.get("authorization")):
            return True
        return self.cookie_granted(request.cookies.get(SESSION_COOKIE))

    def denial(self) -> HTMLResponse:
        """The 401: same answer whether the header is absent, malformed or wrong."""
        return HTMLResponse(
            content=_DENIED,
            status_code=401,
            headers={
                "WWW-Authenticate": "Bearer",
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
            },
        )

    def _same(self, presented: str, expected: str | None = None) -> bool:
        wanted = self.token if expected is None else expected
        # Compared as bytes: `compare_digest` refuses non-ASCII `str` by raising, which
        # would turn a hostile header into a 500 instead of a 401.
        return hmac.compare_digest(
            presented.encode("utf-8", "ignore"), wanted.encode("utf-8", "ignore")
        )


__all__ = [
    "ACCESS_ENV_VAR",
    "MINIMUM_RECOMMENDED_LENGTH",
    "SESSION_COOKIE",
    "SESSION_MESSAGE",
    "AccessControl",
    "configured_token",
]
