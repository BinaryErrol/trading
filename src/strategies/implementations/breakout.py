"""Breakout strategy implementation.

Detects consolidation ranges and generates signals when price breaks out
above or below the range by a configurable ATR multiple.
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


class BreakoutStrategy(BaseStrategy):
    """Breakout trading strategy.

    Detects consolidation ranges (high-low over N bars) and generates signals
    when the current price breaks above or below the range by an ATR multiple.

    Parameters (from config.parameters):
        consolidation_period: Number of bars to define the consolidation range.
        breakout_atr_multiple: ATR multiple required for breakout confirmation.
    """

    def __init__(self, config: StrategyConfig, data_hub: MarketDataHub) -> None:
        super().__init__(config, data_hub)
        self._consolidation_period: int = int(config.parameters.get("consolidation_period", 20))
        self._breakout_atr_multiple: float = float(
            config.parameters.get("breakout_atr_multiple", 1.5)
        )

    @property
    def consolidation_period(self) -> int:
        return self._consolidation_period

    @property
    def breakout_atr_multiple(self) -> float:
        return self._breakout_atr_multiple

    def update_parameters(self, parameters: dict) -> None:
        """Update breakout parameters at runtime for hot-reload support."""
        super().update_parameters(parameters)
        self._consolidation_period = int(
            parameters.get("consolidation_period", self._consolidation_period)
        )
        self._breakout_atr_multiple = float(
            parameters.get("breakout_atr_multiple", self._breakout_atr_multiple)
        )

    def required_indicators(self) -> list[str]:
        """Breakout strategy requires price range and ATR."""
        return [f"HIGH_{self._consolidation_period}", f"LOW_{self._consolidation_period}", "ATR_14"]

    async def evaluate(self) -> list[Signal]:
        """Evaluate breakout conditions for all configured symbols.

        Returns:
            List of signals for symbols where a breakout occurred.
        """
        signals: list[Signal] = []
        timeframe = self._resolve_timeframe()
        # Need consolidation period bars + extra for ATR
        required_bars = self._consolidation_period + 14 + 1

        for symbol in self._config.symbols:
            bars = self._data_hub.get_history(symbol, timeframe, required_bars)

            if len(bars) < required_bars:
                logger.debug(
                    "insufficient_bars_for_breakout",
                    symbol=symbol,
                    required=required_bars,
                    available=len(bars),
                )
                continue

            # Current bar is the latest
            current_bar = bars[-1]
            current_price = current_bar.close

            # Consolidation range: high/low over the N bars BEFORE the current bar
            consolidation_bars = bars[-(self._consolidation_period + 1):-1]
            range_high = max(bar.high for bar in consolidation_bars)
            range_low = min(bar.low for bar in consolidation_bars)

            # Calculate ATR
            atr = self._calculate_atr(bars, period=14)
            if atr is None or atr == 0:
                continue

            breakout_distance = atr * self._breakout_atr_multiple

            # Breakout above consolidation range
            if current_price > range_high + breakout_distance:
                excess = current_price - (range_high + breakout_distance)
                confidence = min(1.0, 0.5 + (excess / atr) * 0.25)
                signals.append(
                    Signal(
                        strategy_name=self.name,
                        symbol=symbol,
                        direction=SignalDirection.LONG,
                        confidence=confidence,
                        suggested_size=Decimal("0"),
                        order_type=OrderType.MARKET,
                        metadata={
                            "breakout_type": "upside",
                            "range_high": range_high,
                            "range_low": range_low,
                            "atr": atr,
                            "current_price": current_price,
                        },
                    )
                )
                logger.info(
                    "breakout_long_signal",
                    symbol=symbol,
                    current_price=round(current_price, 4),
                    range_high=round(range_high, 4),
                    breakout_distance=round(breakout_distance, 4),
                )

            # Breakout below consolidation range
            elif current_price < range_low - breakout_distance:
                excess = (range_low - breakout_distance) - current_price
                confidence = min(1.0, 0.5 + (excess / atr) * 0.25)
                signals.append(
                    Signal(
                        strategy_name=self.name,
                        symbol=symbol,
                        direction=SignalDirection.SHORT,
                        confidence=confidence,
                        suggested_size=Decimal("0"),
                        order_type=OrderType.MARKET,
                        metadata={
                            "breakout_type": "downside",
                            "range_high": range_high,
                            "range_low": range_low,
                            "atr": atr,
                            "current_price": current_price,
                        },
                    )
                )
                logger.info(
                    "breakout_short_signal",
                    symbol=symbol,
                    current_price=round(current_price, 4),
                    range_low=round(range_low, 4),
                    breakout_distance=round(breakout_distance, 4),
                )

        return signals

    def _calculate_atr(self, bars: list[Bar], period: int = 14) -> float | None:
        """Calculate Average True Range over the given period.

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
