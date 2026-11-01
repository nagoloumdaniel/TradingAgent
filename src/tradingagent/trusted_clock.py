"""The agent's clock: online UTC time, immune to the PC clock being changed.

The operator changes the Windows clock often. Until now every module read it, and the market-data
check compared it with the broker tick, so a shifted PC clock halted the robot for long periods.

`now()` answers `online UTC at the last sync + monotonic time elapsed since`. The monotonic clock
ignores changes made to the wall clock, so setting the PC time forward or back moves nothing here.
The online reference comes from the HTTP `Date` header of several large public hosts (second
resolution, corrected by half the round trip), the median of the answers being kept. A background
thread re-syncs every few minutes. Before the first successful sync, or when every host is
unreachable, `now()` falls back to the PC clock and `status()` says so: a clock that cannot be
verified is reported, never silently trusted.

Paris time (UTC+1 / UTC+2 by the EU rule) is a display concern: see `paris_now()`.
"""

import logging
import statistics
import threading
import time
import urllib.request
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime

log = logging.getLogger(__name__)

SOURCES: tuple[str, ...] = (
    "https://www.google.com/generate_204",
    "https://www.cloudflare.com/cdn-cgi/trace",
    "https://www.microsoft.com",
    "https://www.apple.com",
)
SYNC_EVERY_SECONDS = 300.0
RETRY_EVERY_SECONDS = 20.0
TIMEOUT_SECONDS = 4.0
#: Answers further apart than this disagree: the outlier is dropped before the median.
MAX_SPREAD = timedelta(seconds=3)
#: A round is only trusted when this many sources agree within MAX_SPREAD.
QUORUM = 2
#: A reading further than this from the running clock waits for a second round to confirm it.
MAX_JUMP = timedelta(seconds=30)

Fetch = Callable[[str, float], datetime]


def _http_date(url: str, timeout: float) -> datetime:
    """UTC time from the `Date` header of `url`, corrected by half the round trip."""
    request = urllib.request.Request(url, method="HEAD", headers={"User-Agent": "nexagold-clock"})  # noqa: S310
    sent = time.monotonic()
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
        header = response.headers["Date"]
    rtt = time.monotonic() - sent
    if not header:
        raise ValueError("no Date header")
    stamp = parsedate_to_datetime(header)
    if stamp.tzinfo is None:  # "-0000" means UTC; astimezone() would read it as PC local time
        stamp = stamp.replace(tzinfo=UTC)
    return stamp.astimezone(UTC) + timedelta(seconds=rtt / 2)


@dataclass(frozen=True)
class ClockStatus:
    synced: bool
    last_sync: datetime | None
    offset_seconds: float | None  # online time minus the PC clock, at the last sync
    source_count: int
    detail: str


