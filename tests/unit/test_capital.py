"""Unit tests for the Capital Allocator module.

Tests allocation modes, over-allocation rejection, P&L tracking, and release logic.
"""

from decimal import Decimal

import pytest

from src.portfolio.capital_allocator import (  # noqa: I001
    AllocationMode,
    CapitalAllocator,
    StrategyAllocation,
)

# --- Fixtures ---


@pytest.fixture
def allocator() -> CapitalAllocator:
    """Create a CapitalAllocator with 100k total capital."""
    return CapitalAllocator(total_capital=Decimal("100000"))


@pytest.fixture
def allocator_with_strategies(allocator: CapitalAllocator) -> CapitalAllocator:
    """Create an allocator with two strategies already allocated."""
    allocator.allocate("momentum", Decimal("30000"), AllocationMode.FIXED_AMOUNT)
    allocator.allocate("mean_reversion", Decimal("20000"), AllocationMode.FIXED_AMOUNT)
    return allocator


# --- Initialization Tests ---


class TestCapitalAllocatorInit:
    """Tests for CapitalAllocator initialization."""

    def test_init_with_positive_capital(self):
        allocator = CapitalAllocator(total_capital=Decimal("100000"))
        assert allocator.total_capital == Decimal("100000")
        assert allocator.total_allocated == Decimal("0")
        assert allocator.unallocated == Decimal("100000")

    def test_init_with_zero_capital(self):
        allocator = CapitalAllocator(total_capital=Decimal("0"))
        assert allocator.total_capital == Decimal("0")

    def test_init_with_negative_capital_raises(self):
        with pytest.raises(ValueError, match="non-negative"):
            CapitalAllocator(total_capital=Decimal("-1000"))

    def test_allocations_initially_empty(self, allocator: CapitalAllocator):
        assert allocator.allocations == {}


# --- Fixed Amount Allocation Tests ---


class TestFixedAmountAllocation:
    """Tests for FIXED_AMOUNT allocation mode."""

    def test_allocate_fixed_amount(self, allocator: CapitalAllocator):
        result = allocator.allocate("momentum", Decimal("25000"), AllocationMode.FIXED_AMOUNT)

        assert result.strategy_name == "momentum"
        assert result.allocated == Decimal("25000")
        assert result.deployed == Decimal("0")
        assert allocator.total_allocated == Decimal("25000")
        assert allocator.unallocated == Decimal("75000")

    def test_allocate_multiple_strategies(self, allocator: CapitalAllocator):
        allocator.allocate("momentum", Decimal("30000"), AllocationMode.FIXED_AMOUNT)
        allocator.allocate("mean_reversion", Decimal("20000"), AllocationMode.FIXED_AMOUNT)

        assert allocator.total_allocated == Decimal("50000")
        assert allocator.unallocated == Decimal("50000")
        assert len(allocator.allocations) == 2

    def test_allocate_entire_capital(self, allocator: CapitalAllocator):
        allocator.allocate("all_in", Decimal("100000"), AllocationMode.FIXED_AMOUNT)

        assert allocator.total_allocated == Decimal("100000")
        assert allocator.unallocated == Decimal("0")

    def test_reallocate_existing_strategy(self, allocator: CapitalAllocator):
        allocator.allocate("momentum", Decimal("25000"), AllocationMode.FIXED_AMOUNT)
        allocator.allocate("momentum", Decimal("35000"), AllocationMode.FIXED_AMOUNT)

        assert allocator.allocations["momentum"].allocated == Decimal("35000")
        assert allocator.total_allocated == Decimal("35000")


# --- Percentage Allocation Tests ---


class TestPercentageAllocation:
    """Tests for PERCENTAGE allocation mode."""

    def test_allocate_percentage(self, allocator: CapitalAllocator):
        result = allocator.allocate("momentum", Decimal("0.25"), AllocationMode.PERCENTAGE)

        assert result.allocated == Decimal("25000")
        assert allocator.total_allocated == Decimal("25000")

    def test_allocate_50_percent(self, allocator: CapitalAllocator):
        result = allocator.allocate("momentum", Decimal("0.5"), AllocationMode.PERCENTAGE)
        assert result.allocated == Decimal("50000")

    def test_allocate_100_percent(self, allocator: CapitalAllocator):
        result = allocator.allocate("momentum", Decimal("1"), AllocationMode.PERCENTAGE)
        assert result.allocated == Decimal("100000")

    def test_allocate_zero_percent(self, allocator: CapitalAllocator):
        result = allocator.allocate("momentum", Decimal("0"), AllocationMode.PERCENTAGE)
        assert result.allocated == Decimal("0")

    def test_percentage_over_1_raises(self, allocator: CapitalAllocator):
        with pytest.raises(ValueError, match="between 0 and 1"):
            allocator.allocate("momentum", Decimal("1.5"), AllocationMode.PERCENTAGE)

    def test_negative_percentage_raises(self, allocator: CapitalAllocator):
        with pytest.raises(ValueError, match="between 0 and 1"):
            allocator.allocate("momentum", Decimal("-0.1"), AllocationMode.PERCENTAGE)


