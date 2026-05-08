"""Unit tests for the Order Manager module.

Tests cover:
- Rate limiting (token bucket algorithm)
- Order submission and state transitions
- Timeout/stale order cancellation
- Partial fill handling
- Rejection callback
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from src.orders.manager import (
    DEFAULT_TIMEOUTS,
    ManagedOrder,
    OrderManager,
    OrderStatus,
    OrderType,
    Signal,
    SignalDirection,
)
from src.orders.rate_limiter import RateLimiter


# ─── Fixtures ────────────────────────────────────────────────────────────────


class FakeIB:
    """Fake IB client for testing order placement."""

    def __init__(self):
        self.placed_orders: list = []
        self._next_id = 100

    def placeOrder(self, contract, order):
        order_id = self._next_id
        self._next_id += 1
        trade = SimpleNamespace(
            order=SimpleNamespace(orderId=order_id),
            orderStatus=SimpleNamespace(status="Submitted", whyHeld=""),
        )
        self.placed_orders.append((contract, order, trade))
        return trade

    def bracketOrder(self, action, quantity, limit_price, take_profit, stop_loss):
        """Return a list of bracket order objects."""
        parent = SimpleNamespace(action=action, totalQuantity=quantity, exchange="SMART")
        tp = SimpleNamespace(action="SELL" if action == "BUY" else "BUY", exchange="SMART")
        sl = SimpleNamespace(action="SELL" if action == "BUY" else "BUY", exchange="SMART")
        return [parent, tp, sl]


class FakeConnection:
    """Fake ConnectionManager implementing the ConnectionProtocol."""

    def __init__(self, connected: bool = True):
        self._connected = connected
        self.ib = FakeIB()

    @property
    def is_connected(self) -> bool:
        return self._connected


def make_signal(
    order_type: OrderType = OrderType.MARKET,
    symbol: str = "AAPL",
    direction: SignalDirection = SignalDirection.LONG,
    quantity: Decimal = Decimal("100"),
    limit_price: Decimal | None = None,
    stop_price: Decimal | None = None,
) -> Signal:
    """Helper to create a test Signal."""
    return Signal(
        strategy_name="test_strategy",
        symbol=symbol,
        direction=direction,
        confidence=0.8,
        suggested_size=quantity,
        order_type=order_type,
        limit_price=limit_price,
        stop_price=stop_price,
    )


def make_contract(symbol: str = "AAPL") -> SimpleNamespace:
    """Helper to create a fake IBKR Contract."""
    return SimpleNamespace(symbol=symbol, exchange="SMART")


# ─── Rate Limiter Tests ──────────────────────────────────────────────────────


class TestRateLimiter:
    """Tests for the token bucket rate limiter."""

    def test_initial_tokens_at_burst_size(self):
        """Rate limiter starts with full token bucket."""
        rl = RateLimiter(max_per_second=45.0, burst_size=45)
        assert rl.available_tokens >= 44.0  # Allow small float drift

    def test_try_acquire_succeeds_when_tokens_available(self):
        """try_acquire returns True when tokens are available."""
        rl = RateLimiter(max_per_second=10.0, burst_size=10)
        assert rl.try_acquire() is True

    def test_try_acquire_fails_when_exhausted(self):
        """try_acquire returns False when all tokens consumed."""
        rl = RateLimiter(max_per_second=5.0, burst_size=5)
        # Consume all tokens
        for _ in range(5):
            assert rl.try_acquire() is True
        # Next should fail
        assert rl.try_acquire() is False

    @pytest.mark.asyncio
    async def test_acquire_waits_when_no_tokens(self):
        """acquire() blocks until a token is available."""
        rl = RateLimiter(max_per_second=10.0, burst_size=2)
        # Consume all tokens
        await rl.acquire()
        await rl.acquire()

        # Next acquire should wait
        start = time.monotonic()
        await rl.acquire()
        elapsed = time.monotonic() - start

        # Should have waited approximately 0.1 seconds (1/10 per second)
        assert elapsed >= 0.05  # Allow some tolerance

    def test_tokens_refill_over_time(self):
        """Tokens refill based on elapsed time."""
        rl = RateLimiter(max_per_second=100.0, burst_size=10)
        # Consume all tokens
        for _ in range(10):
            rl.try_acquire()
        assert rl.try_acquire() is False

        # Simulate time passing by manipulating internal state
        rl._last_refill = time.monotonic() - 0.1  # 100/sec * 0.1s = 10 tokens
        assert rl.try_acquire() is True

    def test_tokens_capped_at_burst_size(self):
        """Tokens never exceed burst_size even after long idle."""
        rl = RateLimiter(max_per_second=100.0, burst_size=5)
        # Simulate long idle
        rl._last_refill = time.monotonic() - 100.0
        rl._refill()
        assert rl._tokens <= 5.0

    def test_reset_restores_full_capacity(self):
        """reset() fills the bucket back to burst_size."""
        rl = RateLimiter(max_per_second=10.0, burst_size=10)
        for _ in range(10):
            rl.try_acquire()
        assert rl.try_acquire() is False
        rl.reset()
        assert rl.try_acquire() is True

    def test_default_burst_size_equals_max_per_second(self):
        """When burst_size not specified, defaults to max_per_second."""
        rl = RateLimiter(max_per_second=45.0)
        assert rl._burst_size == 45

    @pytest.mark.asyncio
    async def test_concurrent_acquire_is_safe(self):
        """Multiple concurrent acquire calls don't over-consume tokens."""
        rl = RateLimiter(max_per_second=100.0, burst_size=5)

        # Launch 10 concurrent acquires with only 5 tokens
        results = await asyncio.gather(
            *[rl.acquire() for _ in range(10)],
            return_exceptions=True,
        )
        # All should complete (some after waiting)
        assert all(r is None for r in results)


