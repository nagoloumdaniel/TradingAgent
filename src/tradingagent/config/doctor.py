"""What the configuration needs, where each value comes from, and what is missing.

An operator who has just cloned the repository should not have to read the source to learn
that `MT5_SERVER` is on their Deriv account card, or that `TEST_DATABASE_URL` must point at
a *different* database. This module carries that knowledge as data, and `tradingagent
doctor` prints it.

Two rules it never breaks:

* **A value is never printed**, only its state. The report is safe to paste into an issue.
* **A missing key is a diagnosis, not a crash.** Reading `.env` here must not raise: the
  whole point is to work on a configuration that is not yet valid.
"""

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from tradingagent.ai.provider import DEFAULT_ANTHROPIC_MODEL, DEFAULT_DEEPSEEK_MODEL
from tradingagent.config.settings import Settings

GENERATE_WITH = 'uv run python -c "import secrets; print(secrets.token_urlsafe(32))"'


class KeyState(StrEnum):
    OK = "ok"
    EMPTY = "empty"
    MISSING = "missing"


@dataclass(frozen=True)
class KeySpec:
    """One configuration value: what it is for, where to get it, whether it is required."""

    name: str
    purpose: str
    where: str
    required: bool


@dataclass(frozen=True)
class KeyStatus:
    spec: KeySpec
    state: KeyState

    @property
    def blocked(self) -> bool:
        return self.spec.required and self.state is not KeyState.OK


# The required/optional split is asserted against `Settings` by a test: adding a required
# field without documenting it here fails the suite rather than surprising an operator.
KEYS: tuple[KeySpec, ...] = (
    KeySpec(
        "MT5_LOGIN",
        "numéro du compte MetaTrader 5",
        "Espace client Deriv → Trader's Hub → la carte du compte MT5 (numéro de connexion)",
        required=True,
    ),
    KeySpec(
        "MT5_SERVER",
        "serveur du courtier",
        "La même carte de compte, tel qu'affiché (ex. Deriv-Demo, Deriv-Server-02)",
        required=True,
    ),
    KeySpec(
        "MT5_PASSWORD",
        "mot de passe INVESTISSEUR (lecture seule)",
        "Terminal MT5 → Outils → Options → Serveur → « Changez le mot de passe investisseur ». "
        "Jamais le mot de passe de l'espace client Deriv",
        required=True,
    ),
    KeySpec(
        "TELEGRAM_BOT_TOKEN",
        "jeton du bot Telegram",
        "Telegram → @BotFather → /newbot ; le jeton n'est affiché qu'une fois",
        required=True,
    ),
    KeySpec(
        "TELEGRAM_ALLOWED_USER_IDS",
        "identifiants Telegram autorisés",
        "Telegram → @userinfobot ; séparés par des virgules. Les autres sont refusés en silence",
        required=True,
    ),
    KeySpec(
        "DATABASE_URL",
        "URI PostgreSQL du pooler Supabase (session)",
        "Supabase → Project Settings → Database → Connection string → Session pooler (IPv4)",
        required=True,
    ),
    KeySpec(
        "ANTHROPIC_API_KEY",
        "commentaire du modèle sur les verdicts de l'AI Lab",
        "console.anthropic.com → Settings → API Keys → Create Key. OPTIONNELLE : sans elle "
        "l'agent tourne et l'analyse reste déterministe",
        required=False,
    ),
    KeySpec(
        "DEEPSEEK_API_KEY",
        "fournisseur de modèle attendu sur ce projet",
        "platform.deepseek.com → API Keys. DeepSeek sert un point d'entrée compatible "
        "Anthropic : le même client dessert les deux fournisseurs",
        required=False,
    ),
    KeySpec(
        "AI_PROVIDER",
        "quel fournisseur répond : auto, deepseek, anthropic ou none",
        "Défaut auto : DeepSeek si sa clé est présente, sinon Anthropic, sinon aucun — et "
        "l'analyse reste déterministe (RM-011)",
        required=False,
    ),
    KeySpec(
        "DEEPSEEK_MODEL",
        "nom du modèle DeepSeek",
        f"Défaut {DEFAULT_DEEPSEEK_MODEL}. Les fournisseurs retirent des noms : voir leur "
        "page tarifs avant de changer",
        required=False,
    ),
    KeySpec(
        "ANTHROPIC_MODEL",
        "nom du modèle Anthropic",
        f"Défaut {DEFAULT_ANTHROPIC_MODEL}",
        required=False,
    ),
    KeySpec(
        "TRADING_MODE",
        "mode de démarrage : OBSERVATION, SIGNAL, PAPER ou DEMO",
        "Défaut SIGNAL. LIVE ne peut pas être posé ici seul (RM-000)",
        required=False,
    ),
    KeySpec(
        "LIVE_TRADING_ENABLED",
        "moitié serveur de la double condition du mode réel",
        "Défaut false. À laisser false hors phase de production",
        required=False,
    ),
    KeySpec(
        "MT5_TERMINAL_PATH",
        "chemin de terminal64.exe",
        "Seulement si plusieurs terminaux sont installés",
        required=False,
    ),
    KeySpec(
        "EA_FILES_DIR",
        "répertoire d'échange avec les deux Expert Advisors",
        r"%APPDATA%\MetaQuotes\Terminal\<instance>\MQL5\Files\TradingAgent — PAS sous "
        "MT5_TERMINAL_PATH. Vide = l'agent tourne sans EA, état valide",
        required=False,
    ),
    KeySpec(
        "TEST_DATABASE_URL",
        "base des tests PostgreSQL (RLS, immuabilité, concurrence)",
        "Un SECOND projet Supabase (ou une seconde base) dont le nom FINIT PAR _test. "
        "⚠️ Ces tests détruisent toutes les tables : jamais la base de production",
        required=False,
    ),
    KeySpec(
        "BACKUP_PASSPHRASE",
        "chiffrement des sauvegardes",
        f"À générer et à garder HORS du serveur : {GENERATE_WITH}",
        required=False,
    ),
    KeySpec(
        "TRADINGAGENT_WEB_TOKEN",
        "protection de l'accès au tableau de bord",
        f"À générer (16 caractères minimum) : {GENERATE_WITH}. Sans elle, le dashboard "
        "écoute en local sans authentification",
        required=False,
    ),
)

