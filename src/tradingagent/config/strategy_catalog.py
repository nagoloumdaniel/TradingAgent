import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from tradingagent.config._yaml import Problem, read_yaml, render, validation_problems
from tradingagent.config.errors import ConfigError
from tradingagent.core.mode import TradingMode, mode_rank
from tradingagent.core.states import ValidationStage
from tradingagent.strategies.base import Strategy
from tradingagent.strategies.manifest import StrategyManifest

#: The highest `max_mode` a manifest of `config/strategies/` may declare on its own.
#:
#: Above it, the ceiling is a **decision of the operator**, and RM-016 requires that decision
#: to be explicit, dated, and to record the validation results it invokes. The invariant is
#: therefore *no silent escalation*: a manifest may go higher, but only carrying the
#: derogation below. A manifest that declares the same ceiling with no banner is refused --
#: a comment is the only thing standing between a raised `max_mode` and an account, so the
#: catalog reads it and the load is all-or-nothing.
MODE_CEILING = TradingMode.SIGNAL

#: The marker an operator derogation carries in the header banner.
DEROGATION_MARKER = "DEROGATION OPERATEUR"

#: What the banner must name, one entry per missing piece, with the reason a reader needs if
#: it is absent. The figures are the *validation figures that are missing*: what was measured
#: against what was required, how far the p-value is from the Bonferroni line, and how many
#: candidates survived the false-discovery correction. "Validated" with no number is a claim.
_DEROGATION_ITEMS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "the net profit factor measured and the one required",
        re.compile(r"profit\s+factor[^\n]*\d[^\n]*\d"),
    ),
    ("the minimum p-value", re.compile(r"p-?value[^\n]*\d")),
    ("the Bonferroni line it was read against", re.compile(r"bonferroni[^\n]*\d")),
    ("the survivors after the false-discovery correction", re.compile(r"\d+\s+survivant")),
)
_ISO_DATE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
_WHITESPACE = re.compile(r"\s+")


@dataclass(frozen=True)
class LoadedStrategy:
    manifest: StrategyManifest
    strategy: Strategy[Any]


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

    `SIGNAL` and below are nobody's business but the manifest's. Above the ceiling two
    different documents are required, and they are not interchangeable:

    * `PAPER` and `DEMO` take an **operator derogation** -- the marker
      ``DEROGATION OPERATEUR``, an ISO date, and the validation figures that are missing.
      This is the RM-016 decision written where the ceiling is declared.
    * `LIVE` takes the **complete validation** of the nine gates of §49, each named in the
      banner. A derogation is exactly what it means: a ceiling granted *without* validation,
      so it can never be the document that opens the real account. DEMO is a bounded ceiling
      -- a demonstration account -- and LIVE is above it in the exposure order.
    """
    if mode_rank(max_mode) <= mode_rank(MODE_CEILING):
        return []
    banner = _fold(header_banner(text))
    stages = tuple(stage.value for stage in ValidationStage)
    if max_mode is TradingMode.LIVE:
        missing = [stage for stage in stages if stage not in banner]
        if not missing and not _ISO_DATE.search(banner):
            missing = ["its date"]
        if missing:
            return [
                (
                    "max_mode LIVE requires the complete validation of the nine gates of §49, "
                    "named in the header banner (" + ", ".join(stages) + "), and its date; "
                    "missing: " + ", ".join(missing) + ". An operator derogation raises the "
                    "ceiling to DEMO only -- DEMO cannot reach a real account (RM-017) -- and a "
                    "derogation is not a validation (RM-016)"
                )
            ]
        return []
    missing = [label for label, pattern in _DEROGATION_ITEMS if not pattern.search(banner)]
    if not _ISO_DATE.search(banner):
        missing.insert(0, "the date of the operator decision")
    if DEROGATION_MARKER.lower() not in banner:
        missing.insert(0, f"the marker {DEROGATION_MARKER!r}")
    if not missing:
        return []
    return [
        (
            f"max_mode {max_mode} is above the {MODE_CEILING} ceiling without a documented "
            "operator derogation: a manifest may not escalate silently. Write the decision in "
            "a header banner -- the marker 'DEROGATION OPERATEUR', its ISO date, and the "
            "validation figures that are missing -- or lower max_mode. Missing: "
            + "; ".join(missing)
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
