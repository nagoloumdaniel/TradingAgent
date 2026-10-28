"""What the agent does when something outside it fails: wait, say it once, try again.

The database, the terminal and Telegram all sit across a network that drops. None of those
outages is a reason for the process to exit: an exit costs a supervisor restart, a cold
start-up and a flood of tracebacks, and it fixes nothing — the next process meets the same
dead link. These helpers give every caller the same three answers: is this failure worth
retrying, how long to wait, and what the one line in the journal says.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable

from sqlalchemy.exc import InterfaceError, OperationalError
from sqlalchemy.exc import TimeoutError as PoolTimeoutError

from tradingagent.data.terminal import TerminalError

log = logging.getLogger(__name__)

MAX_BACKOFF_SECONDS = 300.0
ONE_LINE_LIMIT = 200


def is_transient(error: BaseException) -> bool:
    """A failure that comes from the other side of a link, and that waiting can cure.

    A constraint violation, a bad statement or a configuration error is not transient: the
    same call would fail the same way forever, and retrying would hide a defect.
    """
    if isinstance(error, (FileNotFoundError, PermissionError, IsADirectoryError)):
        return False
    return isinstance(
        error,
        (OperationalError, InterfaceError, PoolTimeoutError, TerminalError, OSError, TimeoutError),
    )


def backoff_delay(failures: int, base: float, cap: float = MAX_BACKOFF_SECONDS) -> float:
    """`base`, doubled after each further consecutive failure, never above `cap`."""
    if failures <= 1:
        return min(base, cap)
    return min(base * 2 ** (failures - 1), cap)


def one_line(error: BaseException) -> str:
    """The error as a type and its first line: no traceback, no SQL, no parameters.

    A SQLAlchemy error carries the statement and its bound values after the message, and
    psycopg appends hints on further lines. The first line is the cause; the rest is what
    filled the journal on 2026-10-20.
    """
    original = getattr(error, "orig", None)
    source = original if original is not None else error
    first = next((line.strip() for line in str(source).splitlines() if line.strip()), "")
    text = f"{type(error).__name__}: {first}" if first else type(error).__name__
    if len(text) <= ONE_LINE_LIMIT:
        return text
    return text[: ONE_LINE_LIMIT - 1] + "…"


def should_log_attempt(failures: int) -> bool:
    """Log the first failure, then the 2nd, 4th, 8th...: an outage must not fill the journal."""
    return failures >= 1 and failures & (failures - 1) == 0


async def retry_transient[T](
    action: Callable[[], Awaitable[T]],
    what: str,
    *,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    base: float = 5.0,
    cap: float = 60.0,
) -> T:
    """Run `action` until it succeeds, waiting between attempts while the cause is transient.

    Any other error is raised at once: a defect or a configuration refusal must stop the
    start-up, loudly, and not be retried into silence.
    """
    failures = 0
    while True:
        try:
            result = await action()
        except Exception as error:
            if not is_transient(error):
                raise
            failures += 1
            if should_log_attempt(failures):
                log.warning("%s failed (attempt %d), retrying: %s", what, failures, one_line(error))
            await sleep(backoff_delay(failures, base, cap))
            continue
        if failures:
            log.warning("%s succeeded after %d failed attempt(s)", what, failures)
        return result
