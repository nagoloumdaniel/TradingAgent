"""Observability for the running agent: structured logs, metrics, resources (TASK-054).

Covers F-024 and ENF-006: structured, machine-readable logs; availability, latency,
connection state and API-error metrics; resource consumption. Nothing here reads the
clock implicitly: ``Metrics`` and ``ResourceMonitor`` take an injected ``now``/``rss``
so tests are deterministic.

``configure_json_logging`` composes with :mod:`tradingagent.config.redaction`: the
record factory installed there rewrites every record's message, so the JSON formatter
only has to serialise :meth:`logging.LogRecord.getMessage`.
"""

import ctypes
import json
import logging
import os
import shutil
import sys
from collections.abc import Callable, Iterable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tradingagent.config.redaction import install_secret_redaction, redact

Now = Callable[[], datetime]

# Attributes logging itself puts on every record; anything else is a caller's `extra`.
_RESERVED: frozenset[str] = frozenset(
    {
        "args",
        "asctime",
        "created",
        "exc_info",
        "exc_text",
        "filename",
        "funcName",
        "levelname",
        "levelno",
        "lineno",
        "message",
        "module",
        "msecs",
        "msg",
        "name",
        "pathname",
        "process",
        "processName",
        "relativeCreated",
        "stack_info",
        "taskName",
        "thread",
        "threadName",
    }
)

# Once a secret set has been handed to the record factory, re-installing it would stack
# redaction layers for no benefit. Keyed by value, never by the secret itself.
_REDACTION_INSTALLED: set[frozenset[str]] = set()


def _utc_now() -> datetime:
    return datetime.now(UTC)


def upgrade_level(level: int | str) -> int:
    """Accept ``logging.INFO`` or ``"INFO"`` and reject anything logging would misspell."""
    if isinstance(level, str):
        resolved = logging.getLevelNamesMapping().get(level.upper())
        if resolved is None:
            raise ValueError(f"unknown log level: {level!r}")
        return int(resolved)
    return int(level)


class JsonFormatter(logging.Formatter):
    """Render one log record as a single JSON object, redaction already applied."""

    def __init__(
        self,
        *,
        now: Callable[[float], datetime] | None = None,
        secrets: Iterable[str] = (),
    ) -> None:
        super().__init__()
        self._now = now if now is not None else lambda created: datetime.fromtimestamp(created, UTC)
        # The record factory redacts the message; only redacting the serialised line also
        # covers `extra` values and stack text a caller attached.
        self._secrets = tuple(secrets)

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": self._format_time(record),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_text:
            payload["exception"] = record.exc_text
        elif record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        if record.stack_info:
            payload["stack"] = record.stack_info
        extras = {key: value for key, value in record.__dict__.items() if key not in _RESERVED}
        if extras:
            payload["extra"] = extras
        line = json.dumps(payload, ensure_ascii=False, default=str, sort_keys=False)
        return redact(line, self._secrets) if self._secrets else line

    def _format_time(self, record: logging.LogRecord) -> str:
        return self._now(record.created).astimezone(UTC).isoformat()


def configure_json_logging(
    level: int | str,
    secrets: Iterable[str],
    *,
    stream: Any = None,
    logger: logging.Logger | None = None,
) -> logging.Handler:
    """Install secret redaction and one JSON stream handler; return the handler.

    Called with the operator's secrets, so nothing they typed can surface in the logs
    even inside an exception. Idempotent for a given secret set: reconfiguring only
    replaces the handler this function installed.
    """
    values = frozenset(secrets)
    if values not in _REDACTION_INSTALLED:
        install_secret_redaction(values)
        _REDACTION_INSTALLED.add(values)
    target = logger if logger is not None else logging.getLogger()
    target.setLevel(upgrade_level(level))
    for existing in list(target.handlers):
        if getattr(existing, "_tradingagent_json", False):
            target.removeHandler(existing)
    handler = logging.StreamHandler(stream if stream is not None else sys.stderr)
    handler.setFormatter(JsonFormatter(secrets=values))
    handler._tradingagent_json = True  # type: ignore[attr-defined]
    target.addHandler(handler)
    return handler