# ─── Order Manager Tests ─────────────────────────────────────────────────────


class TestOrderSubmission:
    """Tests for order submission."""

    @pytest.mark.asyncio
    async def test_submit_market_order(self):
        """Market order is submitted and tracked."""
        conn = FakeConnection()
        mgr = OrderManager(connection=conn)

        signal = make_signal(order_type=OrderType.MARKET)
        contract = make_contract()

        managed = await mgr.submit_order(signal, contract)

        assert managed.status == OrderStatus.SUBMITTED
        assert managed.symbol == "AAPL"
        assert managed.direction == SignalDirection.LONG
        assert managed.order_type == OrderType.MARKET
        assert managed.quantity == Decimal("100")
        assert managed.order_id in mgr.pending_orders

    @pytest.mark.asyncio
    async def test_submit_limit_order(self):
        """Limit order includes limit price."""
        conn = FakeConnection()
        mgr = OrderManager(connection=conn)

        signal = make_signal(
            order_type=OrderType.LIMIT,
            limit_price=Decimal("150.50"),
        )
        contract = make_contract()

        managed = await mgr.submit_order(signal, contract)

        assert managed.status == OrderStatus.SUBMITTED
        assert managed.limit_price == Decimal("150.50")

    @pytest.mark.asyncio
    async def test_submit_stop_order(self):
        """Stop order includes stop price."""
        conn = FakeConnection()
        mgr = OrderManager(connection=conn)

        signal = make_signal(
            order_type=OrderType.STOP,
            stop_price=Decimal("145.00"),
        )
        contract = make_contract()

        managed = await mgr.submit_order(signal, contract)

        assert managed.status == OrderStatus.SUBMITTED
        assert managed.stop_price == Decimal("145.00")

    @pytest.mark.asyncio
    async def test_submit_stop_limit_order(self):
        """Stop-limit order includes both prices."""
        conn = FakeConnection()
        mgr = OrderManager(connection=conn)

        signal = make_signal(
            order_type=OrderType.STOP_LIMIT,
            limit_price=Decimal("148.00"),
            stop_price=Decimal("145.00"),
        )
        contract = make_contract()

        managed = await mgr.submit_order(signal, contract)

        assert managed.status == OrderStatus.SUBMITTED
        assert managed.limit_price == Decimal("148.00")
        assert managed.stop_price == Decimal("145.00")

    @pytest.mark.asyncio
    async def test_submit_trailing_stop_order(self):
        """Trailing stop order is submitted."""
        conn = FakeConnection()
        mgr = OrderManager(connection=conn)

        signal = make_signal(
            order_type=OrderType.TRAILING_STOP,
            stop_price=Decimal("2.0"),  # trailing percent
        )
        contract = make_contract()

        managed = await mgr.submit_order(signal, contract)

        assert managed.status == OrderStatus.SUBMITTED
        assert managed.order_type == OrderType.TRAILING_STOP

    @pytest.mark.asyncio
    async def test_submit_bracket_order(self):
        """Bracket order is submitted."""
        conn = FakeConnection()
        mgr = OrderManager(connection=conn)

        signal = make_signal(
            order_type=OrderType.BRACKET,
            limit_price=Decimal("150.00"),
            stop_price=Decimal("145.00"),
        )
        contract = make_contract()

        managed = await mgr.submit_order(signal, contract)

        assert managed.status == OrderStatus.SUBMITTED
        assert managed.order_type == OrderType.BRACKET

    @pytest.mark.asyncio
    async def test_submit_raises_when_disconnected(self):
        """Submitting when disconnected raises RuntimeError."""
        conn = FakeConnection(connected=False)
        mgr = OrderManager(connection=conn)

        signal = make_signal()
        contract = make_contract()

        with pytest.raises(RuntimeError, match="not connected"):
            await mgr.submit_order(signal, contract)

    @pytest.mark.asyncio
    async def test_exchange_override(self):
        """Exchange can be overridden from default SMART routing."""
        conn = FakeConnection()
        mgr = OrderManager(connection=conn)

        signal = make_signal()
        contract = make_contract()

        managed = await mgr.submit_order(signal, contract, exchange="NYSE")

        assert managed.exchange == "NYSE"

    @pytest.mark.asyncio
    async def test_default_smart_routing(self):
        """Default exchange is SMART routing."""
        conn = FakeConnection()
        mgr = OrderManager(connection=conn)

        signal = make_signal()
        contract = make_contract()

        managed = await mgr.submit_order(signal, contract)

        assert managed.exchange == "SMART"

    @pytest.mark.asyncio
    async def test_sell_direction_for_short(self):
        """SHORT signal results in SELL action."""
        conn = FakeConnection()
        mgr = OrderManager(connection=conn)

        signal = make_signal(direction=SignalDirection.SHORT)
        contract = make_contract()

        managed = await mgr.submit_order(signal, contract)

        assert managed.direction == SignalDirection.SHORT
        # Verify the placed order used SELL action
        assert len(conn.ib.placed_orders) == 1
        _, order, _ = conn.ib.placed_orders[0]
        assert order.action == "SELL"