# --- Equal Weight Allocation Tests ---


class TestEqualWeightAllocation:
    """Tests for EQUAL_WEIGHT allocation mode."""

    def test_equal_weight_single_strategy(self, allocator: CapitalAllocator):
        # With amount=1, split among 1 strategy
        result = allocator.allocate("momentum", Decimal("1"), AllocationMode.EQUAL_WEIGHT)
        assert result.allocated == Decimal("100000")

    def test_equal_weight_two_strategies(self, allocator: CapitalAllocator):
        # Allocate with 2 total strategies
        allocator.allocate("momentum", Decimal("2"), AllocationMode.EQUAL_WEIGHT)
        allocator.allocate("mean_reversion", Decimal("2"), AllocationMode.EQUAL_WEIGHT)

        assert allocator.allocations["momentum"].allocated == Decimal("50000")
        assert allocator.allocations["mean_reversion"].allocated == Decimal("50000")
        assert allocator.total_allocated == Decimal("100000")

    def test_equal_weight_three_strategies(self, allocator: CapitalAllocator):
        # Use amount as the number of strategies for equal weight
        for name in ["s1", "s2", "s3"]:
            allocator.allocate(name, Decimal("3"), AllocationMode.EQUAL_WEIGHT)

        # Each gets 100000 / 3 = 33333.33...
        expected = Decimal("100000") / Decimal("3")
        for name in ["s1", "s2", "s3"]:
            assert allocator.allocations[name].allocated == expected


# --- Over-Allocation Rejection Tests ---


class TestOverAllocationRejection:
    """Tests that allocations exceeding total capital are rejected."""

    def test_reject_single_over_allocation(self, allocator: CapitalAllocator):
        with pytest.raises(ValueError, match="exceed total capital"):
            allocator.allocate("greedy", Decimal("150000"), AllocationMode.FIXED_AMOUNT)

    def test_reject_cumulative_over_allocation(self, allocator: CapitalAllocator):
        allocator.allocate("s1", Decimal("60000"), AllocationMode.FIXED_AMOUNT)

        with pytest.raises(ValueError, match="exceed total capital"):
            allocator.allocate("s2", Decimal("50000"), AllocationMode.FIXED_AMOUNT)

    def test_reject_percentage_over_allocation(self, allocator: CapitalAllocator):
        allocator.allocate("s1", Decimal("0.7"), AllocationMode.PERCENTAGE)

        with pytest.raises(ValueError, match="exceed total capital"):
            allocator.allocate("s2", Decimal("0.4"), AllocationMode.PERCENTAGE)

    def test_allow_exact_remaining(self, allocator: CapitalAllocator):
        allocator.allocate("s1", Decimal("60000"), AllocationMode.FIXED_AMOUNT)
        # Should succeed — exactly fills remaining
        result = allocator.allocate("s2", Decimal("40000"), AllocationMode.FIXED_AMOUNT)
        assert result.allocated == Decimal("40000")
        assert allocator.unallocated == Decimal("0")


# --- Get Available Tests ---


class TestGetAvailable:
    """Tests for get_available() method."""

    def test_available_equals_allocated_when_nothing_deployed(
        self, allocator_with_strategies: CapitalAllocator
    ):
        assert allocator_with_strategies.get_available("momentum") == Decimal("30000")

    def test_available_decreases_after_fill(
        self, allocator_with_strategies: CapitalAllocator
    ):
        allocator_with_strategies.record_fill("momentum", Decimal("10000"))
        assert allocator_with_strategies.get_available("momentum") == Decimal("20000")

    def test_available_zero_when_fully_deployed(
        self, allocator_with_strategies: CapitalAllocator
    ):
        allocator_with_strategies.record_fill("momentum", Decimal("30000"))
        assert allocator_with_strategies.get_available("momentum") == Decimal("0")

    def test_available_raises_for_unknown_strategy(self, allocator: CapitalAllocator):
        with pytest.raises(KeyError, match="No allocation found"):
            allocator.get_available("nonexistent")


# --- Can Place Order Tests ---


class TestCanPlaceOrder:
    """Tests for can_place_order() method."""

    def test_can_place_order_within_available(
        self, allocator_with_strategies: CapitalAllocator
    ):
        assert allocator_with_strategies.can_place_order("momentum", Decimal("25000")) is True

    def test_can_place_order_exact_available(
        self, allocator_with_strategies: CapitalAllocator
    ):
        assert allocator_with_strategies.can_place_order("momentum", Decimal("30000")) is True

    def test_cannot_place_order_exceeding_available(
        self, allocator_with_strategies: CapitalAllocator
    ):
        assert allocator_with_strategies.can_place_order("momentum", Decimal("35000")) is False

    def test_cannot_place_order_unknown_strategy(self, allocator: CapitalAllocator):
        assert allocator.can_place_order("nonexistent", Decimal("1000")) is False

    def test_can_place_order_after_partial_deployment(
        self, allocator_with_strategies: CapitalAllocator
    ):
        allocator_with_strategies.record_fill("momentum", Decimal("20000"))
        assert allocator_with_strategies.can_place_order("momentum", Decimal("10000")) is True
        assert allocator_with_strategies.can_place_order("momentum", Decimal("15000")) is False


