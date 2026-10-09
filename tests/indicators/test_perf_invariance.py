"""L'invariance de l'axe C : ce que l'optimisation des indicateurs n'a pas le droit de changer.

**Pourquoi ce fichier existe.** Rendre `atr` et `vwap` plus rapides n'a de valeur que si les
nombres qu'ils rendent sont *exactement* les mêmes — pas « à peu près », pas « à 1e-12 près ».
Une différence d'un ulp sur un ATR déplace un stop, un stop déplace un remplissage, et un
remplissage de 20 000 opérations change le facteur de profit. Une optimisation qui change un
seul chiffre n'est pas une optimisation : c'est une autre stratégie.

**Comment il le prouve**, en trois étages qui ne dépendent pas l'un de l'autre :

1. **l'égalité bit à bit avec la version d'avant**, recopiée ici mot pour mot sous les noms
   `_reference_atr` et `_reference_vwap`. La comparaison se fait sur les octets IEEE-754
   (`struct.pack`), pas sur `pytest.approx` : `-0.0` et `0.0` sont égaux en Python, et ne le
   sont pas pour un moteur de backtest qui trie des niveaux ;
2. **les erreurs**, dont le type et le message sont comparés à la référence : index du premier
   élément non fini, longueurs désalignées, horodatage naïf, volume négatif, et l'**ordre** dans
   lequel ces refus tombent ;
3. **une exécution complète du harnais** sur un jeu synthétique déterministe et une stratégie
   locale : le *digest* de toutes les opérations, prix de sortie et excursions est figé. Il ne
   dépend ni de `config/strategies/`, ni des jeux gelés, ni de la stratégie `vwap_pullback` que
   d'autres axes modifient en ce moment — seulement de `atr`, `vwap`, du harnais et de ce
   fichier.

Le jeu synthétique est engendré par une graine fixe : deux exécutions de ce fichier voient
exactement les mêmes bougies, sur n'importe quelle machine.
"""

import hashlib
import math
import random
import struct
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from functools import lru_cache
from typing import Any, ClassVar, cast

import pytest
from pydantic import BaseModel, ConfigDict

from tradingagent.backtest.costs import CostModel
from tradingagent.backtest.harness import BacktestConfig, run_backtest
from tradingagent.core.market import Candle, Direction
from tradingagent.core.mode import TradingMode
from tradingagent.core.signal import SignalCandidate
from tradingagent.core.timeframe import Timeframe
from tradingagent.indicators._checks import require_finite
from tradingagent.indicators.moving_average import ema
from tradingagent.indicators.volatility import atr
from tradingagent.indicators.vwap import typical_price, vwap
from tradingagent.strategies.base import Strategy, StrategyContext
from tradingagent.strategies.manifest import StrategyManifest

SERIES = list[float | None]


# --------------------------------------------------------------------------------------
# La version d'avant, recopiée telle quelle. C'est elle la référence : elle n'appelle rien
# de ce que l'optimisation touche, sinon `require_finite` dans sa forme d'origine.
# --------------------------------------------------------------------------------------


def _reference_require_finite(values: Sequence[float]) -> None:
    for index, value in enumerate(values):
        if not math.isfinite(value):
            raise ValueError(f"value at index {index} is not finite: {value}")


def _reference_true_range(high: float, low: float, previous_close: float) -> float:
    return max(high - low, abs(high - previous_close), abs(low - previous_close))


def _reference_atr(
    highs: Sequence[float], lows: Sequence[float], closes: Sequence[float], period: int
) -> SERIES:
    if period < 1:
        raise ValueError(f"period must be >= 1, got {period}")
    if not len(highs) == len(lows) == len(closes):
        raise ValueError("highs, lows and closes must have the same length")
    for series in (highs, lows, closes):
        _reference_require_finite(series)
    result: SERIES = [None] * len(closes)
    if len(closes) <= period:
        return result
    ranges = [
        _reference_true_range(highs[index], lows[index], closes[index - 1])
        for index in range(1, len(closes))
    ]
    current = math.fsum(ranges[:period]) / period
    result[period] = current
    for index in range(period + 1, len(closes)):
        current = (current * (period - 1) + ranges[index - 1]) / period
        result[index] = current
    return result


