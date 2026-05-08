"""VWAP (Volume Weighted Average Price) strategy implementation.

Generates signals when price deviates significantly from the session VWAP.
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


class VWAPStrategy(BaseStrategy):
    """VWAP deviation trading strategy.

    Generates LONG signals when price is below VWAP by more than the
    deviation threshold, and SHORT signals when price is above VWAP
    by more than the threshold.

    VWAP = cumulative(price * volume) / cumulative(volume)

    Parameters (from config.parameters):
        deviation_threshold: Fractional deviation from VWAP for signal generation.
        session_type: Session type for VWAP calculation ("regular" or "full").
    """

    def __init__(self, config: StrategyConfig, data_hub: MarketDataHub) -> None:
        super().__init__(config, data_hub)
        self._deviation_threshold: float = float(
            config.parameters.get("deviation_threshold", 0.02)
        )
        self._session_type: str = str(config.parameters.get("session_type", "regular")).lower()

    @property
    def deviation_threshold(self) -> float:
        return self._deviation_threshold

    @property
    def session_type(self) -> str:
        return self._session_type

    def update_parameters(self, parameters: dict) -> None:
        """Update VWAP parameters at runtime for hot-reload support."""
        super().update_parameters(parameters)
        self._deviation_threshold = float(
            parameters.get("deviation_threshold", self._deviation_threshold)
        )
        self._session_type = str(parameters.get("session_type", self._session_type)).lower()

    def required_indicators(self) -> list[str]:
        """VWAP strategy requires volume-weighted price data."""
        return [f"VWAP_{self._session_type}"]

    async def evaluate(self) -> list[Signal]:
        """Evaluate VWAP deviation for all configured symbols.

        Returns:
            List of signals for symbols where price deviates from VWAP.
        """
        signals: list[Signal] = []
        timeframe = self._resolve_timeframe()
        # Use a reasonable session length for VWAP calculation
        session_bars = self._get_session_bars()

        for symbol in self._config.symbols:
            bars = self._data_hub.get_history(symbol, timeframe, session_bars)

            if len(bars) < 2:
                logger.debug(
                    "insufficient_bars_for_vwap",
                    symbol=symbol,
                    required=2,
                    available=len(bars),
                )
                continue

            # Calculate VWAP
            vwap = self._calculate_vwap(bars)

            if vwap is None or vwap == 0:
                continue

            current_price = bars[-1].close
            deviation = (current_price - vwap) / vwap

            if deviation < -self._deviation_threshold:
                # Price below VWAP — expect reversion up
                confidence = min(1.0, abs(deviation) / (self._deviation_threshold * 3))
                signals.append(
                    Signal(
                        strategy_name=self.name,
                        symbol=symbol,
                        direction=SignalDirection.LONG,
                        confidence=confidence,
                        suggested_size=Decimal("0"),
                        order_type=OrderType.MARKET,
                        metadata={
                            "vwap": vwap,
                            "current_price": current_price,
                            "deviation": deviation,
                            "session_type": self._session_type,
                        },
                    )
                )
                logger.info(
                    "vwap_long_signal",
                    symbol=symbol,
                    deviation=round(deviation, 4),
                    threshold=self._deviation_threshold,
                )
            elif deviation > self._deviation_threshold:
                # Price above VWAP — expect reversion down
                confidence = min(1.0, abs(deviation) / (self._deviation_threshold * 3))
                signals.append(
                    Signal(
                        strategy_name=self.name,
                        symbol=symbol,
                        direction=SignalDirection.SHORT,
                        confidence=confidence,
                        suggested_size=Decimal("0"),
                        order_type=OrderType.MARKET,
                        metadata={
                            "vwap": vwap,
                            "current_price": current_price,
                            "deviation": deviation,
                            "session_type": self._session_type,
                        },
                    )
                )
                logger.info(
                    "vwap_short_signal",
                    symbol=symbol,
                    deviation=round(deviation, 4),
                    threshold=self._deviation_threshold,
                )

        return signals

    def _calculate_vwap(self, bars: list) -> float | None:
        """Calculate VWAP from bar data.

        VWAP = sum(typical_price * volume) / sum(volume)
        where typical_price = (high + low + close) / 3

        Args:
            bars: List of Bar objects.

        Returns:
            VWAP value, or None if total volume is zero.
        """
        cumulative_tp_volume = 0.0
        cumulative_volume = 0.0

        for bar in bars:
            typical_price = (bar.high + bar.low + bar.close) / 3.0
            cumulative_tp_volume += typical_price * bar.volume
            cumulative_volume += bar.volume

        if cumulative_volume == 0:
            return None

        return cumulative_tp_volume / cumulative_volume

    def _get_session_bars(self) -> int:
        """Return the number of bars to use for session VWAP.

        Regular session (6.5 hours) at 5-min bars = 78 bars.
        Full session (including pre/post market) = ~120 bars.
        """
        if self._session_type == "full":
            return 120
        return 78

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
