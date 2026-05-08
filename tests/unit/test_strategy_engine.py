"""Unit tests for the Strategy Engine module.

Tests cover:
- Strategy scheduling and evaluation loops
- Enable/disable lifecycle management
- Signal routing to callback
- Market hours suppression for intraday strategies
- Capital validation before enabling
"""

from __future__ import annotations

import asyncio
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.config.settings import StrategyConfig
from src.strategies.base import BaseStrategy, StrategyState
from src.strategies.engine import (
    FREQUENCY_SECONDS,
    INTRADAY_FREQUENCIES,
    StrategyEngine,
    _is_market_open,
)
from src.strategies.signals import OrderType, Signal, SignalDirection


# ─── Test Fixtures ───────────────────────────────────────────────────────────


class FakeDataHub:
    """Fake MarketDataHub for testing."""

    pass


class FakeCapitalAllocator:
    """Fake capital allocator for testing."""

    def __init__(self, available: Decimal = Decimal("50000")):
        self._available = available

    def get_available(self, strategy_name: str) -> Decimal:
        return self._available


def make_strategy_config(
    enabled: bool = True,
    frequency: str = "1min",
    symbols: list[str] | None = None,
) -> StrategyConfig:
    """Create a test StrategyConfig."""
    return StrategyConfig(
        enabled=enabled,
        frequency=frequency,
        symbols=symbols or ["AAPL"],
        asset_classes=["equity"],
        parameters={},
    )


class ConcreteStrategy(BaseStrategy):
    """Concrete strategy implementation for testing."""

    def __init__(
        self,
        config: StrategyConfig,
        data_hub: FakeDataHub,
        signals_to_return: list[Signal] | None = None,
        name_override: str | None = None,
    ):
        super().__init__(config, data_hub)
        self._signals = signals_to_return or []
        self._name_override = name_override
        self.evaluate_count = 0

    @property
    def name(self) -> str:
        return self._name_override or super().name

    async def evaluate(self) -> list[Signal]:
        self.evaluate_count += 1
        return self._signals

    def required_indicators(self) -> list[str]:
        return ["SMA_20"]


def make_signal(
    strategy_name: str = "TestStrategy",
    symbol: str = "AAPL",
    direction: SignalDirection = SignalDirection.LONG,
) -> Signal:
    """Create a test Signal."""
    return Signal(
        strategy_name=strategy_name,
        symbol=symbol,
        direction=direction,
        confidence=0.85,
        suggested_size=Decimal("10000"),
        order_type=OrderType.MARKET,
    )


# ─── BaseStrategy Tests ─────────────────────────────────────────────────────


class TestBaseStrategy:
    """Tests for the BaseStrategy ABC."""

    def test_initial_state_is_idle(self):
        """Strategy starts in IDLE state."""
        config = make_strategy_config()
        strategy = ConcreteStrategy(config, FakeDataHub())
        assert strategy.state == StrategyState.IDLE

    def test_state_can_be_set(self):
        """Strategy state can be changed."""
        config = make_strategy_config()
        strategy = ConcreteStrategy(config, FakeDataHub())
        strategy.state = StrategyState.RUNNING
        assert strategy.state == StrategyState.RUNNING

    def test_name_property(self):
        """Strategy name defaults to class name."""
        config = make_strategy_config()
        strategy = ConcreteStrategy(config, FakeDataHub())
        assert strategy.name == "ConcreteStrategy"

    def test_config_property(self):
        """Strategy exposes its config."""
        config = make_strategy_config(frequency="5min")
        strategy = ConcreteStrategy(config, FakeDataHub())
        assert strategy.config.frequency == "5min"

    def test_validate_capital_sufficient(self):
        """validate_capital returns True when capital is sufficient."""
        config = make_strategy_config()
        strategy = ConcreteStrategy(config, FakeDataHub())
        assert strategy.validate_capital(Decimal("5000")) is True

    def test_validate_capital_insufficient(self):
        """validate_capital returns False when capital is below minimum."""
        config = make_strategy_config()
        strategy = ConcreteStrategy(config, FakeDataHub())
        assert strategy.validate_capital(Decimal("500")) is False

    def test_validate_capital_at_minimum(self):
        """validate_capital returns True at exactly the minimum."""
        config = make_strategy_config()
        strategy = ConcreteStrategy(config, FakeDataHub())
        assert strategy.validate_capital(Decimal("1000")) is True

    @pytest.mark.asyncio
    async def test_evaluate_returns_signals(self):
        """evaluate() returns configured signals."""
        config = make_strategy_config()
        signal = make_signal()
        strategy = ConcreteStrategy(config, FakeDataHub(), signals_to_return=[signal])
        result = await strategy.evaluate()
        assert result == [signal]

    def test_required_indicators(self):
        """required_indicators() returns indicator list."""
        config = make_strategy_config()
        strategy = ConcreteStrategy(config, FakeDataHub())
        assert strategy.required_indicators() == ["SMA_20"]


