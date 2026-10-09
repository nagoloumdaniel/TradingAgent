"""Met les mesures de l'axe A en tableaux markdown, sans recopie manuelle.

Les chiffres du rapport `docs/research/vwap-tuning/axe-A-stop.md` sont produits ici depuis les
JSONL de mesure. Recopier un PF a la main est la facon la plus sure d'ecrire un chiffre faux
dans un rapport qui doit servir a decider ; ce script supprime l'etape.

    uv run python scripts/backtest/tune_stop_report.py
"""

from __future__ import annotations

import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
OUTPUT_DIR = ROOT / "docs" / "research" / "vwap-tuning"

#: Les fichiers de mesure, dans l'ordre ou ils doivent apparaitre dans le rapport.
SOURCES: tuple[tuple[str, str, str], ...] = (
    (
        "Exploration, 4 999 bougies (elimination)",
        "axe-A-exploration-5k.jsonl",
        "copie gelee `bfa0e65` — le champ `source` n'existait pas encore",
    ),
    (
        "Decision, 59 999 bougies (jeu complet) — lot 1",
        "axe-A-decision-60k.jsonl",
        "source vivante `fa76d2a`, harnais `338da61`, couts du depot",
    ),
    (
        "Decision, 59 999 bougies (jeu complet) — lot 2",
        "axe-A-decision-60k-b.jsonl",
        "source vivante `fa76d2a`, harnais `338da61`, couts du depot",
    ),
    (
        "Controle d'equivalence des sources (59 999 bougies)",
        "axe-A-verification-source.jsonl",
        "copie gelee `bfa0e65`, a rapprocher du lot 1",
    ),
    (
        "Controle du harnais : la reference, apres la modification de `harness.py`",
        "axe-A-harness-check.jsonl",
        "source vivante, harnais de l'arbre de travail",
    ),
    (
        "Finalistes sous le spread reellement observe (18,424 $)",
        "axe-A-spread-observe.jsonl",
        "source vivante, harnais de l'arbre de travail, spread impose",
    ),
    (
        "Validation par moities de jeu (59 999 bougies)",
        "axe-A-windows-60k.jsonl",
        "source vivante, harnais de l'arbre de travail",
    ),
)

COLUMNS = (
    ("Configuration", lambda r: f"`{r['label']}`"),
    ("Barres", lambda r: str(r.get("bars", ""))),
    ("Trades", lambda r: str(r.get("trades", ""))),
    ("Reussite", lambda r: _percent(r.get("win_rate"))),
    ("Net EUR", lambda r: _number(r.get("net_eur"))),
    ("PF", lambda r: _number(r.get("profit_factor"), 4)),
    ("DD EUR", lambda r: _number(r.get("max_drawdown_eur"))),
    ("MAE gagnants", lambda r: _number(r.get("winners_mae_r_median"))),
    ("MFE gagnants", lambda r: _number(r.get("winners_mfe_r_median"))),
    ("MAE perdants", lambda r: _number(r.get("losers_mae_r_median"))),
    ("MFE perdants", lambda r: _number(r.get("losers_mfe_r_median"))),
)


def _number(value: Any, digits: int = 2) -> str:
    if value is None:
        return "—"
    return f"{float(value):.{digits}f}".replace(".", ",")


def _percent(value: Any) -> str:
    if value is None:
        return "—"
    return f"{100 * float(value):.1f} %".replace(".", ",")


def _harness(row: dict[str, Any]) -> str:
    parts: list[str] = []
    fractions = row["harness"]["partial_exit_fractions"]
    if fractions:
        parts.append("partiel " + "/".join(f"{100 * f:.0f}" for f in fractions))
    else:
        parts.append("sortie unique")
    if row["harness"]["move_stop_to_breakeven_after_first_target"]:
        parts.append("break-even")
    if row["harness"]["trailing_stop_swing_strength"]:
        parts.append(f"trailing structure {row['harness']['trailing_stop_swing_strength']}")
    if row["harness"]["trailing_stop_atr"]:
        parts.append(f"trailing ATR {row['harness']['trailing_stop_atr']}")
    if row["harness"]["max_holding_bars"]:
        parts.append(f"holding {row['harness']['max_holding_bars']}")
    return " + ".join(parts)


def _geometry(row: dict[str, Any]) -> str:
    parameters = row["parameters"]
    stop = _number(parameters["stop_atr_multiplier"], 1)
    first = _number(parameters["first_target_rr"], 1)
    final = _number(parameters["final_target_rr"], 1)
    return f"stop {stop} ATR, TP {first}/{final}"


def load(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def table(rows: Sequence[dict[str, Any]], *, show_geometry: bool = False) -> str:
    """Un tableau markdown, les meilleurs PF en tete, avec la geometrie et le harnais."""
    if not rows:
        return "_aucune mesure_\n"
    header = ["Configuration"] + (["Geometrie", "Harnais"] if show_geometry else [])
    header += [name for name, _ in COLUMNS[1:]]
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    ordered = sorted(
        rows, key=lambda r: (r.get("profit_factor") is None, -(r.get("profit_factor") or 0))
    )
    for row in ordered:
        if row.get("status") != "ok":
            lines.append(f"| `{row['label']}` | {row['status']} |" + " |" * (len(header) - 2))
            continue
        cells = [f"`{row['label']}`"]
        if show_geometry:
            cells += [_geometry(row), _harness(row)]
        cells += [render(row) for name, render in COLUMNS[1:]]
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines) + "\n"


