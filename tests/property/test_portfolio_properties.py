"""Property-based tests for Portfolio correctness properties.

**Validates: Requirements 3.3, 3.10**

P3: Internal position state SHALL always match IBKR account state after reconciliation.
P10: Critical events SHALL always result in at least one notification delivery attempt.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from hypothesis import given, settings
from hypothesis import strategies as st

from src.alerts.service import (
    Alert,
    AlertChannel,
    AlertConfig,
    AlertEventType,
    AlertPriority,
    AlertService,
)
from src.portfolio.monitor import PortfolioMonitor, Position


# ---------------------------------------------------------------------------
# Helpers / Mocks
# ---------------------------------------------------------------------------


class MockIBKRPosition:
    """Mock IBKR position object."""

    def __init__(self, symbol: str, quantity: float, avg_cost: float) -> None:
        self.contract = MagicMock()
        self.contract.symbol = symbol
        self.contract.secType = "STK"
        self.position = quantity
        self.avgCost = avg_cost


class MockConnection:
    """Mock connection that returns configurable positions."""

    def __init__(self, positions: list[MockIBKRPosition]) -> None:
        self._positions = positions
        self._ib = MagicMock()
        self._ib.positions.return_value = positions

    @property
    def ib(self) -> Any:
        return self._ib

    @property
    def is_connected(self) -> bool:
        return True


class TrackingChannel(AlertChannel):
    """Alert channel that tracks all delivery attempts."""

    def __init__(self) -> None:
        self._deliveries: list[Alert] = []

    @property
    def name(self) -> str:
        return "tracking"

    async def deliver(self, alert: Alert) -> bool:
        self._deliveries.append(alert)
        return True

    @property
    def deliveries(self) -> list[Alert]:
        return self._deliveries


# Custom strategies for position generation
position_entry = st.tuples(
    st.sampled_from(["AAPL", "MSFT", "GOOGL", "AMZN", "TSLA", "META", "NVDA"]),
    st.floats(min_value=-1000, max_value=1000, allow_nan=False, allow_infinity=False),
    st.floats(min_value=10.0, max_value=5000.0, allow_nan=False, allow_infinity=False),
)


# ---------------------------------------------------------------------------
# P3: Position Consistency
# ---------------------------------------------------------------------------


@settings(max_examples=100)
@given(
    ibkr_positions=st.lists(
        position_entry,
        min_size=0,
        max_size=10,
        unique_by=lambda x: x[0],  # Unique symbols
    ),
)
def test_p3_internal_position_matches_ibkr_after_reconciliation(
    ibkr_positions: list[tuple[str, float, float]],
) -> None:
    """P3: internal position state matches IBKR after reconciliation.

    **Validates: Requirements 3.3**

    After calling sync_positions(), the internal position state must
    exactly match what IBKR reports — same symbols, quantities, and costs.
    """
    # Create mock IBKR positions
    mock_positions = [
        MockIBKRPosition(symbol=sym, quantity=qty, avg_cost=cost)
        for sym, qty, cost in ibkr_positions
    ]

    connection = MockConnection(mock_positions)
    monitor = PortfolioMonitor(connection=connection)

    # Run reconciliation
    asyncio.run(monitor.sync_positions())

    # Property: internal positions match IBKR positions exactly
    internal_positions = monitor.positions

    # Same set of symbols
    ibkr_symbols = {sym for sym, _, _ in ibkr_positions}
    internal_symbols = set(internal_positions.keys())
    assert internal_symbols == ibkr_symbols, (
        f"Symbol mismatch: internal={internal_symbols}, ibkr={ibkr_symbols}"
    )

    # Same quantities and costs for each symbol
    for sym, qty, cost in ibkr_positions:
        pos = internal_positions[sym]
        assert pos.quantity == Decimal(str(qty)), (
            f"Quantity mismatch for {sym}: internal={pos.quantity}, ibkr={qty}"
        )
        assert pos.avg_entry_price == Decimal(str(cost)), (
            f"Cost mismatch for {sym}: internal={pos.avg_entry_price}, ibkr={cost}"
        )


# ---------------------------------------------------------------------------
# P10: Alert Delivery
# ---------------------------------------------------------------------------


@settings(max_examples=100)
@given(
    event_type=st.sampled_from(list(AlertEventType)),
    title=st.text(min_size=1, max_size=50),
    message=st.text(min_size=1, max_size=200),
)
def test_p10_critical_events_always_result_in_notification_delivery_attempt(
    event_type: AlertEventType,
    title: str,
    message: str,
) -> None:
    """P10: critical events always result in notification delivery attempt.

    **Validates: Requirements 3.10**

    When send_critical() is called, at least one delivery attempt must be
    made regardless of rate limiting state.
    """
    # Configure alert service with a tracking channel
    config = AlertConfig(
        routing={event_type.value: ["tracking"]},
    )
    service = AlertService(config)
    channel = TrackingChannel()
    service.register_channel(channel)

    # Create a critical alert
    alert = Alert(
        event_type=event_type,
        priority=AlertPriority.CRITICAL,
        title=title,
        message=message,
        metadata={"test": True},
        timestamp=datetime.now(timezone.utc),
    )

    # Send multiple regular alerts first to trigger rate limiting
    async def _run_alerts() -> None:
        for _ in range(5):
            regular_alert = Alert(
                event_type=event_type,
                priority=AlertPriority.LOW,
                title="regular",
                message="regular message",
            )
            await service.send(regular_alert)

    asyncio.run(_run_alerts())

    # Clear delivery tracking to isolate critical alert
    initial_count = len(channel.deliveries)

    # Send critical alert — must bypass rate limits
    asyncio.run(service.send_critical(alert))

    # Property: at least one delivery attempt was made for the critical alert
    new_deliveries = channel.deliveries[initial_count:]
    assert len(new_deliveries) >= 1, (
        f"Critical alert must result in at least one delivery attempt, "
        f"got {len(new_deliveries)}"
    )

    # Property: the delivered alert matches what was sent
    delivered = new_deliveries[0]
    assert delivered.event_type == event_type
    assert delivered.priority == AlertPriority.CRITICAL
    assert delivered.title == title
