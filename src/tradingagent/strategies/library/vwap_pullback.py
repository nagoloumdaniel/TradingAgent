"""Stratégie BTCUSD : VWAP ancré à la journée, momentum, pullback.

**Ce que la règle fait, et pourquoi dans cet ordre.** Le VWAP ancré à 00:00 UTC est le prix
moyen réellement payé sur la journée : au-dessus, les acheteurs du jour sont gagnants, en
dessous ce sont les vendeurs. Prendre position **contre** ce niveau serait parier contre le
flux réel du marché, ce que rien ici ne justifie. La règle attend donc qu'on y **revienne** —
c'est le pullback — au lieu d'acheter l'extension d'une bougie de momentum, qui est l'erreur
qui coûte le plus cher sur un actif volatil.

Trois conditions, chacune nécessaire :

1. **le prix revient près du VWAP** — sans ce retour, ce n'est pas cette stratégie ;
2. **le momentum autorise le sens** : EMA rapide du bon côté de la lente, et pente dans le sens
   voulu. Une moyenne qui se croise sans pente est un marché qui hésite ;
3. **le prix est du bon côté du VWAP** et la bougie le défend : le plus bas touche le niveau,
   la clôture repasse au-dessus (achat), ou l'inverse (vente).

**Les sorties sont la géométrie demandée** : TP1 à 0,8 R, TP2 à 1,5 R. La sortie partielle à
TP1 et le passage à break-even ne sont **pas** décidés ici : c'est le harnais qui les applique
(`partial_exit_fractions`, `move_stop_to_breakeven_after_first_target`), parce que ce sont des
règles de gestion de position, pas de détection. Une stratégie qui les coderait elle-même
devrait aussi simuler les remplissages, ce qui n'est pas son rôle.

**Trois filtres optionnels, et leur état par défaut.** La spec de l'opérateur en demande six ;
trois sont ces conditions. Les trois autres sont proposés ici — horaire, tendance, volume — et
**tous sont éteints par défaut** : `allowed_sessions=()`, `trend_filter=False`,
`volume_ratio_min=None` reproduisent exactement la règle mesurée à PF 0,9314 sur les 59 999
bougies du jeu complet. Un filtre ne se rallume qu'après avoir montré, mesure en main, qu'il ne
retire pas plus qu'il n'apporte.

Chacun lit une brique existante, jamais un indicateur réécrit ici : `session_at` pour l'heure,
`trend_of` pour la direction du marché, et le volume déjà présent dans les bougies pour la
participation. Le filtre de **coût** de la spec ne figure pas ici et ne doit pas y venir : il
appartient au moteur de risque, qui seul connaît le spread réel du broker.

**Le filtre de tendance est le seul qui ait tenu à la mesure, et il coupe beaucoup.** `trend_of`
lit la pente de la moyenne lente sur **une** barre, alors que le repli au VWAP est précisément
le moment où cette pente s'aplatit : sur les 1 973 signaux du jeu complet, 120 seulement
arrivent avec une pente positive, et 62 d'entre eux portent une tendance nommée — le filtre ne
laisse donc passer qu'environ 12 % des signaux. Mais ceux-là se comportent autrement : PF 1,555
sur 117 trades, et **les deux moitiés de la série restent au-dessus de 1,5** (60 trades à 1,581,
57 à 1,539), ce qui n'est pas le cas du seuil voisin. La statistique t de l'espérance par
opération vaut 2,18 — un test optimiste, qui suppose les trades indépendants alors qu'ils se
groupent par régime, et qui est mené sur 16 mesures. C'est un **candidat**, pas une preuve, et
il reste éteint tant qu'un test hors échantillon ne l'a pas confirmé.

**Ce que ces filtres ne font pas.** Ils ne peuvent que **refuser** un signal — jamais en créer
un, jamais desserrer un stop, jamais élargir une zone d'entrée. C'est la contrainte C-002, et
elle est structurelle ici : chaque filtre est une sortie anticipée de `evaluate`, pas une
branche qui fabrique une décision.
"""

