"""Simulated execution model for backtesting.

Models realistic order execution with configurable slippage (basis points),
commissions (per share), and market impact. Used by BacktestEngine to
simulate fills against historical OHLCV data.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from src.strategies.signals import OrderType, SignalDirection


@dataclass
class SimulatedFill:
    """Result of a simulated order execution.

    Attributes:
        fill_price: The effective fill price after slippage and market impact.
        quantity: Number of shares filled.
        commission: Total commission for this fill.
        slippage_cost: Dollar cost of slippage applied.
        market_impact_cost: Dollar cost of market impact applied.
        total_cost: Total execution cost (commission + slippage + market impact).
    """

    fill_price: Decimal
    quantity: Decimal
    commission: Decimal
    slippage_cost: Decimal
    market_impact_cost: Decimal
    total_cost: Decimal


class SimulatedExecution:
    """Models realistic execution with slippage, commissions, and market impact.

    Slippage is applied as basis points adverse to the trade direction.
    Market impact is applied as additional basis points for larger orders.
    Commission is charged per share.

    Args:
        slippage_bps: Slippage in basis points (1 bp = 0.01%).
        commission_per_share: Commission cost per share in dollars.
        market_impact_bps: Market impact in basis points.
    """

    def __init__(
        self,
        slippage_bps: float = 5.0,
        commission_per_share: Decimal = Decimal("0.005"),
        market_impact_bps: float = 2.0,
    ) -> None:
        self._slippage_bps = slippage_bps
        self._commission_per_share = commission_per_share
        self._market_impact_bps = market_impact_bps

    @property
    def slippage_bps(self) -> float:
        """Slippage in basis points."""
        return self._slippage_bps

    @property
    def commission_per_share(self) -> Decimal:
        """Commission per share."""
        return self._commission_per_share

    @property
    def market_impact_bps(self) -> float:
        """Market impact in basis points."""
        return self._market_impact_bps

    def simulate_fill(
        self,
        price: Decimal,
        quantity: Decimal,
        direction: SignalDirection,
        order_type: OrderType = OrderType.MARKET,
        limit_price: Decimal | None = None,
    ) -> SimulatedFill:
        """Simulate an order fill with realistic execution costs.

        For market orders, the fill price is the bar's close price adjusted
        by slippage and market impact. For limit orders, the fill occurs at
        the limit price if it would have been reached.

        Args:
            price: The market price (typically bar close or OHLCV-derived).
            quantity: Number of shares to fill.
            direction: Trade direction (LONG buys, SHORT sells, CLOSE closes).
            order_type: Type of order being simulated.
            limit_price: Limit price for limit orders.

        Returns:
            SimulatedFill with adjusted price and cost breakdown.
        """
        # Determine base fill price
        if order_type == OrderType.LIMIT and limit_price is not None:
            base_price = limit_price
        else:
            base_price = price

        # Calculate slippage: adverse to trade direction
        slippage_factor = Decimal(str(self._slippage_bps)) / Decimal("10000")
        market_impact_factor = Decimal(str(self._market_impact_bps)) / Decimal("10000")

        if direction == SignalDirection.LONG:
            # Buying: price moves up (adverse)
            adjusted_price = base_price * (
                Decimal("1") + slippage_factor + market_impact_factor
            )
        elif direction == SignalDirection.SHORT:
            # Selling short: price moves down (adverse)
            adjusted_price = base_price * (
                Decimal("1") - slippage_factor - market_impact_factor
            )
        else:
            # CLOSE: direction depends on whether closing a long or short
            # For simplicity, treat as selling (adverse = lower price)
            adjusted_price = base_price * (
                Decimal("1") - slippage_factor - market_impact_factor
            )

        # Calculate costs
        abs_quantity = abs(quantity)
        commission = self._commission_per_share * abs_quantity
        slippage_cost = abs(base_price * slippage_factor * abs_quantity)
        market_impact_cost = abs(base_price * market_impact_factor * abs_quantity)
        total_cost = commission + slippage_cost + market_impact_cost

        return SimulatedFill(
            fill_price=adjusted_price,
            quantity=quantity,
            commission=commission,
            slippage_cost=slippage_cost,
            market_impact_cost=market_impact_cost,
            total_cost=total_cost,
        )