SPECS: dict[str, KeySpec] = {spec.name: spec for spec in KEYS}


@dataclass(frozen=True)
class Diagnosis:
    keys: tuple[KeyStatus, ...]

    @property
    def blocked(self) -> tuple[KeyStatus, ...]:
        """The keys that stop the agent from starting at all."""
        return tuple(status for status in self.keys if status.blocked)

    @property
    def ok(self) -> bool:
        return not self.blocked


def read_env(path: Path) -> dict[str, str]:
    """`KEY=value` pairs from a dotenv file. Missing file is an empty mapping, never an error."""
    if not path.is_file():
        return {}
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        name, _, value = stripped.partition("=")
        values[name.strip()] = value.strip()
    return values


def diagnose(env_path: Path) -> Diagnosis:
    """The state of every documented key. Reads the file only; opens no connection."""
    values = read_env(env_path)
    statuses: list[KeyStatus] = []
    for spec in KEYS:
        if spec.name not in values:
            state = KeyState.MISSING
        elif not values[spec.name].strip().strip('"').strip("'"):
            state = KeyState.EMPTY
        else:
            state = KeyState.OK
        statuses.append(KeyStatus(spec=spec, state=state))
    return Diagnosis(keys=tuple(statuses))


def missing_required() -> tuple[str, ...]:
    """The names `Settings` needs and this module forgot to document.

    Used by the test that keeps the catalogue honest; returns an empty tuple in a healthy
    repository.
    """
    required = {
        name.upper() for name, field in Settings.model_fields.items() if field.is_required()
    }
    documented = {spec.name for spec in KEYS}
    return tuple(sorted(required - documented))


_MARKS = {KeyState.OK: "OK", KeyState.EMPTY: "VIDE", KeyState.MISSING: "ABSENTE"}


def render(diagnosis: Diagnosis) -> str:
    """The report an operator reads, in French, with no value ever printed.

    Every key that is missing or empty — required or not — gets its "Où :" instructions.
    An optional key nobody can find is still an unconfigured key.
    """
    lines: list[str] = ["Configuration — état des clés", ""]
    required = [status for status in diagnosis.keys if status.spec.required]
    optional = [status for status in diagnosis.keys if not status.spec.required]

    lines.append(f"Obligatoires ({len(required)}) :")
    for status in required:
        lines.append(f"  [{_MARKS[status.state]:>7}] {status.spec.name:<28} {status.spec.purpose}")
    lines.append("")
    lines.append("Optionnelles :")
    for status in optional:
        lines.append(f"  [{_MARKS[status.state]:>7}] {status.spec.name:<28} {status.spec.purpose}")

    to_fill = [status for status in diagnosis.keys if status.state is not KeyState.OK]
    lines.append("")
    if not to_fill:
        lines.append("Toutes les clés documentées sont renseignées.")
        return "\n".join(lines)

    blocking = diagnosis.blocked
    header = (
        f"À renseigner avant de démarrer ({len(blocking)} bloquante(s)) :"
        if blocking
        else "À renseigner, aucune n'est bloquante :"
    )
    lines.append(header)
    # Blocking keys first: those are the ones that stop the agent from starting at all.
    for status in sorted(to_fill, key=lambda item: not item.blocked):
        lines.append("")
        label = status.spec.name + ("  [OBLIGATOIRE]" if status.spec.required else "")
        lines.append(f"  {label} — {status.spec.purpose}")
        lines.append(f"    Où : {status.spec.where}")
    return "\n".join(lines)
