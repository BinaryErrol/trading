"""Bollinger Bands strategy implementation.

Generates signals when price touches or crosses the Bollinger Bands
(mean ± N standard deviations).
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


class BollingerStrategy(BaseStrategy):
    """Bollinger Bands trading strategy.

    Generates LONG signals when price touches the lower band (oversold)
    and SHORT signals when price touches the upper band (overbought).

    Upper Band = SMA + (bb_std * std_dev)
    Lower Band = SMA - (bb_std * std_dev)

    Parameters (from config.parameters):
        bb_period: Number of bars for the moving average and std calculation.
        bb_std: Number of standard deviations for band width.
        entry_band: Which band triggers signals ("lower", "upper", or "both").
    """

    def __init__(self, config: StrategyConfig, data_hub: MarketDataHub) -> None:
        super().__init__(config, data_hub)
        self._bb_period: int = int(config.parameters.get("bb_period", 20))
        self._bb_std: float = float(config.parameters.get("bb_std", 2.0))
        self._entry_band: str = str(config.parameters.get("entry_band", "both")).lower()

    @property
    def bb_period(self) -> int:
        return self._bb_period

    @property
    def bb_std(self) -> float:
        return self._bb_std

    @property
    def entry_band(self) -> str:
        return self._entry_band

    def update_parameters(self, parameters: dict) -> None:
        """Update Bollinger Band parameters at runtime for hot-reload support."""
        super().update_parameters(parameters)
        self._bb_period = int(parameters.get("bb_period", self._bb_period))
        self._bb_std = float(parameters.get("bb_std", self._bb_std))
        self._entry_band = str(parameters.get("entry_band", self._entry_band)).lower()

    def required_indicators(self) -> list[str]:
        """Bollinger strategy requires price history for band calculation."""
        return [f"BB_{self._bb_period}_{self._bb_std}"]

    async def evaluate(self) -> list[Signal]:
        """Evaluate Bollinger Band touches for all configured symbols.

        Returns:
            List of signals for symbols where price touches a band.
        """
        signals: list[Signal] = []
        timeframe = self._resolve_timeframe()

        for symbol in self._config.symbols:
            bars = self._data_hub.get_history(symbol, timeframe, self._bb_period)

            if len(bars) < self._bb_period:
                logger.debug(
                    "insufficient_bars_for_bollinger",
                    symbol=symbol,
                    required=self._bb_period,
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

            upper_band = mean + self._bb_std * std_dev
            lower_band = mean - self._bb_std * std_dev

            # LONG when price touches or goes below lower band
            if current_price <= lower_band and self._entry_band in ("lower", "both"):
                band_distance = (lower_band - current_price) / std_dev if std_dev > 0 else 0
                confidence = min(1.0, 0.5 + band_distance * 0.25)
                signals.append(
                    Signal(
                        strategy_name=self.name,
                        symbol=symbol,
                        direction=SignalDirection.LONG,
                        confidence=confidence,
                        suggested_size=Decimal("0"),
                        order_type=OrderType.MARKET,
                        metadata={
                            "band_touch": "lower",
                            "current_price": current_price,
                            "lower_band": lower_band,
                            "upper_band": upper_band,
                            "mean": mean,
                        },
                    )
                )
                logger.info(
                    "bollinger_lower_band_signal",
                    symbol=symbol,
                    price=round(current_price, 4),
                    lower_band=round(lower_band, 4),
                )

            # SHORT when price touches or goes above upper band
            elif current_price >= upper_band and self._entry_band in ("upper", "both"):
                band_distance = (current_price - upper_band) / std_dev if std_dev > 0 else 0
                confidence = min(1.0, 0.5 + band_distance * 0.25)
                signals.append(
                    Signal(
                        strategy_name=self.name,
                        symbol=symbol,
                        direction=SignalDirection.SHORT,
                        confidence=confidence,
                        suggested_size=Decimal("0"),
                        order_type=OrderType.MARKET,
                        metadata={
                            "band_touch": "upper",
                            "current_price": current_price,
                            "lower_band": lower_band,
                            "upper_band": upper_band,
                            "mean": mean,
                        },
                    )
                )
                logger.info(
                    "bollinger_upper_band_signal",
                    symbol=symbol,
                    price=round(current_price, 4),
                    upper_band=round(upper_band, 4),
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
