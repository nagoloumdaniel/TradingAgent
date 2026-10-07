"""EA health for the dashboard (F-024, phase 7).

One function, one answer: for every Guardian that should be running, is its heartbeat
fresh? The dashboard reads this and nothing else. An EA whose report is absent, truncated
or unparseable is **OFFLINE**, never an exception: a monitoring surface that can itself
fail is worse than no monitoring at all.
"""

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from tradingagent.ea.bridge import (
    DEFAULT_HEARTBEAT_TIMEOUT_SECONDS,
    REPORT_SUFFIX,
    EaStatus,
    parse_utc,
    read_json,
)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def ea_health(
    reports_dir: Path | str,
    *,
    now: Callable[[], datetime] = _utc_now,
    timeout_seconds: float = DEFAULT_HEARTBEAT_TIMEOUT_SECONDS,
) -> dict[str, EaStatus]:
    """One status per EA report found in `reports_dir`, keyed by symbol.

    The symbol comes from the report itself, or from the file name when the report cannot
    be read. A directory that does not exist yields an empty mapping: no EA is installed,
    which is not an error, it is a fact.
    """
    directory = Path(reports_dir)
    health: dict[str, EaStatus] = {}
    if not directory.is_dir():
        return health
    reference = now()
    for path in sorted(directory.glob(f"*{REPORT_SUFFIX}")):
        fallback = path.name[: -len(REPORT_SUFFIX)]
        payload = read_json(path)
        symbol = fallback
        heartbeat: datetime | None = None
        if payload is not None:
            reported = payload.get("symbol")
            if isinstance(reported, str) and reported:
                symbol = reported
            try:
                heartbeat = parse_utc(payload.get("updated_at"))
            except ValueError:
                heartbeat = None
        fresh = heartbeat is not None and (reference - heartbeat).total_seconds() < timeout_seconds
        health[symbol] = EaStatus.ONLINE if fresh else EaStatus.OFFLINE
    return health


__all__ = ["ea_health"]