def best(rows: Sequence[dict[str, Any]]) -> dict[str, Any] | None:
    measured = [r for r in rows if r.get("status") == "ok" and r.get("profit_factor") is not None]
    return max(measured, key=lambda r: r["profit_factor"]) if measured else None


def splice(report: Path, sections: dict[str, str]) -> list[str]:
    """Injecte chaque tableau entre ses marqueurs dans le rapport, sans y toucher autrement.

    Le rapport est ecrit a la main ; ses tableaux ne le sont pas. Un chiffre recopie a la main
    dans un rapport qui sert a decider est la faute la plus couteuse a rattraper, donc les
    tableaux sont engendres ici et poses entre `<!-- DEBUT:cle -->` et `<!-- FIN:cle -->`.
    L'operation est idempotente : relancer le script remplace ce qui est entre les marqueurs.
    """
    if not report.exists():
        return []
    text = report.read_text(encoding="utf-8")
    replaced: list[str] = []
    for key, fragment in sections.items():
        start = f"<!-- DEBUT:{key} -->"
        end = f"<!-- FIN:{key} -->"
        if start not in text or end not in text:
            continue
        head, _, rest = text.partition(start)
        _, _, tail = rest.partition(end)
        text = f"{head}{start}\n{fragment}\n{end}{tail}"
        replaced.append(key)
    report.write_text(text, encoding="utf-8")
    return replaced


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None and not stream.isatty():
            reconfigure(encoding="utf-8", errors="replace")

    fragments: list[str] = []
    sections: dict[str, str] = {}
    summary: list[str] = []
    for title, name, origin in SOURCES:
        rows = load(OUTPUT_DIR / name)
        if not rows:
            continue
        windows = sorted({str(r.get("window", r.get("bars"))) for r in rows})
        fragments.append(f"### {title}\n")
        fragments.append(f"Source : {origin}. {len(rows)} mesure(s).\n")
        for window in windows:
            subset = [r for r in rows if str(r.get("window", r.get("bars"))) == window]
            if len(windows) > 1:
                fragments.append(f"Fenetre : `{window}`\n")
            fragments.append(table(subset, show_geometry=True))
            fragments.append("")
        top = best(rows)
        if top is not None:
            summary.append(
                f"- **{title}** : meilleur PF {_number(top['profit_factor'], 4)} "
                f"(`{top['label']}`, {top.get('trades')} trades, "
                f"net {_number(top.get('net_eur'))} EUR)"
            )

    def source(name: str) -> list[dict[str, Any]]:
        return load(OUTPUT_DIR / name)

    exploration = source("axe-A-exploration-5k.jsonl")
    sections["exploration"] = (
        f"Source : {SOURCES[0][2]}. {len(exploration)} mesure(s).\n\n"
        + table(exploration, show_geometry=True)
    )
    decision_rows = source("axe-A-decision-60k.jsonl") + source("axe-A-decision-60k-b.jsonl")
    sections["decision"] = (
        f"Source : source vivante `fa76d2a`, harnais `338da61`, couts du depot. "
        f"{len(decision_rows)} mesure(s) sur 59 999 bougies.\n\n"
        + table(decision_rows, show_geometry=True)
    )
    controls = source("axe-A-verification-source.jsonl") + source("axe-A-harness-check.jsonl")
    sections["controles"] = (
        f"{len(controls)} mesure(s).\n\n" + table(controls, show_geometry=True)
    )
    spread = source("axe-A-spread-observe.jsonl")
    sections["spread"] = (
        f"Source : spread impose a 18,424 $, reste du modele du depot. "
        f"{len(spread)} mesure(s).\n\n" + table(spread, show_geometry=True)
    )
    windows = source("axe-A-windows-60k.jsonl")
    sections["fenetres"] = (
        f"{len(windows)} mesure(s), deux moities disjointes, deux revisions du harnais.\n\n"
        + table(windows, show_geometry=True)
    )

    target = OUTPUT_DIR / "axe-A-tableaux.md"
    target.write_text(
        "# Axe A — tableaux de mesure (engendres)\n\n"
        "Ce fichier est produit par `scripts/backtest/tune_stop_report.py` depuis les JSONL de "
        "mesure. Il ne se recopie pas a la main.\n\n" + "\n".join(fragments),
        encoding="utf-8",
    )
    report = OUTPUT_DIR / "axe-A-stop.md"
    replaced = splice(report, sections)
    print("\n".join(summary))
    print(f"\n== {target} ==")
    print(f"== tableaux injectes dans {report.name} : {', '.join(replaced) or 'aucun'} ==")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__: Sequence[str] = ["SOURCES", "best", "load", "main", "splice", "table"]