class Metrics:
    """Counters, latencies and connection state, with an injected clock.

    Availability is derived, not invented: a component is available when its last
    observed connection state is ``True``.
    """

    def __init__(self, *, now: Now | None = None) -> None:
        self._now = now if now is not None else _utc_now
        self._started_at = self._now()
        self._counters: dict[str, float] = {}
        self._latency_count: dict[str, int] = {}
        self._latency_total: dict[str, float] = {}
        self._latency_min: dict[str, float] = {}
        self._latency_max: dict[str, float] = {}
        self._latency_last: dict[str, float] = {}
        self._connections: dict[str, bool] = {}
        self._connection_changed_at: dict[str, datetime] = {}

    def incr(self, name: str, value: float = 1) -> None:
        """Count anything worth counting: API errors, retries, alerts sent."""
        self._counters[name] = self._counters.get(name, 0) + value

    def observe_latency(self, name: str, seconds: float) -> None:
        """Record one duration in seconds; negative durations are a caller bug."""
        if seconds < 0:
            raise ValueError(f"latency of {name!r} must not be negative")
        self._latency_count[name] = self._latency_count.get(name, 0) + 1
        self._latency_total[name] = self._latency_total.get(name, 0.0) + seconds
        self._latency_last[name] = seconds
        current_min = self._latency_min.get(name)
        self._latency_min[name] = seconds if current_min is None else min(current_min, seconds)
        current_max = self._latency_max.get(name)
        self._latency_max[name] = seconds if current_max is None else max(current_max, seconds)

    def set_connection(self, symbol: str, ok: bool) -> None:
        """Record the state of one connection; call on every check, not only on change."""
        if self._connections.get(symbol) != ok:
            self._connection_changed_at[symbol] = self._now()
        self._connections[symbol] = ok

    def snapshot(self) -> dict[str, Any]:
        """A JSON-ready picture of the process, safe to log or serve on /health."""
        connections: dict[str, Any] = {}
        for symbol, ok in sorted(self._connections.items()):
            changed = self._connection_changed_at.get(symbol)
            connections[symbol] = {
                "ok": ok,
                "since": changed.astimezone(UTC).isoformat() if changed is not None else None,
                "seconds": (self._now() - changed).total_seconds() if changed is not None else None,
            }
        latencies: dict[str, Any] = {}
        for name, count in sorted(self._latency_count.items()):
            latencies[name] = {
                "count": count,
                "total_seconds": self._latency_total[name],
                "min_seconds": self._latency_min[name],
                "max_seconds": self._latency_max[name],
                "last_seconds": self._latency_last[name],
                "mean_seconds": self._latency_total[name] / count,
            }
        return {
            "started_at": self._started_at.astimezone(UTC).isoformat(),
            "uptime_seconds": (self._now() - self._started_at).total_seconds(),
            "counters": dict(sorted(self._counters.items())),
            "latencies": latencies,
            "connections": connections,
            "available": all(entry["ok"] for entry in connections.values()),
        }


class ResourceMonitor:
    """Disk and process memory, read without psutil.

    RSS comes from the Windows process-memory API or ``/proc/self/statm``; the reader is
    injectable so tests never depend on the host. When no reader is available the
    snapshot reports ``rss_bytes: None`` instead of guessing.
    """

    def __init__(self, path: Path | str, *, rss: Callable[[], int] | None = None) -> None:
        self._path = Path(path)
        self._rss = rss if rss is not None else default_rss_bytes

    def disk(self) -> dict[str, Any]:
        usage = shutil.disk_usage(self._path)
        used_percent = 100.0 * (usage.total - usage.free) / usage.total if usage.total else 0.0
        return {
            "path": str(self._path),
            "total_bytes": usage.total,
            "used_bytes": usage.used,
            "free_bytes": usage.free,
            "used_percent": used_percent,
        }

    def rss_bytes(self) -> int | None:
        try:
            return int(self._rss())
        except (OSError, AttributeError, NotImplementedError, ValueError):
            return None

    def snapshot(self) -> dict[str, Any]:
        return {"disk": self.disk(), "rss_bytes": self.rss_bytes()}


def default_rss_bytes() -> int:
    """Resident set size of this process, in bytes. Raises when unsupported."""
    if sys.platform == "win32":
        return _windows_rss_bytes()
    statm = Path("/proc/self/statm")
    if statm.exists():
        pages = int(statm.read_text(encoding="ascii").split()[1])
        return pages * _page_size()
    raise NotImplementedError("no resident-set-size reader on this platform")


class _ProcessMemoryCounters(ctypes.Structure):
    _fields_ = [
        ("cb", ctypes.c_ulong),
        ("PageFaultCount", ctypes.c_ulong),
        ("PeakWorkingSetSize", ctypes.c_size_t),
        ("WorkingSetSize", ctypes.c_size_t),
        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
        ("PagefileUsage", ctypes.c_size_t),
        ("PeakPagefileUsage", ctypes.c_size_t),
    ]


def _windows_rss_bytes() -> int:
    counters = _ProcessMemoryCounters()
    counters.cb = ctypes.sizeof(counters)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    psapi = ctypes.WinDLL("psapi", use_last_error=True)
    kernel32.GetCurrentProcess.restype = ctypes.c_void_p
    psapi.GetProcessMemoryInfo.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(_ProcessMemoryCounters),
        ctypes.c_ulong,
    ]
    psapi.GetProcessMemoryInfo.restype = ctypes.c_int
    process = kernel32.GetCurrentProcess()
    ok = psapi.GetProcessMemoryInfo(process, ctypes.byref(counters), counters.cb)
    if not ok:
        raise OSError("GetProcessMemoryInfo failed")
    return int(counters.WorkingSetSize)


def _page_size() -> int:
    # `os.sysconf` does not exist on Windows, which is why it is looked up dynamically.
    sysconf = getattr(os, "sysconf", None)
    if sysconf is None:
        return 4096
    try:
        return int(sysconf("SC_PAGE_SIZE"))
    except (ValueError, OSError):
        return 4096


def format_snapshot(snapshot: Mapping[str, Any]) -> str:
    """One-line JSON, for a health endpoint or a log field."""
    return json.dumps(dict(snapshot), ensure_ascii=False, default=str)


__all__ = [
    "JsonFormatter",
    "Metrics",
    "ResourceMonitor",
    "configure_json_logging",
    "default_rss_bytes",
    "format_snapshot",
    "upgrade_level",
]