def _reference_vwap(
    open_times: Sequence[datetime],
    highs: Sequence[float],
    lows: Sequence[float],
    closes: Sequence[float],
    volumes: Sequence[float | None],
    *,
    session_anchor_minutes: int = 0,
) -> SERIES:
    lengths = {len(item) for item in (open_times, highs, lows, closes, volumes)}
    if len(lengths) > 1:
        raise ValueError(f"every series must have the same length, got {sorted(lengths)}")
    for series in (highs, lows, closes):
        _reference_require_finite(series)
    for moment in open_times:
        if moment.utcoffset() is None:
            raise ValueError(f"open_times must be timezone-aware, got {moment!r}")
    for volume in volumes:
        if volume is None:
            continue
        if volume < 0:
            raise ValueError(f"a volume must not be negative, got {volume!r}")

    result: SERIES = []
    session: int | None = None
    volume_sum = 0.0
    weighted_sum = 0.0
    for moment, high, low, close, volume in zip(
        open_times, highs, lows, closes, volumes, strict=True
    ):
        seconds = int(moment.astimezone(UTC).timestamp()) - session_anchor_minutes * 60
        current = seconds // (24 * 60 * 60)
        if current != session:
            session = current
            volume_sum = 0.0
            weighted_sum = 0.0
        if volume is None:
            result.append(None)
            continue
        weighted_sum += _reference_typical_price(high, low, close) * volume
        volume_sum += volume
        result.append(None if volume_sum <= 0 else weighted_sum / volume_sum)
    return result


def _reference_typical_price(high: float, low: float, close: float) -> float:
    _reference_require_finite((high, low, close))
    return (high + low + close) / 3.0


# --------------------------------------------------------------------------------------
# Les outils de comparaison. Les octets, pas l'à-peu-près.
# --------------------------------------------------------------------------------------


def bits(value: float) -> bytes:
    """L'octet IEEE-754 : `-0.0` et `0.0` sont égaux en Python et différents ici."""
    return struct.pack(">d", value)


def assert_same_series(actual: SERIES, expected: SERIES) -> None:
    assert len(actual) == len(expected)
    for index, (left, right) in enumerate(zip(actual, expected, strict=True)):
        if left is None or right is None:
            assert left is None and right is None, f"index {index}: {left!r} != {right!r}"
            continue
        assert bits(left) == bits(right), f"index {index}: {left!r} != {right!r} (bit à bit)"


def assert_same_failure(call: Any, reference: Any) -> None:
    """Le même type d'erreur, avec le même message : un refus qui bouge est un bug."""
    with pytest.raises(Exception) as actual_error:
        call()
    with pytest.raises(Exception) as reference_error:
        reference()
    assert type(actual_error.value) is type(reference_error.value)
    assert str(actual_error.value) == str(reference_error.value)


def assert_same_outcome(call: Any, reference: Any) -> None:
    """Soit les deux refusent identiquement, soit les deux passent — jamais l'un sans l'autre."""
    actual_failure: Exception | None = None
    reference_failure: Exception | None = None
    try:
        call()
    except Exception as error:
        actual_failure = error
    try:
        reference()
    except Exception as error:
        reference_failure = error
    assert (actual_failure is None) == (reference_failure is None)
    if actual_failure is not None and reference_failure is not None:
        assert type(actual_failure) is type(reference_failure)
        assert str(actual_failure) == str(reference_failure)


