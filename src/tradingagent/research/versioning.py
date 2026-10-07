"""Turning an accepted improvement into a version, without letting it reach production.

The operator's rule ends with "if it is good, we adopt it and update". An accepted
improvement is a *new version of the strategy* — and the trap is that "update" is read as
"write it where the agent loads it". That would let a single day's comparison outrank the
nine gates, which exist precisely because a backtest that looks better usually is not.

So this module produces the version and stops at the door: it writes a manifest a human and
the protocol can both read, under `docs/research/candidates/`, never under
`config/strategies/`. Promotion remains `registry.promote`, which demands every gate.

The version number is a patch bump, and that is not decoration: `1.1.0` and `1.1.1` are the
same idea with different parameters, which is what a parameter improvement is. A change of
idea is a minor bump and belongs to the discovery lab, not here.
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# Where a candidate manifest lives. Deliberately not `config/strategies`: the production
# catalog only ever loads what the protocol promoted.
CANDIDATES_DIR = Path("docs") / "research" / "candidates"

_VERSION = re.compile(r"^(?P<major>\d+)\.(?P<minor>\d+)\.(?P<patch>\d+)$")


@dataclass(frozen=True)
class Version:
    major: int
    minor: int
    patch: int

    @staticmethod
    def parse(text: str) -> "Version":
        match = _VERSION.match(text.strip())
        if match is None:
            raise ValueError(f"not a version: {text!r} (expected MAJOR.MINOR.PATCH)")
        return Version(int(match["major"]), int(match["minor"]), int(match["patch"]))

    def patched(self) -> "Version":
        """Same idea, different parameters — what a parameter improvement produces."""
        return Version(self.major, self.minor, self.patch + 1)

    def __str__(self) -> str:
        return f"{self.major}.{self.minor}.{self.patch}"


def next_ref(ref: str) -> str:
    """`witness@1.1.0` becomes `witness@1.1.1`.

    A malformed ref is refused rather than guessed: a version the protocol cannot parse
    would be stored and then never promotable, which is worse than an error now.
    """
    if "@" not in ref:
        raise ValueError(
            f"a reference carries its version: {ref!r} (expected id@MAJOR.MINOR.PATCH)"
        )
    strategy_id, _, version = ref.partition("@")
    if not strategy_id:
        raise ValueError(f"a reference carries its strategy id: {ref!r}")
    return f"{strategy_id}@{Version.parse(version).patched()}"


@dataclass(frozen=True)
class CandidateVersion:
    """A version built from an accepted improvement, ready for the gates."""

    ref: str
    supersedes: str
    strategy_id: str
    parameters: Mapping[str, float]
    manifest: Mapping[str, Any]
    market: str

    def to_yaml(self) -> str:
        """The manifest as the production files are written: comments first, then values."""
        lines = [
            f"# Candidat issu de la boucle d'amélioration, proposé pour {self.market}.",
            f"# Remplace {self.supersedes} si — et seulement si — les neuf portes passent.",
            "# NE PAS copier dans config/strategies/ à la main : la promotion est le travail",
            "# de `registry.promote`, qui exige chaque porte de §49.",
            f"strategy_id: {self.strategy_id}",
            f"version: {self.ref.partition('@')[2]}",
            f"max_mode: {self.manifest['max_mode']}",
            f"allowed_symbols: [{self.market}]",
            f"timeframes: [{', '.join(self.manifest['timeframes'])}]",
            f"history_bars: {self.manifest['history_bars']}",
            f"expiry_bars: {self.manifest['expiry_bars']}",
        ]
        if "ai_filter" in self.manifest:
            lines.append(f"ai_filter: {self.manifest['ai_filter']}")
        lines.append("parameters:")
        for key in sorted(self.parameters):
            lines.append(f"  {key}: {self.parameters[key]}")
        return "\n".join(lines) + "\n"


def build_candidate(
    *,
    market: str,
    supersedes: str,
    parameters: Mapping[str, float],
    incumbent_manifest: Mapping[str, Any],
) -> CandidateVersion:
    """The next version of a strategy, carrying the accepted parameters.

    Every field but the version and the parameters is copied from the incumbent: a candidate
    must differ by exactly what was measured. Letting anything else drift — a timeframe, a
    symbol, the mode ceiling — would mean the comparison validated one strategy and approved
    another.
    """
    ref = next_ref(supersedes)
    strategy_id = supersedes.partition("@")[0]
    manifest = dict(incumbent_manifest)
    declared = manifest.get("allowed_symbols", [market])
    if isinstance(declared, str):
        declared = [declared]
    if market not in declared:
        raise ValueError(
            f"{supersedes} is not declared for {market}: refusing to build a version for a "
            "market the incumbent never traded"
        )
    manifest["allowed_symbols"] = [market]
    return CandidateVersion(
        ref=ref,
        supersedes=supersedes,
        strategy_id=strategy_id,
        parameters=dict(parameters),
        manifest=manifest,
        market=market,
    )


def write_candidate(candidate: CandidateVersion, directory: Path | None = None) -> Path:
    """Write the candidate manifest, and only ever under the research directory.

    The check is not defensive noise: this function is the one place where an accepted
    improvement could reach production by accident, so it refuses to write anywhere that
    the agent loads from.
    """
    target_dir = Path(directory) if directory is not None else CANDIDATES_DIR
    name = candidate.ref.replace("@", "-")
    path = target_dir / f"{name}.yaml"
    resolved = path.resolve()
    production = (Path("config") / "strategies").resolve()
    if production in resolved.parents or resolved.parent == production:
        raise ValueError(
            f"refusing to write a candidate into {resolved}: candidates live under "
            f"{target_dir} and reach production only through the promotion gates"
        )
    target_dir.mkdir(parents=True, exist_ok=True)
    path.write_text(candidate.to_yaml(), encoding="utf-8")
    return path


__all__ = [
    "CANDIDATES_DIR",
    "CandidateVersion",
    "Version",
    "build_candidate",
    "next_ref",
    "write_candidate",
]
