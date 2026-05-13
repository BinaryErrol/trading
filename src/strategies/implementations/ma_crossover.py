"""Moving Average Crossover strategy implementation.

Generates signals based on golden cross (fast MA crosses above slow MA)
and death cross (fast MA crosses below slow MA) events.
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

import structlog

from src.data.bar_builder import Timeframe
from src.strategies.base import BaseStrategy
from src.strategies.signals import OrderType, Signal, SignalDirection

if TYPE_CHECKING:
    from src.config.settings import StrategyConfig
    from src.data.market_data_hub import MarketDataHub

logger = structlog.get_logger(__name__)


class MACrossoverStrategy(BaseStrategy):
    """Moving Average Crossover trading strategy.

    Generates LONG signals on golden cross (fast MA crosses above slow MA)
    and SHORT signals on death cross (fast MA crosses below slow MA).

    Supports both SMA (Simple Moving Average) and EMA (Exponential Moving Average).

    Parameters (from config.parameters):
        fast_period: Period for the fast moving average.
        slow_period: Period for the slow moving average.
        ma_type: Type of moving average ("sma" or "ema").
    """

    def __init__(self, config: StrategyConfig, data_hub: MarketDataHub) -> None:
        super().__init__(config, data_hub)
        self._fast_period: int = int(config.parameters.get("fast_period", 10))
        self._slow_period: int = int(config.parameters.get("slow_period", 30))
        self._ma_type: str = str(config.parameters.get("ma_type", "sma")).lower()

    @property
    def fast_period(self) -> int:
        return self._fast_period

    @property
    def slow_period(self) -> int:
        return self._slow_period

    @property
    def ma_type(self) -> str:
        return self._ma_type

    def update_parameters(self, parameters: dict) -> None:
        """Update MA crossover parameters at runtime for hot-reload support."""
        super().update_parameters(parameters)
        self._fast_period = int(parameters.get("fast_period", self._fast_period))
        self._slow_period = int(parameters.get("slow_period", self._slow_period))
        self._ma_type = str(parameters.get("ma_type", self._ma_type)).lower()

    def required_indicators(self) -> list[str]:
        """MA crossover requires two moving averages."""
        ma_prefix = self._ma_type.upper()
        return [f"{ma_prefix}_{self._fast_period}", f"{ma_prefix}_{self._slow_period}"]

    async def evaluate(self) -> list[Signal]:
        """Evaluate MA crossover for all configured symbols.

        Returns:
            List of signals for symbols where a crossover occurred.
        """
        signals: list[Signal] = []
        timeframe = self._resolve_timeframe()
        # Need slow_period + 1 bars to detect a crossover (current + previous)
        required_bars = self._slow_period + 1

        for symbol in self._config.symbols:
            bars = self._data_hub.get_history(symbol, timeframe, required_bars)

            if len(bars) < required_bars:
                logger.debug(
                    "insufficient_bars_for_ma_crossover",
                    symbol=symbol,
                    required=required_bars,
                    available=len(bars),
                )
                continue

            closes = [bar.close for bar in bars]

            # Calculate current MAs
            fast_ma_current = self._calculate_ma(closes, self._fast_period)
            slow_ma_current = self._calculate_ma(closes, self._slow_period)

            # Calculate previous MAs (excluding last bar)
            fast_ma_prev = self._calculate_ma(closes[:-1], self._fast_period)
            slow_ma_prev = self._calculate_ma(closes[:-1], self._slow_period)

            if any(
                v is None for v in [fast_ma_current, slow_ma_current, fast_ma_prev, slow_ma_prev]
            ):
                continue

            # Golden cross: fast was below slow, now fast is above slow
            if fast_ma_prev <= slow_ma_prev and fast_ma_current > slow_ma_current:
                spread = (fast_ma_current - slow_ma_current) / slow_ma_current
                confidence = min(1.0, spread * 20)
                signals.append(
                    Signal(
                        strategy_name=self.name,
                        symbol=symbol,
                        direction=SignalDirection.LONG,
                        confidence=confidence,
                        suggested_size=Decimal("0"),
                        order_type=OrderType.MARKET,
                        metadata={
                            "crossover": "golden",
                            "fast_ma": fast_ma_current,
                            "slow_ma": slow_ma_current,
                            "ma_type": self._ma_type,
                        },
                    )
                )
                logger.info(
                    "golden_cross_signal",
                    symbol=symbol,
                    fast_ma=round(fast_ma_current, 4),
                    slow_ma=round(slow_ma_current, 4),
                )

            # Death cross: fast was above slow, now fast is below slow
            elif fast_ma_prev >= slow_ma_prev and fast_ma_current < slow_ma_current:
                spread = (slow_ma_current - fast_ma_current) / slow_ma_current
                confidence = min(1.0, spread * 20)
                signals.append(
                    Signal(
                        strategy_name=self.name,
                        symbol=symbol,
                        direction=SignalDirection.SHORT,
                        confidence=confidence,
                        suggested_size=Decimal("0"),
                        order_type=OrderType.MARKET,
                        metadata={
                            "crossover": "death",
                            "fast_ma": fast_ma_current,
                            "slow_ma": slow_ma_current,
                            "ma_type": self._ma_type,
                        },
                    )
                )
                logger.info(
                    "death_cross_signal",
                    symbol=symbol,
                    fast_ma=round(fast_ma_current, 4),
                    slow_ma=round(slow_ma_current, 4),
                )

        return signals

    def _calculate_ma(self, closes: list[float], period: int) -> float | None:
        """Calculate moving average over the last N closes.

        Args:
            closes: List of closing prices.
            period: Number of periods for the MA.

        Returns:
            The moving average value, or None if insufficient data.
        """
        if len(closes) < period:
            return None

        if self._ma_type == "ema":
            return self._calculate_ema(closes, period)
        return self._calculate_sma(closes, period)

    def _calculate_sma(self, closes: list[float], period: int) -> float:
        """Calculate Simple Moving Average."""
        return sum(closes[-period:]) / period

    def _calculate_ema(self, closes: list[float], period: int) -> float:
        """Calculate Exponential Moving Average."""
        multiplier = 2.0 / (period + 1)
        # Start with SMA as the initial EMA value
        ema = sum(closes[:period]) / period
        for price in closes[period:]:
            ema = (price - ema) * multiplier + ema
        return ema

    def _resolve_timeframe(self) -> Timeframe:
        """Map the config frequency string to a Timeframe enum."""
        freq_map = {
            "tick": Timeframe.TICK,
            "1min": Timeframe.ONE_MIN,
            "5min": Timeframe.FIVE_MIN,
            "15min": Timeframe.FIFTEEN_MIN,
            "1hour": Timeframe.ONE_HOUR,
            "daily": Timeframe.DAILY,
            "weekly": Timeframe.WEEKLY,
        }
        return freq_map.get(self._config.frequency, Timeframe.FIVE_MIN)
