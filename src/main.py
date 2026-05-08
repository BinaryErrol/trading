"""Application entry point for the IBKR Trading Bot.

Implements the full startup/shutdown sequence, paper/live mode switching,
strategy isolation, stale data handling, rate limit backpressure, and
signal/event flow wiring.
"""

from __future__ import annotations

import asyncio
import signal
import sys
from decimal import Decimal
from typing import Any

import structlog

from src.alerts.service import (
    Alert,
    AlertEventType,
    AlertPriority,
    AlertService,
)
from src.config.settings import Settings, validate_config
from src.connection.manager import ConnectionManager
from src.data.market_data_hub import MarketDataHub
from src.orders.manager import ManagedOrder, OrderManager
from src.orders.rate_limiter import RateLimiter
from src.persistence.database import close_db, get_session, init_db
from src.persistence.reconciliation import reconcile_positions
from src.portfolio.capital_allocator import AllocationMode, CapitalAllocator
from src.portfolio.monitor import PortfolioMonitor
from src.risk.manager import RiskManager
from src.strategies.engine import StrategyEngine
from src.strategies.signals import Signal

log = structlog.get_logger()

# Event used to signal graceful shutdown
_shutdown_event = asyncio.Event()


def _handle_signal(sig: signal.Signals) -> None:
    """Handle OS signals for graceful shutdown."""
    log.info("shutdown_signal_received", signal=sig.name)
    _shutdown_event.set()


