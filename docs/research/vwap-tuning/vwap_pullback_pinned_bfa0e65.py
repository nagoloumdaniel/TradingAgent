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
"""

from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from tradingagent.core.market import Direction
from tradingagent.core.signal import SignalCandidate
from tradingagent.indicators.moving_average import ema
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

        sign = 1 if direction is Direction.BUY else -1
        risk = parameters.stop_atr_multiplier * volatility
        zone = parameters.entry_zone_atr * volatility
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
                f"TP1 {parameters.first_target_rr}R puis TP2 {parameters.final_target_rr}R"
            ),
            indicators={
                "vwap": level,
                "reference": close,
                "ema_fast": fast[-1],
                "ema_slow": slow[-1],
                "slope_atr": slope,
                "atr": volatility,
                "distance_atr": distance / volatility,
            },
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
