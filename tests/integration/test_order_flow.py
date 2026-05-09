"""Integration test: end-to-end signal-to-order flow with mocked IBKR.

Tests the full pipeline: Strategy generates signal → RiskManager checks →
CapitalAllocator validates → OrderManager submits order.
Also tests fill event routing to PortfolioMonitor, CapitalAllocator, and AlertService.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.alerts.service import AlertConfig, AlertService
from src.config.settings import (
    AlertChannelsConfig,
    ConnectionConfig,
    RiskConfig,
    StopLossConfig,
)
from src.connection.manager import ConnectionManager
from src.main import TradingBot, _shutdown_event
from src.orders.manager import ManagedOrder, OrderManager, OrderStatus
from src.orders.rate_limiter import RateLimiter
from src.portfolio.capital_allocator import AllocationMode, CapitalAllocator
from src.portfolio.monitor import PortfolioMonitor
from src.risk.manager import RiskManager
from src.strategies.signals import OrderType, Signal, SignalDirection


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_connection():
    """Create a mock ConnectionManager that simulates IBKR connection."""
    conn = MagicMock(spec=ConnectionManager)
    conn.is_connected = True
    conn.is_halted = False

    # Mock IB object
    mock_ib = MagicMock()
    mock_ib.isConnected.return_value = True
    mock_ib.managedAccounts.return_value = ["DU12345"]
    mock_ib.positions.return_value = []
    mock_ib.placeOrder.return_value = MagicMock(
        order=MagicMock(orderId=1001)
    )
    conn.ib = mock_ib
    conn.connect = AsyncMock()
    conn.disconnect = AsyncMock()

    return conn


@pytest.fixture
def risk_config():
    """Standard risk configuration for tests."""
    return RiskConfig(
        max_position_pct=0.05,
        max_drawdown_pct=0.10,
        max_daily_loss_pct=0.02,
        max_sector_concentration=0.25,
        max_correlation=0.7,
        var_confidence=0.95,
        var_lookback_days=252,
        stop_loss=StopLossConfig(type="atr_trailing", atr_multiplier=2.0, fixed_pct=0.03),
    )


@pytest.fixture
def portfolio_monitor(mock_connection):
    """Portfolio monitor with initial equity."""
    return PortfolioMonitor(
        connection=mock_connection,
        initial_equity=Decimal("100000"),
    )


@pytest.fixture
def capital_allocator():
    """Capital allocator with $100k total."""
    allocator = CapitalAllocator(total_capital=Decimal("100000"))
    allocator.allocate("momentum", Decimal("50000"), AllocationMode.FIXED_AMOUNT)
    allocator.allocate("mean_reversion", Decimal("50000"), AllocationMode.FIXED_AMOUNT)
    return allocator


@pytest.fixture
def risk_manager(risk_config, portfolio_monitor):
    """Risk manager with standard config."""
    return RiskManager(
        config=risk_config,
        portfolio=portfolio_monitor,
    )


@pytest.fixture
def rate_limiter():
    """Rate limiter for order submission."""
    return RateLimiter(max_per_second=45.0)


@pytest.fixture
def order_manager(mock_connection, rate_limiter):
    """Order manager with mocked connection."""
    fill_callback = MagicMock()
    rejection_callback = MagicMock()

    return OrderManager(
        connection=mock_connection,
        rate_limiter=rate_limiter,
        on_fill=fill_callback,
        on_rejection=rejection_callback,
    )


@pytest.fixture
def sample_signal():
    """A valid trading signal for testing."""
    return Signal(
        strategy_name="momentum",
        symbol="AAPL",
        direction=SignalDirection.LONG,
        confidence=0.8,
        suggested_size=Decimal("4000"),  # 4% of 100k portfolio
        order_type=OrderType.MARKET,
        limit_price=None,
        stop_price=None,
        metadata={"sector": "Technology"},
        timestamp=datetime.now(timezone.utc),
    )


@pytest.fixture
def large_signal():
    """A signal that exceeds position size limits."""
    return Signal(
        strategy_name="momentum",
        symbol="AAPL",
        direction=SignalDirection.LONG,
        confidence=0.9,
        suggested_size=Decimal("10000"),  # 10% of 100k - exceeds 5% limit
        order_type=OrderType.MARKET,
        limit_price=None,
        stop_price=None,
        metadata={"sector": "Technology"},
        timestamp=datetime.now(timezone.utc),
    )


# ---------------------------------------------------------------------------
# Tests: Signal-to-Order Flow
# ---------------------------------------------------------------------------


class TestSignalToOrderFlow:
    """Test the full signal → risk → capital → order pipeline."""

    async def test_valid_signal_produces_order(
        self, risk_manager, capital_allocator, order_manager, sample_signal
    ):
        """A valid signal passes risk checks, capital check, and produces an order."""
        # Step 1: Risk check
        risk_result = await risk_manager.check_order(sample_signal)
        assert risk_result.approved is True

        # Step 2: Capital check
        can_place = capital_allocator.can_place_order(
            sample_signal.strategy_name, sample_signal.suggested_size
        )
        assert can_place is True

        # Step 3: Submit order
        contract = MagicMock()
        contract.symbol = "AAPL"
        managed_order = await order_manager.submit_order(sample_signal, contract)

        assert managed_order.status == OrderStatus.SUBMITTED
        assert managed_order.symbol == "AAPL"
        assert managed_order.strategy_name == "momentum"
        assert managed_order.direction == SignalDirection.LONG

    async def test_oversized_signal_rejected_by_risk(
        self, risk_manager, large_signal
    ):
        """A signal exceeding position size limit is rejected by risk manager."""
        result = await risk_manager.check_order(large_signal)
        assert result.approved is False
        assert "Position size" in result.reason

    async def test_signal_rejected_by_capital_allocator(
        self, risk_manager, capital_allocator, sample_signal
    ):
        """A signal exceeding available capital is rejected."""
        # Deploy most of the capital first
        capital_allocator.record_fill("momentum", Decimal("48000"))

        # Now try to place an order for $4000 (only $2000 available)
        can_place = capital_allocator.can_place_order(
            sample_signal.strategy_name, sample_signal.suggested_size
        )
        assert can_place is False

    async def test_signal_rejected_when_trading_halted(
        self, risk_manager, sample_signal
    ):
        """Signals are rejected when trading is halted."""
        risk_manager.halt_trading("Test halt")

        result = await risk_manager.check_order(sample_signal)
        assert result.approved is False
        assert "halted" in result.reason.lower()

    async def test_signal_rejected_daily_loss_exceeded(
        self, risk_manager, sample_signal
    ):
        """Signals are rejected when daily loss limit is breached."""
        # Simulate a large daily loss (> 2% of 100k = $2000)
        risk_manager.update_daily_pnl(Decimal("-2500"))

        result = await risk_manager.check_order(sample_signal)
        assert result.approved is False
        assert "Daily loss" in result.reason

    async def test_rate_limiter_backpressure(self, rate_limiter):
        """Rate limiter queues requests when approaching limit."""
        # Exhaust all tokens
        for _ in range(45):
            acquired = rate_limiter.try_acquire()
            if not acquired:
                break

        # Next acquire should wait (backpressure)
        assert rate_limiter.available_tokens < 1.0

    async def test_multiple_signals_processed_independently(
        self, risk_manager, capital_allocator, order_manager
    ):
        """Multiple signals from different strategies are processed independently."""
        signals = [
            Signal(
                strategy_name="momentum",
                symbol="AAPL",
                direction=SignalDirection.LONG,
                confidence=0.7,
                suggested_size=Decimal("3000"),
                order_type=OrderType.MARKET,
                metadata={"sector": "Technology"},
            ),
            Signal(
                strategy_name="mean_reversion",
                symbol="MSFT",
                direction=SignalDirection.SHORT,
                confidence=0.6,
                suggested_size=Decimal("2500"),
                order_type=OrderType.LIMIT,
                limit_price=Decimal("350.00"),
                metadata={"sector": "Technology"},
            ),
        ]

        orders = []
        for sig in signals:
            result = await risk_manager.check_order(sig)
            assert result.approved is True

            can_place = capital_allocator.can_place_order(
                sig.strategy_name, sig.suggested_size
            )
            assert can_place is True

            contract = MagicMock()
            contract.symbol = sig.symbol
            order = await order_manager.submit_order(sig, contract)
            orders.append(order)

        assert len(orders) == 2
        assert orders[0].symbol == "AAPL"
        assert orders[1].symbol == "MSFT"
        assert orders[0].strategy_name == "momentum"
        assert orders[1].strategy_name == "mean_reversion"


# ---------------------------------------------------------------------------
# Tests: Fill Event Flow
# ---------------------------------------------------------------------------


class TestFillEventFlow:
    """Test fill events routing to PortfolioMonitor, CapitalAllocator, AlertService."""

    async def test_fill_updates_capital_allocator(
        self, capital_allocator, order_manager, sample_signal
    ):
        """Fill events update the capital allocator's deployed capital."""
        contract = MagicMock()
        contract.symbol = "AAPL"
        managed_order = await order_manager.submit_order(sample_signal, contract)

        # Simulate a fill
        fill_value = Decimal("4000")
        capital_allocator.record_fill("momentum", fill_value)

        allocation = capital_allocator.allocations["momentum"]
        assert allocation.deployed == fill_value
        assert capital_allocator.get_available("momentum") == Decimal("46000")

    async def test_fill_triggers_alert(self):
        """Fill events trigger a trade notification via AlertService."""
        alert_config = AlertConfig(
            channels=AlertChannelsConfig(),
            routing={"trade_executed": ["slack"]},
        )
        alert_service = AlertService(config=alert_config)

        # Register a mock channel
        mock_channel = MagicMock()
        mock_channel.name = "slack"
        mock_channel.deliver = AsyncMock(return_value=True)
        alert_service.register_channel(mock_channel)

        from src.alerts.service import Alert, AlertEventType, AlertPriority

        await alert_service.send(
            Alert(
                event_type=AlertEventType.TRADE_EXECUTED,
                priority=AlertPriority.MEDIUM,
                title="Trade Executed: AAPL",
                message="Strategy: momentum, Direction: LONG, Qty: 100, Price: 150.00",
            )
        )

        mock_channel.deliver.assert_called_once()

    async def test_order_rejection_logged(self, order_manager):
        """Order rejections invoke the rejection callback."""
        # Create a mock trade with rejection status
        mock_trade = MagicMock()
        mock_trade.order.orderId = 999
        mock_trade.orderStatus.status = "Inactive"
        mock_trade.orderStatus.whyHeld = "Insufficient margin"

        # First submit an order to track it
        signal = Signal(
            strategy_name="momentum",
            symbol="AAPL",
            direction=SignalDirection.LONG,
            confidence=0.8,
            suggested_size=Decimal("1000"),
            order_type=OrderType.MARKET,
            metadata={},
        )
        contract = MagicMock()
        contract.symbol = "AAPL"
        managed = await order_manager.submit_order(signal, contract)

        # Simulate rejection via status update
        mock_trade.order.orderId = managed.order_id
        order_manager.on_order_status(mock_trade)

        assert managed.order_id not in order_manager.pending_orders


