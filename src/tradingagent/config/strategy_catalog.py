import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from tradingagent.config._yaml import Problem, read_yaml, render, validation_problems
from tradingagent.config.errors import ConfigError
from tradingagent.core.mode import TradingMode, mode_rank
from tradingagent.core.states import ValidationStage
from tradingagent.core.timeframe import Timeframe
from tradingagent.strategies.base import Strategy
from tradingagent.strategies.manifest import StrategyManifest

#: The highest `max_mode` a manifest of `config/strategies/` may declare on its own: DEMO, the
#: demonstration account, where no real money is at stake. Above it sits LIVE, which is never
#: granted silently: the header banner must carry the complete validation (see below). A
#: comment is the only thing standing between a raised `max_mode` and the real account, so the
#: catalog reads it and the load is all-or-nothing.
MODE_CEILING = TradingMode.DEMO

_ISO_DATE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
_WHITESPACE = re.compile(r"\s+")


@dataclass(frozen=True)
class LoadedStrategy:
    manifest: StrategyManifest
    strategy: Strategy[Any]
    #: One strategy instance per scanned timeframe that has its own parameters; the others
    #: use `strategy` unchanged.
    by_timeframe: Mapping[Timeframe, Strategy[Any]] = field(default_factory=dict)

    def strategy_for(self, timeframe: Timeframe) -> Strategy[Any]:
        return self.by_timeframe.get(timeframe, self.strategy)


def header_banner(text: str) -> str:
    """The comment block above the first key, with its `#` stripped.

    This is the one place an operator can document a decision *about* a manifest, so it is
    the place the ceiling rule reads. Everything after the first non-comment, non-blank line
    is configuration, not a banner.
    """
    banner: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            banner.append(stripped.lstrip("#").strip())
        elif not stripped:
            if banner:
                banner.append("")
        else:
            break
    return "\n".join(banner).strip("\n")


def _fold(text: str) -> str:
    """Lower-case, accent-free: the banner is prose, and prose is written either way."""
    stripped = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    return _WHITESPACE.sub(" ", stripped).lower()


def mode_ceiling_problems(text: str, max_mode: TradingMode) -> list[str]:
    """Why this `max_mode` may not be declared in this manifest, if it may not.

    `DEMO` is nobody's business but the manifest's. `LIVE` takes the **complete validation** of
    the nine gates of §49, each named in the header banner, and its date: nothing short of that
    opens the real account (RM-016, RM-017).
    """
    if mode_rank(max_mode) <= mode_rank(MODE_CEILING):
        return []
    banner = _fold(header_banner(text))
    stages = tuple(stage.value for stage in ValidationStage)
    missing = [stage for stage in stages if stage not in banner]
    if not missing and not _ISO_DATE.search(banner):
        missing = ["its date"]
    if not missing:
        return []
    return [
        (
            "max_mode LIVE requires the complete validation of the nine gates of §49, "
            "named in the header banner (" + ", ".join(stages) + "), and its date; "
            "missing: " + ", ".join(missing) + ". DEMO is the highest ceiling a manifest "
            "may declare without it (RM-016, RM-017)"
        )
    ]


def load_strategy_catalog(
    directory: Path, registry: Mapping[str, type[Strategy[Any]]]
) -> dict[str, LoadedStrategy]:
    """Load every `<id>@<version>.yaml` manifest, all or nothing, keyed by reference.

    The registry is injected by the composition root: configuration never imports
    strategy code, and a manifest can only select a class that code registered.
    """
    if not directory.is_dir():
        raise ConfigError(f"{directory}: strategy directory not found")
    catalog: dict[str, LoadedStrategy] = {}
    lines: list[str] = []
    for path in sorted(directory.glob("*.yaml")):
        try:
            document = read_yaml(path)
        except ConfigError as error:
            lines.append(f"  {error}")
            continue
        try:
            manifest = StrategyManifest.model_validate(document.data)
        except ValidationError as error:
            lines += document.locate(validation_problems(error))
            continue
        try:
            # The banner is a comment, so it lives in the text and not in the parsed document.
            text = path.read_text(encoding="utf-8")
        except OSError as error:  # pragma: no cover - read_yaml read it microseconds ago
            lines.append(f"  {path}: cannot read configuration ({error.strerror})")
            continue

        problems: list[Problem] = [
            (("max_mode",), reason) for reason in mode_ceiling_problems(text, manifest.max_mode)
        ]
        if path.stem != manifest.ref:
            problems.append(
                (("strategy_id",), f"file name {path.name!r} must be {manifest.ref}.yaml")
            )
        strategy_class = registry.get(manifest.strategy_id)
        parameters = None
        per_timeframe: dict[Timeframe, Strategy[Any]] = {}
        if strategy_class is None:
            problems.append(
                (("strategy_id",), f"strategy {manifest.strategy_id!r} is not registered in code")
            )
        else:
            try:
                parameters = strategy_class.parameters_model.model_validate(manifest.parameters)
            except ValidationError as error:
                problems += validation_problems(error, prefix=("parameters",))
            for timeframe in manifest.timeframe_parameters:
                try:
                    own = strategy_class.parameters_model.model_validate(
                        manifest.parameters_for(timeframe)
                    )
                except ValidationError as error:
                    problems += validation_problems(
                        error, prefix=("timeframe_parameters", str(timeframe))
                    )
                else:
                    per_timeframe[timeframe] = strategy_class(own)

        if problems or strategy_class is None or parameters is None:
            lines += document.locate(problems)
            continue
        catalog[manifest.ref] = LoadedStrategy(manifest, strategy_class(parameters), per_timeframe)

    if lines:
        raise ConfigError(render("Invalid strategy manifests", lines))
    return catalog


class StrategyCatalog:
    """The live set of strategy manifests, reloadable without a restart (TASK-032).

    Construction validates: the process never starts on an invalid catalog. `reload()`
    re-reads the directory all or nothing; an invalid candidate raises and leaves the
    current snapshot untouched, so a broken deployment never interrupts a running agent.
    Readers grab `current()` once per evaluation — the swap is a single reference
    assignment, and an already-loaded strategy is never rebuilt behind their back.
    """

    def __init__(self, directory: Path, registry: Mapping[str, type[Strategy[Any]]]) -> None:
        self._directory = directory
        self._registry = registry
        self._catalog = load_strategy_catalog(directory, registry)

    def current(self) -> dict[str, LoadedStrategy]:
        return self._catalog

    def reload(self) -> dict[str, LoadedStrategy]:
        candidate = load_strategy_catalog(self._directory, self._registry)
        merged: dict[str, LoadedStrategy] = {}
        for ref, loaded in candidate.items():
            previous = self._catalog.get(ref)
            if previous is not None and previous.manifest == loaded.manifest:
                # A manifest that did not change keeps its loaded strategy: no rebuild
                # behind the back of an evaluation in flight.
                merged[ref] = previous
            else:
                merged[ref] = loaded
        self._catalog = merged
        return self._catalog