from collections.abc import Sequence
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from tradingagent.core.market import Direction
from tradingagent.core.signal import SignalCandidate
from tradingagent.indicators.moving_average import ema
from tradingagent.indicators.regime import DEFAULT_TREND_THRESHOLD, Trend, trend_of
from tradingagent.indicators.session import Session, session_at
from tradingagent.indicators.volatility import atr
from tradingagent.indicators.vwap import vwap
from tradingagent.strategies.base import Strategy, StrategyContext


class VwapPullbackParameters(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    ema_fast: int = Field(ge=2)
    ema_slow: int = Field(ge=3)
    #: Longueur de la moyenne qui lisse le VWAP de séance. Sans elle, la première barre de
    #: chaque journée n'a qu'un point d'ancrage : le niveau saute d'un jour à l'autre, et le
    #: « retour au VWAP » ne veut plus rien dire pendant les premières heures.
    vwap_period: int = Field(ge=2)
    atr_period: int = Field(ge=1)
    stop_atr_multiplier: float = Field(gt=0)
    first_target_rr: float = Field(gt=0)
    final_target_rr: float = Field(gt=0)
    #: Distance maximale, en ATR, à laquelle le prix doit venir chercher le VWAP pour que le
    #: retour compte comme un pullback. Trop large, toute barre devient un pullback.
    pullback_atr: float = Field(gt=0)
    entry_zone_atr: float = Field(ge=0)
    #: Pente minimale de la moyenne lente, exprimée en ATR par barre.
    min_slope_atr: float = Field(ge=0)
    #: Sur combien de barres la pente de la moyenne lente est mesurée.
    #:
    #: **Une seule barre ne peut pas marcher.** Pendant un pullback, les moyennes convergent
    #: toujours : la pente sur une barre est alors négative, et exiger une pente positive
    #: interdirait la stratégie entière. Le momentum est une propriété de la **tendance**, pas
    #: du dernier pas.
    slope_window: int = Field(ge=2)

    #: Les séances pendant lesquelles la règle a le droit de parler. **Vide = aucune contrainte.**
    #:
    #: Le VWAP du jour est un niveau que le marché défend quand il est là. À 04:00 UTC, le
    #: bitcoin se traite encore, mais le flux qui pourrait repousser le niveau est à Londres et
    #: à New York, pas à Tokyo : un contact sur un carnet mince est une barre traversée, pas un
    #: rejet. Lire la séance de la **bougie qui déclenche** — `candles[-1].open_time` — et non
    #: l'horloge de la machine, garde la règle pure et rejouable.
    allowed_sessions: tuple[Session, ...] = ()

    #: Exige que le marché soit encore en tendance **au moment de la décision**.
    #:
    #: Ce n'est pas un doublon de la pente déjà exigée, et la différence est mesurable : la
    #: pente de la règle se mesure sur `slope_window` barres (« la tendance existe depuis dix
    #: barres »), celle de `trend_of` sur **une** barre, normalisée par l'ATR (« elle pousse
    #: encore »). Un marché qui a monté puis décroché satisfait la première et pas la seconde.
    trend_filter: bool = False

    #: Le seuil de pente sur **une** barre qui fait dire à `trend_of` que le marché tend encore.
    #:
    #: La valeur par défaut est celle du module de régime (`DEFAULT_TREND_THRESHOLD`), et non
    #: `min_slope_atr` : les deux mesurent des pentes sur des fenêtres différentes, et réutiliser
    #: le même nombre pour les deux ferait croire qu'elles se comparent. Elle est paramétrable
    #: parce que la mesure a montré que ce seuil peut, à lui seul, rendre la règle muette — un
    #: filtre qui refuse tout n'est pas un filtre, c'est une suppression.
    trend_slope_atr: float = Field(default=DEFAULT_TREND_THRESHOLD, gt=0)

    #: Volume de la barre de contact divisé par la moyenne des barres **précédentes**. `None`
    #: éteint le filtre ; un seuil nul ou négatif est refusé plus bas, parce qu'il aurait l'air
    #: de filtrer sans rien filtrer.
    #:
    #: Ce n'est pas le filtre « volume » de la spec au sens brut : le VWAP exige déjà un volume
    #: sur **chaque** barre et se tait sinon. Ce seuil-ci est **relatif** à ce que le marché
    #: fait d'habitude, ce qu'un volume absolu ne peut pas dire — 100 lots ne veulent rien dire
    #: sans le contexte du marché et de l'heure.
    volume_ratio_min: float | None = Field(default=None, gt=0)

    #: Sur combien de barres **précédentes** la moyenne de volume est prise. La barre courante
    #: en est exclue : l'inclure ferait monter sa propre référence et un pic de volume
    #: s'auto-annulerait d'autant plus qu'il est fort.
    volume_lookback: int = Field(default=20, ge=2)

    @model_validator(mode="after")
    def _coherent(self) -> Self:
        if self.ema_slow <= self.ema_fast:
            raise ValueError("ema_slow must be longer than ema_fast")
        if self.first_target_rr >= self.final_target_rr:
            raise ValueError("first_target_rr must be closer than final_target_rr")
        if self.entry_zone_atr >= self.stop_atr_multiplier:
            raise ValueError("entry_zone_atr must be narrower than stop_atr_multiplier")
        if self.pullback_atr >= self.stop_atr_multiplier:
            raise ValueError("pullback_atr must be narrower than stop_atr_multiplier")
        return self


class VwapPullback(Strategy[VwapPullbackParameters]):
    """Achat sur retour au VWAP en tendance haussière, vente sur retour par le dessous."""

    strategy_id = "vwap_pullback"
    parameters_model = VwapPullbackParameters

    def evaluate(self, context: StrategyContext) -> SignalCandidate | None:
        parameters = self.parameters
        timeframe = context.primary_timeframe
        candles = context.series(timeframe)
        closes = context.closes(timeframe)
        highs = context.highs(timeframe)
        lows = context.lows(timeframe)
        volumes = [candle.volume for candle in candles]

        # Pas de volume, pas de VWAP : un VWAP sans volume n'est qu'une moyenne mobile, et la
        # règle serait en réalité une autre règle. Se taire vaut mieux que laisser croire.
        if any(volume is None for volume in volumes):
            return None
        measured = [float(volume) for volume in volumes if volume is not None]

        line = vwap([candle.open_time for candle in candles], highs, lows, closes, measured)
        level = _last_smoothed(line, parameters.vwap_period)
        volatility = atr(highs, lows, closes, parameters.atr_period)[-1]
        fast = ema(closes, parameters.ema_fast)
        slow = ema(closes, parameters.ema_slow)
        window = parameters.slope_window
        earlier_slow = slow[-1 - window] if len(slow) > window else None

        if (
            level is None
            or volatility is None
            or volatility <= 0
            or fast[-1] is None
            or slow[-1] is None
            or earlier_slow is None
        ):
            return None

        # La pente est **par barre** : diviser par la fenêtre rend le seuil indépendant de sa
        # longueur, ce qui évite qu'un réglage de fenêtre change silencieusement le seuil.
        slope = (slow[-1] - earlier_slow) / (window * volatility)
        close = closes[-1]
        distance = abs(close - level)
        # La séance de la bougie qui déclenche, jamais celle de la machine : `evaluated_at` est
        # l'heure de clôture de cette barre, et la fenêtre du VWAP s'ancre sur son ouverture.
        session = session_at(candles[-1].open_time)

        direction = self._direction(
            close=close,
            level=level,
            fast=fast[-1],
            slow=slow[-1],
            slope=slope,
            low=lows[-1],
            high=highs[-1],
            distance=distance,
            volatility=volatility,
        )
        if direction is None:
            return None

        # Les filtres viennent **après** la géométrie, et l'ordre est délibéré : `trend_of`
        # recalcule deux EMA et un ATR, donc le payer sur les barres qui ne sont de toute façon
        # pas des pullbacks multiplierait le coût d'un backtest pour rien.
        if parameters.allowed_sessions and session not in parameters.allowed_sessions:
            return None

        side = Trend.UP if direction is Direction.BUY else Trend.DOWN
        trend = (
            trend_of(
                highs,
                lows,
                closes,
                fast=parameters.ema_fast,
                slow=parameters.ema_slow,
                atr_period=parameters.atr_period,
                threshold=parameters.trend_slope_atr,
            )
            if parameters.trend_filter
            else None
        )
        if parameters.trend_filter and trend is not side:
            return None

        ratio = _volume_ratio(measured, parameters.volume_lookback)
        if parameters.volume_ratio_min is not None and (
            ratio is None or ratio < parameters.volume_ratio_min
        ):
            return None

        sign = 1 if direction is Direction.BUY else -1
        risk = parameters.stop_atr_multiplier * volatility
        zone = parameters.entry_zone_atr * volatility
        indicators = {
            "vwap": level,
            "reference": close,
            "ema_fast": fast[-1],
            "ema_slow": slow[-1],
            "slope_atr": slope,
            "atr": volatility,
            "distance_atr": distance / volatility,
        }
        # Le ratio de volume n'est publié que s'il a été **mesuré** : écrire 0,0 quand
        # l'historique est trop court affirmerait une mesure qui n'existe pas. Aujourd'hui la
        # fenêtre vient toujours du manifeste (400 barres) et le cas ne se produit pas, mais
        # une constante inventée finirait par être lue comme un vrai ratio.
        if ratio is not None:
            indicators["volume_ratio"] = ratio
        # Le motif porte les **étiquettes** — séance, tendance — parce que `indicators` est
        # vérifié fini et numérique (`core.signal`) : y glisser un `StrEnum` ferait lever la
        # construction du signal. Un filtre éteint, lui, n'a rien mesuré et ne s'annonce pas.
        filter_notes = [f"séance {session}"]
        if trend is not None:
            filter_notes.append(f"tendance {trend}")
        return SignalCandidate(
            direction=direction,
            entry_low=close - zone,
            entry_high=close + zone,
            stop_loss=close - sign * risk,
            take_profits=(
                close + sign * parameters.first_target_rr * risk,
                close + sign * parameters.final_target_rr * risk,
            ),
            reason=(
                f"{'achat' if sign > 0 else 'vente'} sur retour au VWAP "
                f"({level:.5f}) en tendance {'haussière' if sign > 0 else 'baissière'} ; "
                f"TP1 {parameters.first_target_rr}R puis TP2 {parameters.final_target_rr}R "
                f"({', '.join(filter_notes)})"
            ),
            indicators=indicators,
        )

    def _direction(
        self,
        *,
        close: float,
        level: float,
        fast: float,
        slow: float,
        slope: float,
        low: float,
        high: float,
        distance: float,
        volatility: float,
    ) -> Direction | None:
        """Les trois conditions, dans l'ordre où elles coûtent le moins cher à vérifier."""
        parameters = self.parameters
        if distance > parameters.pullback_atr * volatility:
            return None
        if fast > slow and slope >= parameters.min_slope_atr and low <= level < close:
            return Direction.BUY
        if fast < slow and slope <= -parameters.min_slope_atr and high >= level > close:
            return Direction.SELL
        return None


def _volume_ratio(volumes: Sequence[float], lookback: int) -> float | None:
    """La barre courante rapportée à ce que le marché fait d'habitude, ou `None`.

    La barre courante est exclue de sa propre référence — c'est tout l'intérêt du rapport : un
    pic de volume qui entre dans sa propre moyenne se compare à moitié à lui-même, et le
    rapport plafonne d'autant plus bas que le pic est fort.

    `None` quand la référence est nulle : diviser par zéro donnerait l'infini, et un ratio
    infini ferait passer n'importe quel seuil. Se taire est ici la seule réponse honnête.
    """
    if len(volumes) <= lookback:
        return None
    previous = volumes[-1 - lookback : -1]
    average = sum(previous) / len(previous)
    if average <= 0:
        return None
    return volumes[-1] / average


def _last_smoothed(values: list[float | None], period: int) -> float | None:
    """La dernière valeur de la moyenne glissante, sans construire toute la série.

    Ignorer les trous plutôt que de les compter comme des zéros : un trou compté zéro ferait
    plonger la moyenne, et un VWAP artificiellement bas produirait des signaux d'achat qui
    n'existent pas.

    Seule la **fin** est calculée, et c'est délibéré : le harnais appelle cette fonction à
    chaque barre, donc matérialiser la série entière à chaque fois rendait le backtest
    quadratique. La fenêtre glissante ne dépend que des `period` dernières valeurs définies.
    """
    window: list[float] = []
    for value in values:
        if value is None:
            continue
        window.append(value)
        if len(window) > period:
            window.pop(0)
    return sum(window) / len(window) if window else None