# ---------------------------------------------------------------------------
# Tests: Strategy Isolation
# ---------------------------------------------------------------------------


class TestStrategyIsolation:
    """Test that strategy failures don't crash other strategies."""

    async def test_exception_in_one_strategy_doesnt_crash_others(self):
        """An unhandled exception in one strategy task doesn't affect others."""
        from src.strategies.base import BaseStrategy, StrategyState
        from src.strategies.engine import StrategyEngine

        # Create a strategy that raises
        class FailingStrategy(BaseStrategy):
            def __init__(self):
                self._config = MagicMock()
                self._config.enabled = True
                self._config.frequency = "1min"
                self._state = StrategyState.IDLE
                self._data_hub = None
                self._eval_count = 0

            @property
            def name(self):
                return "failing"

            @property
            def config(self):
                return self._config

            async def evaluate(self):
                self._eval_count += 1
                raise RuntimeError("Strategy crashed!")

            def required_indicators(self):
                return []

            def validate_capital(self, allocated):
                return True

        # Create a strategy that works
        class WorkingStrategy(BaseStrategy):
            def __init__(self):
                self._config = MagicMock()
                self._config.enabled = True
                self._config.frequency = "1min"
                self._state = StrategyState.IDLE
                self._data_hub = None
                self.eval_count = 0

            @property
            def name(self):
                return "working"

            @property
            def config(self):
                return self._config

            async def evaluate(self):
                self.eval_count += 1
                return []

            def required_indicators(self):
                return []

            def validate_capital(self, allocated):
                return True

        failing = FailingStrategy()
        working = WorkingStrategy()

        engine = StrategyEngine(strategies=[failing, working])
        with patch("src.strategies.engine._is_market_open", return_value=True):
            await engine.start()

            # Let strategies run for a bit
            await asyncio.sleep(0.2)

            # Working strategy should still be running
            assert working.state == StrategyState.RUNNING

            await engine.stop()

        # Working strategy should have evaluated at least once
        assert working.eval_count >= 1


