"""Property-based tests for Capital Allocator correctness properties.

**Validates: Requirements 1.1, 1.9**

P1: The sum of all strategy allocations SHALL never exceed total portfolio value.
P9: No strategy SHALL deploy capital exceeding its allocation.
"""

from decimal import Decimal

from hypothesis import given, settings
from hypothesis import strategies as st

from src.portfolio.capital_allocator import AllocationMode, CapitalAllocator


# ---------------------------------------------------------------------------
# Custom Hypothesis strategies
# ---------------------------------------------------------------------------

# Positive capital values (avoid zero to keep tests meaningful)
positive_capital = st.decimals(
    min_value=Decimal("100"),
    max_value=Decimal("10000000"),
    places=2,
    allow_nan=False,
    allow_infinity=False,
)

# Strategy names
strategy_names = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N"), whitelist_characters="_"),
    min_size=1,
    max_size=20,
)

# Lists of unique strategy names with allocation fractions
allocation_entries = st.lists(
    st.tuples(
        strategy_names,
        st.decimals(
            min_value=Decimal("0.01"),
            max_value=Decimal("0.50"),
            places=2,
            allow_nan=False,
            allow_infinity=False,
        ),
    ),
    min_size=1,
    max_size=5,
    unique_by=lambda x: x[0],
)


# ---------------------------------------------------------------------------
# P1: Capital Conservation
# ---------------------------------------------------------------------------


@settings(max_examples=100)
@given(
    total_capital=positive_capital,
    entries=allocation_entries,
)
def test_p1_sum_of_allocations_never_exceeds_total(
    total_capital: Decimal,
    entries: list[tuple[str, Decimal]],
) -> None:
    """P1: sum of allocations never exceeds total portfolio value.

    **Validates: Requirements 1.1**

    For any sequence of valid allocations, the total allocated capital
    must never exceed the total portfolio value.
    """
    allocator = CapitalAllocator(total_capital)

    for name, fraction in entries:
        amount = total_capital * fraction
        try:
            allocator.allocate(name, amount, AllocationMode.FIXED_AMOUNT)
        except ValueError:
            # Allocation correctly rejected when it would exceed total
            pass

    # Property: sum of allocations <= total capital
    assert allocator.total_allocated <= allocator.total_capital


# ---------------------------------------------------------------------------
# P9: Allocation Boundary
# ---------------------------------------------------------------------------


@settings(max_examples=100)
@given(
    total_capital=positive_capital,
    allocation_fraction=st.decimals(
        min_value=Decimal("0.05"),
        max_value=Decimal("0.50"),
        places=2,
        allow_nan=False,
        allow_infinity=False,
    ),
    fill_fractions=st.lists(
        st.decimals(
            min_value=Decimal("0.01"),
            max_value=Decimal("0.30"),
            places=2,
            allow_nan=False,
            allow_infinity=False,
        ),
        min_size=1,
        max_size=10,
    ),
)
def test_p9_no_strategy_deploys_capital_exceeding_allocation(
    total_capital: Decimal,
    allocation_fraction: Decimal,
    fill_fractions: list[Decimal],
) -> None:
    """P9: no strategy deploys capital exceeding its allocation.

    **Validates: Requirements 1.9**

    After allocating capital and recording fills, can_place_order must
    reject any order that would cause deployed capital to exceed the allocation.
    """
    allocator = CapitalAllocator(total_capital)
    strategy_name = "test_strategy"
    allocation_amount = total_capital * allocation_fraction

    allocator.allocate(strategy_name, allocation_amount, AllocationMode.FIXED_AMOUNT)

    # Record fills up to the allocation limit
    total_deployed = Decimal("0")
    for fraction in fill_fractions:
        fill_value = allocation_amount * fraction
        if total_deployed + fill_value <= allocation_amount:
            allocator.record_fill(strategy_name, fill_value)
            total_deployed += fill_value

    # Property: can_place_order rejects orders exceeding remaining allocation
    available = allocator.get_available(strategy_name)
    over_limit_order = available + Decimal("0.01")

    assert not allocator.can_place_order(strategy_name, over_limit_order)
    # And orders within limit are accepted
    if available > Decimal("0"):
        assert allocator.can_place_order(strategy_name, available)
