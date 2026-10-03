from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from tradingagent.core.market import Direction
from tradingagent.core.signal import SignalCandidate
from tradingagent.indicators.moving_average import ema
from tradingagent.indicators.volatility import atr
from tradingagent.strategies.base import Strategy, StrategyContext


class WitnessParameters(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    ema_fast: int = Field(ge=2)
    ema_slow: int = Field(ge=3)
    atr_period: int = Field(ge=1)
    stop_atr_multiplier: float = Field(gt=0)
    take_profit_rr: float = Field(gt=0)
    entry_zone_atr: float = Field(ge=0)

    @model_validator(mode="after")
    def _coherent(self) -> Self:
        if self.ema_slow <= self.ema_fast:
            raise ValueError("ema_slow must be longer than ema_fast")
        if self.entry_zone_atr >= self.stop_atr_multiplier:
            raise ValueError("entry_zone_atr must be narrower than stop_atr_multiplier")
        if self.take_profit_rr * self.stop_atr_multiplier <= self.entry_zone_atr:
            raise ValueError("the take-profit must lie beyond the entry zone")
        return self


class Witness(Strategy[WitnessParameters]):
    """Reference strategy proving the mechanics end to end. Not meant to be traded:
    its manifest is capped at SIGNAL.

    Buys when the fast EMA crosses above the slow one on the last closed candle, sells on
    the opposite cross. Stop and target are ATR multiples measured from the close.
    """

    strategy_id = "witness"
    parameters_model = WitnessParameters

    def evaluate(self, context: StrategyContext) -> SignalCandidate | None:
        parameters = self.parameters
        timeframe = context.primary_timeframe
        closes = context.closes(timeframe)
        fast = ema(closes, parameters.ema_fast)
        slow = ema(closes, parameters.ema_slow)
        volatility = atr(
            context.highs(timeframe), context.lows(timeframe), closes, parameters.atr_period
        )[-1]
        previous_fast, previous_slow, fast_now, slow_now = fast[-2], slow[-2], fast[-1], slow[-1]
        if (
            volatility is None
            or volatility <= 0
            or previous_fast is None
            or previous_slow is None
            or fast_now is None
            or slow_now is None
        ):
            return None

        if previous_fast <= previous_slow and fast_now > slow_now:
            direction, side, sign = Direction.BUY, "above", 1
        elif previous_fast >= previous_slow and fast_now < slow_now:
            direction, side, sign = Direction.SELL, "below", -1
        else:
            return None

        close = closes[-1]
        risk = parameters.stop_atr_multiplier * volatility
        zone = parameters.entry_zone_atr * volatility
        return SignalCandidate(
            direction=direction,
            entry_low=close - zone,
            entry_high=close + zone,
            stop_loss=close - sign * risk,
            take_profits=(close + sign * parameters.take_profit_rr * risk,),
            reason=(
                f"EMA{parameters.ema_fast} crossed {side} EMA{parameters.ema_slow}; "
                f"stop {parameters.stop_atr_multiplier} x ATR{parameters.atr_period}, "
                f"target {parameters.take_profit_rr}R"
            ),
            indicators={"ema_fast": fast_now, "ema_slow": slow_now, "atr": volatility},
        )
