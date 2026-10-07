from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from tradingagent.core.market import Direction
from tradingagent.core.signal import SignalCandidate
from tradingagent.indicators.moving_average import ema
from tradingagent.indicators.volatility import atr
from tradingagent.strategies.base import Strategy, StrategyContext


class TrendBreakoutParameters(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    channel_period: int = Field(ge=2)
    trend_period: int = Field(ge=3)
    atr_period: int = Field(ge=1)
    stop_atr_multiplier: float = Field(gt=0)
    take_profit_rr: float = Field(gt=0)
    entry_zone_atr: float = Field(ge=0)

    @model_validator(mode="after")
    def _coherent(self) -> Self:
        if self.entry_zone_atr >= self.stop_atr_multiplier:
            raise ValueError("entry_zone_atr must be narrower than stop_atr_multiplier")
        if self.take_profit_rr * self.stop_atr_multiplier <= self.entry_zone_atr:
            raise ValueError("the take-profit must lie beyond the entry zone")
        return self


class TrendBreakout(Strategy[TrendBreakoutParameters]):
    """Donchian breakout filtered by a long EMA. Not meant to be traded yet: its manifest
    is capped at SIGNAL until TASK-064/TASK-065 validate it out of sample.

    Buys when the last closed candle closes above the highest high of the previous
    `channel_period` candles and above the trend EMA; sells on the symmetric break below.
    The stop and the target are ATR multiples measured from the triggering close.
    """

    strategy_id = "trend_breakout"
    parameters_model = TrendBreakoutParameters

    def evaluate(self, context: StrategyContext) -> SignalCandidate | None:
        parameters = self.parameters
        timeframe = context.primary_timeframe
        closes = context.closes(timeframe)
        highs = context.highs(timeframe)
        lows = context.lows(timeframe)
        if len(closes) < parameters.channel_period + 1:
            return None
        # The channel excludes the triggering candle: using it would look ahead within the
        # very bar we are deciding on.
        channel_high = max(highs[-parameters.channel_period - 1 : -1])
        channel_low = min(lows[-parameters.channel_period - 1 : -1])
        trend = ema(closes, parameters.trend_period)[-1]
        volatility = atr(highs, lows, closes, parameters.atr_period)[-1]
        if trend is None or volatility is None or volatility <= 0:
            return None

        close = closes[-1]
        if close > channel_high and close > trend:
            direction, sign, side = Direction.BUY, 1, "above"
        elif close < channel_low and close < trend:
            direction, sign, side = Direction.SELL, -1, "below"
        else:
            return None

        risk = parameters.stop_atr_multiplier * volatility
        zone = parameters.entry_zone_atr * volatility
        return SignalCandidate(
            direction=direction,
            entry_low=close - zone,
            entry_high=close + zone,
            stop_loss=close - sign * risk,
            take_profits=(close + sign * parameters.take_profit_rr * risk,),
            reason=(
                f"close {side} the {parameters.channel_period}-bar channel and the "
                f"EMA{parameters.trend_period}; stop {parameters.stop_atr_multiplier} x "
                f"ATR{parameters.atr_period}, target {parameters.take_profit_rr}R"
            ),
            indicators={
                "channel_high": channel_high,
                "channel_low": channel_low,
                "trend_ema": trend,
                "atr": volatility,
            },
        )