# ─── Order Status Transition Tests ───────────────────────────────────────────


class TestOrderStatusTransitions:
    """Tests for order state machine transitions."""

    @pytest.mark.asyncio
    async def test_submitted_to_accepted(self):
        """Order transitions from SUBMITTED to ACCEPTED."""
        conn = FakeConnection()
        mgr = OrderManager(connection=conn)

        signal = make_signal()
        contract = make_contract()
        managed = await mgr.submit_order(signal, contract)
        order_id = managed.order_id

        # Simulate IBKR status update
        trade = SimpleNamespace(
            order=SimpleNamespace(orderId=order_id),
            orderStatus=SimpleNamespace(status="Submitted", whyHeld=""),
        )
        mgr.on_order_status(trade)

        assert mgr.pending_orders[order_id].status == OrderStatus.ACCEPTED

    @pytest.mark.asyncio
    async def test_accepted_to_filled(self):
        """Order transitions from ACCEPTED to FILLED."""
        conn = FakeConnection()
        mgr = OrderManager(connection=conn)

        signal = make_signal()
        contract = make_contract()
        managed = await mgr.submit_order(signal, contract)
        order_id = managed.order_id

        # Simulate fill
        trade = SimpleNamespace(
            order=SimpleNamespace(orderId=order_id),
            orderStatus=SimpleNamespace(status="Filled", whyHeld=""),
        )
        mgr.on_order_status(trade)

        # Should be moved to completed
        assert order_id not in mgr.pending_orders
        assert any(o.order_id == order_id and o.status == OrderStatus.FILLED for o in mgr.completed_orders)

    @pytest.mark.asyncio
    async def test_submitted_to_cancelled(self):
        """Order transitions to CANCELLED."""
        conn = FakeConnection()
        mgr = OrderManager(connection=conn)

        signal = make_signal()
        contract = make_contract()
        managed = await mgr.submit_order(signal, contract)
        order_id = managed.order_id

        trade = SimpleNamespace(
            order=SimpleNamespace(orderId=order_id),
            orderStatus=SimpleNamespace(status="Cancelled", whyHeld=""),
        )
        mgr.on_order_status(trade)

        assert order_id not in mgr.pending_orders
        assert any(o.order_id == order_id and o.status == OrderStatus.CANCELLED for o in mgr.completed_orders)

    @pytest.mark.asyncio
    async def test_submitted_to_rejected(self):
        """Order transitions to REJECTED with reason."""
        conn = FakeConnection()
        rejection_callback = MagicMock()
        mgr = OrderManager(connection=conn, on_rejection=rejection_callback)

        signal = make_signal()
        contract = make_contract()
        managed = await mgr.submit_order(signal, contract)
        order_id = managed.order_id

        trade = SimpleNamespace(
            order=SimpleNamespace(orderId=order_id),
            orderStatus=SimpleNamespace(status="Inactive", whyHeld="Insufficient margin"),
        )
        mgr.on_order_status(trade)

        assert order_id not in mgr.pending_orders
        rejected = next(o for o in mgr.completed_orders if o.order_id == order_id)
        assert rejected.status == OrderStatus.REJECTED
        assert rejected.rejection_reason == "Insufficient margin"
        rejection_callback.assert_called_once()

    @pytest.mark.asyncio
    async def test_unknown_order_id_ignored(self):
        """Status update for unknown order ID is silently ignored."""
        conn = FakeConnection()
        mgr = OrderManager(connection=conn)

        trade = SimpleNamespace(
            order=SimpleNamespace(orderId=9999),
            orderStatus=SimpleNamespace(status="Filled", whyHeld=""),
        )
        # Should not raise
        mgr.on_order_status(trade)