def walk(
    count: int, seed: int, *, start: float = 30_000.0, amplitude: float = 40.0
) -> tuple[list[float], list[float], list[float], list[float]]:
    """Des bougies OHLC cohérentes, déterministes, avec des mèches et des gaps.

    Un mouvement brownien de pas croissant : les vrais extrêmes locaux apparaissent, et le
    VWAP change de côté, ce qui fait vivre les deux branches de la stratégie.
    """
    generator = random.Random(seed)
    opens: list[float] = []
    highs: list[float] = []
    lows: list[float] = []
    closes: list[float] = []
    price = start
    for index in range(count):
        body = price + generator.uniform(-amplitude, amplitude) * (1 + index % 7) / 4
        wick = abs(generator.gauss(0, amplitude)) + 0.5
        high = max(price, body) + wick
        low = min(price, body) - wick
        opens.append(price)
        closes.append(body)
        highs.append(high)
        lows.append(low)
        price = body
    return opens, highs, lows, closes


def minutes(count: int, start: datetime, step: int = 15) -> list[datetime]:
    return [start + timedelta(minutes=step * index) for index in range(count)]


def volumes_for(count: int, seed: int, *, zeroes: bool = True) -> list[float | None]:
    generator = random.Random(seed)
    values: list[float | None] = []
    for index in range(count):
        if zeroes and index % 13 == 5:
            values.append(0.0)
        else:
            values.append(round(generator.uniform(0.5, 500.0), 3))
    return values


# --------------------------------------------------------------------------------------
# 1. `require_finite` : le contrat de sûreté, inchangé
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("size", [0, 1, 3, 400, 1_200])
def test_require_finite_accepts_every_finite_value(size: int) -> None:
    require_finite(tuple(float(index) for index in range(size)))
    require_finite([float(index) for index in range(size)])


def test_require_finite_accepts_ints_and_other_finite_numbers() -> None:
    """Les séries d'entiers passent : le contrat porte sur la finitude, pas sur le type.

    Le `Decimal` est testé par un `cast` : il est accepté à l'exécution — `math.isfinite` le
    convertit — mais la signature déclare des flottants, et c'est bien elle que vérifie mypy.
    Le test prouve le comportement, le cast dit que le type n'est pas celui annoncé.
    """
    require_finite([1, 2, 3])
    require_finite(cast("list[float]", [Decimal("1.5"), Decimal("2")]))
    require_finite([True, False])


@pytest.mark.parametrize("bad", [math.nan, math.inf, -math.inf])
@pytest.mark.parametrize("position", [0, 1, 7])
def test_require_finite_names_the_first_offending_index(bad: float, position: int) -> None:
    values = [1.0] * position + [bad, 2.0, 3.0]

    with pytest.raises(ValueError) as error:
        require_finite(values)

    assert str(error.value) == f"value at index {position} is not finite: {bad}"


def test_require_finite_reports_the_first_of_several_offending_values() -> None:
    with pytest.raises(ValueError) as error:
        require_finite([1.0, math.nan, math.inf])

    assert str(error.value) == f"value at index 1 is not finite: {math.nan}"


def test_require_finite_matches_the_reference_on_random_series() -> None:
    generator = random.Random(20261009)
    for _ in range(200):
        values = [float(generator.randint(-100, 100)) for _ in range(generator.randint(0, 30))]
        if generator.random() < 0.7:
            values.insert(
                generator.randrange(len(values) + 1), generator.choice([math.nan, math.inf])
            )
        assert_same_outcome(
            lambda bound=values: require_finite(bound),
            lambda bound=values: _reference_require_finite(bound),
        )


def test_require_finite_still_refuses_a_non_number() -> None:
    """Un `str` n'est pas un flottant : `math.isfinite` doit lever, pas rendre `True`."""
    assert_same_failure(
        lambda: require_finite([1.0, "2.0"]),  # type: ignore[list-item]
        lambda: _reference_require_finite([1.0, "2.0"]),  # type: ignore[list-item]
    )


