"""Mean Reversion strategy implementation.

Calculates the z-score of the current price relative to the mean over a
lookback period and generates signals when price deviates significantly.
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


class MeanReversionStrategy(BaseStrategy):
    """Mean reversion trading strategy using z-score.

    Generates LONG signals when price is significantly below the mean
    (z-score < -threshold) and SHORT signals when price is significantly
    above the mean (z-score > threshold).

    z_score = (current_price - mean) / std_dev

    Parameters (from config.parameters):
        lookback_period: Number of bars for mean/std calculation.
        z_score_threshold: Absolute z-score threshold for signal generation.
    """

    def __init__(self, config: StrategyConfig, data_hub: MarketDataHub) -> None:
        super().__init__(config, data_hub)
        self._lookback_period: int = int(config.parameters.get("lookback_period", 20))
        self._z_score_threshold: float = float(config.parameters.get("z_score_threshold", 2.0))

    @property
    def lookback_period(self) -> int:
        return self._lookback_period

    @property
    def z_score_threshold(self) -> float:
        return self._z_score_threshold

    def update_parameters(self, parameters: dict) -> None:
        """Update mean reversion parameters at runtime for hot-reload support."""
        super().update_parameters(parameters)
        self._lookback_period = int(parameters.get("lookback_period", self._lookback_period))
        self._z_score_threshold = float(
            parameters.get("z_score_threshold", self._z_score_threshold)
        )

    def required_indicators(self) -> list[str]:
        """Mean reversion requires price history for z-score calculation."""
        return [f"ZSCORE_{self._lookback_period}"]

    async def evaluate(self) -> list[Signal]:
        """Evaluate z-score for all configured symbols.

        Returns:
            List of signals for symbols where z-score exceeds threshold.
        """
        signals: list[Signal] = []
        timeframe = self._resolve_timeframe()

        for symbol in self._config.symbols:
            bars = self._data_hub.get_history(symbol, timeframe, self._lookback_period)

            if len(bars) < self._lookback_period:
                logger.debug(
                    "insufficient_bars_for_mean_reversion",
                    symbol=symbol,
                    required=self._lookback_period,
                    available=len(bars),
                )
                continue

            closes = [bar.close for bar in bars]
            current_price = closes[-1]
            mean = sum(closes) / len(closes)
            variance = sum((c - mean) ** 2 for c in closes) / len(closes)
            std_dev = variance**0.5

            if std_dev == 0:
                continue

            z_score = (current_price - mean) / std_dev

            if z_score < -self._z_score_threshold:
                # Price is significantly below mean — expect reversion up
                confidence = min(1.0, abs(z_score) / (self._z_score_threshold * 2))
                signals.append(
                    Signal(
                        strategy_name=self.name,
                        symbol=symbol,
                        direction=SignalDirection.LONG,
                        confidence=confidence,
                        suggested_size=Decimal("0"),
                        order_type=OrderType.MARKET,
                        metadata={
                            "z_score": z_score,
                            "mean": mean,
                            "std_dev": std_dev,
                            "lookback": self._lookback_period,
                        },
                    )
                )
                logger.info(
                    "mean_reversion_long_signal",
                    symbol=symbol,
                    z_score=round(z_score, 4),
                    threshold=self._z_score_threshold,
                )
            elif z_score > self._z_score_threshold:
                # Price is significantly above mean — expect reversion down
                confidence = min(1.0, abs(z_score) / (self._z_score_threshold * 2))
                signals.append(
                    Signal(
                        strategy_name=self.name,
                        symbol=symbol,
                        direction=SignalDirection.SHORT,
                        confidence=confidence,
                        suggested_size=Decimal("0"),
                        order_type=OrderType.MARKET,
                        metadata={
                            "z_score": z_score,
                            "mean": mean,
                            "std_dev": std_dev,
                            "lookback": self._lookback_period,
                        },
                    )
                )
                logger.info(
                    "mean_reversion_short_signal",
                    symbol=symbol,
                    z_score=round(z_score, 4),
                    threshold=self._z_score_threshold,
                )

        return signals

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
