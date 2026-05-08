"""Shared test fixtures for strategy tests.

Provides a FakeDataHub that returns predetermined bar data for testing
strategy signal generation with known price patterns.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.config.settings import StrategyConfig
from src.data.bar_builder import Bar, Timeframe

_BASE_TIME = datetime(2024, 1, 1, 9, 0, tzinfo=timezone.utc)


class FakeDataHub:
    """Fake MarketDataHub that returns predetermined bar data for testing.

    Allows tests to inject specific price patterns and verify strategy
    signal generation without needing real market data.
    """

    def __init__(self, bars: dict[str, list[Bar]] | None = None) -> None:
        """Initialize with optional pre-set bars.

        Args:
            bars: Dict mapping symbol to list of bars.
        """
        self._bars: dict[str, dict[Timeframe, list[Bar]]] = {}
        if bars:
            for symbol, bar_list in bars.items():
                if bar_list:
                    tf = bar_list[0].timeframe
                    self._bars.setdefault(symbol, {})[tf] = bar_list

    def set_bars(self, symbol: str, timeframe: Timeframe, bars: list[Bar]) -> None:
        """Set bars for a specific symbol and timeframe."""
        self._bars.setdefault(symbol, {})[timeframe] = bars

    def get_history(self, symbol: str, timeframe: Timeframe, periods: int = 20) -> list[Bar]:
        """Return the last N bars for a symbol/timeframe."""
        symbol_bars = self._bars.get(symbol, {})
        tf_bars = symbol_bars.get(timeframe, [])
        return tf_bars[-periods:]

    def get_latest_bar(self, symbol: str, timeframe: Timeframe) -> Bar | None:
        """Return the latest bar for a symbol/timeframe."""
        symbol_bars = self._bars.get(symbol, {})
        tf_bars = symbol_bars.get(timeframe, [])
        return tf_bars[-1] if tf_bars else None


def make_bars(
    symbol: str,
    prices: list[float],
    timeframe: Timeframe = Timeframe.FIVE_MIN,
    spread: float = 0.5,
) -> list[Bar]:
    """Create a list of bars from a list of close prices.

    Generates bars with open/high/low derived from close price and spread.

    Args:
        symbol: Ticker symbol.
        prices: List of closing prices.
        timeframe: Timeframe for the bars.
        spread: Amount to add/subtract for high/low.

    Returns:
        List of Bar objects.
    """
    bars = []
    for i, price in enumerate(prices):
        bar = Bar(
            symbol=symbol,
            timeframe=timeframe,
            open=price - spread * 0.3,
            high=price + spread,
            low=price - spread,
            close=price,
            volume=1000.0,
            timestamp=_BASE_TIME + timedelta(minutes=i * 5),
        )
        bars.append(bar)
    return bars


def make_strategy_config(
    symbols: list[str] | None = None,
    frequency: str = "5min",
    parameters: dict | None = None,
) -> StrategyConfig:
    """Create a StrategyConfig for testing.

    Args:
        symbols: List of symbols. Defaults to ["AAPL"].
        frequency: Evaluation frequency. Defaults to "5min".
        parameters: Strategy-specific parameters.

    Returns:
        A StrategyConfig instance.
    """
    return StrategyConfig(
        enabled=True,
        frequency=frequency,
        symbols=symbols or ["AAPL"],
        asset_classes=["equity"],
        parameters=parameters or {},
    )
