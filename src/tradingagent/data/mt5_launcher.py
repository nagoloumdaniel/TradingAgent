"""Start the MetaTrader 5 terminal without a visible window.

`MetaTrader5.initialize()` launches `terminal64.exe` itself, with its window open on the desktop.
The agent has no use for that window, so the terminal is started here first, hidden (`SW_HIDE`),
and `initialize()` then attaches to the running instance. A terminal that is already running is
left alone, whether the operator started it by hand from the MT5 icon or an earlier run did.
The operator opens MT5 from its icon when they want to see it: the hidden instance is stopped
first with `scripts/show_mt5.ps1`.
"""

import logging
import os
import subprocess
import sys
import time
from pathlib import Path

log = logging.getLogger(__name__)

_IMAGE = "terminal64.exe"
_CANDIDATES = (
    r"C:\Program Files\MetaTrader 5\terminal64.exe",
    r"C:\Program Files (x86)\MetaTrader 5\terminal64.exe",
)
_BOOT_POLLS = 20  # one second each
_SETTLE_SECONDS = 3.0


def is_running() -> bool:
    if sys.platform != "win32":
        return False
    try:
        out = subprocess.run(  # noqa: S603
            [
                str(
                    Path(os.environ.get("SYSTEMROOT", r"C:\Windows")) / "System32" / "tasklist.exe"
                ),
                "/FI",
                f"IMAGENAME eq {_IMAGE}",
                "/NH",
            ],
            capture_output=True,
            text=True,
            timeout=15,
            creationflags=subprocess.CREATE_NO_WINDOW,
            check=False,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    return _IMAGE in out.lower()


def find_terminal(configured: Path | None) -> Path | None:
    if configured is not None and configured.is_file() and configured.name.lower() == _IMAGE:
        return configured
    return next((Path(c) for c in _CANDIDATES if os.path.isfile(c)), None)


def start_hidden(configured: Path | None) -> bool:
    """Launch the terminal hidden unless one is running. True when a launch was made."""
    if sys.platform != "win32" or is_running():
        return False
    exe = find_terminal(configured)
    if exe is None:
        log.info("terminal64.exe not found: MetaTrader5 will start the terminal itself")
        return False
    info = subprocess.STARTUPINFO()
    info.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    info.wShowWindow = 0  # SW_HIDE
    try:
        subprocess.Popen(  # noqa: S603
            [str(exe)],
            startupinfo=info,
            creationflags=subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS,
            close_fds=True,
        )
    except OSError as error:
        log.warning("hidden MT5 launch failed (%s): MetaTrader5 will start it itself", error)
        return False
    # initialize() would otherwise race the terminal's own start-up: wait for the process to
    # appear, then let it settle.
    for _ in range(_BOOT_POLLS):
        time.sleep(1.0)
        if is_running():
            time.sleep(_SETTLE_SECONDS)
            break
    return True
