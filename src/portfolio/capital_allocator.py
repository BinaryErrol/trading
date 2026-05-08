"""Capital allocation across strategies with allocation modes and P&L tracking.

Distributes capital to strategies using fixed amount, percentage, or equal-weight modes.
Tracks per-strategy deployed capital, realized/unrealized P&L, and enforces allocation limits.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum

import structlog

logger = structlog.get_logger(__name__)


class AllocationMode(Enum):
    """Mode for allocating capital to a strategy."""

    FIXED_AMOUNT = "fixed_amount"
    PERCENTAGE = "percentage"
    EQUAL_WEIGHT = "equal_weight"


@dataclass
class StrategyAllocation:
    """Tracks capital allocation and P&L for a single strategy."""

    strategy_name: str
    allocated: Decimal = Decimal("0")
    deployed: Decimal = Decimal("0")
    realized_pnl: Decimal = Decimal("0")
    unrealized_pnl: Decimal = Decimal("0")


class CapitalAllocator:
    """Manages capital distribution across trading strategies.

    Supports three allocation modes:
    - FIXED_AMOUNT: allocate an exact dollar amount to a strategy
    - PERCENTAGE: allocate as a fraction of total_capital
    - EQUAL_WEIGHT: split evenly across all strategies

    Enforces that total allocations never exceed total_capital.
    """

    def __init__(self, total_capital: Decimal) -> None:
        if total_capital < Decimal("0"):
            raise ValueError("total_capital must be non-negative")
        self._total_capital: Decimal = total_capital
        self._allocations: dict[str, StrategyAllocation] = {}

    @property
    def total_capital(self) -> Decimal:
        """Total available capital for allocation."""
        return self._total_capital

    @property
    def allocations(self) -> dict[str, StrategyAllocation]:
        """Current strategy allocations."""
        return dict(self._allocations)

    @property
    def total_allocated(self) -> Decimal:
        """Sum of all strategy allocations."""
        return sum(a.allocated for a in self._allocations.values())

    @property
    def unallocated(self) -> Decimal:
        """Capital not yet allocated to any strategy."""
        return self._total_capital - self.total_allocated

    def allocate(
        self,
        strategy_name: str,
        amount: Decimal,
        mode: AllocationMode,
    ) -> StrategyAllocation:
        """Assign capital to a strategy.

        Args:
            strategy_name: Name of the strategy to allocate to.
            amount: The allocation value (dollar amount, percentage as decimal,
                or ignored for equal_weight).
            mode: The allocation mode to use.

        Returns:
            The updated StrategyAllocation.

        Raises:
            ValueError: If the allocation would exceed total_capital or amount is invalid.
        """
        allocation_amount = self._resolve_amount(amount, mode, strategy_name)

        if allocation_amount < Decimal("0"):
            raise ValueError(
                f"Allocation amount must be non-negative, got {allocation_amount}"
            )

        # Calculate what the new total would be
        existing = self._allocations.get(strategy_name)
        current_allocation = existing.allocated if existing else Decimal("0")
        new_total = self.total_allocated - current_allocation + allocation_amount

        if new_total > self._total_capital:
            raise ValueError(
                f"Allocation of {allocation_amount} for '{strategy_name}' would exceed "
                f"total capital ({self._total_capital}). "
                f"Currently allocated: {self.total_allocated - current_allocation}, "
                f"available: {self._total_capital - (self.total_allocated - current_allocation)}"
            )

        if existing:
            existing.allocated = allocation_amount
        else:
            self._allocations[strategy_name] = StrategyAllocation(
                strategy_name=strategy_name,
                allocated=allocation_amount,
            )

        logger.info(
            "capital_allocated",
            strategy=strategy_name,
            amount=str(allocation_amount),
            mode=mode.value,
            total_allocated=str(self.total_allocated),
        )

        return self._allocations[strategy_name]

    def get_available(self, strategy_name: str) -> Decimal:
        """Return remaining available capital for a strategy (allocated - deployed).

        Args:
            strategy_name: Name of the strategy.

        Returns:
            Available capital that can still be deployed.

        Raises:
            KeyError: If strategy has no allocation.
        """
        allocation = self._get_allocation(strategy_name)
        return allocation.allocated - allocation.deployed

    def can_place_order(self, strategy_name: str, order_value: Decimal) -> bool:
        """Check if an order fits within the strategy's available capital.

        Args:
            strategy_name: Name of the strategy.
            order_value: The value of the proposed order.

        Returns:
            True if the order can be placed, False otherwise.
        """
        try:
            available = self.get_available(strategy_name)
        except KeyError:
            return False

        return order_value <= available

    def record_fill(
        self,
        strategy_name: str,
        fill_value: Decimal,
        pnl: Decimal = Decimal("0"),
    ) -> None:
        """Record a fill, updating deployed capital and P&L.

        Args:
            strategy_name: Name of the strategy.
            fill_value: The value of the filled order (positive = deploying capital).
            pnl: Realized P&L from this fill (positive = profit).

        Raises:
            KeyError: If strategy has no allocation.
        """
        allocation = self._get_allocation(strategy_name)
        allocation.deployed += fill_value
        allocation.realized_pnl += pnl

        logger.info(
            "fill_recorded",
            strategy=strategy_name,
            fill_value=str(fill_value),
            pnl=str(pnl),
            deployed=str(allocation.deployed),
            realized_pnl=str(allocation.realized_pnl),
        )

    def release(self, strategy_name: str) -> Decimal:
        """Release undeployed capital back to the pool.

        Removes the strategy allocation entirely, returning undeployed capital
        to the available pool. Deployed capital remains tracked until positions close.

        Args:
            strategy_name: Name of the strategy to release.

        Returns:
            The amount of capital released (allocated - deployed).

        Raises:
            KeyError: If strategy has no allocation.
        """
        allocation = self._get_allocation(strategy_name)
        released = allocation.allocated - allocation.deployed

        if released < Decimal("0"):
            released = Decimal("0")

        # Remove the allocation from tracking
        del self._allocations[strategy_name]

        logger.info(
            "capital_released",
            strategy=strategy_name,
            released=str(released),
        )

        return released

    def _resolve_amount(
        self, amount: Decimal, mode: AllocationMode, strategy_name: str = ""
    ) -> Decimal:
        """Resolve the allocation amount based on mode.

        Args:
            amount: Raw amount value.
            mode: Allocation mode.
            strategy_name: Name of the strategy (used for equal weight counting).

        Returns:
            Resolved dollar amount.
        """
        if mode == AllocationMode.FIXED_AMOUNT:
            return amount
        elif mode == AllocationMode.PERCENTAGE:
            if amount < Decimal("0") or amount > Decimal("1"):
                raise ValueError(
                    f"Percentage must be between 0 and 1, got {amount}"
                )
            return self._total_capital * amount
        elif mode == AllocationMode.EQUAL_WEIGHT:
            # Count total strategies: existing ones + new one if not already tracked
            num_strategies = len(self._allocations)
            if strategy_name not in self._allocations:
                num_strategies += 1
            # amount parameter is treated as the total number of strategies
            # if provided as > 0, use it as the divisor
            if amount > Decimal("0"):
                num_strategies = int(amount)
            return self._total_capital / Decimal(str(max(num_strategies, 1)))
        else:
            raise ValueError(f"Unknown allocation mode: {mode}")

    def _get_allocation(self, strategy_name: str) -> StrategyAllocation:
        """Get allocation for a strategy or raise KeyError."""
        if strategy_name not in self._allocations:
            raise KeyError(f"No allocation found for strategy '{strategy_name}'")
        return self._allocations[strategy_name]
