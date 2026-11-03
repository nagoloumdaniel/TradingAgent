"""Four-digit PIN in front of the dashboard (cahier v3 §43).

The dashboard listens on ``127.0.0.1`` on the operator's own machine, so the threat is not a
network attacker: it is anything else that can reach the loopback port, a browser tab, a
colleague at the keyboard. A short code the operator types is the right size for that.

How it works:

* The PIN is chosen in the browser on the first visit and stored **hashed** (scrypt, with a
  salt) in a local file under ``data/``, which git ignores. No environment variable, no
  header, no secret in the repository. Deleting the file resets the PIN.
* A correct PIN earns a signed cookie valid 30 days. The cookie carries its own expiry and
  an HMAC under a key stored next to the hash; it never contains the PIN. Changing the PIN
  (deleting the file and choosing again) rotates that key, so every older cookie dies.
* Logging out clears the cookie; the next visit asks for the PIN again.
* Five wrong PINs in a row lock the login for 15 minutes. Four digits are only 10 000
  possibilities: without a lockout they would fall in seconds. The counter lives in memory
  and is global — on a loopback dashboard there is one operator, and a per-client counter
  would only help an attacker who can change client.

The PIN is read from a form body, never from a URL, and is never logged or echoed.
"""

import contextlib
import hashlib
import hmac
import ipaddress
import json
import os
import re
import secrets
import tempfile
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from pathlib import Path

from fastapi import Request

# The cookie a browser presents on every page and on the SSE feed (`EventSource` cannot set
# a header).
SESSION_COOKIE = "tradingagent_session"
SESSION_DAYS = 30
SESSION_SECONDS = SESSION_DAYS * 24 * 3600
# Domain separation for the HMAC: the signature is unusable as anything but this cookie.
SESSION_MESSAGE = b"tradingagent-web-session-v2|"

PIN_LENGTH = 4
# ASCII digits only: ``str.isdigit`` would also accept Arabic-Indic and superscript digits.
_PIN_PATTERN = re.compile(rf"[0-9]{{{PIN_LENGTH}}}")

MAX_ATTEMPTS = 5
LOCKOUT = timedelta(minutes=15)

# scrypt cost: ~50 ms, which is invisible to the operator and makes an offline guess of a
# stolen file's 10 000 candidates cost minutes rather than microseconds.
_SCRYPT = {"n": 2**14, "r": 8, "p": 1}

# The one name that always resolves to the loopback interface, and the spelling Windows also
# answers. Anything else must be an address literal to be trusted as local.
LOOPBACK_NAMES = frozenset({"localhost", "localhost."})


class Outcome(StrEnum):
    GRANTED = "granted"
    WRONG = "wrong"
    LOCKED = "locked"


@dataclass(frozen=True)
class Attempt:
    outcome: Outcome
    #: Whole seconds until the lockout ends; ``0`` unless the outcome is ``LOCKED``.
    retry_after: int = 0
    #: Tries left before the lockout, shown to the operator after a wrong PIN.
    remaining: int = 0


def valid_pin(candidate: str) -> bool:
    return _PIN_PATTERN.fullmatch(candidate) is not None


def exposure_problem(host: str) -> str | None:
    """Why this bind address would publish the dashboard, or ``None`` when it would not.

    Four digits protect a dashboard from a neighbouring tab, not from a network. A bind
    beyond the loopback interface is therefore refused outright — never silently downgraded
    to ``127.0.0.1``, because an operator who asked for a public bind and got a private one
    would believe the dashboard is reachable when it is not. Callers print the message and
    stop.
    """
    candidate = host.strip()
    if candidate.lower() in LOOPBACK_NAMES:
        return None
    try:
        if ipaddress.ip_address(candidate).is_loopback:
            return None
    except ValueError:
        # Not an address literal: a hostname, a blank, or something hostile. None of them is
        # proof that the surface stays local, so none of them is accepted as local.
        pass
    shown = candidate or "<vide>"
    return (
        f"Refus de démarrer : --host « {shown} » écoute au-delà de la boucle locale. Un code à "
        f"{PIN_LENGTH} chiffres protège le tableau de bord d'un autre onglet de cette machine, "
        "pas d'un réseau : toute machine qui atteindrait ce port pourrait deviner le code. "
        "Écoutez sur 127.0.0.1."
    )