# --- Record Fill Tests ---


class TestRecordFill:
    """Tests for record_fill() method and P&L tracking."""

    def test_record_fill_increases_deployed(
        self, allocator_with_strategies: CapitalAllocator
    ):
        allocator_with_strategies.record_fill("momentum", Decimal("10000"))
        alloc = allocator_with_strategies.allocations["momentum"]
        assert alloc.deployed == Decimal("10000")

    def test_record_fill_tracks_realized_pnl(
        self, allocator_with_strategies: CapitalAllocator
    ):
        allocator_with_strategies.record_fill("momentum", Decimal("10000"))
        # Close with profit
        allocator_with_strategies.record_fill("momentum", Decimal("-10000"), pnl=Decimal("500"))

        alloc = allocator_with_strategies.allocations["momentum"]
        assert alloc.deployed == Decimal("0")
        assert alloc.realized_pnl == Decimal("500")

    def test_record_fill_accumulates_pnl(
        self, allocator_with_strategies: CapitalAllocator
    ):
        allocator_with_strategies.record_fill("momentum", Decimal("5000"), pnl=Decimal("100"))
        allocator_with_strategies.record_fill("momentum", Decimal("3000"), pnl=Decimal("-50"))

        alloc = allocator_with_strategies.allocations["momentum"]
        assert alloc.realized_pnl == Decimal("50")
        assert alloc.deployed == Decimal("8000")

    def test_record_fill_raises_for_unknown_strategy(self, allocator: CapitalAllocator):
        with pytest.raises(KeyError, match="No allocation found"):
            allocator.record_fill("nonexistent", Decimal("1000"))

    def test_record_fill_with_loss(
        self, allocator_with_strategies: CapitalAllocator
    ):
        allocator_with_strategies.record_fill("momentum", Decimal("10000"))
        allocator_with_strategies.record_fill("momentum", Decimal("-10000"), pnl=Decimal("-300"))

        alloc = allocator_with_strategies.allocations["momentum"]
        assert alloc.realized_pnl == Decimal("-300")


# --- Release Tests ---


class TestRelease:
    """Tests for release() method."""

    def test_release_returns_undeployed_capital(
        self, allocator_with_strategies: CapitalAllocator
    ):
        allocator_with_strategies.record_fill("momentum", Decimal("10000"))
        released = allocator_with_strategies.release("momentum")

        # 30000 allocated - 10000 deployed = 20000 released
        assert released == Decimal("20000")

    def test_release_removes_strategy_from_allocations(
        self, allocator_with_strategies: CapitalAllocator
    ):
        allocator_with_strategies.release("momentum")
        assert "momentum" not in allocator_with_strategies.allocations

    def test_release_frees_capital_for_reallocation(
        self, allocator_with_strategies: CapitalAllocator
    ):
        # Initially: 30k + 20k = 50k allocated, 50k unallocated
        assert allocator_with_strategies.unallocated == Decimal("50000")

        allocator_with_strategies.release("momentum")
        # After release: only 20k allocated, 80k unallocated
        assert allocator_with_strategies.unallocated == Decimal("80000")

    def test_release_fully_deployed_returns_zero(
        self, allocator_with_strategies: CapitalAllocator
    ):
        allocator_with_strategies.record_fill("momentum", Decimal("30000"))
        released = allocator_with_strategies.release("momentum")
        assert released == Decimal("0")

    def test_release_nothing_deployed_returns_full_allocation(
        self, allocator_with_strategies: CapitalAllocator
    ):
        released = allocator_with_strategies.release("momentum")
        assert released == Decimal("30000")

    def test_release_raises_for_unknown_strategy(self, allocator: CapitalAllocator):
        with pytest.raises(KeyError, match="No allocation found"):
            allocator.release("nonexistent")


# --- StrategyAllocation Dataclass Tests ---


class TestStrategyAllocation:
    """Tests for the StrategyAllocation dataclass."""

    def test_default_values(self):
        alloc = StrategyAllocation(strategy_name="test")
        assert alloc.strategy_name == "test"
        assert alloc.allocated == Decimal("0")
        assert alloc.deployed == Decimal("0")
        assert alloc.realized_pnl == Decimal("0")
        assert alloc.unrealized_pnl == Decimal("0")

    def test_custom_values(self):
        alloc = StrategyAllocation(
            strategy_name="momentum",
            allocated=Decimal("50000"),
            deployed=Decimal("30000"),
            realized_pnl=Decimal("1500"),
            unrealized_pnl=Decimal("-200"),
        )
        assert alloc.allocated == Decimal("50000")
        assert alloc.deployed == Decimal("30000")
        assert alloc.realized_pnl == Decimal("1500")
        assert alloc.unrealized_pnl == Decimal("-200")
