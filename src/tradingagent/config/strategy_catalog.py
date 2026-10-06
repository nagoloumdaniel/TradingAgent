from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from tradingagent.config._yaml import Problem, read_yaml, render, validation_problems
from tradingagent.config.errors import ConfigError
from tradingagent.strategies.base import Strategy
from tradingagent.strategies.manifest import StrategyManifest


@dataclass(frozen=True)
class LoadedStrategy:
    manifest: StrategyManifest
    strategy: Strategy[Any]


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

        problems: list[Problem] = []
        if path.stem != manifest.ref:
            problems.append(
                (("strategy_id",), f"file name {path.name!r} must be {manifest.ref}.yaml")
            )
        strategy_class = registry.get(manifest.strategy_id)
        parameters = None
        if strategy_class is None:
            problems.append(
                (("strategy_id",), f"strategy {manifest.strategy_id!r} is not registered in code")
            )
        else:
            try:
                parameters = strategy_class.parameters_model.model_validate(manifest.parameters)
            except ValidationError as error:
                problems += validation_problems(error, prefix=("parameters",))

        if problems or strategy_class is None or parameters is None:
            lines += document.locate(problems)
            continue
        catalog[manifest.ref] = LoadedStrategy(manifest, strategy_class(parameters))

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