# ─── StrategyEngine Lifecycle Tests ──────────────────────────────────────────


class TestStrategyEngineLifecycle:
    """Tests for engine start/stop and strategy enable/disable."""

    @pytest.mark.asyncio
    async def test_start_enables_configured_strategies(self):
        """start() enables strategies with enabled=True in config."""
        config = make_strategy_config(enabled=True)
        strategy = ConcreteStrategy(config, FakeDataHub(), name_override="strat1")

        engine = StrategyEngine(strategies=[strategy])
        await engine.start()

        assert strategy.state == StrategyState.RUNNING
        assert "strat1" in engine._tasks
        assert engine.running is True

        await engine.stop()

    @pytest.mark.asyncio
    async def test_start_skips_disabled_strategies(self):
        """start() does not enable strategies with enabled=False."""
        config = make_strategy_config(enabled=False)
        strategy = ConcreteStrategy(config, FakeDataHub(), name_override="disabled_strat")

        engine = StrategyEngine(strategies=[strategy])
        await engine.start()

        assert strategy.state == StrategyState.IDLE
        assert "disabled_strat" not in engine._tasks

        await engine.stop()

    @pytest.mark.asyncio
    async def test_stop_disables_all_strategies(self):
        """stop() cancels all running strategy tasks."""
        config = make_strategy_config(enabled=True)
        strategy = ConcreteStrategy(config, FakeDataHub(), name_override="strat1")

        engine = StrategyEngine(strategies=[strategy])
        await engine.start()
        await engine.stop()

        assert strategy.state == StrategyState.IDLE
        assert "strat1" not in engine._tasks
        assert engine.running is False

    @pytest.mark.asyncio
    async def test_enable_strategy(self):
        """enable_strategy() starts a strategy's evaluation loop."""
        config = make_strategy_config(enabled=False)
        strategy = ConcreteStrategy(config, FakeDataHub(), name_override="manual_strat")

        engine = StrategyEngine(strategies=[strategy])
        await engine.enable_strategy("manual_strat")

        assert strategy.state == StrategyState.RUNNING
        assert "manual_strat" in engine._tasks

        await engine.stop()

    @pytest.mark.asyncio
    async def test_disable_strategy(self):
        """disable_strategy() stops a running strategy."""
        config = make_strategy_config(enabled=True)
        strategy = ConcreteStrategy(config, FakeDataHub(), name_override="strat1")

        engine = StrategyEngine(strategies=[strategy])
        await engine.start()
        await engine.disable_strategy("strat1")

        assert strategy.state == StrategyState.IDLE
        assert "strat1" not in engine._tasks

        await engine.stop()

    @pytest.mark.asyncio
    async def test_enable_unknown_strategy_raises(self):
        """enable_strategy() raises KeyError for unknown name."""
        engine = StrategyEngine(strategies=[])
        with pytest.raises(KeyError, match="not registered"):
            await engine.enable_strategy("nonexistent")

    @pytest.mark.asyncio
    async def test_disable_unknown_strategy_raises(self):
        """disable_strategy() raises KeyError for unknown name."""
        engine = StrategyEngine(strategies=[])
        with pytest.raises(KeyError, match="not registered"):
            await engine.disable_strategy("nonexistent")

    @pytest.mark.asyncio
    async def test_enable_already_running_is_noop(self):
        """Enabling an already-running strategy does nothing."""
        config = make_strategy_config(enabled=True)
        strategy = ConcreteStrategy(config, FakeDataHub(), name_override="strat1")

        engine = StrategyEngine(strategies=[strategy])
        await engine.enable_strategy("strat1")
        task1 = engine._tasks["strat1"]

        # Enable again
        await engine.enable_strategy("strat1")
        task2 = engine._tasks["strat1"]

        # Same task object
        assert task1 is task2

        await engine.stop()

    @pytest.mark.asyncio
    async def test_strategies_property(self):
        """strategies property returns registered strategies."""
        config = make_strategy_config()
        strategy = ConcreteStrategy(config, FakeDataHub(), name_override="my_strat")

        engine = StrategyEngine(strategies=[strategy])
        assert "my_strat" in engine.strategies


# ─── Signal Routing Tests ────────────────────────────────────────────────────