# --------------------------------------------------------------------------------------
# 2. `atr` : bit à bit la version d'avant
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("count", [0, 1, 13, 14, 15, 16, 60, 400, 1_003])
@pytest.mark.parametrize("period", [1, 2, 3, 14, 20])
def test_atr_is_bit_for_bit_the_reference(count: int, period: int) -> None:
    _opens, highs, lows, closes = walk(count, seed=count * 100 + period)
    assert_same_series(
        atr(highs, lows, closes, period), _reference_atr(highs, lows, closes, period)
    )


def test_atr_is_bit_for_bit_the_reference_on_integers_and_extremes() -> None:
    """Des prix entiers, un marché plat, un gap énorme : trois cas qui font diverger un calcul."""
    flat = [42] * 50
    assert_same_series(atr(flat, flat, flat, 5), _reference_atr(flat, flat, flat, 5))
    huge = [1e300, 1e300, 1e300, 1e300, 1e300]
    small = [1e-300] * 5
    assert_same_series(
        atr([value * 2 for value in huge], small * 0 + [0.0] * 5, huge, 2),
        _reference_atr([value * 2 for value in huge], [0.0] * 5, huge, 2),
    )
    integers = [10, 11, 12, 16, 13, 13, 14, 15, 14, 12]
    assert_same_series(
        atr(integers, [value - 2 for value in integers], integers, 3),
        _reference_atr(integers, [value - 2 for value in integers], integers, 3),
    )


@pytest.mark.parametrize("bad", [math.nan, math.inf, -math.inf])
@pytest.mark.parametrize("series_index", [0, 1, 2])
def test_atr_refuses_the_same_inputs_with_the_same_message(bad: float, series_index: int) -> None:
    _, highs, lows, closes = walk(30, seed=7)
    for position, series in enumerate((highs, lows, closes)):
        if position == series_index:
            series[9] = bad

    assert_same_failure(
        lambda: atr(highs, lows, closes, 5), lambda: _reference_atr(highs, lows, closes, 5)
    )


def test_atr_keeps_the_order_of_its_refusals() -> None:
    """Trois séries invalides : c'est `highs` qui parle, et à son premier index fautif."""
    _, highs, lows, closes = walk(20, seed=11)
    highs[3] = math.nan
    lows[1] = math.inf
    closes[0] = math.nan

    assert_same_failure(
        lambda: atr(highs, lows, closes, 5), lambda: _reference_atr(highs, lows, closes, 5)
    )


def test_atr_keeps_its_length_and_period_refusals() -> None:
    assert_same_failure(
        lambda: atr([1.0], [1.0], [1.0], 0), lambda: _reference_atr([1.0], [1.0], [1.0], 0)
    )
    assert_same_failure(
        lambda: atr([1.0, 2.0], [1.0, 2.0], [1.0], 2),
        lambda: _reference_atr([1.0, 2.0], [1.0, 2.0], [1.0], 2),
    )


# --------------------------------------------------------------------------------------
# 3. `vwap` : bit à bit la version d'avant, quelle que soit l'écriture de l'instant
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("count", [0, 1, 20, 96, 400, 1_001])
@pytest.mark.parametrize("anchor", [0, 60, 11 * 60 + 2, 20 * 60])
def test_vwap_is_bit_for_bit_the_reference(count: int, anchor: int) -> None:
    moments = minutes(count, datetime(2026, 9, 25, 3, 7, tzinfo=UTC))
    _, highs, lows, closes = walk(count, seed=count + anchor)
    volumes = volumes_for(count, seed=count * 3 + anchor)

    assert_same_series(
        vwap(moments, highs, lows, closes, volumes, session_anchor_minutes=anchor),
        _reference_vwap(moments, highs, lows, closes, volumes, session_anchor_minutes=anchor),
    )


