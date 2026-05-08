"""Trend Following strategy implementation.

Uses dual moving average trend detection with ATR (Average True Range) filter
for trend strength confirmation. Only generates signals when the trend is
confirmed by sufficient ATR-based volatility.
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

import structlog

from src.data.bar_builder import Bar, Timeframe
from src.strategies.base import BaseStrategy
from src.strategies.signals import OrderType, Signal, SignalDirection

if TYPE_CHECKING:
    from src.config.settings import StrategyConfig
    from src.data.market_data_hub import MarketDataHub

logger = structlog.get_logger(__name__)


class TrendFollowingStrategy(BaseStrategy):
    """Trend Following trading strategy with ATR filter.

    Uses dual moving averages to detect trend direction and ATR to confirm
    trend strength. Only generates signals when the ATR ratio exceeds the
    configured filter threshold.

    Parameters (from config.parameters):
        fast_ma: Period for the fast moving average.
        slow_ma: Period for the slow moving average.
        atr_filter: Minimum ATR ratio (ATR/price) to confirm trend strength.
    """

    def __init__(self, config: StrategyConfig, data_hub: MarketDataHub) -> None:
        super().__init__(config, data_hub)
        self._fast_ma: int = int(config.parameters.get("fast_ma", 10))
        self._slow_ma: int = int(config.parameters.get("slow_ma", 30))
        self._atr_filter: float = float(config.parameters.get("atr_filter", 0.01))

    @property
    def fast_ma(self) -> int:
        return self._fast_ma

    @property
    def slow_ma(self) -> int:
        return self._slow_ma

    @property
    def atr_filter(self) -> float:
        return self._atr_filter

    def update_parameters(self, parameters: dict) -> None:
        """Update trend following parameters at runtime for hot-reload support."""
        super().update_parameters(parameters)
        self._fast_ma = int(parameters.get("fast_ma", self._fast_ma))
        self._slow_ma = int(parameters.get("slow_ma", self._slow_ma))
        self._atr_filter = float(parameters.get("atr_filter", self._atr_filter))

    def required_indicators(self) -> list[str]:
        """Trend following requires two MAs and ATR."""
        return [f"SMA_{self._fast_ma}", f"SMA_{self._slow_ma}", "ATR_14"]

    async def evaluate(self) -> list[Signal]:
        """Evaluate trend following signals for all configured symbols.

        Returns:
            List of signals for symbols with confirmed trend.
        """
        signals: list[Signal] = []
        timeframe = self._resolve_timeframe()
        # Need enough bars for slow MA + ATR calculation
        required_bars = self._slow_ma + 14  # ATR typically uses 14 periods

        for symbol in self._config.symbols:
            bars = self._data_hub.get_history(symbol, timeframe, required_bars)

            if len(bars) < required_bars:
                logger.debug(
                    "insufficient_bars_for_trend_following",
                    symbol=symbol,
                    required=required_bars,
                    available=len(bars),
                )
                continue

            closes = [bar.close for bar in bars]
            current_price = closes[-1]

            # Calculate moving averages
            fast_ma_value = sum(closes[-self._fast_ma:]) / self._fast_ma
            slow_ma_value = sum(closes[-self._slow_ma:]) / self._slow_ma

            # Calculate ATR
            atr = self._calculate_atr(bars, period=14)
            if atr is None or current_price == 0:
                continue

            atr_ratio = atr / current_price

            # Check if ATR filter is met (trend is strong enough)
            if atr_ratio < self._atr_filter:
                logger.debug(
                    "atr_filter_not_met",
                    symbol=symbol,
                    atr_ratio=round(atr_ratio, 4),
                    threshold=self._atr_filter,
                )
                continue

            # Determine trend direction
            if fast_ma_value > slow_ma_value:
                confidence = min(1.0, atr_ratio / (self._atr_filter * 2))
                signals.append(
                    Signal(
                        strategy_name=self.name,
                        symbol=symbol,
                        direction=SignalDirection.LONG,
                        confidence=confidence,
                        suggested_size=Decimal("0"),
                        order_type=OrderType.MARKET,
                        metadata={
                            "fast_ma": fast_ma_value,
                            "slow_ma": slow_ma_value,
                            "atr": atr,
                            "atr_ratio": atr_ratio,
                        },
                    )
                )
                logger.info(
                    "trend_following_long_signal",
                    symbol=symbol,
                    fast_ma=round(fast_ma_value, 4),
                    slow_ma=round(slow_ma_value, 4),
                    atr_ratio=round(atr_ratio, 4),
                )
            elif fast_ma_value < slow_ma_value:
                confidence = min(1.0, atr_ratio / (self._atr_filter * 2))
                signals.append(
                    Signal(
                        strategy_name=self.name,
                        symbol=symbol,
                        direction=SignalDirection.SHORT,
                        confidence=confidence,
                        suggested_size=Decimal("0"),
                        order_type=OrderType.MARKET,
                        metadata={
                            "fast_ma": fast_ma_value,
                            "slow_ma": slow_ma_value,
                            "atr": atr,
                            "atr_ratio": atr_ratio,
                        },
                    )
                )
                logger.info(
                    "trend_following_short_signal",
                    symbol=symbol,
                    fast_ma=round(fast_ma_value, 4),
                    slow_ma=round(slow_ma_value, 4),
                    atr_ratio=round(atr_ratio, 4),
                )

        return signals

    def _calculate_atr(self, bars: list[Bar], period: int = 14) -> float | None:
        """Calculate Average True Range over the given period.

        True Range = max(high - low, |high - prev_close|, |low - prev_close|)
        ATR = SMA of True Range over period.

        Args:
            bars: List of OHLCV bars.
            period: Number of periods for ATR calculation.

        Returns:
            ATR value, or None if insufficient data.
        """
        if len(bars) < period + 1:
            return None

        true_ranges: list[float] = []
        for i in range(1, len(bars)):
            high = bars[i].high
            low = bars[i].low
            prev_close = bars[i - 1].close

            tr = max(
                high - low,
                abs(high - prev_close),
                abs(low - prev_close),
            )
            true_ranges.append(tr)

        if len(true_ranges) < period:
            return None

        # Use the last `period` true ranges
        return sum(true_ranges[-period:]) / period

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
