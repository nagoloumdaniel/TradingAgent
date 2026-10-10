"""L'accord ou le refus d'une entrée, et rien d'autre.

**Ce module ne décide pas d'un signal.** Il répond à une seule question, posée après qu'une
stratégie a déjà produit un candidat : *cette entrée a-t-elle le droit d'exister ?* Sa sortie
n'a donc aucun champ pour porter un prix, une taille, un stop ou une direction — la seule
chose qu'il puisse faire à un signal est de le refuser (C-002, `CLAUDE.md`).

Deux règles, toutes deux exprimées en configuration et jamais en dur :

* **la séance UTC** (`indicators/session.py`) : seules les séances nommées dans le manifeste
  laissent passer. Une séance non nommée est refusée, y compris `off` ;
* **le régime de volatilité** (`indicators/regime.py`) : seuls les régimes nommés dans le
  manifeste laissent passer. Le régime vient du **rapport** ATR courant / moyenne de l'ATR,
  pas d'un niveau en points : trois points d'ATR ne veulent rien dire seuls.

**Le doute laisse passer, et c'est une décision de sécurité.** Quand le rapport ne peut pas
être mesuré — pas assez de barres pour l'ATR, historique insuffisant — le filtre rend
`PASS`/`UNMEASURED`. Un filtre qui bloque sur « je ne sais pas » éteint l'agent en silence,
sans qu'aucune alerte ne le dise : c'est le risque que TASK-069 a documenté, et la règle
inverse est interdite ici.

Le filtre est **inerte tant que le manifeste ne le configure pas** : un manifeste sans clé
`session_filter` / `volatility_filter` produit un `EntryFilter` sans règle, qui laisse tout
passer. Activer un filtre est donc un acte de configuration daté et versionné, pas un effet
de bord du code.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from tradingagent.core.market import Candle
from tradingagent.indicators.regime import (
    DEFAULT_CALM_RATIO,
    DEFAULT_VOLATILE_RATIO,
    Volatility,
    atr_ratio,
)
from tradingagent.indicators.session import Session, session_at

#: Le nombre de valeurs d'ATR moyennées pour situer la volatilité courante. 100 est le défaut
#: de `regime.atr_ratio` : le reprendre ici évite qu'un manifeste hérite d'une autre fenêtre
#: que celle sous laquelle les régimes du dépôt ont été décrits.
DEFAULT_VOLATILITY_LOOKBACK = 100


class EntryVerdict(StrEnum):
    """Ce qu'un filtre d'entrée peut dire : passer, ou refuser. Rien d'autre."""

    # Le nom `PASS` n'est pas un mot de passe : S105 lit une valeur courte en majuscules.
    PASS = "pass"  # noqa: S105
    REFUSED = "refused"


class FilterReason(StrEnum):
    """Pourquoi une entrée est passée ou refusée. Chaque refus est nommé, donc persistable."""

    NONE = "none"  # aucune règle ne s'applique : le manifeste n'a rien configuré
    MEASURED = "measured"  # la mesure a été faite et la règle est satisfaite
    UNMEASURED = "unmeasured"  # la mesure manque : on laisse passer (fail-open)
    SESSION = "session"
    VOLATILITY = "volatility"


@dataclass(frozen=True, slots=True)
class EntryDecision:
    """Le verdict, son motif, et la phrase lisible qui les accompagne.

    Pas de candidat, pas de taille, pas de stop : une entrée refusée meurt ici, et rien ne
    peut la remplacer.
    """

    verdict: EntryVerdict
    reason: FilterReason
    detail: str = ""

    @property
    def passed(self) -> bool:
        return self.verdict is EntryVerdict.PASS


#: Aucune règle configurée : le filtre est inerte et ne change rien.
_INERT = EntryDecision(EntryVerdict.PASS, FilterReason.NONE, "")
#: La mesure a été faite et la règle est satisfaite.
_ALLOWED_BY_MEASUREMENT = EntryDecision(EntryVerdict.PASS, FilterReason.MEASURED, "")
#: La mesure manque : on laisse passer, et la phrase le dit.
_FAIL_OPEN = EntryDecision(
    EntryVerdict.PASS,
    FilterReason.UNMEASURED,
    "entry filter: volatility not measurable on this history, entry allowed (fail-open)",
)


@dataclass(frozen=True, slots=True)
class SessionFilter:
    """Les séances UTC autorisées, nommées par le manifeste.

    Les fenêtres elles-mêmes ne sont pas configurables : elles viennent de
    `session.DEFAULT_WINDOWS`, seule définition des séances du dépôt. Le manifeste choisit
    **lesquelles** il garde, jamais où elles commencent — deux fichiers ne peuvent donc pas
    décrire deux « Londres » différentes.
    """

    allowed: tuple[Session, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "allowed", tuple(self.allowed))
        if not self.allowed:
            raise ValueError("session_filter.allowed must name at least one session")
        if len(set(self.allowed)) != len(self.allowed):
            raise ValueError("session_filter.allowed must not repeat a session")

    def allows(self, moment: datetime) -> bool:
        return session_at(moment) in self.allowed