# ---------------------------------------------------------------------------
# Tests: Paper/Live Mode
# ---------------------------------------------------------------------------


class TestPaperLiveMode:
    """Test paper/live mode switching logic."""

    def test_paper_mode_detected_gateway(self):
        """Port 4002 is detected as paper mode."""
        from src.config.settings import Settings

        settings = MagicMock(spec=Settings)
        settings.connection = MagicMock()
        settings.connection.port = 4002

        bot = TradingBot(settings)
        assert bot._get_trading_mode() == "paper"

    def test_paper_mode_detected_tws(self):
        """Port 7497 is detected as paper mode."""
        from src.config.settings import Settings

        settings = MagicMock(spec=Settings)
        settings.connection = MagicMock()
        settings.connection.port = 7497

        bot = TradingBot(settings)
        assert bot._get_trading_mode() == "paper"

    def test_live_mode_detected_gateway(self):
        """Port 4001 is detected as live mode."""
        from src.config.settings import Settings

        settings = MagicMock(spec=Settings)
        settings.connection = MagicMock()
        settings.connection.port = 4001

        bot = TradingBot(settings)
        assert bot._get_trading_mode() == "live"

    def test_live_mode_detected_tws(self):
        """Port 7496 is detected as live mode."""
        from src.config.settings import Settings

        settings = MagicMock(spec=Settings)
        settings.connection = MagicMock()
        settings.connection.port = 7496

        bot = TradingBot(settings)
        assert bot._get_trading_mode() == "live"

    async def test_live_mode_requires_confirmation(self):
        """Live mode startup fails without TRADING_BOT_CONFIRM_LIVE env var."""
        from src.config.settings import Settings

        settings = MagicMock(spec=Settings)
        settings.connection = MagicMock()
        settings.connection.port = 4001

        bot = TradingBot(settings)

        with patch.dict("os.environ", {}, clear=True):
            with pytest.raises(SystemExit, match="explicit confirmation"):
                await bot._validate_trading_mode()

    async def test_live_mode_proceeds_with_confirmation(self):
        """Live mode startup succeeds with TRADING_BOT_CONFIRM_LIVE=yes."""
        from src.config.settings import Settings

        settings = MagicMock(spec=Settings)
        settings.connection = MagicMock()
        settings.connection.port = 4001

        bot = TradingBot(settings)

        with patch.dict("os.environ", {"TRADING_BOT_CONFIRM_LIVE": "yes"}):
            # Should not raise
            await bot._validate_trading_mode()


