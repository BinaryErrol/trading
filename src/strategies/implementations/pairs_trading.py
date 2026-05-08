"""Pairs Trading strategy implementation.

Monitors the spread between two correlated symbols and generates signals
based on the z-score of the spread. When the spread deviates significantly
from its mean, the strategy enters a position expecting mean reversion.

Hedge ratio = price_A / price_B
Spread = price_A - hedge_ratio * price_B
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


class PairsTradingStrategy(BaseStrategy):
    """Statistical arbitrage strategy trading the spread between two correlated symbols.

    Calculates the spread between two symbols using a simple hedge ratio,
    then computes the z-score of the spread over a rolling window. Generates
    entry signals when the z-score exceeds thresholds and exit signals when
    it reverts.

    LONG spread (buy A, sell B) when z < -entry_z
    SHORT spread (sell A, buy B) when z > entry_z
    CLOSE when |z| < exit_z

    Parameters (from config.parameters):
        pair_symbols: List of exactly two symbols [symbol_A, symbol_B].
        cointegration_window: Number of bars for spread mean/std calculation.
        entry_z: Z-score threshold for entering a position.
        exit_z: Z-score threshold for closing a position.
    """

    def __init__(self, config: StrategyConfig, data_hub: MarketDataHub) -> None:
        super().__init__(config, data_hub)
        self._pair_symbols: list[str] = list(config.parameters.get("pair_symbols", []))
        self._cointegration_window: int = int(config.parameters.get("cointegration_window", 30))
        self._entry_z: float = float(config.parameters.get("entry_z", 2.0))
        self._exit_z: float = float(config.parameters.get("exit_z", 0.5))

    @property
    def pair_symbols(self) -> list[str]:
        return self._pair_symbols

    @property
    def cointegration_window(self) -> int:
        return self._cointegration_window

    @property
    def entry_z(self) -> float:
        return self._entry_z

    @property
    def exit_z(self) -> float:
        return self._exit_z

    def update_parameters(self, parameters: dict) -> None:
        """Update pairs trading parameters at runtime for hot-reload support."""
        super().update_parameters(parameters)
        self._pair_symbols = list(parameters.get("pair_symbols", self._pair_symbols))
        self._cointegration_window = int(
            parameters.get("cointegration_window", self._cointegration_window)
        )
        self._entry_z = float(parameters.get("entry_z", self._entry_z))
        self._exit_z = float(parameters.get("exit_z", self._exit_z))

    def required_indicators(self) -> list[str]:
        """Pairs trading requires price history for both symbols."""
        return [f"SPREAD_ZSCORE_{self._cointegration_window}"]

    async def evaluate(self) -> list[Signal]:
        """Evaluate the spread z-score and generate entry/exit signals.

        Returns:
            List of signals for the pair when z-score crosses thresholds.
        """
        signals: list[Signal] = []

        if len(self._pair_symbols) < 2:
            logger.warning("pairs_trading_insufficient_symbols", symbols=self._pair_symbols)
            return signals

        symbol_a = self._pair_symbols[0]
        symbol_b = self._pair_symbols[1]
        timeframe = self._resolve_timeframe()

        bars_a = self._data_hub.get_history(symbol_a, timeframe, self._cointegration_window)
        bars_b = self._data_hub.get_history(symbol_b, timeframe, self._cointegration_window)

        if len(bars_a) < self._cointegration_window or len(bars_b) < self._cointegration_window:
            logger.debug(
                "insufficient_bars_for_pairs_trading",
                symbol_a=symbol_a,
                symbol_b=symbol_b,
                required=self._cointegration_window,
                available_a=len(bars_a),
                available_b=len(bars_b),
            )
            return signals

        closes_a = [bar.close for bar in bars_a]
        closes_b = [bar.close for bar in bars_b]

        # Calculate hedge ratio as simple price ratio (mean of A / mean of B)
        mean_a = sum(closes_a) / len(closes_a)
        mean_b = sum(closes_b) / len(closes_b)

        if mean_b == 0:
            return signals

        hedge_ratio = mean_a / mean_b

        # Calculate spread series: spread = price_A - hedge_ratio * price_B
        spreads = [a - hedge_ratio * b for a, b in zip(closes_a, closes_b)]

        spread_mean = sum(spreads) / len(spreads)
        spread_variance = sum((s - spread_mean) ** 2 for s in spreads) / len(spreads)
        spread_std = spread_variance**0.5

        if spread_std == 0:
            return signals

        current_spread = spreads[-1]
        z_score = (current_spread - spread_mean) / spread_std

        metadata = {
            "z_score": z_score,
            "spread": current_spread,
            "spread_mean": spread_mean,
            "spread_std": spread_std,
            "hedge_ratio": hedge_ratio,
            "symbol_a": symbol_a,
            "symbol_b": symbol_b,
        }

        if z_score < -self._entry_z:
            # Spread is too low — LONG spread (buy A, sell B)
            confidence = min(1.0, abs(z_score) / (self._entry_z * 2))
            signals.append(
                Signal(
                    strategy_name=self.name,
                    symbol=symbol_a,
                    direction=SignalDirection.LONG,
                    confidence=confidence,
                    suggested_size=Decimal("0"),
                    order_type=OrderType.MARKET,
                    metadata={**metadata, "leg": "A", "action": "buy"},
                )
            )
            signals.append(
                Signal(
                    strategy_name=self.name,
                    symbol=symbol_b,
                    direction=SignalDirection.SHORT,
                    confidence=confidence,
                    suggested_size=Decimal("0"),
                    order_type=OrderType.MARKET,
                    metadata={**metadata, "leg": "B", "action": "sell"},
                )
            )
            logger.info(
                "pairs_trading_long_spread",
                symbol_a=symbol_a,
                symbol_b=symbol_b,
                z_score=round(z_score, 4),
                entry_z=self._entry_z,
            )
        elif z_score > self._entry_z:
            # Spread is too high — SHORT spread (sell A, buy B)
            confidence = min(1.0, abs(z_score) / (self._entry_z * 2))
            signals.append(
                Signal(
                    strategy_name=self.name,
                    symbol=symbol_a,
                    direction=SignalDirection.SHORT,
                    confidence=confidence,
                    suggested_size=Decimal("0"),
                    order_type=OrderType.MARKET,
                    metadata={**metadata, "leg": "A", "action": "sell"},
                )
            )
            signals.append(
                Signal(
                    strategy_name=self.name,
                    symbol=symbol_b,
                    direction=SignalDirection.LONG,
                    confidence=confidence,
                    suggested_size=Decimal("0"),
                    order_type=OrderType.MARKET,
                    metadata={**metadata, "leg": "B", "action": "buy"},
                )
            )
            logger.info(
                "pairs_trading_short_spread",
                symbol_a=symbol_a,
                symbol_b=symbol_b,
                z_score=round(z_score, 4),
                entry_z=self._entry_z,
            )
        elif abs(z_score) < self._exit_z:
            # Spread has reverted — CLOSE both legs
            signals.append(
                Signal(
                    strategy_name=self.name,
                    symbol=symbol_a,
                    direction=SignalDirection.CLOSE,
                    confidence=1.0,
                    suggested_size=Decimal("0"),
                    order_type=OrderType.MARKET,
                    metadata={**metadata, "leg": "A", "action": "close"},
                )
            )
            signals.append(
                Signal(
                    strategy_name=self.name,
                    symbol=symbol_b,
                    direction=SignalDirection.CLOSE,
                    confidence=1.0,
                    suggested_size=Decimal("0"),
                    order_type=OrderType.MARKET,
                    metadata={**metadata, "leg": "B", "action": "close"},
                )
            )
            logger.info(
                "pairs_trading_close_spread",
                symbol_a=symbol_a,
                symbol_b=symbol_b,
                z_score=round(z_score, 4),
                exit_z=self._exit_z,
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
