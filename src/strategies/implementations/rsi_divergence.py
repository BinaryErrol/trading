"""RSI Divergence strategy implementation.

Generates signals based on RSI (Relative Strength Index) overbought/oversold
conditions.
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


class RSIDivergenceStrategy(BaseStrategy):
    """RSI-based trading strategy.

    Generates LONG signals when RSI drops below the oversold level
    and SHORT signals when RSI rises above the overbought level.

    RSI = 100 - (100 / (1 + avg_gain / avg_loss))

    Parameters (from config.parameters):
        rsi_period: Number of bars for RSI calculation.
        overbought: RSI level above which to generate SHORT signals.
        oversold: RSI level below which to generate LONG signals.
    """

    def __init__(self, config: StrategyConfig, data_hub: MarketDataHub) -> None:
        super().__init__(config, data_hub)
        self._rsi_period: int = int(config.parameters.get("rsi_period", 14))
        self._overbought: int = int(config.parameters.get("overbought", 70))
        self._oversold: int = int(config.parameters.get("oversold", 30))

    @property
    def rsi_period(self) -> int:
        return self._rsi_period

    @property
    def overbought(self) -> int:
        return self._overbought

    @property
    def oversold(self) -> int:
        return self._oversold

    def update_parameters(self, parameters: dict) -> None:
        """Update RSI parameters at runtime for hot-reload support."""
        super().update_parameters(parameters)
        self._rsi_period = int(parameters.get("rsi_period", self._rsi_period))
        self._overbought = int(parameters.get("overbought", self._overbought))
        self._oversold = int(parameters.get("oversold", self._oversold))

    def required_indicators(self) -> list[str]:
        """RSI strategy requires price history for RSI calculation."""
        return [f"RSI_{self._rsi_period}"]

    async def evaluate(self) -> list[Signal]:
        """Evaluate RSI for all configured symbols.

        Returns:
            List of signals for symbols where RSI is in overbought/oversold territory.
        """
        signals: list[Signal] = []
        timeframe = self._resolve_timeframe()
        # Need rsi_period + 1 bars to calculate price changes
        required_bars = self._rsi_period + 1

        for symbol in self._config.symbols:
            bars = self._data_hub.get_history(symbol, timeframe, required_bars)

            if len(bars) < required_bars:
                logger.debug(
                    "insufficient_bars_for_rsi",
                    symbol=symbol,
                    required=required_bars,
                    available=len(bars),
                )
                continue

            closes = [bar.close for bar in bars]
            rsi = self._calculate_rsi(closes)

            if rsi is None:
                continue

            if rsi < self._oversold:
                # Oversold — expect price to rise
                confidence = min(1.0, (self._oversold - rsi) / self._oversold)
                signals.append(
                    Signal(
                        strategy_name=self.name,
                        symbol=symbol,
                        direction=SignalDirection.LONG,
                        confidence=confidence,
                        suggested_size=Decimal("0"),
                        order_type=OrderType.MARKET,
                        metadata={
                            "rsi": rsi,
                            "oversold_level": self._oversold,
                            "rsi_period": self._rsi_period,
                        },
                    )
                )
                logger.info(
                    "rsi_oversold_signal",
                    symbol=symbol,
                    rsi=round(rsi, 2),
                    oversold=self._oversold,
                )
            elif rsi > self._overbought:
                # Overbought — expect price to fall
                confidence = min(1.0, (rsi - self._overbought) / (100 - self._overbought))
                signals.append(
                    Signal(
                        strategy_name=self.name,
                        symbol=symbol,
                        direction=SignalDirection.SHORT,
                        confidence=confidence,
                        suggested_size=Decimal("0"),
                        order_type=OrderType.MARKET,
                        metadata={
                            "rsi": rsi,
                            "overbought_level": self._overbought,
                            "rsi_period": self._rsi_period,
                        },
                    )
                )
                logger.info(
                    "rsi_overbought_signal",
                    symbol=symbol,
                    rsi=round(rsi, 2),
                    overbought=self._overbought,
                )

        return signals

    def _calculate_rsi(self, closes: list[float]) -> float | None:
        """Calculate RSI from a list of closing prices.

        Uses the standard RSI formula:
        RSI = 100 - (100 / (1 + avg_gain / avg_loss))

        Args:
            closes: List of closing prices (needs at least rsi_period + 1 values).

        Returns:
            RSI value between 0 and 100, or None if calculation not possible.
        """
        if len(closes) < self._rsi_period + 1:
            return None

        # Calculate price changes
        changes = [closes[i] - closes[i - 1] for i in range(1, len(closes))]

        gains = [max(0, c) for c in changes]
        losses = [max(0, -c) for c in changes]

        avg_gain = sum(gains[-self._rsi_period :]) / self._rsi_period
        avg_loss = sum(losses[-self._rsi_period :]) / self._rsi_period

        if avg_loss == 0:
            return 100.0 if avg_gain > 0 else 50.0

        rs = avg_gain / avg_loss
        rsi = 100.0 - (100.0 / (1.0 + rs))
        return rsi

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
