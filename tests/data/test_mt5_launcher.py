"""The terminal is started hidden, only when none runs, and never twice."""

from pathlib import Path
from typing import Any

import pytest

from tradingagent.data import mt5_launcher


def test_a_running_terminal_is_left_alone(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mt5_launcher.sys, "platform", "win32")
    monkeypatch.setattr(mt5_launcher, "is_running", lambda: True)
    popen: list[Any] = []
    monkeypatch.setattr(mt5_launcher.subprocess, "Popen", lambda *a, **k: popen.append(a))
    assert mt5_launcher.start_hidden(None) is False
    assert popen == []


def test_launch_is_hidden_and_detached(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    exe = tmp_path / "terminal64.exe"
    exe.write_bytes(b"")
    monkeypatch.setattr(mt5_launcher.sys, "platform", "win32")
    monkeypatch.setattr(mt5_launcher, "is_running", lambda: False)
    monkeypatch.setattr(mt5_launcher.time, "sleep", lambda _: None)
    seen: dict[str, Any] = {}

    class Info:
        dwFlags = 0
        wShowWindow = 1

    monkeypatch.setattr(mt5_launcher.subprocess, "STARTUPINFO", Info, raising=False)
    monkeypatch.setattr(mt5_launcher.subprocess, "STARTF_USESHOWWINDOW", 1, raising=False)
    monkeypatch.setattr(mt5_launcher.subprocess, "CREATE_NO_WINDOW", 0x08000000, raising=False)
    monkeypatch.setattr(mt5_launcher.subprocess, "DETACHED_PROCESS", 0x8, raising=False)

    def popen(args: list[str], **kwargs: Any) -> None:
        seen["args"], seen["kwargs"] = args, kwargs

    monkeypatch.setattr(mt5_launcher.subprocess, "Popen", popen)
    assert mt5_launcher.start_hidden(exe) is True
    assert seen["args"] == [str(exe)]
    assert seen["kwargs"]["startupinfo"].wShowWindow == 0  # SW_HIDE


def test_without_a_terminal_nothing_is_launched(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mt5_launcher.sys, "platform", "win32")
    monkeypatch.setattr(mt5_launcher, "is_running", lambda: False)
    monkeypatch.setattr(mt5_launcher, "find_terminal", lambda configured: None)
    assert mt5_launcher.start_hidden(None) is False