class PinStore:
    """The hashed PIN and the cookie-signing key, in one small JSON file."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def _read(self) -> dict[str, str] | None:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(raw, dict):
            return None
        if not all(isinstance(raw.get(name), str) for name in ("salt", "hash", "key")):
            return None
        return raw

    @property
    def configured(self) -> bool:
        """True as soon as a file exists, readable or not.

        An unreadable file must never reopen the "choose a PIN" page: anyone reaching the
        port could then replace the operator's PIN. It is reported by ``damaged`` instead.
        """
        return self.path.exists()

    @property
    def damaged(self) -> bool:
        return self.path.exists() and self._read() is None

    def choose(self, pin: str) -> None:
        """Store a new PIN. A fresh key is drawn, so every older cookie stops working."""
        salt = secrets.token_bytes(16)
        document = {
            "salt": salt.hex(),
            "hash": _digest(pin, salt).hex(),
            "key": secrets.token_hex(32),
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Written beside the target and moved into place: a crash never leaves half a file.
        descriptor, name = tempfile.mkstemp(
            dir=self.path.parent, prefix=self.path.name, suffix=".tmp"
        )
        scratch = Path(name)  # mkstemp creates it 0600
        try:
            try:
                os.write(descriptor, json.dumps(document).encode("utf-8"))
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            os.replace(scratch, self.path)
        finally:
            with contextlib.suppress(OSError):
                scratch.unlink()

    def matches(self, pin: str) -> bool:
        stored = self._read()
        if stored is None:
            return False
        try:
            expected = bytes.fromhex(stored["hash"])
            salt = bytes.fromhex(stored["salt"])
        except ValueError:
            return False
        return hmac.compare_digest(_digest(pin, salt), expected)

    def key(self) -> bytes | None:
        stored = self._read()
        if stored is None:
            return None
        try:
            return bytes.fromhex(stored["key"])
        except ValueError:
            return None


def _digest(pin: str, salt: bytes) -> bytes:
    return hashlib.scrypt(pin.encode("ascii"), salt=salt, dklen=32, **_SCRYPT)


class PinGate:
    """Who may see the dashboard: the PIN check, the lockout and the session cookie."""

    def __init__(self, store: PinStore, clock: Callable[[], datetime]) -> None:
        self.store = store
        self._clock = clock
        self._failures = 0
        self._locked_until: datetime | None = None
        self._mutex = threading.Lock()

    # --- PIN ---------------------------------------------------------------------------

    def attempt(self, pin: str) -> Attempt:
        """Check a PIN against the stored one, counting failures."""
        with self._mutex:
            now = self._clock()
            if self._locked_until is not None:
                if now < self._locked_until:
                    return Attempt(Outcome.LOCKED, retry_after=_seconds(self._locked_until - now))
                self._locked_until = None
                self._failures = 0
            if valid_pin(pin) and self.store.matches(pin):
                self._failures = 0
                return Attempt(Outcome.GRANTED)
            self._failures += 1
            if self._failures >= MAX_ATTEMPTS:
                self._locked_until = now + LOCKOUT
                return Attempt(Outcome.LOCKED, retry_after=_seconds(LOCKOUT))
            return Attempt(Outcome.WRONG, remaining=MAX_ATTEMPTS - self._failures)

    # --- session cookie ----------------------------------------------------------------

    def issue_cookie(self) -> str | None:
        """A cookie value valid for 30 days, or ``None`` when no PIN is chosen yet."""
        key = self.store.key()
        if key is None:
            return None
        expires = int((self._clock() + timedelta(seconds=SESSION_SECONDS)).timestamp())
        return f"{expires}.{_sign(key, expires)}"

    def cookie_granted(self, presented: str | None) -> bool:
        if not presented:
            return False
        stamp, dot, signature = presented.strip().partition(".")
        if not dot or not stamp.isascii() or not stamp.isdigit() or len(stamp) > 12:
            return False
        key = self.store.key()
        if key is None:
            return False
        # Compared as bytes: `compare_digest` refuses non-ASCII `str` by raising, which
        # would turn a hostile cookie into a 500 instead of a refusal.
        valid = hmac.compare_digest(
            signature.encode("utf-8", "ignore"), _sign(key, int(stamp)).encode("ascii")
        )
        return valid and int(self._clock().timestamp()) < int(stamp)

    def granted(self, request: Request) -> bool:
        return self.cookie_granted(request.cookies.get(SESSION_COOKIE))


def _sign(key: bytes, expires: int) -> str:
    return hmac.new(key, SESSION_MESSAGE + str(expires).encode("ascii"), hashlib.sha256).hexdigest()


def _seconds(delta: timedelta) -> int:
    return max(1, int(delta.total_seconds() + 0.999))


__all__ = [
    "LOCKOUT",
    "LOOPBACK_NAMES",
    "MAX_ATTEMPTS",
    "PIN_LENGTH",
    "SESSION_COOKIE",
    "SESSION_DAYS",
    "SESSION_SECONDS",
    "Attempt",
    "Outcome",
    "PinGate",
    "PinStore",
    "exposure_problem",
    "valid_pin",
]