class TestSignalRouting:
    """Tests for signal generation and routing to callback."""

    @pytest.mark.asyncio
    async def test_signals_routed_to_callback(self):
        """Generated signals are passed to the on_signal callback."""
        received_signals: list[Signal] = []

        def on_signal(signal: Signal) -> None:
            received_signals.append(signal)

        config = make_strategy_config(enabled=True, frequency="tick")
        signal = make_signal(strategy_name="router_test")
        strategy = ConcreteStrategy(
            config, FakeDataHub(), signals_to_return=[signal], name_override="router_test"
        )

        engine = StrategyEngine(strategies=[strategy], on_signal=on_signal)

        # Patch market open to always return True
        with patch("src.strategies.engine._is_market_open", return_value=True):
            await engine.start()
            # Give the loop time to run at least once
            await asyncio.sleep(0.05)
            await engine.stop()

        assert len(received_signals) >= 1
        assert received_signals[0].strategy_name == "router_test"
        assert received_signals[0].symbol == "AAPL"

    @pytest.mark.asyncio
    async def test_no_signals_when_evaluate_returns_empty(self):
        """No signals routed when evaluate() returns empty list."""
        received_signals: list[Signal] = []

        def on_signal(signal: Signal) -> None:
            received_signals.append(signal)

        config = make_strategy_config(enabled=True, frequency="tick")
        strategy = ConcreteStrategy(
            config, FakeDataHub(), signals_to_return=[], name_override="empty_strat"
        )

        engine = StrategyEngine(strategies=[strategy], on_signal=on_signal)

        with patch("src.strategies.engine._is_market_open", return_value=True):
            await engine.start()
            await asyncio.sleep(0.05)
            await engine.stop()

        assert len(received_signals) == 0

    @pytest.mark.asyncio
    async def test_callback_exception_does_not_crash_engine(self):
        """Exception in on_signal callback doesn't crash the strategy loop."""

        def bad_callback(signal: Signal) -> None:
            raise RuntimeError("callback error")

        config = make_strategy_config(enabled=True, frequency="tick")
        signal = make_signal()
        strategy = ConcreteStrategy(
            config, FakeDataHub(), signals_to_return=[signal], name_override="robust_strat"
        )

        engine = StrategyEngine(strategies=[strategy], on_signal=bad_callback)

        with patch("src.strategies.engine._is_market_open", return_value=True):
            await engine.start()
            await asyncio.sleep(0.05)
            await engine.stop()

        # Strategy should still have been evaluated multiple times
        assert strategy.evaluate_count >= 1

    @pytest.mark.asyncio
    async def test_evaluate_exception_does_not_crash_engine(self):
        """Exception in evaluate() doesn't crash the strategy loop."""

        class FailingStrategy(BaseStrategy):
            def __init__(self, config, data_hub):
                super().__init__(config, data_hub)
                self.call_count = 0

            @property
            def name(self) -> str:
                return "failing_strat"

            async def evaluate(self) -> list[Signal]:
                self.call_count += 1
                if self.call_count == 1:
                    raise ValueError("evaluation error")
                return []

            def required_indicators(self) -> list[str]:
                return []

        config = make_strategy_config(enabled=True, frequency="tick")
        strategy = FailingStrategy(config, FakeDataHub())

        engine = StrategyEngine(strategies=[strategy])

        with patch("src.strategies.engine._is_market_open", return_value=True):
            await engine.start()
            await asyncio.sleep(0.05)
            await engine.stop()

        # Should have been called more than once despite the error
        assert strategy.call_count >= 2


# ─── Market Hours Suppression Tests ──────────────────────────────────────────


class TestMarketHoursSuppression:
    """Tests for intraday strategy suppression outside market hours."""

    @pytest.mark.asyncio
    async def test_intraday_suppressed_when_market_closed(self):
        """Intraday strategies don't evaluate when market is closed."""
        received_signals: list[Signal] = []

        def on_signal(signal: Signal) -> None:
            received_signals.append(signal)

        config = make_strategy_config(enabled=True, frequency="1min")
        signal = make_signal()
        strategy = ConcreteStrategy(
            config, FakeDataHub(), signals_to_return=[signal], name_override="intraday_strat"
        )

        engine = StrategyEngine(strategies=[strategy], on_signal=on_signal)

        # Market is closed
        with patch("src.strategies.engine._is_market_open", return_value=False):
            await engine.start()
            await asyncio.sleep(0.1)
            await engine.stop()

        # Should not have evaluated (market closed)
        assert strategy.evaluate_count == 0
        assert len(received_signals) == 0

    @pytest.mark.asyncio
    async def test_daily_strategy_runs_regardless_of_market_hours(self):
        """Daily strategies are not suppressed outside market hours."""
        received_signals: list[Signal] = []

        def on_signal(signal: Signal) -> None:
            received_signals.append(signal)

        config = make_strategy_config(enabled=True, frequency="daily")
        signal = make_signal()
        strategy = ConcreteStrategy(
            config, FakeDataHub(), signals_to_return=[signal], name_override="daily_strat"
        )

        engine = StrategyEngine(strategies=[strategy], on_signal=on_signal)

        # Market is closed, but daily should still run
        with patch("src.strategies.engine._is_market_open", return_value=False):
            await engine.start()
            # Daily interval is 86400s, but the first evaluation happens immediately
            await asyncio.sleep(0.05)
            await engine.stop()

        # Daily strategy should have evaluated at least once
        assert strategy.evaluate_count >= 1

    def test_intraday_frequencies_defined(self):
        """Intraday frequencies include tick through 1hour."""
        assert "tick" in INTRADAY_FREQUENCIES
        assert "1min" in INTRADAY_FREQUENCIES
        assert "5min" in INTRADAY_FREQUENCIES
        assert "15min" in INTRADAY_FREQUENCIES
        assert "1hour" in INTRADAY_FREQUENCIES
        assert "daily" not in INTRADAY_FREQUENCIES
        assert "weekly" not in INTRADAY_FREQUENCIES

    def test_frequency_seconds_mapping(self):
        """Frequency to seconds mapping is correct."""
        assert FREQUENCY_SECONDS["tick"] == 0.0
        assert FREQUENCY_SECONDS["1min"] == 60.0
        assert FREQUENCY_SECONDS["5min"] == 300.0
        assert FREQUENCY_SECONDS["15min"] == 900.0
        assert FREQUENCY_SECONDS["1hour"] == 3600.0
        assert FREQUENCY_SECONDS["daily"] == 86400.0
        assert FREQUENCY_SECONDS["weekly"] == 604800.0