def test_vwap_is_bit_for_bit_the_reference_with_missing_volumes() -> None:
    """`None` veut dire « non enregistré » : la session entière devient indéfinie."""
    count = 200
    moments = minutes(count, datetime(2026, 9, 25, 22, 0, tzinfo=UTC))
    _, highs, lows, closes = walk(count, seed=99)
    volumes: list[float | None] = volumes_for(count, seed=5)
    for index in (3, 90, 120, 199):
        volumes[index] = None

    assert_same_series(
        vwap(moments, highs, lows, closes, volumes),
        _reference_vwap(moments, highs, lows, closes, volumes),
    )


@pytest.mark.parametrize("offset_hours", [2, -5, 14, 0])
def test_vwap_does_not_depend_on_how_the_same_instants_are_written(offset_hours: int) -> None:
    """Un instant reste le même instant : 02:00+02:00 et 00:00Z sont la même session.

    C'est la propriété qui autorise `vwap` à lire l'instant directement au lieu de le
    reconvertir en UTC à chaque barre : `timestamp()` porte déjà l'instant absolu.
    """
    count = 200
    utc_moments = minutes(count, datetime(2026, 9, 25, 22, 30, tzinfo=UTC))
    _, highs, lows, closes = walk(count, seed=17)
    volumes = volumes_for(count, seed=23)
    shifted = [moment.astimezone(timezone(timedelta(hours=offset_hours))) for moment in utc_moments]

    assert_same_series(
        vwap(shifted, highs, lows, closes, volumes),
        vwap(utc_moments, highs, lows, closes, volumes),
    )


def test_the_session_index_reads_the_instant_not_the_wall_clock() -> None:
    """La preuve directe, sur des instants choisis : les deux lectures coïncident toujours."""
    days = [
        datetime(2026, 1, 1, tzinfo=UTC) + timedelta(hours=offset) for offset in range(0, 400, 7)
    ]
    offsets = [0, 1, -3, 5, 12, -11, 14]
    for moment in days:
        expected = int(moment.astimezone(UTC).timestamp())
        for hours in offsets:
            written = moment.astimezone(timezone(timedelta(hours=hours)))
            assert int(written.timestamp()) == expected


def test_vwap_refuses_the_same_inputs_with_the_same_message() -> None:
    moments = minutes(3, datetime(2026, 9, 25, tzinfo=UTC))
    highs, lows, closes = [101.0, 102.0, 103.0], [99.0, 98.0, 97.0], [100.0, 101.0, 102.0]
    volumes = [1.0, 2.0, 3.0]

    cases = [
        (moments, [1.0], lows, closes, volumes),
        (moments, highs, lows, closes, [1.0]),
        ([datetime(2026, 9, 25, 0, 0)], [101.0], [99.0], [100.0], [1.0]),  # noqa: DTZ001
        (moments, [math.nan, 102.0, 103.0], lows, closes, volumes),
        (moments, highs, [99.0, math.inf, 97.0], closes, volumes),
        (moments, highs, lows, [100.0, 101.0, -math.inf], volumes),
        (moments, highs, lows, closes, [-1.0, 2.0, 3.0]),
    ]
    for case in cases:
        assert_same_failure(lambda case=case: vwap(*case), lambda case=case: _reference_vwap(*case))


def test_vwap_keeps_the_order_of_its_refusals() -> None:
    """NaN dans `highs` **et** volume négatif : c'est la finitude qui parle, comme avant."""
    moments = minutes(3, datetime(2026, 9, 25, tzinfo=UTC))
    assert_same_failure(
        lambda: vwap(moments, [math.nan], [99.0], [100.0], [-1.0]),
        lambda: _reference_vwap(moments, [math.nan], [99.0], [100.0], [-1.0]),
    )


def test_typical_price_is_still_a_public_validated_function() -> None:
    """`typical_price` garde son contrat propre : l'optimisation de `vwap` ne le déshabille pas."""
    assert typical_price(12.0, 9.0, 10.5) == pytest.approx(10.5)
    with pytest.raises(ValueError, match="not finite"):
        typical_price(math.nan, 9.0, 10.5)
    with pytest.raises(ValueError, match="value at index 2"):
        typical_price(12.0, 9.0, math.inf)


