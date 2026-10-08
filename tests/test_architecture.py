import ast
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src" / "tradingagent"
ROOT_PACKAGE = "tradingagent"
PACKAGES = {
    "core",
    "config",
    "control",
    "data",
    "indicators",
    "strategies",
    "ai",
    "risk",
    "execution",
    "notify",
    "signals",
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
# The root is also the one production module allowed to import `research` and `backtest`, and
# only in order to compose dependencies: it builds the improvement chain out of the real
# search, the real version builder and the real campaign, and hands the result to `ai.daily`,
# which imports none of them. A composition root that cannot compose is only a name — without
# this exception the daily chain stays wired to nothing. The exception is exactly one module
# wide, and `test_the_research_exception_does_not_leak` proves it does not spread.
OFFLINE_IMPORTERS = {COMPOSITION_ROOT}
# Pure-calculation packages and the project packages each may depend on.
# Pure means: no clock read, no network, no randomness, no I/O, no project state.
PURE_PACKAGES = {
    "indicators": {"core"},
    "strategies": {"core", "indicators"},
}
# datetime stays importable: strategies handle candle times. Reading the clock is what's banned.
CLOCK_READS = {"now", "utcnow", "today"}
# The broker SDK may be imported by one adapter only, so the rest stays testable without it.
BROKER_SDK = "MetaTrader5"
BROKER_ADAPTER = "tradingagent.data.mt5_terminal"
IMPURE_MODULES = {
    "time",
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


def clock_reads(tree: ast.Module) -> list[str]:
    return [
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in CLOCK_READS
    ]


def find_violations(src_root: Path) -> list[str]:
    violations: list[str] = []
    for path in sorted(src_root.rglob("*.py")):
        module = module_name(path, src_root)
        importer = subpackage(module)
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        allowed = PURE_PACKAGES.get(importer or "")
        if allowed is not None:
            violations.extend(
                f"{module} calls .{call}(): {importer} must not read the clock"
                for call in clock_reads(tree)
            )
        for target in sorted(imported_modules(tree, module, path.name == "__init__.py")):
            dependency = subpackage(target)
            if target.split(".")[0] == BROKER_SDK and module != BROKER_ADAPTER:
                violations.append(f"{module} imports {target}: only {BROKER_ADAPTER} may")
            if allowed is not None:
                if target.split(".")[0] in IMPURE_MODULES:
                    violations.append(f"{module} imports {target}: {importer} must stay pure")
                if dependency not in {None, importer, *allowed}:
                    violations.append(
                        f"{module} imports {target}: {importer} may only depend on "
                        f"{', '.join(sorted(allowed))}"
                    )
            if dependency is None or dependency == importer:
                continue
            if (
                dependency == "execution"
                and importer not in EXECUTION_GATEKEEPERS
                and module != COMPOSITION_ROOT
            ):
                violations.append(f"{module} imports {target}: only risk may reach execution")
            if (
                dependency in OFFLINE_ONLY
                and importer not in OFFLINE_ONLY
                and module not in OFFLINE_IMPORTERS
            ):
                violations.append(
                    f"{module} imports {target}: only the composition root {COMPOSITION_ROOT} "
                    "may import research or backtest, and only to compose them"
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
        ("runtime/loop.py", "import tradingagent.research.explore\n"),
        ("ai/improvement_cycle.py", "from tradingagent.research.improvement import search\n"),
        ("analytics/__init__.py", "from ..backtest import harness\n"),
        ("indicators/rsi.py", "import time\n"),
        ("indicators/rsi.py", "import urllib.request\n"),
        ("indicators/rsi.py", "import random\n"),
        ("indicators/rsi.py", "from tradingagent.storage import repository\n"),
        ("indicators/rsi.py", "from ..data import feed\n"),
        ("indicators/rsi.py", "from tradingagent.strategies import base\n"),
        ("indicators/rsi.py", "from datetime import date\nx = date.today()\n"),
        ("strategies/trend.py", "from datetime import UTC, datetime\nx = datetime.now(UTC)\n"),
        ("strategies/trend.py", "import datetime\nx = datetime.datetime.utcnow()\n"),
        ("strategies/trend.py", "import time\n"),
        ("strategies/trend.py", "from tradingagent.config import settings\n"),
        ("strategies/trend.py", "from tradingagent.data import feed\n"),
        ("strategies/trend.py", "from ..storage import repository\n"),
        ("data/market_data.py", "import MetaTrader5 as mt5\n"),
        ("execution/deriv.py", "import MetaTrader5\n"),
        ("risk/gate.py", "from MetaTrader5 import order_send\n"),
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
        ("app.py", "import tradingagent.research.explore\n"),
        ("app.py", "from tradingagent.research.improvement import search_improvement\n"),
        ("app.py", "from tradingagent.research.versioning import build_candidate\n"),
        ("app.py", "from tradingagent.backtest.costs import CostModel\n"),
        ("app.py", "import tradingagent.backtest.harness\n"),
        ("backtest/harness.py", "from tradingagent.strategies import base\n"),
        ("backtest/harness.py", "from ..analytics import metrics\n"),
        ("research/explore.py", "from tradingagent.backtest import harness\n"),
        ("execution/deriv.py", "from tradingagent.core import types\n"),
        ("indicators/rsi.py", "import math\nfrom collections.abc import Sequence\n"),
        ("indicators/rsi.py", "from tradingagent.core import timeframe\n"),
        ("indicators/rsi.py", "from ._checks import require_period\n"),
        ("indicators/rsi.py", "from datetime import datetime\n"),
        ("data/feed.py", "import asyncio\nimport time\n"),
        ("data/feed.py", "from datetime import UTC, datetime\nx = datetime.now(UTC)\n"),
        ("strategies/trend.py", "from datetime import datetime, timedelta\n"),
        ("strategies/trend.py", "from tradingagent.indicators.momentum import rsi\n"),
        ("strategies/trend.py", "from tradingagent.core.market import Candle\n"),
        ("strategies/trend.py", "from pydantic import BaseModel\n"),
        ("data/mt5_terminal.py", "import MetaTrader5 as mt5\n"),
    ],
)
def test_allowed_import_passes(tmp_path: Path, relative: str, source: str) -> None:
    src_root = build_tree(tmp_path, {relative: source})
    assert find_violations(src_root) == []


# The three imports the composition root needs to wire the daily improvement chain: the real
# acceptance rule, the real version builder and the frozen datasets it measures on.
COMPOSITION_SOURCE = (
    "from tradingagent.research.improvement import search_improvement\n"
    "from tradingagent.research.versioning import build_candidate, write_candidate\n"
    "from tradingagent.backtest.datasets import DatasetStore\n"
)
# Every production package, one module each, plus the root package itself. None of them is the
# composition root, so none of them may reach the offline layers.
NOT_THE_COMPOSITION_ROOT = (
    "core/improvement.py",
    "ai/improvement_cycle.py",
    "ai/daily.py",
    "runtime/loop.py",
    "signals/generator.py",
    "risk/gate.py",
    "reporting/daily.py",
    "registry/store.py",
    "data/feed.py",
    "notify/bot.py",
    "storage/repository.py",
    "control/cli.py",
    "__init__.py",
)


@pytest.mark.parametrize("relative", NOT_THE_COMPOSITION_ROOT)
def test_the_research_exception_does_not_leak(tmp_path: Path, relative: str) -> None:
    """Exactly one module may compose the offline layers; everywhere else it is still refused.

    Naming a composition root only holds if the exception stops there. The very same imports
    that are legal in `app.py` must be violations in `ai/`, `runtime/`, `risk/`, `notify/`,
    `storage/` — and in `core/` most of all, since `core` is imported by everyone and must
    import nobody: granting it `research` would put the offline layers in every module's
    import graph.
    """
    src_root = build_tree(tmp_path / "elsewhere", {relative: COMPOSITION_SOURCE})
    violations = find_violations(src_root)
    assert violations, f"{relative} must not import research or backtest"
    assert all("research" in violation or "backtest" in violation for violation in violations)
    assert any(COMPOSITION_ROOT in violation for violation in violations), violations


def test_the_composition_root_may_compose_the_offline_layers(tmp_path: Path) -> None:
    """The other half of the rule: the same imports, in the root, are what wires the chain."""
    src_root = build_tree(tmp_path / "root", {"app.py": COMPOSITION_SOURCE})
    assert find_violations(src_root) == []