# ─── Capital Validation Tests ────────────────────────────────────────────────


class TestCapitalValidation:
    """Tests for capital validation before enabling strategies."""

    @pytest.mark.asyncio
    async def test_enable_fails_with_insufficient_capital(self):
        """Strategy not enabled when capital is insufficient."""
        config = make_strategy_config(enabled=True)
        strategy = ConcreteStrategy(config, FakeDataHub(), name_override="underfunded")

        allocator = FakeCapitalAllocator(available=Decimal("500"))
        engine = StrategyEngine(
            strategies=[strategy], capital_allocator=allocator
        )

        await engine.enable_strategy("underfunded")

        # Should not be running due to insufficient capital
        assert strategy.state != StrategyState.RUNNING
        assert "underfunded" not in engine._tasks

    @pytest.mark.asyncio
    async def test_enable_succeeds_with_sufficient_capital(self):
        """Strategy enabled when capital is sufficient."""
        config = make_strategy_config(enabled=True)
        strategy = ConcreteStrategy(config, FakeDataHub(), name_override="funded")

        allocator = FakeCapitalAllocator(available=Decimal("10000"))
        engine = StrategyEngine(
            strategies=[strategy], capital_allocator=allocator
        )

        await engine.enable_strategy("funded")

        assert strategy.state == StrategyState.RUNNING
        assert "funded" in engine._tasks

        await engine.stop()

    @pytest.mark.asyncio
    async def test_no_allocator_skips_capital_check(self):
        """Without a capital allocator, capital check is skipped."""
        config = make_strategy_config(enabled=True)
        strategy = ConcreteStrategy(config, FakeDataHub(), name_override="no_alloc")

        engine = StrategyEngine(strategies=[strategy], capital_allocator=None)

        await engine.enable_strategy("no_alloc")

        assert strategy.state == StrategyState.RUNNING
        assert "no_alloc" in engine._tasks

        await engine.stop()


# ─── Strategy Scheduling Tests ───────────────────────────────────────────────


class TestStrategyScheduling:
    """Tests for strategy evaluation frequency."""

    @pytest.mark.asyncio
    async def test_tick_frequency_evaluates_rapidly(self):
        """Tick frequency evaluates as fast as possible."""
        config = make_strategy_config(enabled=True, frequency="tick")
        strategy = ConcreteStrategy(config, FakeDataHub(), name_override="tick_strat")

        engine = StrategyEngine(strategies=[strategy])

        with patch("src.strategies.engine._is_market_open", return_value=True):
            await engine.start()
            await asyncio.sleep(0.1)
            await engine.stop()

        # Tick frequency should evaluate many times in 0.1s
        assert strategy.evaluate_count >= 5

    @pytest.mark.asyncio
    async def test_multiple_strategies_run_independently(self):
        """Multiple strategies run in separate tasks."""
        config1 = make_strategy_config(enabled=True, frequency="tick")
        config2 = make_strategy_config(enabled=True, frequency="tick")
        strategy1 = ConcreteStrategy(config1, FakeDataHub(), name_override="strat_a")
        strategy2 = ConcreteStrategy(config2, FakeDataHub(), name_override="strat_b")

        engine = StrategyEngine(strategies=[strategy1, strategy2])

        with patch("src.strategies.engine._is_market_open", return_value=True):
            await engine.start()
            await asyncio.sleep(0.05)
            await engine.stop()

        # Both should have evaluated
        assert strategy1.evaluate_count >= 1
        assert strategy2.evaluate_count >= 1
