import ast
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src" / "tradingagent"
ROOT_PACKAGE = "tradingagent"
PACKAGES = {
    "core",
    "config",
    "data",
    "indicators",
    "strategies",
    "ai",
    "risk",
    "execution",
    "notify",
    "storage",
    "analytics",
    "reporting",
    "backtest",
    "research",
}
OFFLINE_ONLY = {"backtest", "research"}
EXECUTION_GATEKEEPERS = {"risk", "execution"}
# The composition root wires the executor into risk; it may import execution but never call it.
COMPOSITION_ROOT = "tradingagent.app"
# Pure-calculation packages: no clock, no network, no randomness, no I/O, no project state.
PURE_PACKAGES = {"indicators"}
PURE_ALLOWED_DEPENDENCIES = {"core"}
IMPURE_MODULES = {
    "time",
    "datetime",
    "socket",
    "ssl",
    "http",
    "urllib",
    "requests",
    "httpx",
    "aiohttp",
    "websockets",
    "asyncio",
    "random",
    "secrets",
    "os",
    "subprocess",
}


def module_name(path: Path, src_root: Path) -> str:
    parts = path.relative_to(src_root.parent).with_suffix("").parts
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def subpackage(module: str) -> str | None:
    parts = module.split(".")
    if parts[0] != ROOT_PACKAGE or len(parts) < 2:
        return None
    return parts[1]


def imported_modules(tree: ast.Module, module: str, is_package: bool) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0:
                base = node.module or ""
            else:
                package_parts = module.split(".") if is_package else module.split(".")[:-1]
                anchor = package_parts[: len(package_parts) - (node.level - 1)]
                base = ".".join(anchor + ([node.module] if node.module else []))
            found.add(base)
            found.update(f"{base}.{alias.name}" for alias in node.names)
    return found


def find_violations(src_root: Path) -> list[str]:
    violations: list[str] = []
    for path in sorted(src_root.rglob("*.py")):
        module = module_name(path, src_root)
        importer = subpackage(module)
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for target in sorted(imported_modules(tree, module, path.name == "__init__.py")):
            dependency = subpackage(target)
            if importer in PURE_PACKAGES:
                if target.split(".")[0] in IMPURE_MODULES:
                    violations.append(f"{module} imports {target}: {importer} must stay pure")
                if dependency not in {None, importer, *PURE_ALLOWED_DEPENDENCIES}:
                    violations.append(
                        f"{module} imports {target}: {importer} may depend on core only"
                    )
            if dependency is None or dependency == importer:
                continue
            if (
                dependency == "execution"
                and importer not in EXECUTION_GATEKEEPERS
                and module != COMPOSITION_ROOT
            ):
                violations.append(f"{module} imports {target}: only risk may reach execution")
            if dependency in OFFLINE_ONLY and importer not in OFFLINE_ONLY:
                violations.append(
                    f"{module} imports {target}: backtest and research never load in production"
                )
    return violations


def discovered_packages(src_root: Path) -> set[str]:
    return {path.parent.name for path in src_root.glob("*/__init__.py")}


def test_scan_covers_every_package() -> None:
    assert discovered_packages(SRC) >= PACKAGES


def test_real_tree_respects_boundaries() -> None:
    assert find_violations(SRC) == []


def build_tree(root: Path, files: dict[str, str]) -> Path:
    src_root = root / ROOT_PACKAGE
    for package in PACKAGES:
        (src_root / package).mkdir(parents=True, exist_ok=True)
        (src_root / package / "__init__.py").write_text("", encoding="utf-8")
    (src_root / "__init__.py").write_text("", encoding="utf-8")
    for relative, source in files.items():
        target = src_root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(source, encoding="utf-8")
    return src_root


@pytest.mark.parametrize(
    ("relative", "source"),
    [
        ("ai/client.py", "import tradingagent.execution.deriv\n"),
        ("strategies/engine.py", "from ..execution import deriv\n"),
        ("notify/bot.py", "from tradingagent import execution\n"),
        ("data/feed.py", "from tradingagent.execution.deriv import place_order\n"),
        ("__init__.py", "from tradingagent import execution\n"),
        ("reporting/daily.py", "from tradingagent.backtest import harness\n"),
        ("app.py", "import tradingagent.research.explore\n"),
        ("analytics/__init__.py", "from ..backtest import harness\n"),
        ("indicators/rsi.py", "import time\n"),
        ("indicators/rsi.py", "from datetime import datetime\n"),
        ("indicators/rsi.py", "import urllib.request\n"),
        ("indicators/rsi.py", "import random\n"),
        ("indicators/rsi.py", "from tradingagent.storage import repository\n"),
        ("indicators/rsi.py", "from ..data import feed\n"),
    ],
)
def test_forbidden_import_is_detected(tmp_path: Path, relative: str, source: str) -> None:
    src_root = build_tree(tmp_path, {relative: source})
    assert find_violations(src_root) != []


@pytest.mark.parametrize(
    ("relative", "source"),
    [
        ("risk/gate.py", "from tradingagent.execution import deriv\n"),
        ("risk/gate.py", "from ..execution import deriv\n"),
        ("app.py", "from tradingagent.execution import deriv\nfrom tradingagent import risk\n"),
        ("backtest/harness.py", "from tradingagent.strategies import base\n"),
        ("backtest/harness.py", "from ..analytics import metrics\n"),
        ("research/explore.py", "from tradingagent.backtest import harness\n"),
        ("execution/deriv.py", "from tradingagent.core import types\n"),
        ("indicators/rsi.py", "import math\nfrom collections.abc import Sequence\n"),
        ("indicators/rsi.py", "from tradingagent.core import timeframe\n"),
        ("indicators/rsi.py", "from ._checks import require_period\n"),
        ("data/feed.py", "import asyncio\nimport time\n"),
    ],
)
def test_allowed_import_passes(tmp_path: Path, relative: str, source: str) -> None:
    src_root = build_tree(tmp_path, {relative: source})
    assert find_violations(src_root) == []