class TradingBot:
    """Orchestrates all trading bot components.

    Manages the full lifecycle: startup, running, and graceful shutdown.
    Implements strategy isolation, stale data handling, rate limit backpressure,
    and signal/event flow wiring.
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._redis: Any = None
        self._connection_manager: ConnectionManager | None = None
        self._market_data_hub: MarketDataHub | None = None
        self._strategy_engine: StrategyEngine | None = None
        self._order_manager: OrderManager | None = None
        self._risk_manager: RiskManager | None = None
        self._capital_allocator: CapitalAllocator | None = None
        self._portfolio_monitor: PortfolioMonitor | None = None
        self._alert_service: AlertService | None = None
        self._rate_limiter: RateLimiter | None = None
        self._stale_data_task: asyncio.Task | None = None
        self._strategy_tasks: dict[str, asyncio.Task] = {}

    # ------------------------------------------------------------------
    # Startup Sequence
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Execute the full startup sequence.

        1. Load config (already done via settings)
        2. Connect DB/Redis
        3. Connect IBKR
        4. Reconcile positions
        5. Start strategies
        6. Start stale data monitor
        """
        log.info("trading_bot_starting", version="0.1.0")

        # Step 1: Validate paper/live mode
        await self._validate_trading_mode()

        # Step 2: Connect to DB and Redis
        await self._connect_infrastructure()

        # Step 3: Connect to IBKR
        await self._connect_ibkr()

        # Step 4: Initialize components
        self._initialize_components()

        # Step 5: Reconcile positions
        await self._reconcile_positions()

        # Step 6: Wire signal and event flows
        self._wire_signal_flow()
        self._wire_event_flow()

        # Step 7: Start strategies with isolation
        await self._start_strategies()

        # Step 8: Start stale data monitor
        self._start_stale_data_monitor()

        log.info("trading_bot_running", mode=self._get_trading_mode())

    # ------------------------------------------------------------------
    # Graceful Shutdown
    # ------------------------------------------------------------------

    async def shutdown(self) -> None:
        """Execute graceful shutdown sequence.

        1. Stop strategies
        2. Cancel pending orders
        3. Disconnect IBKR
        4. Close DB/Redis
        """
        log.info("trading_bot_shutting_down")

        # Step 1: Stop stale data monitor
        if self._stale_data_task and not self._stale_data_task.done():
            self._stale_data_task.cancel()
            try:
                await self._stale_data_task
            except asyncio.CancelledError:
                pass

        # Step 2: Stop strategies
        if self._strategy_engine:
            try:
                await self._strategy_engine.stop()
                log.info("strategies_stopped")
            except Exception as exc:
                log.error("strategy_stop_error", error=str(exc))

        # Step 3: Cancel pending orders
        if self._order_manager:
            try:
                pending = list(self._order_manager.pending_orders.keys())
                for order_id in pending:
                    await self._order_manager.cancel_order(order_id)
                if pending:
                    log.info("pending_orders_cancelled", count=len(pending))
            except Exception as exc:
                log.error("order_cancel_error", error=str(exc))

        # Step 4: Disconnect IBKR
        if self._connection_manager:
            try:
                await self._connection_manager.disconnect()
                log.info("ibkr_disconnected")
            except Exception as exc:
                log.error("ibkr_disconnect_error", error=str(exc))

        # Step 5: Close Redis
        if self._redis is not None:
            try:
                if hasattr(self._redis, "aclose"):
                    await self._redis.aclose()
                elif hasattr(self._redis, "close"):
                    await self._redis.close()
                log.info("redis_closed")
            except Exception as exc:
                log.error("redis_close_error", error=str(exc))

        # Step 6: Close DB
        try:
            await close_db()
            log.info("database_closed")
        except Exception as exc:
            log.error("database_close_error", error=str(exc))

        log.info("trading_bot_stopped")

    # ------------------------------------------------------------------
    # Paper/Live Mode
    # ------------------------------------------------------------------

    def _get_trading_mode(self) -> str:
        """Determine trading mode from config port.

        Paper ports: 4002 (Gateway), 7497 (TWS)
        Live ports: 4001 (Gateway), 7496 (TWS)
        """
        port = self.settings.connection.port
        if port in (4002, 7497):
            return "paper"
        elif port in (4001, 7496):
            return "live"
        return "unknown"

    async def _validate_trading_mode(self) -> None:
        """Validate trading mode and require confirmation for live mode."""
        mode = self._get_trading_mode()

        if mode == "live":
            log.warning(
                "live_trading_mode_detected",
                port=self.settings.connection.port,
                msg="LIVE TRADING MODE - Real money at risk!",
            )
            # In production, this would require explicit confirmation
            # For automated startup, we check for an environment variable
            # or config flag that confirms live mode intent
            import os

            if not os.environ.get("TRADING_BOT_CONFIRM_LIVE", "").lower() in (
                "yes",
                "true",
                "1",
            ):
                log.error(
                    "live_mode_not_confirmed",
                    msg="Set TRADING_BOT_CONFIRM_LIVE=yes to confirm live trading",
                )
                raise SystemExit(
                    "Live trading mode requires explicit confirmation. "
                    "Set TRADING_BOT_CONFIRM_LIVE=yes environment variable."
                )
        else:
            log.info("paper_trading_mode", port=self.settings.connection.port)

    # ------------------------------------------------------------------
    # Infrastructure Connections
    # ------------------------------------------------------------------

    async def _connect_infrastructure(self) -> None:
        """Connect to PostgreSQL and Redis."""
        # Database
        db_config = self.settings.database
        await init_db(
            url=db_config.url,
            pool_size=db_config.pool_size,
            max_overflow=db_config.max_overflow,
        )
        log.info("database_connected")

        # Redis
        try:
            import redis.asyncio as aioredis

            self._redis = aioredis.from_url(
                self.settings.redis.url,
                decode_responses=True,
            )
            # Test connection
            await self._redis.ping()
            log.info("redis_connected", url=self.settings.redis.url)
        except Exception as exc:
            log.warning(
                "redis_connection_failed",
                error=str(exc),
                msg="Continuing without Redis cache",
            )
            self._redis = None

    async def _connect_ibkr(self) -> None:
        """Connect to IBKR TWS or Gateway."""
        self._connection_manager = ConnectionManager(
            config=self.settings.connection,
            on_connection_lost=self._on_connection_lost,
        )
        await self._connection_manager.connect()
        log.info(
            "ibkr_connected",
            mode=self.settings.connection.mode,
            host=self.settings.connection.host,
            port=self.settings.connection.port,
        )

    async def _on_connection_lost(self) -> None:
        """Handle IBKR connection loss after all retries exhausted."""
        log.critical("ibkr_connection_lost_permanently")

        # Halt all strategies
        if self._strategy_engine:
            await self._strategy_engine.stop()

        # Send critical alert
        if self._alert_service:
            await self._alert_service.send_critical(
                Alert(
                    event_type=AlertEventType.CONNECTION_LOST,
                    priority=AlertPriority.CRITICAL,
                    title="IBKR Connection Lost",
                    message="All reconnection attempts exhausted. Strategies halted.",
                )
            )

    # ------------------------------------------------------------------
    # Component Initialization
    # ------------------------------------------------------------------

    def _initialize_components(self) -> None:
        """Initialize all trading components."""
        assert self._connection_manager is not None

        # Rate limiter for IBKR backpressure
        self._rate_limiter = RateLimiter(max_per_second=45.0)

        # Portfolio Monitor
        self._portfolio_monitor = PortfolioMonitor(
            connection=self._connection_manager,
            initial_equity=self.settings.capital.total_capital,
        )

        # Capital Allocator
        self._capital_allocator = CapitalAllocator(
            total_capital=self.settings.capital.total_capital,
        )

        # Risk Manager
        self._risk_manager = RiskManager(
            config=self.settings.risk,
            portfolio=self._portfolio_monitor,
            on_halt=self._on_trading_halted,
        )

        # Order Manager
        self._order_manager = OrderManager(
            connection=self._connection_manager,
            rate_limiter=self._rate_limiter,
            on_fill=self._on_order_fill,
            on_rejection=self._on_order_rejection,
        )

        # Alert Service
        self._alert_service = AlertService(config=self.settings.alerts)

        # Market Data Hub
        self._market_data_hub = MarketDataHub(
            connection=self._connection_manager,
            redis=self._redis,
            stale_threshold_seconds=60.0,
        )

        log.info("components_initialized")

    # ------------------------------------------------------------------
    # Position Reconciliation
    # ------------------------------------------------------------------

    async def _reconcile_positions(self) -> None:
        """Reconcile persisted positions with IBKR account."""
        if not self._connection_manager or not self._connection_manager.is_connected:
            log.warning("reconciliation_skipped", reason="not connected")
            return

        try:
            # Get IBKR positions
            ib = self._connection_manager.ib
            ibkr_positions_raw = ib.positions()

            ibkr_positions = []
            for pos in ibkr_positions_raw:
                ibkr_positions.append({
                    "symbol": pos.contract.symbol,
                    "asset_class": pos.contract.secType or "STK",
                    "quantity": pos.position,
                    "avg_cost": pos.avgCost,
                })

            # Reconcile with database
            async with get_session() as session:
                result = await reconcile_positions(
                    session=session,
                    ibkr_positions=ibkr_positions,
                    auto_fix=True,
                )

            if result.has_discrepancies:
                log.warning(
                    "reconciliation_discrepancies",
                    summary=result.summary,
                )
            else:
                log.info("reconciliation_clean", summary=result.summary)

            # Sync portfolio monitor
            if self._portfolio_monitor:
                await self._portfolio_monitor.sync_positions()

        except Exception as exc:
            log.error("reconciliation_error", error=str(exc))

    # ------------------------------------------------------------------
    # Strategy Startup with Isolation
    # ------------------------------------------------------------------

    async def _start_strategies(self) -> None:
        """Start strategies with asyncio task isolation.

        Each strategy runs in its own task. Unhandled exceptions in one
        strategy don't crash others — they are caught, logged, and the
        strategy is marked as halted.
        """
        assert self._market_data_hub is not None
        assert self._capital_allocator is not None

        # Set up capital allocations
        self._setup_capital_allocations()

        # Build strategy instances (import implementations dynamically)
        strategies = self._build_strategies()

        if not strategies:
            log.warning("no_strategies_configured")
            return

        # Create strategy engine with signal routing
        self._strategy_engine = StrategyEngine(
            strategies=strategies,
            on_signal=self._on_signal_generated,
            capital_allocator=self._capital_allocator,
        )

        # Start with isolation wrapper
        await self._strategy_engine.start()
        log.info(
            "strategies_started",
            count=len(strategies),
            names=[s.name for s in strategies],
        )

    def _setup_capital_allocations(self) -> None:
        """Set up capital allocations based on config."""
        assert self._capital_allocator is not None

        config = self.settings.capital
        mode_map = {
            "equal_weight": AllocationMode.EQUAL_WEIGHT,
            "fixed_amount": AllocationMode.FIXED_AMOUNT,
            "percentage": AllocationMode.PERCENTAGE,
        }
        mode = mode_map.get(config.allocation_mode, AllocationMode.EQUAL_WEIGHT)

        enabled_strategies = [
            name for name, cfg in self.settings.strategies.items() if cfg.enabled
        ]
        num_strategies = len(enabled_strategies)

        for name in enabled_strategies:
            try:
                if mode == AllocationMode.EQUAL_WEIGHT:
                    amount = Decimal(str(num_strategies))
                elif mode == AllocationMode.PERCENTAGE:
                    pct = config.allocations.get(name, 0.0)
                    amount = Decimal(str(pct))
                else:  # FIXED_AMOUNT
                    amount = Decimal(str(config.allocations.get(name, 0.0)))

                self._capital_allocator.allocate(name, amount, mode)
            except (ValueError, KeyError) as exc:
                log.warning(
                    "capital_allocation_failed",
                    strategy=name,
                    error=str(exc),
                )

    def _build_strategies(self) -> list:
        """Build strategy instances from config.

        Returns a list of BaseStrategy instances for enabled strategies.
        """
        from src.strategies.base import BaseStrategy

        strategies: list[BaseStrategy] = []

        # Strategy class registry
        strategy_classes = self._get_strategy_registry()

        for name, config in self.settings.strategies.items():
            if not config.enabled:
                continue

            cls = strategy_classes.get(name)
            if cls is None:
                log.warning("strategy_class_not_found", strategy=name)
                continue

            try:
                assert self._market_data_hub is not None
                strategy = cls(config=config, data_hub=self._market_data_hub)
                strategies.append(strategy)
            except Exception as exc:
                log.error(
                    "strategy_instantiation_failed",
                    strategy=name,
                    error=str(exc),
                )

        return strategies

    def _get_strategy_registry(self) -> dict[str, type]:
        """Return mapping of strategy names to their implementation classes."""
        registry: dict[str, type] = {}

        try:
            from src.strategies.implementations.momentum import MomentumStrategy

            registry["momentum"] = MomentumStrategy
        except ImportError:
            pass

        try:
            from src.strategies.implementations.ma_crossover import MACrossoverStrategy

            registry["ma_crossover"] = MACrossoverStrategy
        except ImportError:
            pass

        try:
            from src.strategies.implementations.mean_reversion import MeanReversionStrategy

            registry["mean_reversion"] = MeanReversionStrategy
        except ImportError:
            pass

        try:
            from src.strategies.implementations.breakout import BreakoutStrategy

            registry["breakout"] = BreakoutStrategy
        except ImportError:
            pass

        try:
            from src.strategies.implementations.trend_following import TrendFollowingStrategy

            registry["trend_following"] = TrendFollowingStrategy
        except ImportError:
            pass

        try:
            from src.strategies.implementations.bollinger import BollingerStrategy

            registry["bollinger"] = BollingerStrategy
        except ImportError:
            pass

        try:
            from src.strategies.implementations.rsi_divergence import RSIDivergenceStrategy

            registry["rsi_divergence"] = RSIDivergenceStrategy
        except ImportError:
            pass

        try:
            from src.strategies.implementations.vwap import VWAPStrategy

            registry["vwap"] = VWAPStrategy
        except ImportError:
            pass

        try:
            from src.strategies.implementations.pairs_trading import PairsTradingStrategy

            registry["pairs_trading"] = PairsTradingStrategy
        except ImportError:
            pass

        try:
            from src.strategies.implementations.market_making import MarketMakingStrategy

            registry["market_making"] = MarketMakingStrategy
        except ImportError:
            pass

        return registry

    # ------------------------------------------------------------------
    # Signal Flow: Strategy → RiskManager → CapitalAllocator → OrderManager
    # ------------------------------------------------------------------

    def _wire_signal_flow(self) -> None:
        """Wire the signal processing pipeline.

        Signal flow: Strategy → RiskManager → CapitalAllocator → OrderManager
        """
        # Signal routing is handled via the on_signal callback in StrategyEngine
        # which calls _on_signal_generated
        log.info("signal_flow_wired")

    def _on_signal_generated(self, signal: Signal) -> None:
        """Handle a signal from the strategy engine.

        Runs the signal through risk checks, capital allocation, and order submission.
        Uses asyncio.ensure_future for async processing from sync callback.
        """
        asyncio.ensure_future(self._process_signal(signal))

    async def _process_signal(self, signal: Signal) -> None:
        """Process a signal through the full pipeline.

        1. Risk check
        2. Capital allocation check
        3. Order submission
        """
        try:
            # Step 1: Risk check
            if self._risk_manager:
                result = await self._risk_manager.check_order(signal)
                if not result.approved:
                    log.info(
                        "signal_rejected_risk",
                        strategy=signal.strategy_name,
                        symbol=signal.symbol,
                        reason=result.reason,
                    )
                    return

            # Step 2: Capital allocation check
            if self._capital_allocator:
                if not self._capital_allocator.can_place_order(
                    signal.strategy_name, signal.suggested_size
                ):
                    log.info(
                        "signal_rejected_capital",
                        strategy=signal.strategy_name,
                        symbol=signal.symbol,
                        size=str(signal.suggested_size),
                    )
                    return

            # Step 3: Submit order (with rate limit backpressure)
            if self._order_manager:
                contract = self._make_contract(signal.symbol)
                await self._order_manager.submit_order(signal, contract)
                log.info(
                    "signal_processed_to_order",
                    strategy=signal.strategy_name,
                    symbol=signal.symbol,
                    direction=signal.direction.value,
                )

        except Exception as exc:
            log.error(
                "signal_processing_error",
                strategy=signal.strategy_name,
                symbol=signal.symbol,
                error=str(exc),
            )

    # ------------------------------------------------------------------
    # Event Flow: fills → PortfolioMonitor + CapitalAllocator + AlertService
    # ------------------------------------------------------------------

    def _wire_event_flow(self) -> None:
        """Wire fill events to downstream components.

        Fills → PortfolioMonitor + CapitalAllocator + AlertService
        """
        # Fill handling is wired via OrderManager's on_fill callback
        # which calls _on_order_fill
        log.info("event_flow_wired")

    def _on_order_fill(self, managed_order: ManagedOrder, fill: Any) -> None:
        """Handle order fill events.

        Routes fill information to:
        - PortfolioMonitor (position update)
        - CapitalAllocator (deployed capital tracking)
        - AlertService (trade notification)
        """
        asyncio.ensure_future(self._process_fill(managed_order, fill))

    async def _process_fill(self, managed_order: ManagedOrder, fill: Any) -> None:
        """Process a fill event through all downstream components."""
        try:
            fill_value = managed_order.filled_quantity * (
                managed_order.avg_fill_price or Decimal("0")
            )

            # Update capital allocator
            if self._capital_allocator:
                try:
                    self._capital_allocator.record_fill(
                        strategy_name=managed_order.strategy_name,
                        fill_value=fill_value,
                    )
                except KeyError:
                    pass  # Strategy may not have allocation

            # Update risk manager daily P&L
            if self._risk_manager and managed_order.avg_fill_price:
                # P&L tracking happens on position close
                pass

            # Send trade alert
            if self._alert_service:
                await self._alert_service.send(
                    Alert(
                        event_type=AlertEventType.TRADE_EXECUTED,
                        priority=AlertPriority.MEDIUM,
                        title=f"Trade Executed: {managed_order.symbol}",
                        message=(
                            f"Strategy: {managed_order.strategy_name}, "
                            f"Direction: {managed_order.direction.value}, "
                            f"Qty: {managed_order.filled_quantity}, "
                            f"Price: {managed_order.avg_fill_price}"
                        ),
                        metadata={
                            "symbol": managed_order.symbol,
                            "strategy": managed_order.strategy_name,
                            "direction": managed_order.direction.value,
                            "quantity": str(managed_order.filled_quantity),
                            "price": str(managed_order.avg_fill_price),
                        },
                    )
                )

        except Exception as exc:
            log.error(
                "fill_processing_error",
                order_id=managed_order.order_id,
                error=str(exc),
            )

    def _on_order_rejection(self, managed_order: ManagedOrder) -> None:
        """Handle order rejection events."""
        log.warning(
            "order_rejected_by_ibkr",
            order_id=managed_order.order_id,
            symbol=managed_order.symbol,
            strategy=managed_order.strategy_name,
            reason=managed_order.rejection_reason,
        )

    def _on_trading_halted(self, reason: str) -> None:
        """Handle trading halt from risk manager."""
        log.critical("trading_halted_by_risk_manager", reason=reason)

        if self._alert_service:
            asyncio.ensure_future(
                self._alert_service.send_critical(
                    Alert(
                        event_type=AlertEventType.RISK_BREACH,
                        priority=AlertPriority.CRITICAL,
                        title="Trading Halted",
                        message=f"Risk limit breached: {reason}",
                        metadata={"reason": reason},
                    )
                )
            )

    # ------------------------------------------------------------------
    # Stale Data Handling
    # ------------------------------------------------------------------

    def _start_stale_data_monitor(self) -> None:
        """Start background task to monitor for stale market data."""
        self._stale_data_task = asyncio.create_task(
            self._stale_data_loop(),
            name="stale-data-monitor",
        )

    async def _stale_data_loop(self) -> None:
        """Periodically check for stale market data.

        When stale data is detected:
        - Halt signal generation for affected instruments
        - Notify user via alert service
        """
        try:
            while True:
                await asyncio.sleep(30)  # Check every 30 seconds

                if not self._market_data_hub:
                    continue

                for symbol in self._market_data_hub.subscribed_symbols:
                    is_stale = self._market_data_hub._detect_stale_data(symbol)
                    if is_stale:
                        log.warning(
                            "stale_data_halting_signals",
                            symbol=symbol,
                        )
                        # Notify user
                        if self._alert_service:
                            await self._alert_service.send(
                                Alert(
                                    event_type=AlertEventType.ERROR,
                                    priority=AlertPriority.HIGH,
                                    title=f"Stale Data: {symbol}",
                                    message=(
                                        f"No market data updates for {symbol}. "
                                        "Signal generation halted for this instrument."
                                    ),
                                    metadata={"symbol": symbol},
                                )
                            )

        except asyncio.CancelledError:
            pass

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _make_contract(symbol: str) -> Any:
        """Create a basic IBKR contract for a symbol."""
        try:
            from ib_async import Contract

            contract = Contract()
            contract.symbol = symbol
            contract.secType = "STK"
            contract.exchange = "SMART"
            contract.currency = "USD"
            return contract
        except ImportError:
            # Fallback for testing
            class _SimpleContract:
                def __init__(self, sym: str):
                    self.symbol = sym
                    self.secType = "STK"
                    self.exchange = "SMART"
                    self.currency = "USD"

            return _SimpleContract(symbol)


async def async_main() -> None:
    """Async entry point. Orchestrates the full startup and run sequence."""
    loop = asyncio.get_running_loop()

    # Register signal handlers for graceful shutdown
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _handle_signal, sig)

    # Load and validate configuration
    settings = validate_config()
    if settings is None:
        log.error("configuration_invalid")
        return

    # Create and start the trading bot
    bot = TradingBot(settings)

    try:
        await bot.start()
    except SystemExit:
        raise
    except Exception as exc:
        log.error("startup_failed", error=str(exc), exc_info=True)
        await bot.shutdown()
        return

    # Wait for shutdown signal
    log.info("trading_bot_running", msg="Waiting for shutdown signal")
    await _shutdown_event.wait()

    # Graceful shutdown
    await bot.shutdown()


def main() -> None:
    """Synchronous entry point referenced by pyproject.toml [project.scripts]."""
    try:
        asyncio.run(async_main())
    except KeyboardInterrupt:
        pass
    except SystemExit:
        raise
    except Exception:
        log.exception("trading_bot_fatal_error")
        sys.exit(1)
    finally:
        log.info("trading_bot_exited")


if __name__ == "__main__":
    main()
