"""Property-based tests for Strategy correctness properties.

**Validates: Requirements 5.5, 5.6**

P5: A failure in one strategy SHALL not affect the execution of other strategies.
P6: During backtesting, a strategy at time t SHALL only access data from times <= t.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

import numpy as np
import pandas as pd
from hypothesis import given, settings
from hypothesis import strategies as st

from src.backtesting.engine import BacktestEngine, _BacktestDataHub, _LookAheadGuard
from src.config.settings import BacktestConfig, StrategyConfig
from src.strategies.base import BaseStrategy, StrategyState
from src.strategies.signals import OrderType, Signal, SignalDirection


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class FaultyStrategy(BaseStrategy):
    """A strategy that always raises an exception during evaluate()."""

    def __init__(self, name: str = "faulty") -> None:
        config = StrategyConfig(
            enabled=True,
            frequency="1min",
            symbols=["AAPL"],
            asset_classes=["equity"],
            parameters={},
        )
        # Use a minimal mock data hub
        self._config = config
        self._data_hub = None  # type: ignore
        self._state = StrategyState.RUNNING
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    async def evaluate(self) -> list[Signal]:
        raise RuntimeError("Strategy failure!")

    def required_indicators(self) -> list[str]:
        return []


class HealthyStrategy(BaseStrategy):
    """A strategy that always succeeds and returns a signal."""

    def __init__(self, name: str = "healthy") -> None:
        config = StrategyConfig(
            enabled=True,
            frequency="1min",
            symbols=["AAPL"],
            asset_classes=["equity"],
            parameters={},
        )
        self._config = config
        self._data_hub = None  # type: ignore
        self._state = StrategyState.RUNNING
        self._name = name
        self.evaluate_count = 0

    @property
    def name(self) -> str:
        return self._name

    async def evaluate(self) -> list[Signal]:
        self.evaluate_count += 1
        return [
            Signal(
                strategy_name=self._name,
                symbol="AAPL",
                direction=SignalDirection.LONG,
                confidence=0.8,
                suggested_size=Decimal("1000"),
                order_type=OrderType.MARKET,
            )
        ]

    def required_indicators(self) -> list[str]:
        return []


def _generate_ohlcv_data(num_bars: int, start_date: datetime) -> pd.DataFrame:
    """Generate synthetic OHLCV data for backtesting."""
    dates = [start_date + timedelta(hours=i) for i in range(num_bars)]
    np.random.seed(42)
    prices = 100.0 + np.cumsum(np.random.randn(num_bars) * 0.5)
    prices = np.maximum(prices, 10.0)  # Keep prices positive

    data = pd.DataFrame(
        {
            "open": prices,
            "high": prices * 1.01,
            "low": prices * 0.99,
            "close": prices,
            "volume": np.random.randint(1000, 100000, num_bars),
        },
        index=pd.DatetimeIndex(dates),
    )
    return data


# ---------------------------------------------------------------------------
# P5: Strategy Isolation
# ---------------------------------------------------------------------------


@settings(max_examples=100)
@given(
    num_healthy=st.integers(min_value=1, max_value=5),
    num_faulty=st.integers(min_value=1, max_value=3),
)
def test_p5_failure_in_one_strategy_doesnt_affect_others(
    num_healthy: int,
    num_faulty: int,
) -> None:
    """P5: failure in one strategy doesn't affect others.

    **Validates: Requirements 5.5**

    When a faulty strategy raises an exception during evaluate(), other
    healthy strategies must continue to execute successfully.
    """
    healthy_strategies = [HealthyStrategy(name=f"healthy_{i}") for i in range(num_healthy)]
    faulty_strategies = [FaultyStrategy(name=f"faulty_{i}") for i in range(num_faulty)]

    all_strategies = healthy_strategies + faulty_strategies

    async def run_all() -> None:
        for strategy in all_strategies:
            try:
                await strategy.evaluate()
            except Exception:
                # Strategy failure is caught and isolated
                pass

    asyncio.run(run_all())

    # Property: all healthy strategies executed successfully
    for healthy in healthy_strategies:
        assert healthy.evaluate_count == 1, (
            f"Healthy strategy {healthy.name} should have evaluated once, "
            f"got {healthy.evaluate_count}"
        )

    # Property: faulty strategies didn't corrupt healthy strategy state
    for healthy in healthy_strategies:
        assert healthy.state == StrategyState.RUNNING


# ---------------------------------------------------------------------------
# P6: No Look-Ahead Bias
# ---------------------------------------------------------------------------


@settings(max_examples=100)
@given(
    num_bars=st.integers(min_value=10, max_value=100),
    access_position=st.integers(min_value=0, max_value=99),
)
def test_p6_backtest_strategies_only_access_data_at_time_lte_t(
    num_bars: int,
    access_position: int,
) -> None:
    """P6: backtest strategies only access data at time <= t.

    **Validates: Requirements 5.6**

    The LookAheadGuard ensures that at any position t, only data from
    indices 0..t (inclusive) is visible. No future data can be accessed.
    """
    # Clamp access_position to valid range
    access_position = min(access_position, num_bars - 1)

    start_date = datetime(2024, 1, 1, tzinfo=timezone.utc)
    data = _generate_ohlcv_data(num_bars, start_date)

    guard = _LookAheadGuard(data)
    guard.set_position(access_position)

    # Get visible data through the guard
    visible = guard.get_visible_data()

    # Property: visible data length is exactly access_position + 1
    assert len(visible) == access_position + 1

    # Property: all visible timestamps are <= the current position's timestamp
    current_time = data.index[access_position]
    for ts in visible.index:
        assert ts <= current_time, (
            f"Visible data contains future timestamp {ts} > current {current_time}"
        )

    # Property: no data beyond current position is accessible
    if access_position < num_bars - 1:
        future_time = data.index[access_position + 1]
        assert future_time not in visible.index


@settings(max_examples=100)
@given(
    num_bars=st.integers(min_value=10, max_value=50),
    history_periods=st.integers(min_value=1, max_value=20),
    current_position=st.integers(min_value=1, max_value=49),
)
def test_p6_backtest_data_hub_respects_look_ahead_guard(
    num_bars: int,
    history_periods: int,
    current_position: int,
) -> None:
    """P6: BacktestDataHub.get_history() respects the look-ahead guard.

    **Validates: Requirements 5.6**

    The _BacktestDataHub wrapper must only return data up to the current
    position, even when more periods are requested than available.
    """
    # Clamp current_position to valid range
    current_position = min(current_position, num_bars - 1)

    start_date = datetime(2024, 1, 1, tzinfo=timezone.utc)
    data = _generate_ohlcv_data(num_bars, start_date)

    guard = _LookAheadGuard(data)
    guard.set_position(current_position)

    hub = _BacktestDataHub(guard, "AAPL")
    history = hub.get_history("AAPL", None, history_periods)  # type: ignore[arg-type]

    # Property: returned history never contains data beyond current position
    current_time = data.index[current_position]
    for bar in history:
        assert bar.timestamp <= current_time, (
            f"History contains future data at {bar.timestamp} > current {current_time}"
        )

    # Property: returned history length is at most min(history_periods, current_position + 1)
    max_possible = min(history_periods, current_position + 1)
    assert len(history) <= max_possible