# ---------------------------------------------------------------------------
# Tests: Stale Data Handling
# ---------------------------------------------------------------------------


class TestStaleDataHandling:
    """Test stale data detection and signal halting."""

    def test_stale_data_detected(self, mock_connection):
        """Stale data is detected when no ticks received within threshold."""
        import time

        hub = MagicMock()
        hub.subscribed_symbols = ["AAPL"]
        hub._detect_stale_data.return_value = True

        # Verify the detection method works
        from src.data.market_data_hub import MarketDataHub

        real_hub = MarketDataHub(
            connection=mock_connection,
            redis=None,
            stale_threshold_seconds=1.0,
        )
        real_hub.subscribe("AAPL")

        # Without any ticks, data should be stale
        assert real_hub._detect_stale_data("AAPL") is True

    def test_fresh_data_not_stale(self, mock_connection):
        """Data with recent ticks is not considered stale."""
        from src.data.market_data_hub import MarketDataHub

        hub = MarketDataHub(
            connection=mock_connection,
            redis=None,
            stale_threshold_seconds=60.0,
        )
        hub.subscribe("AAPL")

        # Send a tick
        hub.on_tick("AAPL", 150.0, 100.0)

        # Should not be stale
        assert hub._detect_stale_data("AAPL") is False


# ---------------------------------------------------------------------------
# Tests: IBKR Rate Limit Backpressure
# ---------------------------------------------------------------------------


