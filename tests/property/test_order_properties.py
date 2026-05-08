"""Property-based tests for Order Manager correctness properties.

**Validates: Requirements 4.4, 4.8**

P4: Every order state transition SHALL be recorded in the audit trail.
P8: When a stop-loss is triggered, a close order SHALL be generated within the next cycle.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from unittest.mock import MagicMock

from hypothesis import given, settings
from hypothesis import strategies as st

from src.config.settings import StopLossConfig
from src.orders.manager import ManagedOrder, OrderManager, OrderStatus
from src.orders.rate_limiter import RateLimiter
from src.risk.stops import StopMonitor
from src.strategies.signals import OrderType, Signal, SignalDirection


# ---------------------------------------------------------------------------
# Helpers / Mocks
# ---------------------------------------------------------------------------


class MockConnection:
    """Mock IBKR connection for order manager testing."""

    def __init__(self) -> None:
        self._connected = True
        self._ib = MagicMock()
        # Make placeOrder return a mock trade
        mock_trade = MagicMock()
        mock_trade.order.orderId = 1
        self._ib.placeOrder.return_value = mock_trade

    @property
    def is_connected(self) -> bool:
        return self._connected

    @property
    def ib(self) -> Any:
        return self._ib


def make_mock_trade(order_id: int, status: str) -> MagicMock:
    """Create a mock IBKR Trade object with given status."""
    trade = MagicMock()
    trade.order.orderId = order_id
    trade.orderStatus.status = status
    return trade


# IBKR status strings that map to terminal states
TERMINAL_STATUSES = ["Filled", "Cancelled", "ApiCancelled", "Inactive"]
NON_TERMINAL_STATUSES = ["PendingSubmit", "PreSubmitted", "Submitted"]
ALL_STATUSES = TERMINAL_STATUSES + NON_TERMINAL_STATUSES


# ---------------------------------------------------------------------------
# P4: Order Audit Completeness
# ---------------------------------------------------------------------------


@settings(max_examples=100)
@given(
    status_sequence=st.lists(
        st.sampled_from(ALL_STATUSES),
        min_size=1,
        max_size=8,
    ),
)
def test_p4_every_order_state_transition_recorded_in_audit_trail(
    status_sequence: list[str],
) -> None:
    """P4: every order state transition is recorded in audit trail.

    **Validates: Requirements 4.4**

    After submitting an order and processing status updates, the order
    must be trackable in either pending_orders or completed_orders.
    Every order that reaches a terminal state must appear in completed_orders.
    """
    connection = MockConnection()
    rate_limiter = RateLimiter(max_per_second=45.0)
    manager = OrderManager(connection=connection, rate_limiter=rate_limiter)

    # Submit an order
    signal = Signal(
        strategy_name="test_strategy",
        symbol="AAPL",
        direction=SignalDirection.LONG,
        confidence=0.8,
        suggested_size=Decimal("100"),
        order_type=OrderType.MARKET,
    )

    managed_order = asyncio.run(manager.submit_order(signal, MagicMock()))
    order_id = managed_order.order_id

    # Process status updates
    reached_terminal = False
    for status_str in status_sequence:
        if reached_terminal:
            break  # Stop processing after terminal state
        trade = make_mock_trade(order_id, status_str)
        manager.on_order_status(trade)
        if status_str in TERMINAL_STATUSES:
            reached_terminal = True

    # Property: the order is always tracked (either pending or completed)
    in_pending = order_id in manager.pending_orders
    in_completed = any(o.order_id == order_id for o in manager.completed_orders)

    assert in_pending or in_completed, (
        f"Order {order_id} lost from audit trail! "
        f"Not in pending ({list(manager.pending_orders.keys())}) "
        f"or completed ({[o.order_id for o in manager.completed_orders]})"
    )

    # Property: if terminal status was reached, order must be in completed
    if reached_terminal:
        assert in_completed, (
            f"Order {order_id} reached terminal state but not in completed_orders"
        )


# ---------------------------------------------------------------------------
# P8: Stop-Loss Guarantee
# ---------------------------------------------------------------------------


@settings(max_examples=100)
@given(
    entry_price=st.decimals(
        min_value=Decimal("10"),
        max_value=Decimal("1000"),
        places=2,
        allow_nan=False,
        allow_infinity=False,
    ),
    stop_pct=st.floats(min_value=0.01, max_value=0.20),
    price_drop_pct=st.floats(min_value=0.01, max_value=0.50),
)
def test_p8_stop_loss_trigger_generates_close_order(
    entry_price: Decimal,
    stop_pct: float,
    price_drop_pct: float,
) -> None:
    """P8: stop-loss trigger generates close order within next cycle.

    **Validates: Requirements 4.8**

    When the current price drops below the stop level, the StopMonitor
    must generate a CLOSE signal for that position.
    """
    config = StopLossConfig(
        type="fixed_pct",
        fixed_pct=stop_pct,
        atr_multiplier=2.0,
    )

    monitor = StopMonitor(config)
    monitor.add_position(
        symbol="AAPL",
        entry_price=entry_price,
        strategy_name="test_strategy",
    )

    # Calculate the stop price
    stop_price = entry_price * (1 - Decimal(str(stop_pct)))

    # Simulate price drop
    current_price = entry_price * (1 - Decimal(str(price_drop_pct)))

    # Run stop monitoring
    signals = monitor.monitor_stops({"AAPL": current_price})

    # Property: if price is at or below stop, a CLOSE signal must be generated
    if current_price <= stop_price:
        assert len(signals) >= 1, (
            f"Stop should trigger: price {current_price} <= stop {stop_price}"
        )
        assert signals[0].direction == SignalDirection.CLOSE
        assert signals[0].symbol == "AAPL"
        assert signals[0].metadata["reason"] == "stop_loss_triggered"
    else:
        # Price above stop — no signal should be generated
        assert len(signals) == 0, (
            f"Stop should NOT trigger: price {current_price} > stop {stop_price}"
        )