# ─── Fill Handling Tests ─────────────────────────────────────────────────────


class TestFillHandling:
    """Tests for fill event processing."""

    @pytest.mark.asyncio
    async def test_full_fill(self):
        """Complete fill updates quantity and moves to completed."""
        conn = FakeConnection()
        fill_callback = MagicMock()
        mgr = OrderManager(connection=conn, on_fill=fill_callback)

        signal = make_signal(quantity=Decimal("100"))
        contract = make_contract()
        managed = await mgr.submit_order(signal, contract)
        order_id = managed.order_id

        # Simulate full fill
        trade = SimpleNamespace(order=SimpleNamespace(orderId=order_id))
        fill = SimpleNamespace(
            execution=SimpleNamespace(shares=100.0, price=150.25),
        )
        mgr.on_fill(trade, fill)

        # Should be completed
        assert order_id not in mgr.pending_orders
        filled = next(o for o in mgr.completed_orders if o.order_id == order_id)
        assert filled.status == OrderStatus.FILLED
        assert filled.filled_quantity == Decimal("100.0")
        assert filled.avg_fill_price == Decimal("150.25")
        fill_callback.assert_called_once()

    @pytest.mark.asyncio
    async def test_partial_fill(self):
        """Partial fill updates quantity but keeps order pending."""
        conn = FakeConnection()
        mgr = OrderManager(connection=conn)

        signal = make_signal(quantity=Decimal("100"))
        contract = make_contract()
        managed = await mgr.submit_order(signal, contract)
        order_id = managed.order_id

        # Simulate partial fill (50 of 100)
        trade = SimpleNamespace(order=SimpleNamespace(orderId=order_id))
        fill = SimpleNamespace(
            execution=SimpleNamespace(shares=50.0, price=150.00),
        )
        mgr.on_fill(trade, fill)

        # Should still be pending
        assert order_id in mgr.pending_orders
        pending = mgr.pending_orders[order_id]
        assert pending.status == OrderStatus.PARTIALLY_FILLED
        assert pending.filled_quantity == Decimal("50.0")
        assert pending.avg_fill_price == Decimal("150.0")

    @pytest.mark.asyncio
    async def test_multiple_partial_fills(self):
        """Multiple partial fills accumulate correctly with weighted avg price."""
        conn = FakeConnection()
        mgr = OrderManager(connection=conn)

        signal = make_signal(quantity=Decimal("100"))
        contract = make_contract()
        managed = await mgr.submit_order(signal, contract)
        order_id = managed.order_id

        # First partial fill: 40 @ 150.00
        trade = SimpleNamespace(order=SimpleNamespace(orderId=order_id))
        fill1 = SimpleNamespace(execution=SimpleNamespace(shares=40.0, price=150.00))
        mgr.on_fill(trade, fill1)

        assert mgr.pending_orders[order_id].filled_quantity == Decimal("40.0")
        assert mgr.pending_orders[order_id].avg_fill_price == Decimal("150.0")

        # Second partial fill: 60 @ 151.00
        fill2 = SimpleNamespace(execution=SimpleNamespace(shares=60.0, price=151.00))
        mgr.on_fill(trade, fill2)

        # Should now be fully filled
        assert order_id not in mgr.pending_orders
        filled = next(o for o in mgr.completed_orders if o.order_id == order_id)
        assert filled.filled_quantity == Decimal("100.0")
        # Weighted avg: (40*150 + 60*151) / 100 = 150.60
        assert filled.avg_fill_price == Decimal("150.6")

    @pytest.mark.asyncio
    async def test_fill_for_unknown_order_ignored(self):
        """Fill event for unknown order ID is silently ignored."""
        conn = FakeConnection()
        mgr = OrderManager(connection=conn)

        trade = SimpleNamespace(order=SimpleNamespace(orderId=9999))
        fill = SimpleNamespace(execution=SimpleNamespace(shares=100.0, price=150.00))

        # Should not raise
        mgr.on_fill(trade, fill)


