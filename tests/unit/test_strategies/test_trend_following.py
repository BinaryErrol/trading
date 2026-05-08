"""Tests for TrendFollowingStrategy signal generation."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.data.bar_builder import Bar, Timeframe
from src.strategies.implementations.trend_following import TrendFollowingStrategy
from src.strategies.signals import SignalDirection

from .conftest import FakeDataHub, make_strategy_config

_BASE_TIME = datetime(2024, 1, 1, 9, 0, tzinfo=timezone.utc)


def make_trend_bars(
    symbol: str,
    prices: list[float],
    timeframe: Timeframe = Timeframe.FIVE_MIN,
    volatility: float = 2.0,
) -> list[Bar]:
    """Create bars with configurable volatility for ATR testing.

    Args:
        symbol: Ticker symbol.
        prices: List of closing prices.
        timeframe: Timeframe for bars.
        volatility: Amount to add/subtract for high/low (affects ATR).
    """
    bars = []
    for i, price in enumerate(prices):
        bar = Bar(
            symbol=symbol,
            timeframe=timeframe,
            open=price - volatility * 0.3,
            high=price + volatility,
            low=price - volatility,
            close=price,
            volume=1000.0,
            timestamp=_BASE_TIME + timedelta(minutes=i * 5),
        )
        bars.append(bar)
    return bars


class TestTrendFollowingStrategy:
    """Test TrendFollowingStrategy with known trend patterns and ATR."""

    def _make_strategy(
        self,
        data_hub: FakeDataHub,
        fast_ma: int = 3,
        slow_ma: int = 5,
        atr_filter: float = 0.01,
        symbols: list[str] | None = None,
    ) -> TrendFollowingStrategy:
        config = make_strategy_config(
            symbols=symbols or ["AAPL"],
            parameters={
                "fast_ma": fast_ma,
                "slow_ma": slow_ma,
                "atr_filter": atr_filter,
            },
        )
        return TrendFollowingStrategy(config=config, data_hub=data_hub)

    @pytest.mark.asyncio
    async def test_long_signal_with_uptrend_and_sufficient_atr(self):
        """LONG signal when fast MA > slow MA and ATR filter is met."""
        # Strong uptrend with high volatility
        # Need slow_ma(5) + 14 = 19 bars
        prices = [
            90.0, 91.0, 92.0, 93.0, 94.0,
            95.0, 96.0, 97.0, 98.0, 99.0,
            100.0, 101.0, 102.0, 103.0, 104.0,
            106.0, 108.0, 110.0, 112.0,
        ]
        bars = make_trend_bars("AAPL", prices, volatility=2.0)
        data_hub = FakeDataHub()
        data_hub.set_bars("AAPL", Timeframe.FIVE_MIN, bars)

        strategy = self._make_strategy(data_hub, fast_ma=3, slow_ma=5, atr_filter=0.01)
        signals = await strategy.evaluate()

        assert len(signals) == 1
        assert signals[0].direction == SignalDirection.LONG
        assert signals[0].metadata["atr_ratio"] >= 0.01

    @pytest.mark.asyncio
    async def test_short_signal_with_downtrend_and_sufficient_atr(self):
        """SHORT signal when fast MA < slow MA and ATR filter is met."""
        # Strong downtrend with high volatility
        prices = [
            120.0, 119.0, 118.0, 117.0, 116.0,
            115.0, 114.0, 113.0, 112.0, 111.0,
            110.0, 108.0, 106.0, 104.0, 102.0,
            100.0, 97.0, 94.0, 91.0,
        ]
        bars = make_trend_bars("AAPL", prices, volatility=2.0)
        data_hub = FakeDataHub()
        data_hub.set_bars("AAPL", Timeframe.FIVE_MIN, bars)

        strategy = self._make_strategy(data_hub, fast_ma=3, slow_ma=5, atr_filter=0.01)
        signals = await strategy.evaluate()

        assert len(signals) == 1
        assert signals[0].direction == SignalDirection.SHORT

    @pytest.mark.asyncio
    async def test_no_signal_when_atr_filter_not_met(self):
        """No signal when ATR is too low (trend not strong enough)."""
        # Uptrend but very low volatility
        prices = [
            100.0, 100.1, 100.2, 100.3, 100.4,
            100.5, 100.6, 100.7, 100.8, 100.9,
            101.0, 101.1, 101.2, 101.3, 101.4,
            101.5, 101.6, 101.7, 101.8,
        ]
        # Very low volatility (0.01) means ATR will be tiny
        bars = make_trend_bars("AAPL", prices, volatility=0.01)
        data_hub = FakeDataHub()
        data_hub.set_bars("AAPL", Timeframe.FIVE_MIN, bars)

        # Set a high ATR filter that won't be met
        strategy = self._make_strategy(data_hub, fast_ma=3, slow_ma=5, atr_filter=0.05)
        signals = await strategy.evaluate()

        assert len(signals) == 0

    @pytest.mark.asyncio
    async def test_no_signal_with_insufficient_bars(self):
        """No signal when there aren't enough bars."""
        prices = [100.0, 102.0, 104.0]  # Only 3 bars, need 19
        bars = make_trend_bars("AAPL", prices)
        data_hub = FakeDataHub()
        data_hub.set_bars("AAPL", Timeframe.FIVE_MIN, bars)

        strategy = self._make_strategy(data_hub, fast_ma=3, slow_ma=5)
        signals = await strategy.evaluate()

        assert len(signals) == 0

    @pytest.mark.asyncio
    async def test_required_indicators(self):
        """required_indicators returns MA and ATR indicator names."""
        data_hub = FakeDataHub()
        strategy = self._make_strategy(data_hub, fast_ma=10, slow_ma=30)
        assert strategy.required_indicators() == ["SMA_10", "SMA_30", "ATR_14"]
