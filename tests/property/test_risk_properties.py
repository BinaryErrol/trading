"""Property-based tests for Risk Manager correctness properties.

**Validates: Requirements 2.2, 2.7**

P2: No order SHALL be executed that would cause any risk limit to be breached.
P7: The system SHALL never exceed IBKR's message rate limit of 50 messages per second.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from hypothesis import given, settings
from hypothesis import strategies as st

from src.config.settings import RiskConfig
from src.orders.rate_limiter import RateLimiter
from src.risk.manager import RiskManager
from src.strategies.signals import OrderType, Signal, SignalDirection


# ---------------------------------------------------------------------------
# Helpers / Mocks
# ---------------------------------------------------------------------------


class MockPortfolioMonitor:
    """Minimal portfolio monitor for risk manager testing."""

    def __init__(self, total_value: Decimal, peak_equity: Decimal) -> None:
        self._total_value = total_value
        self._peak_equity = peak_equity
        self._positions: dict[str, Any] = {}

    def get_total_value(self) -> Decimal:
        return self._total_value

    def get_peak_equity(self) -> Decimal:
        return self._peak_equity

    @property
    def positions(self) -> dict[str, Any]:
        return self._positions


# Custom strategies for Signal generation
def signal_strategy(
    max_size: Decimal = Decimal("100000"),
) -> st.SearchStrategy[Signal]:
    """Generate random valid Signal objects."""
    return st.builds(
        Signal,
        strategy_name=st.just("test_strategy"),
        symbol=st.sampled_from(["AAPL", "MSFT", "GOOGL", "AMZN", "TSLA"]),
        direction=st.sampled_from([SignalDirection.LONG, SignalDirection.SHORT]),
        confidence=st.floats(min_value=0.0, max_value=1.0),
        suggested_size=st.decimals(
            min_value=Decimal("100"),
            max_value=max_size,
            places=2,
            allow_nan=False,
            allow_infinity=False,
        ),
        order_type=st.just(OrderType.MARKET),
        metadata=st.just({"sector": "Technology"}),
    )


# ---------------------------------------------------------------------------
# P2: Risk Limit Enforcement
# ---------------------------------------------------------------------------


@settings(max_examples=100)
@given(
    portfolio_value=st.decimals(
        min_value=Decimal("10000"),
        max_value=Decimal("10000000"),
        places=2,
        allow_nan=False,
        allow_infinity=False,
    ),
    max_position_pct=st.floats(min_value=0.01, max_value=0.20),
    signal_size_fraction=st.floats(min_value=0.001, max_value=0.50),
)
def test_p2_no_order_executes_breaching_risk_limits(
    portfolio_value: Decimal,
    max_position_pct: float,
    signal_size_fraction: float,
) -> None:
    """P2: no order executes that would breach risk limits.

    **Validates: Requirements 2.2**

    If a signal's suggested_size exceeds the max_position_pct of portfolio value,
    the risk manager must reject it.
    """
    config = RiskConfig(
        max_position_pct=max_position_pct,
        max_drawdown_pct=0.50,  # High to avoid triggering
        max_daily_loss_pct=0.50,  # High to avoid triggering
        max_sector_concentration=1.0,  # Permissive
        max_correlation=0.7,
    )

    portfolio = MockPortfolioMonitor(
        total_value=portfolio_value,
        peak_equity=portfolio_value,  # No drawdown
    )

    risk_manager = RiskManager(config=config, portfolio=portfolio)

    # Create signal with size as a fraction of portfolio
    signal_size = portfolio_value * Decimal(str(signal_size_fraction))
    signal = Signal(
        strategy_name="test_strategy",
        symbol="AAPL",
        direction=SignalDirection.LONG,
        confidence=0.8,
        suggested_size=signal_size,
        order_type=OrderType.MARKET,
        metadata={"sector": "Technology"},
    )

    # Run the check
    result = asyncio.run(risk_manager.check_order(signal))

    # Property: if signal size exceeds position limit, it must be rejected
    position_pct = float(signal_size / portfolio_value)
    if position_pct > max_position_pct:
        assert not result.approved, (
            f"Order should be rejected: position {position_pct:.4f} > "
            f"limit {max_position_pct:.4f}"
        )


# ---------------------------------------------------------------------------
# P7: Rate Limit Compliance
# ---------------------------------------------------------------------------


@settings(max_examples=100)
@given(
    num_requests=st.integers(min_value=1, max_value=100),
    max_per_second=st.floats(min_value=10.0, max_value=50.0),
)
def test_p7_message_rate_never_exceeds_50_per_sec(
    num_requests: int,
    max_per_second: float,
) -> None:
    """P7: message rate never exceeds 50/sec.

    **Validates: Requirements 2.7**

    The RateLimiter's try_acquire() method must never allow more than
    max_per_second tokens to be consumed in a single burst (without time passing).
    """
    # Use integer burst size to match the RateLimiter implementation
    burst_size = int(max_per_second)
    limiter = RateLimiter(max_per_second=max_per_second, burst_size=burst_size)

    # Try to acquire tokens as fast as possible (no time passing)
    acquired = 0
    for _ in range(num_requests):
        if limiter.try_acquire():
            acquired += 1

    # Property: acquired tokens never exceed burst_size (which is <= 50)
    assert acquired <= burst_size
    # And burst_size is always <= 50 (IBKR limit)
    assert burst_size <= 50
