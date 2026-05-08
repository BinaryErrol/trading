"""Momentum strategy implementation.

Calculates price momentum over a lookback period and generates signals
when momentum exceeds a configurable threshold.
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


class MomentumStrategy(BaseStrategy):
    """Momentum-based trading strategy.

    Generates LONG signals when price momentum exceeds the positive threshold,
    and SHORT signals when momentum falls below the negative threshold.

    Momentum = (current_price - price_N_bars_ago) / price_N_bars_ago

    Parameters (from config.parameters):
        lookback_period: Number of bars to look back for momentum calculation.
        momentum_threshold: Absolute threshold for signal generation.
    """

    def __init__(self, config: StrategyConfig, data_hub: MarketDataHub) -> None:
        super().__init__(config, data_hub)
        self._lookback_period: int = int(config.parameters.get("lookback_period", 14))
        self._momentum_threshold: float = float(config.parameters.get("momentum_threshold", 0.02))

    @property
    def lookback_period(self) -> int:
        return self._lookback_period

    @property
    def momentum_threshold(self) -> float:
        return self._momentum_threshold

    def update_parameters(self, parameters: dict) -> None:
        """Update momentum parameters at runtime for hot-reload support."""
        super().update_parameters(parameters)
        self._lookback_period = int(parameters.get("lookback_period", self._lookback_period))
        self._momentum_threshold = float(
            parameters.get("momentum_threshold", self._momentum_threshold)
        )

    def required_indicators(self) -> list[str]:
        """Momentum strategy requires price history."""
        return [f"MOMENTUM_{self._lookback_period}"]

    async def evaluate(self) -> list[Signal]:
        """Evaluate momentum for all configured symbols.

        Returns:
            List of signals for symbols where momentum exceeds threshold.
        """
        signals: list[Signal] = []
        timeframe = self._resolve_timeframe()

        for symbol in self._config.symbols:
            bars = self._data_hub.get_history(symbol, timeframe, self._lookback_period + 1)

            if len(bars) < self._lookback_period + 1:
                logger.debug(
                    "insufficient_bars_for_momentum",
                    symbol=symbol,
                    required=self._lookback_period + 1,
                    available=len(bars),
                )
                continue

            current_price = bars[-1].close
            past_price = bars[-(self._lookback_period + 1)].close

            if past_price == 0:
                continue

            momentum = (current_price - past_price) / past_price

            if momentum > self._momentum_threshold:
                confidence = min(1.0, momentum / (self._momentum_threshold * 3))
                signals.append(
                    Signal(
                        strategy_name=self.name,
                        symbol=symbol,
                        direction=SignalDirection.LONG,
                        confidence=confidence,
                        suggested_size=Decimal("0"),
                        order_type=OrderType.MARKET,
                        metadata={"momentum": momentum, "lookback": self._lookback_period},
                    )
                )
                logger.info(
                    "momentum_long_signal",
                    symbol=symbol,
                    momentum=round(momentum, 4),
                    threshold=self._momentum_threshold,
                )
            elif momentum < -self._momentum_threshold:
                confidence = min(1.0, abs(momentum) / (self._momentum_threshold * 3))
                signals.append(
                    Signal(
                        strategy_name=self.name,
                        symbol=symbol,
                        direction=SignalDirection.SHORT,
                        confidence=confidence,
                        suggested_size=Decimal("0"),
                        order_type=OrderType.MARKET,
                        metadata={"momentum": momentum, "lookback": self._lookback_period},
                    )
                )
                logger.info(
                    "momentum_short_signal",
                    symbol=symbol,
                    momentum=round(momentum, 4),
                    threshold=self._momentum_threshold,
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