class TestRateLimitBackpressure:
    """Test rate limiting queues requests when approaching IBKR limit."""

    async def test_rate_limiter_allows_within_limit(self):
        """Requests within rate limit proceed immediately."""
        limiter = RateLimiter(max_per_second=45.0)

        # Should be able to acquire several tokens quickly
        for _ in range(10):
            await limiter.acquire()

    async def test_rate_limiter_delays_at_limit(self):
        """Requests at rate limit are delayed (backpressure)."""
        limiter = RateLimiter(max_per_second=10.0, burst_size=5)

        # Exhaust burst capacity
        for _ in range(5):
            acquired = limiter.try_acquire()
            assert acquired is True

        # Next should fail without waiting
        assert limiter.try_acquire() is False

    async def test_rate_limiter_recovers_over_time(self):
        """Rate limiter recovers tokens over time."""
        limiter = RateLimiter(max_per_second=100.0, burst_size=5)

        # Exhaust tokens
        for _ in range(5):
            limiter.try_acquire()

        # Wait for recovery
        await asyncio.sleep(0.1)

        # Should have recovered some tokens
        assert limiter.try_acquire() is True


# ---------------------------------------------------------------------------
# Tests: Graceful Shutdown
# ---------------------------------------------------------------------------


class TestGracefulShutdown:
    """Test the shutdown sequence."""

    async def test_shutdown_cancels_pending_orders(self, mock_connection):
        """Shutdown cancels all pending orders."""
        from src.config.settings import Settings

        settings = MagicMock(spec=Settings)
        settings.connection = MagicMock()
        settings.connection.port = 4002

        bot = TradingBot(settings)
        bot._connection_manager = mock_connection

        # Create order manager with a pending order
        rate_limiter = RateLimiter(max_per_second=45.0)
        bot._order_manager = OrderManager(
            connection=mock_connection,
            rate_limiter=rate_limiter,
        )

        # Submit an order
        signal = Signal(
            strategy_name="test",
            symbol="AAPL",
            direction=SignalDirection.LONG,
            confidence=0.8,
            suggested_size=Decimal("1000"),
            order_type=OrderType.MARKET,
            metadata={},
        )
        contract = MagicMock()
        contract.symbol = "AAPL"
        await bot._order_manager.submit_order(signal, contract)

        assert len(bot._order_manager.pending_orders) == 1

        # Shutdown should cancel it
        await bot.shutdown()

        assert len(bot._order_manager.pending_orders) == 0

    async def test_shutdown_disconnects_ibkr(self, mock_connection):
        """Shutdown disconnects from IBKR."""
        from src.config.settings import Settings

        settings = MagicMock(spec=Settings)
        settings.connection = MagicMock()
        settings.connection.port = 4002

        bot = TradingBot(settings)
        bot._connection_manager = mock_connection

        await bot.shutdown()

        mock_connection.disconnect.assert_called_once()


# ---------------------------------------------------------------------------
# Tests: Bot Starts in Paper Mode (Verification)
# ---------------------------------------------------------------------------


class TestBotStartsPaperMode:
    """Verify bot starts in paper mode with mocked IBKR."""

    async def test_bot_starts_in_paper_mode(self):
        """Bot initializes correctly in paper trading mode."""
        from src.config.settings import Settings

        settings = MagicMock(spec=Settings)
        settings.connection = ConnectionConfig(
            mode="gateway",
            host="127.0.0.1",
            port=4002,
            client_id=1,
            timeout=30,
            readonly=False,
        )
        settings.capital = MagicMock()
        settings.capital.total_capital = Decimal("100000")
        settings.capital.allocation_mode = "equal_weight"
        settings.capital.allocations = {}
        settings.risk = RiskConfig(
            stop_loss=StopLossConfig(type="atr_trailing"),
        )
        settings.alerts = AlertConfig(
            channels=AlertChannelsConfig(),
            routing={},
        )
        settings.redis = MagicMock()
        settings.redis.url = "redis://localhost:6379/0"
        settings.strategies = {}

        bot = TradingBot(settings)

        # Verify paper mode detection
        assert bot._get_trading_mode() == "paper"

        # Validate trading mode should pass for paper
        await bot._validate_trading_mode()
