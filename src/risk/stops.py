"""Stop-loss monitoring — fixed percentage and ATR-based trailing stops.

StopMonitor tracks open positions and generates CLOSE signals when price
breaches the computed stop level.

Fixed stop: stop_price = entry_price * (1 - stop_pct)
Trailing stop: stop_price = highest_price - N * ATR, only moves up
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Literal

import structlog

from src.config.settings import StopLossConfig
from src.strategies.signals import OrderType, Signal, SignalDirection

logger = structlog.get_logger(__name__)


@dataclass
class StopLevel:
    """Tracked stop level for a single position.

    Attributes:
        symbol: Ticker symbol.
        entry_price: Original entry price.
        stop_price: Current stop-loss price (may trail upward).
        highest_price: Highest price observed since entry (for trailing).
        stop_type: "fixed_pct" or "atr_trailing".
        strategy_name: Strategy that owns this position.
        direction: Direction of the position (LONG or SHORT).
    """

    symbol: str
    entry_price: Decimal
    stop_price: Decimal
    highest_price: Decimal
    stop_type: Literal["fixed_pct", "atr_trailing"]
    strategy_name: str
    direction: SignalDirection = SignalDirection.LONG


class StopMonitor:
    """Monitors open positions against their stop-loss levels.

    Supports two stop types:
    - fixed_pct: stop_price = entry_price * (1 - fixed_pct), never changes.
    - atr_trailing: stop_price = highest_price - atr_multiplier * ATR,
      only moves up as price increases.

    Args:
        config: StopLossConfig with type, atr_multiplier, and fixed_pct.
    """

    def __init__(self, config: StopLossConfig) -> None:
        self._config = config
        self._stops: dict[str, StopLevel] = {}  # keyed by symbol

    @property
    def stops(self) -> dict[str, StopLevel]:
        """Current stop levels for all tracked positions."""
        return dict(self._stops)

    def add_position(
        self,
        symbol: str,
        entry_price: Decimal,
        strategy_name: str,
        atr: Decimal | None = None,
        stop_type: Literal["fixed_pct", "atr_trailing"] | None = None,
        direction: SignalDirection = SignalDirection.LONG,
    ) -> StopLevel:
        """Register a new position for stop monitoring.

        Args:
            symbol: Ticker symbol.
            entry_price: Price at which the position was entered.
            strategy_name: Strategy that owns this position.
            atr: Current ATR value (required for atr_trailing type).
            stop_type: Override the default stop type from config.
            direction: Direction of the position (LONG or SHORT).

        Returns:
            The computed StopLevel for this position.

        Raises:
            ValueError: If atr_trailing is used without providing ATR.
        """
        effective_type = stop_type or self._config.type

        if effective_type == "fixed_pct":
            if direction == SignalDirection.SHORT:
                stop_price = entry_price * (1 + Decimal(str(self._config.fixed_pct)))
            else:
                stop_price = entry_price * (1 - Decimal(str(self._config.fixed_pct)))
        elif effective_type == "atr_trailing":
            if atr is None:
                raise ValueError("ATR value required for atr_trailing stop type")
            if direction == SignalDirection.SHORT:
                stop_price = entry_price + Decimal(str(self._config.atr_multiplier)) * atr
            else:
                stop_price = entry_price - Decimal(str(self._config.atr_multiplier)) * atr
        else:
            raise ValueError(f"Unknown stop type: {effective_type}")

        stop_level = StopLevel(
            symbol=symbol,
            entry_price=entry_price,
            stop_price=stop_price,
            highest_price=entry_price,
            stop_type=effective_type,
            strategy_name=strategy_name,
            direction=direction,
        )
        self._stops[symbol] = stop_level

        logger.info(
            "stop_added",
            symbol=symbol,
            entry_price=str(entry_price),
            stop_price=str(stop_price),
            stop_type=effective_type,
            direction=direction.value,
        )
        return stop_level

    def update_price(self, symbol: str, current_price: Decimal, atr: Decimal | None = None) -> StopLevel | None:
        """Update the highest price and trailing stop for a position.

        For trailing stops, the stop_price only moves up (never down).
        For fixed stops, this only updates highest_price tracking.

        Args:
            symbol: Ticker symbol.
            current_price: Latest market price.
            atr: Current ATR value (used for trailing stop recalculation).

        Returns:
            Updated StopLevel, or None if symbol is not tracked.
        """
        stop = self._stops.get(symbol)
        if stop is None:
            return None

        # Update highest price if current is higher
        if current_price > stop.highest_price:
            stop.highest_price = current_price

            # Recalculate trailing stop (only moves up)
            if stop.stop_type == "atr_trailing" and atr is not None:
                new_stop = current_price - Decimal(str(self._config.atr_multiplier)) * atr
                if new_stop > stop.stop_price:
                    stop.stop_price = new_stop
                    logger.debug(
                        "trailing_stop_updated",
                        symbol=symbol,
                        new_stop=str(new_stop),
                        highest_price=str(current_price),
                    )

        return stop

    def remove_position(self, symbol: str) -> None:
        """Remove a position from stop monitoring.

        Args:
            symbol: Ticker symbol to stop tracking.
        """
        if symbol in self._stops:
            del self._stops[symbol]
            logger.info("stop_removed", symbol=symbol)

    def monitor_stops(self, current_prices: dict[str, Decimal]) -> list[Signal]:
        """Check all tracked positions against their stop levels.

        For LONG positions: triggers when price <= stop_price.
        For SHORT positions: triggers when price >= stop_price.

        Args:
            current_prices: Mapping of symbol to current market price.

        Returns:
            List of CLOSE signals for positions that hit their stops.
        """
        close_signals: list[Signal] = []
        triggered_symbols: list[str] = []

        for symbol, stop in list(self._stops.items()):
            price = current_prices.get(symbol)
            if price is None:
                continue

            if stop.direction == SignalDirection.LONG:
                triggered = price <= stop.stop_price
            elif stop.direction == SignalDirection.SHORT:
                triggered = price >= stop.stop_price
            else:
                triggered = False

            if triggered:
                signal = Signal(
                    strategy_name=stop.strategy_name,
                    symbol=symbol,
                    direction=SignalDirection.CLOSE,
                    confidence=1.0,
                    suggested_size=Decimal("0"),  # Close entire position
                    order_type=OrderType.MARKET,
                    metadata={
                        "reason": "stop_loss_triggered",
                        "stop_type": stop.stop_type,
                        "stop_price": str(stop.stop_price),
                        "trigger_price": str(price),
                        "entry_price": str(stop.entry_price),
                        "direction": stop.direction.value,
                    },
                    timestamp=datetime.now(timezone.utc),
                )
                close_signals.append(signal)
                triggered_symbols.append(symbol)

                logger.warning(
                    "stop_loss_triggered",
                    symbol=symbol,
                    stop_type=stop.stop_type,
                    stop_price=str(stop.stop_price),
                    trigger_price=str(price),
                    strategy=stop.strategy_name,
                    direction=stop.direction.value,
                )

        # Remove stops after trigger to prevent duplicate signals
        for symbol in triggered_symbols:
            del self._stops[symbol]

        return close_signals