@dataclass(frozen=True, slots=True)
class VolatilityFilter:
    """Les régimes de volatilité autorisés, en rapport ATR courant / moyenne de l'ATR.

    `calm_ratio` et `volatile_ratio` viennent de `regime` par défaut, et un manifeste peut les
    remplacer : c'est ce qui permet d'écrire un seuil dans un fichier versionné plutôt que dans
    le code. `lookback` est le nombre de valeurs d'ATR moyennées.
    """

    allowed: tuple[Volatility, ...]
    lookback: int = DEFAULT_VOLATILITY_LOOKBACK
    calm_ratio: float = DEFAULT_CALM_RATIO
    volatile_ratio: float = DEFAULT_VOLATILE_RATIO

    def __post_init__(self) -> None:
        object.__setattr__(self, "allowed", tuple(self.allowed))
        if not self.allowed:
            raise ValueError("volatility_filter.allowed must name at least one regime")
        if len(set(self.allowed)) != len(self.allowed):
            raise ValueError("volatility_filter.allowed must not repeat a regime")
        if self.lookback < 1:
            raise ValueError(f"volatility_filter.lookback must be >= 1, got {self.lookback}")
        if self.calm_ratio < 0:
            raise ValueError(
                f"volatility_filter.calm_ratio must not be negative, got {self.calm_ratio}"
            )
        if self.calm_ratio > self.volatile_ratio:
            raise ValueError(
                f"volatility_filter.calm_ratio ({self.calm_ratio}) must not exceed "
                f"volatile_ratio ({self.volatile_ratio})"
            )

    def regime(self, ratio: float | None) -> Volatility | None:
        """Le régime d'un rapport, ou `None` quand il n'y a rien à classer."""
        if ratio is None:
            return None
        if ratio < self.calm_ratio:
            return Volatility.CALM
        if ratio > self.volatile_ratio:
            return Volatility.VOLATILE
        return Volatility.NORMAL


@dataclass(frozen=True)
class EntryFilter:
    """Le filtre compilé d'un manifeste : zéro, une ou deux règles.

    Un `EntryFilter` sans règle est un filtre inactif qui laisse tout passer, et c'est l'état
    de tout manifeste qui ne déclare ni `session_filter` ni `volatility_filter`.
    """

    session: SessionFilter | None = None
    volatility: VolatilityFilter | None = None
    atr_period: int = 14

    def __post_init__(self) -> None:
        if self.atr_period < 1:
            raise ValueError(f"atr_period must be >= 1, got {self.atr_period}")

    @property
    def active(self) -> bool:
        """Vrai dès qu'une règle existe : c'est ce qu'un rapport doit pouvoir afficher."""
        return self.session is not None or self.volatility is not None

    def decide(self, *, time: datetime, candles: Sequence[Candle]) -> EntryDecision:
        """L'accord ou le refus, à partir de l'instant de décision et des barres connues.

        L'instant est celui de la **décision** (la clôture de la bougie déclenchante), pas
        celui du remplissage : c'est l'information dont la stratégie disposait, et un filtre
        qui lirait une heure postérieure déciderait avec du futur.

        L'ordre des contrôles porte une décision : la séance est vérifiée avant la volatilité,
        et un refus de séance est rendu tel quel même quand la volatilité est inconnue. Sur un
        marché fermé, « on ne sait pas » ne doit pas devenir un accord.
        """
        if not self.active:
            return _INERT

        if self.session is not None and not self.session.allows(time):
            current = session_at(time)
            allowed = ", ".join(item.value for item in self.session.allowed)
            return EntryDecision(
                EntryVerdict.REFUSED,
                FilterReason.SESSION,
                f"entry filter: session {current} is not in the allowed set ({allowed})",
            )

        if self.volatility is not None:
            ratio = self._ratio(candles)
            regime = self.volatility.regime(ratio)
            if regime is None:
                return _FAIL_OPEN
            if regime not in self.volatility.allowed:
                allowed = ", ".join(item.value for item in self.volatility.allowed)
                return EntryDecision(
                    EntryVerdict.REFUSED,
                    FilterReason.VOLATILITY,
                    f"entry filter: volatility {regime} (ATR ratio {ratio:.2f}) is not in "
                    f"the allowed set ({allowed})",
                )
            return _ALLOWED_BY_MEASUREMENT

        return _ALLOWED_BY_MEASUREMENT

    def _ratio(self, candles: Sequence[Candle]) -> float | None:
        """L'ATR courant rapporté à sa moyenne, ou `None` si l'histoire est trop courte."""
        if not candles or self.volatility is None:
            return None
        return atr_ratio(
            [candle.high for candle in candles],
            [candle.low for candle in candles],
            [candle.close for candle in candles],
            period=self.volatility.lookback,
        )
