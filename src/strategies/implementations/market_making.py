"""Market Making strategy implementation.

Places bid/ask quotes around the mid price with dynamic spread based on
recent volatility (ATR). Skews quotes based on current inventory to manage
risk and avoid building excessive directional exposure.
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


class MarketMakingStrategy(BaseStrategy):
    """Market making strategy that provides liquidity by quoting bid/ask.

    Places limit orders on both sides of the mid price. The spread is
    dynamically adjusted based on recent volatility (ATR). Inventory
    skew shifts quotes away from building further directional exposure.

    Parameters (from config.parameters):
        spread_bps: Base spread in basis points (e.g., 10 = 0.1%).
        inventory_limit: Maximum net inventory before halting one side.
        skew_factor: How aggressively to skew quotes based on inventory (0-1).
        atr_period: Number of bars for ATR calculation (default 14).
        current_inventory: Current net inventory position (positive = long).
    """

    def __init__(self, config: StrategyConfig, data_hub: MarketDataHub) -> None:
        super().__init__(config, data_hub)
        self._spread_bps: float = float(config.parameters.get("spread_bps", 10.0))
        self._inventory_limit: int = int(config.parameters.get("inventory_limit", 100))
        self._skew_factor: float = float(config.parameters.get("skew_factor", 0.5))
        self._atr_period: int = int(config.parameters.get("atr_period", 14))
        self._current_inventory: int = int(config.parameters.get("current_inventory", 0))

    @property
    def spread_bps(self) -> float:
        return self._spread_bps

    @property
    def inventory_limit(self) -> int:
        return self._inventory_limit

    @property
    def skew_factor(self) -> float:
        return self._skew_factor

    @property
    def atr_period(self) -> int:
        return self._atr_period

    @property
    def current_inventory(self) -> int:
        return self._current_inventory

    @current_inventory.setter
    def current_inventory(self, value: int) -> None:
        self._current_inventory = value

    def update_inventory(self, quantity: int) -> None:
        """Update the current inventory position.

        This should be called by the fill handler when a fill is received
        to keep the market maker's inventory tracking in sync with actual
        positions.

        Args:
            quantity: The fill quantity (positive for buys, negative for sells).
        """
        self._current_inventory += quantity
        logger.info(
            "inventory_updated",
            strategy=self.name,
            fill_quantity=quantity,
            new_inventory=self._current_inventory,
        )

    def update_parameters(self, parameters: dict) -> None:
        """Update market making parameters at runtime for hot-reload support."""
        super().update_parameters(parameters)
        self._spread_bps = float(parameters.get("spread_bps", self._spread_bps))
        self._inventory_limit = int(parameters.get("inventory_limit", self._inventory_limit))
        self._skew_factor = float(parameters.get("skew_factor", self._skew_factor))
        self._atr_period = int(parameters.get("atr_period", self._atr_period))

    def required_indicators(self) -> list[str]:
        """Market making requires ATR for dynamic spread calculation."""
        return [f"ATR_{self._atr_period}"]

    async def evaluate(self) -> list[Signal]:
        """Generate bid/ask signals around mid price with inventory skew.

        Returns:
            List of signals — typically one LONG (bid) and one SHORT (ask),
            unless inventory limits prevent quoting on one side.
        """
        signals: list[Signal] = []
        timeframe = self._resolve_timeframe()

        for symbol in self._config.symbols:
            bars = self._data_hub.get_history(symbol, timeframe, self._atr_period + 1)

            if len(bars) < self._atr_period + 1:
                logger.debug(
                    "insufficient_bars_for_market_making",
                    symbol=symbol,
                    required=self._atr_period + 1,
                    available=len(bars),
                )
                continue

            mid_price = bars[-1].close
            if mid_price <= 0:
                continue

            # Calculate ATR for dynamic spread
            atr = self._calculate_atr(bars)

            # Dynamic spread: base spread adjusted by volatility
            base_spread = mid_price * (self._spread_bps / 10000.0)
            dynamic_spread = max(base_spread, atr * 0.5) if atr > 0 else base_spread

            half_spread = dynamic_spread / 2.0

            # Inventory skew: shift quotes away from building inventory
            # Positive inventory → lower bid, higher ask (discourage more buying)
            # Negative inventory → higher bid, lower ask (discourage more selling)
            inventory_ratio = (
                self._current_inventory / self._inventory_limit
                if self._inventory_limit > 0
                else 0.0
            )
            skew = inventory_ratio * self._skew_factor * half_spread

            bid_price = mid_price - half_spread - skew
            ask_price = mid_price + half_spread - skew

            metadata = {
                "mid_price": mid_price,
                "atr": atr,
                "base_spread_bps": self._spread_bps,
                "dynamic_spread": dynamic_spread,
                "inventory": self._current_inventory,
                "inventory_ratio": inventory_ratio,
                "skew": skew,
                "bid_price": bid_price,
                "ask_price": ask_price,
            }

            # Place bid (LONG signal) if not at positive inventory limit
            if self._current_inventory < self._inventory_limit:
                confidence = max(0.1, 1.0 - abs(inventory_ratio))
                signals.append(
                    Signal(
                        strategy_name=self.name,
                        symbol=symbol,
                        direction=SignalDirection.LONG,
                        confidence=confidence,
                        suggested_size=Decimal("0"),
                        order_type=OrderType.LIMIT,
                        limit_price=Decimal(str(round(bid_price, 2))),
                        metadata={**metadata, "side": "bid"},
                    )
                )

            # Place ask (SHORT signal) if not at negative inventory limit
            if self._current_inventory > -self._inventory_limit:
                confidence = max(0.1, 1.0 - abs(inventory_ratio))
                signals.append(
                    Signal(
                        strategy_name=self.name,
                        symbol=symbol,
                        direction=SignalDirection.SHORT,
                        confidence=confidence,
                        suggested_size=Decimal("0"),
                        order_type=OrderType.LIMIT,
                        limit_price=Decimal(str(round(ask_price, 2))),
                        metadata={**metadata, "side": "ask"},
                    )
                )

            logger.info(
                "market_making_quotes",
                symbol=symbol,
                bid=round(bid_price, 4),
                ask=round(ask_price, 4),
                spread_bps=round(dynamic_spread / mid_price * 10000, 2),
                inventory=self._current_inventory,
            )

        return signals

    def _calculate_atr(self, bars: list) -> float:
        """Calculate Average True Range over the ATR period.

        True Range = max(high - low, |high - prev_close|, |low - prev_close|)
        ATR = simple moving average of True Range over atr_period bars.

        Args:
            bars: List of bars (must have at least atr_period + 1 bars).

        Returns:
            The ATR value.
        """
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

        if not true_ranges:
            return 0.0

        # Use the last atr_period true ranges
        recent_trs = true_ranges[-self._atr_period :]
        return sum(recent_trs) / len(recent_trs)

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
