"""Strategy orchestration engine.

Manages strategy lifecycle (enable, disable, start, stop), schedules evaluations
at configured frequencies, suppresses intraday strategies outside market hours,
and routes generated signals to a callback for downstream processing.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, time, timezone
from decimal import Decimal
from typing import Callable

import structlog

from src.strategies.base import BaseStrategy, StrategyState
from src.strategies.signals import Signal

logger = structlog.get_logger(__name__)

# NYSE market hours in Eastern Time (ET)
MARKET_OPEN = time(9, 30)
MARKET_CLOSE = time(16, 0)

# Frequency to seconds mapping
FREQUENCY_SECONDS: dict[str, float] = {
    "tick": 0.0,
    "1min": 60.0,
    "5min": 300.0,
    "15min": 900.0,
    "1hour": 3600.0,
    "daily": 86400.0,
    "weekly": 604800.0,
}

# Intraday frequencies that should be suppressed outside market hours
INTRADAY_FREQUENCIES: frozenset[str] = frozenset(
    {"tick", "1min", "5min", "15min", "1hour"}
)


def _is_market_open() -> bool:
    """Check if NYSE is currently open (9:30-16:00 ET, weekdays only).

    Uses a simplified check based on UTC offset for US Eastern Time.
    Does not account for holidays.
    """
    try:
        import zoneinfo

        et = zoneinfo.ZoneInfo("America/New_York")
    except (ImportError, KeyError):
        # Fallback: assume UTC-5 (EST) if zoneinfo unavailable
        from datetime import timedelta, timezone as tz

        et = tz(timedelta(hours=-5))

    now_et = datetime.now(et)

    # Weekday check: Monday=0, Friday=4
    if now_et.weekday() > 4:
        return False

    current_time = now_et.time()
    return MARKET_OPEN <= current_time < MARKET_CLOSE


class StrategyEngine:
    """Orchestrates strategy evaluation loops and signal routing.

    Manages the lifecycle of multiple strategies, scheduling their evaluation
    at configured frequencies. Intraday strategies are suppressed outside
    NYSE market hours. Generated signals are routed to the on_signal callback.

    Args:
        strategies: List of strategy instances to manage.
        on_signal: Callback invoked with each generated signal.
        capital_allocator: Optional reference for capital validation.
    """

    def __init__(
        self,
        strategies: list[BaseStrategy],
        on_signal: Callable[[Signal], None] | None = None,
        capital_allocator: object | None = None,
    ) -> None:
        self._strategies: dict[str, BaseStrategy] = {s.name: s for s in strategies}
        self._tasks: dict[str, asyncio.Task] = {}
        self._on_signal = on_signal
        self._capital_allocator = capital_allocator
        self._running = False

    @property
    def strategies(self) -> dict[str, BaseStrategy]:
        """Return registered strategies by name."""
        return dict(self._strategies)

    @property
    def running(self) -> bool:
        """Whether the engine is currently running."""
        return self._running

    async def start(self) -> None:
        """Start all enabled strategies on their configured schedules.

        Only starts strategies whose config has enabled=True and that
        pass capital validation.
        """
        self._running = True
        logger.info("strategy_engine_starting", strategy_count=len(self._strategies))

        for name, strategy in self._strategies.items():
            if strategy.config.enabled:
                await self.enable_strategy(name)

        logger.info(
            "strategy_engine_started",
            active_strategies=list(self._tasks.keys()),
        )

    async def stop(self) -> None:
        """Stop all running strategies gracefully."""
        self._running = False
        logger.info("strategy_engine_stopping")

        names = list(self._tasks.keys())
        for name in names:
            await self.disable_strategy(name)

        logger.info("strategy_engine_stopped")

    async def enable_strategy(self, name: str) -> None:
        """Enable a strategy and start its evaluation loop.

        Validates capital allocation before enabling. If validation fails,
        the strategy remains in IDLE state.

        Args:
            name: Name of the strategy to enable.

        Raises:
            KeyError: If strategy name is not registered.
        """
        if name not in self._strategies:
            raise KeyError(f"Strategy '{name}' not registered")

        strategy = self._strategies[name]

        # Skip if already running
        if name in self._tasks and not self._tasks[name].done():
            logger.debug("strategy_already_running", strategy=name)
            return

        # Validate capital if allocator is available
        if self._capital_allocator is not None:
            allocated = self._get_allocated_capital(name)
            if not strategy.validate_capital(allocated):
                logger.warning(
                    "strategy_enable_failed_capital",
                    strategy=name,
                    allocated=str(allocated),
                )
                return

        strategy.state = StrategyState.RUNNING
        task = asyncio.create_task(
            self._run_strategy_loop(strategy),
            name=f"strategy-{name}",
        )
        self._tasks[name] = task

        logger.info("strategy_enabled", strategy=name)

    async def disable_strategy(self, name: str) -> None:
        """Disable a strategy and cancel its evaluation loop.

        Args:
            name: Name of the strategy to disable.

        Raises:
            KeyError: If strategy name is not registered.
        """
        if name not in self._strategies:
            raise KeyError(f"Strategy '{name}' not registered")

        strategy = self._strategies[name]

        # Cancel the running task
        if name in self._tasks:
            task = self._tasks.pop(name)
            if not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

        strategy.state = StrategyState.IDLE
        logger.info("strategy_disabled", strategy=name)

    async def _run_strategy_loop(self, strategy: BaseStrategy) -> None:
        """Run strategy evaluation at configured frequency.

        Suppresses intraday strategies outside NYSE market hours.
        Routes generated signals to the on_signal callback.

        Args:
            strategy: The strategy instance to evaluate.
        """
        frequency = strategy.config.frequency
        interval = FREQUENCY_SECONDS.get(frequency, 60.0)
        is_intraday = frequency in INTRADAY_FREQUENCIES

        logger.info(
            "strategy_loop_started",
            strategy=strategy.name,
            frequency=frequency,
            interval_seconds=interval,
        )

        consecutive_failures = 0
        MAX_CONSECUTIVE_FAILURES = 5

        # Determine if this strategy trades 24/7 assets (crypto)
        is_always_on = "crypto" in (strategy.config.asset_classes or [])

        try:
            while True:
                # Suppress intraday strategies outside market hours (except crypto)
                if is_intraday and not is_always_on and not _is_market_open():
                    logger.debug(
                        "strategy_suppressed_market_closed",
                        strategy=strategy.name,
                    )
                    # Check again in 60 seconds
                    await asyncio.sleep(60.0)
                    continue

                # Evaluate the strategy
                try:
                    signals = await strategy.evaluate()
                    if signals:
                        for signal in signals:
                            self._route_signal(signal)
                    consecutive_failures = 0
                except Exception as exc:
                    consecutive_failures += 1
                    if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                        strategy.state = StrategyState.HALTED
                        logger.error(
                            "strategy_halted_consecutive_failures",
                            strategy=strategy.name,
                            failures=consecutive_failures,
                        )
                        break
                    logger.error(
                        "strategy_evaluation_error",
                        strategy=strategy.name,
                        error=str(exc),
                        exc_info=True,
                    )

                # Sleep for the configured interval
                if interval > 0:
                    await asyncio.sleep(interval)
                else:
                    # Tick frequency: minimum 10ms to prevent hot loops
                    await asyncio.sleep(max(interval, 0.01))

        except asyncio.CancelledError:
            logger.info("strategy_loop_cancelled", strategy=strategy.name)
            raise

    def _route_signal(self, signal: Signal) -> None:
        """Route a signal to the on_signal callback.

        If the signal carries option_params, builds an ib_async Option contract
        via build_option_contract and attaches it as signal metadata for the
        OrderManager to use instead of the default _make_contract logic.

        Args:
            signal: The trading signal to route.
        """
        # Build options contract if signal has option_params
        if signal.option_params is not None:
            try:
                from src.orders.options_contract import build_option_contract

                contract = build_option_contract(signal.option_params)
                signal.metadata["_option_contract"] = contract
                logger.debug(
                    "option_contract_built",
                    strategy=signal.strategy_name,
                    symbol=signal.symbol,
                    underlying=signal.option_params.underlying,
                    strike=str(signal.option_params.strike),
                    right=signal.option_params.right,
                )
            except Exception as exc:
                logger.error(
                    "option_contract_build_error",
                    strategy=signal.strategy_name,
                    symbol=signal.symbol,
                    error=str(exc),
                )

        if self._on_signal is not None:
            try:
                self._on_signal(signal)
            except Exception as exc:
                logger.error(
                    "signal_routing_error",
                    strategy=signal.strategy_name,
                    symbol=signal.symbol,
                    error=str(exc),
                )
        else:
            logger.debug(
                "signal_generated_no_handler",
                strategy=signal.strategy_name,
                symbol=signal.symbol,
                direction=signal.direction.value,
            )

    def _get_allocated_capital(self, strategy_name: str) -> Decimal:
        """Get allocated capital for a strategy from the capital allocator.

        Tries the strategy name as-is first, then falls back to config-style
        names to handle class name vs config key mismatches.

        Args:
            strategy_name: Name of the strategy.

        Returns:
            Allocated capital amount, or Decimal("0") if not available.
        """
        if self._capital_allocator is None:
            return Decimal("0")

        if hasattr(self._capital_allocator, "get_available"):
            # Try exact name first
            try:
                return self._capital_allocator.get_available(strategy_name)
            except KeyError:
                pass

            # Try lowercase with "Strategy" stripped (MomentumStrategy -> momentum)
            config_name = strategy_name.replace("Strategy", "").lower()
            try:
                return self._capital_allocator.get_available(config_name)
            except KeyError:
                pass

            # Try proper snake_case (MACrossoverStrategy -> ma_crossover)
            import re
            base = strategy_name.replace("Strategy", "")
            # Insert underscore before uppercase letters that follow lowercase
            snake_name = re.sub(r'([a-z])([A-Z])', r'\1_\2', base).lower()
            try:
                return self._capital_allocator.get_available(snake_name)
            except KeyError:
                pass

            # Try all registered allocations for a case-insensitive match
            if hasattr(self._capital_allocator, 'allocations'):
                for key in self._capital_allocator.allocations:
                    if key.lower().replace("_", "") == config_name.replace("_", ""):
                        try:
                            return self._capital_allocator.get_available(key)
                        except KeyError:
                            pass

            logger.warning(
                "capital_allocation_not_found",
                strategy=strategy_name,
                tried=[strategy_name, config_name, snake_name],
            )

        return Decimal("0")