class TrustedClock:
    def __init__(
        self,
        sources: Sequence[str] = SOURCES,
        fetch: Fetch = _http_date,
        *,
        monotonic: Callable[[], float] = time.monotonic,
        system_now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._sources = tuple(sources)
        self._fetch = fetch
        self._mono = monotonic
        self._system_now = system_now
        self._lock = threading.Lock()
        self._anchor: tuple[datetime, float] | None = None  # (online UTC, monotonic then)
        self._offset: float | None = None
        self._used = 0
        self._pending: float | None = None  # a jump waiting for confirmation (UTC timestamp)
        self._detail = "pas encore synchronisée : heure du PC utilisée"
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    def now(self) -> datetime:
        with self._lock:
            anchor = self._anchor
        if anchor is None:
            return self._system_now()
        online, mono = anchor
        return online + timedelta(seconds=self._mono() - mono)

    def sync(self) -> bool:
        """One round over every source. True when a quorum agreed and the reading was accepted."""
        answers: list[datetime] = []
        failures: list[str] = []
        for url in self._sources:
            try:
                answers.append(self._fetch(url, TIMEOUT_SECONDS))
            except Exception as error:  # a host down is routine; the others decide
                failures.append(f"{url}: {type(error).__name__}")
        if not answers:
            with self._lock:
                self._detail = "aucune source d'heure joignable : " + "; ".join(failures)
            return False
        stamps = [a.timestamp() for a in answers]
        middle = statistics.median(stamps)
        kept = [s for s in stamps if abs(s - middle) <= MAX_SPREAD.total_seconds()]
        if len(kept) < QUORUM:
            with self._lock:
                self._detail = (
                    f"sources en désaccord ou trop peu nombreuses ({len(kept)}/{QUORUM}) : "
                    "lecture ignorée"
                )
            log.warning(
                "trusted clock: no quorum (%d of %d answers agree)", len(kept), len(answers)
            )
            return False
        online = datetime.fromtimestamp(statistics.median(kept), tz=UTC)
        mono = self._mono()
        with self._lock:
            if self._anchor is not None:
                running = self._anchor[0] + timedelta(seconds=mono - self._anchor[1])
                jump = abs((online - running).total_seconds())
                confirmed = (
                    self._pending is not None
                    and abs(online.timestamp() - self._pending) <= MAX_SPREAD.total_seconds()
                )
                if jump > MAX_JUMP.total_seconds() and not confirmed:
                    self._pending = online.timestamp()
                    self._detail = f"saut de {jump:.0f} s en attente de confirmation"
                    log.warning(
                        "trusted clock: %.0f s jump held back until a second round confirms it",
                        jump,
                    )
                    return False
            self._pending = None
            self._anchor = (online, mono)
            self._offset = (online - self._system_now()).total_seconds()
            self._used = len(kept)
            self._detail = f"synchronisée sur {len(kept)} source(s) en ligne"
        return True

    def status(self) -> ClockStatus:
        with self._lock:
            anchor, offset, used, detail = self._anchor, self._offset, self._used, self._detail
        return ClockStatus(
            synced=anchor is not None,
            last_sync=anchor[0] if anchor else None,
            offset_seconds=offset,
            source_count=used,
            detail=detail,
        )

    def start(self) -> None:
        """Sync once now (bounded), then keep syncing in a daemon thread. Idempotent."""
        if self._thread is not None:
            return
        self.sync()
        self._thread = threading.Thread(target=self._loop, name="trusted-clock", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        while not self._stop.is_set():
            ok = self.status().synced
            wait = SYNC_EVERY_SECONDS if ok else RETRY_EVERY_SECONDS
            if self._stop.wait(wait):
                return
            try:
                self.sync()
            except Exception:
                log.exception("trusted clock sync crashed")


_CLOCK = TrustedClock()


def now() -> datetime:
    """Current UTC time, from the trusted clock."""
    return _CLOCK.now()


def start() -> None:
    _CLOCK.start()


start_trusted_clock = start


def system_now() -> datetime:
    """The PC clock, on purpose: for exchanges with the EA, which stamps with the PC's `TimeGMT()`.

    Freshness checks between Python and the EA must read the same clock on both sides, or a PC
    clock that is off would make a healthy EA look stale (or the reverse).
    """
    return _CLOCK._system_now()


def status() -> ClockStatus:
    return _CLOCK.status()


def _last_sunday(year: int, month: int) -> datetime:
    day = datetime(year + (month == 12), month % 12 + 1, 1, tzinfo=UTC) - timedelta(days=1)
    return day - timedelta(days=(day.weekday() + 1) % 7)


def paris_offset(moment: datetime) -> timedelta:
    """UTC+1, or UTC+2 from the last Sunday of March to the last of October (01:00 UTC)."""
    utc = moment.astimezone(UTC)
    begins = _last_sunday(utc.year, 3).replace(hour=1)
    ends = _last_sunday(utc.year, 10).replace(hour=1)
    return timedelta(hours=2) if begins <= utc < ends else timedelta(hours=1)


def paris_now() -> datetime:
    """Paris wall clock (fields only; tagged UTC, for display)."""
    moment = now()
    return moment + paris_offset(moment)