# ─── Cancellation & Timeout Tests ────────────────────────────────────────────


class TestCancellation:
    """Tests for order cancellation and stale order handling."""

    @pytest.mark.asyncio
    async def test_cancel_pending_order(self):
        """Cancelling a pending order moves it to completed."""
        conn = FakeConnection()
        mgr = OrderManager(connection=conn)

        signal = make_signal()
        contract = make_contract()
        managed = await mgr.submit_order(signal, contract)
        order_id = managed.order_id

        result = await mgr.cancel_order(order_id)

        assert result is True
        assert order_id not in mgr.pending_orders
        cancelled = next(o for o in mgr.completed_orders if o.order_id == order_id)
        assert cancelled.status == OrderStatus.CANCELLED

    @pytest.mark.asyncio
    async def test_cancel_nonexistent_order(self):
        """Cancelling a non-existent order returns False."""
        conn = FakeConnection()
        mgr = OrderManager(connection=conn)

        result = await mgr.cancel_order(9999)
        assert result is False

    @pytest.mark.asyncio
    async def test_cancel_stale_market_orders(self):
        """Market orders exceeding 60s timeout are cancelled."""
        conn = FakeConnection()
        mgr = OrderManager(connection=conn)

        signal = make_signal(order_type=OrderType.MARKET)
        contract = make_contract()
        managed = await mgr.submit_order(signal, contract)
        order_id = managed.order_id

        # Backdate the submission time to simulate staleness
        mgr._pending_orders[order_id].submitted_at = datetime.now(timezone.utc) - timedelta(seconds=90)

        cancelled = await mgr.cancel_stale_orders()

        assert order_id in cancelled
        assert order_id not in mgr.pending_orders

    @pytest.mark.asyncio
    async def test_cancel_stale_limit_orders(self):
        """Limit orders exceeding 5min timeout are cancelled."""
        conn = FakeConnection()
        mgr = OrderManager(connection=conn)

        signal = make_signal(order_type=OrderType.LIMIT, limit_price=Decimal("150.00"))
        contract = make_contract()
        managed = await mgr.submit_order(signal, contract)
        order_id = managed.order_id

        # Backdate to exceed 5 minute timeout
        mgr._pending_orders[order_id].submitted_at = datetime.now(timezone.utc) - timedelta(minutes=6)

        cancelled = await mgr.cancel_stale_orders()

        assert order_id in cancelled

    @pytest.mark.asyncio
    async def test_fresh_orders_not_cancelled(self):
        """Orders within timeout are not cancelled."""
        conn = FakeConnection()
        mgr = OrderManager(connection=conn)

        signal = make_signal(order_type=OrderType.MARKET)
        contract = make_contract()
        await mgr.submit_order(signal, contract)

        # Don't backdate - order is fresh
        cancelled = await mgr.cancel_stale_orders()

        assert cancelled == []

    @pytest.mark.asyncio
    async def test_default_timeout_market_60s(self):
        """Market orders get 60 second default timeout."""
        assert DEFAULT_TIMEOUTS[OrderType.MARKET] == timedelta(seconds=60)

    @pytest.mark.asyncio
    async def test_default_timeout_limit_5min(self):
        """Limit orders get 5 minute default timeout."""
        assert DEFAULT_TIMEOUTS[OrderType.LIMIT] == timedelta(minutes=5)


# ─── Rate Limiting Integration Tests ─────────────────────────────────────────


class TestRateLimitingIntegration:
    """Tests verifying rate limiting is applied during order operations."""

    @pytest.mark.asyncio
    async def test_submit_consumes_rate_limit_token(self):
        """Each order submission consumes a rate limit token."""
        conn = FakeConnection()
        rl = RateLimiter(max_per_second=45.0, burst_size=3)
        mgr = OrderManager(connection=conn, rate_limiter=rl)

        contract = make_contract()

        # Submit 3 orders (should consume all burst tokens)
        for _ in range(3):
            signal = make_signal()
            await mgr.submit_order(signal, contract)

        # Verify tokens are consumed
        assert rl.try_acquire() is False

    @pytest.mark.asyncio
    async def test_rate_limiter_45_per_second_default(self):
        """Default rate limiter is configured at 45 msg/sec."""
        conn = FakeConnection()
        mgr = OrderManager(connection=conn)

        assert mgr._rate_limiter.max_per_second == 45.0