# --------------------------------------------------------------------------------------
# 4. Le bout en bout : mêmes opérations, mêmes sorties, même facteur de profit
# --------------------------------------------------------------------------------------


class _ProbeParameters(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    vwap_period: int = 20
    atr_period: int = 14
    stop_atr_multiplier: float = 1.5
    first_target_rr: float = 0.8
    final_target_rr: float = 1.5
    #: Distance au VWAP lissé, en ATR, au-delà de laquelle la sonde se tait.
    pullback_atr: float = 0.4


class _VwapAtrProbe(Strategy[_ProbeParameters]):
    """Une règle locale, minimale, qui **dépend numériquement** de `vwap` et de `atr`.

    Elle n'est pas la stratégie de production : c'est une sonde. Son intérêt est que chaque
    niveau qu'elle publie (zone d'entrée, stop, objectifs) est calculé à partir de la valeur de
    l'ATR et du VWAP lissé. Un ulp de différence sur l'un des deux déplace un remplissage, donc
    change le digest — c'est exactement ce qu'un test d'invariance doit détecter.
    """

    strategy_id: ClassVar[str] = "axe_c_probe"
    parameters_model: ClassVar[type[BaseModel]] = _ProbeParameters

    def evaluate(self, context: StrategyContext) -> SignalCandidate | None:
        parameters = self.parameters
        candles = context.series(context.primary_timeframe)
        closes = context.closes(context.primary_timeframe)
        highs = context.highs(context.primary_timeframe)
        lows = context.lows(context.primary_timeframe)
        volumes = [float(candle.volume) for candle in candles if candle.volume is not None]
        if len(volumes) != len(candles):
            return None

        line = vwap([candle.open_time for candle in candles], highs, lows, closes, volumes)
        defined = [value for value in line if value is not None]
        if len(defined) < parameters.vwap_period:
            return None
        level = math.fsum(defined[-parameters.vwap_period :]) / parameters.vwap_period
        volatility = atr(highs, lows, closes, parameters.atr_period)[-1]
        if volatility is None or volatility <= 0:
            return None
        close = closes[-1]
        if abs(close - level) > parameters.pullback_atr * volatility:
            return None
        fast = ema(closes, 10)[-1]
        if fast is None:
            return None
        direction = Direction.BUY if fast >= level else Direction.SELL
        sign = 1 if direction is Direction.BUY else -1
        risk = parameters.stop_atr_multiplier * volatility
        zone = 0.1 * volatility
        return SignalCandidate(
            direction=direction,
            entry_low=close - zone,
            entry_high=close + zone,
            stop_loss=close - sign * risk,
            take_profits=(
                close + sign * parameters.first_target_rr * risk,
                close + sign * parameters.final_target_rr * risk,
            ),
            reason="sonde d'invariance axe C",
            indicators={"vwap": level, "atr": volatility},
        )


PROBE_MANIFEST = StrategyManifest(
    strategy_id="axe_c_probe",
    version="1.0.0",
    max_mode=TradingMode.SIGNAL,
    allowed_symbols=("BTCUSD",),
    timeframes=(Timeframe.M15,),
    history_bars=400,
    expiry_bars=2,
)


def probe_dataset(count: int = 2_400) -> list[Candle]:
    """Le jeu de la sonde : déterministe, avec du volume, des mèches et des sessions."""
    opens, highs, lows, closes = walk(count, seed=20261009, start=30_000.0, amplitude=60.0)
    volumes = volumes_for(count, seed=4242)
    moments = minutes(count, datetime(2026, 9, 1, 0, 0, tzinfo=UTC))
    return [
        Candle(
            timeframe=Timeframe.M15,
            open_time=moments[index],
            open=opens[index],
            high=highs[index],
            low=lows[index],
            close=closes[index],
            volume=volumes[index],
        )
        for index in range(count)
    ]


def probe_config() -> BacktestConfig:
    return BacktestConfig(
        symbol="BTCUSD",
        costs=CostModel(
            spread=1.5,
            slippage_fixed=0.6,
            commission_per_trade=Decimal("0.5"),
        ),
        mode=TradingMode.SIGNAL,
        max_concurrent_positions=1,
        partial_exit_fractions=(0.5, 0.5),
        move_stop_to_breakeven_after_first_target=True,
    )


def probe_digest(result: Any) -> str:
    """Tout ce qui est observable d'une exécution, sur une seule ligne hachée."""
    lines = [f"{key}={value!r}" for key, value in sorted(result.performance.__dict__.items())]
    for trade in result.trades:
        lines.append(
            "|".join(
                (
                    trade.opened_at.isoformat(),
                    trade.closed_at.isoformat(),
                    str(trade.direction),
                    repr(trade.pnl_eur),
                    repr(trade.mae_r),
                    repr(trade.mfe_r),
                    repr(trade.slippage),
                    ",".join(f"{key}={value!r}" for key, value in sorted(trade.features.items())),
                )
            )
        )
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


def test_the_probe_actually_trades() -> None:
    """Sans opération, le test d'invariance en bout de chaîne ne prouverait rien."""
    assert _probe_result().performance.trades >= 30


@lru_cache(maxsize=1)
def _probe_result() -> Any:
    """L'exécution de référence, calculée une fois : les deux tests lisent le même résultat."""
    return run_backtest(
        _VwapAtrProbe(_ProbeParameters()),
        PROBE_MANIFEST,
        {Timeframe.M15: probe_dataset()},
        probe_config(),
    )


#: Empreinte figée **avant** l'optimisation, sur le jeu et la sonde ci-dessus. Elle couvre le
#: nombre d'opérations, les prix de sortie, le PnL, les excursions et les features : si un seul
#: flottant change, elle change. Régénérer cette constante est un acte, pas une formalité.
#:
#: Ces deux condensés sont signalés par `detect-secrets` comme « Hex High Entropy String », et
#: c'est inévitable : un SHA-256 ressemble à une clé. Le `pragma: allowlist secret` habituel ne
#: marche pas ici, parce que le détecteur rapporte la ligne où le littéral hexadécimal **se
#: termine** : découpé, le pragma tombe sur la mauvaise ligne ; d'un seul tenant, il dépasse la
#: limite de 100 caractères. Le hook exclut donc ce fichier nommément, avec sa justification.
GOLDEN_PROBE_DIGEST = "281279991f638cdc4dd5a88030766807f84c4bf7d9e82cdf5d722ca1e800c6d5"
GOLDEN_INDICATOR_DIGEST = "d68c8dadbed4a5785cb08f608cb2c4b97c2a9d19d073f69ed500ca5bcc37b861"


def test_the_probe_backtest_digest_is_unchanged() -> None:
    assert probe_digest(_probe_result()) == GOLDEN_PROBE_DIGEST


def test_the_indicators_digest_on_the_probe_series_is_unchanged() -> None:
    """Le même contrôle, un étage plus bas : chaque flottant des deux séries, haché."""
    candles = probe_dataset()
    highs = [candle.high for candle in candles]
    lows = [candle.low for candle in candles]
    closes = [candle.close for candle in candles]
    volumes = [candle.volume for candle in candles]
    moments = [candle.open_time for candle in candles]
    payload = "\n".join(
        [
            *(_bits_line(value) for value in atr(highs, lows, closes, 14)),
            *(_bits_line(value) for value in vwap(moments, highs, lows, closes, volumes)),
        ]
    )

    assert hashlib.sha256(payload.encode("utf-8")).hexdigest() == GOLDEN_INDICATOR_DIGEST


def _bits_line(value: float | None) -> str:
    return "None" if value is None else bits(value).hex()
