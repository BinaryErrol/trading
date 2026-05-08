"""Tests for BreakoutStrategy signal generation."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.data.bar_builder import Bar, Timeframe
from src.strategies.implementations.breakout import BreakoutStrategy
from src.strategies.signals import SignalDirection

from .conftest import FakeDataHub, make_strategy_config

_BASE_TIME = datetime(2024, 1, 1, 9, 0, tzinfo=timezone.utc)


def make_breakout_bars(
    symbol: str,
    prices: list[float],
    highs: list[float] | None = None,
    lows: list[float] | None = None,
    timeframe: Timeframe = Timeframe.FIVE_MIN,
) -> list[Bar]:
    """Create bars with explicit high/low for breakout testing.

    Args:
        symbol: Ticker symbol.
        prices: List of closing prices.
        highs: Optional explicit high prices. Defaults to close + 0.5.
        lows: Optional explicit low prices. Defaults to close - 0.5.
        timeframe: Timeframe for bars.
    """
    if highs is None:
        highs = [p + 0.5 for p in prices]
    if lows is None:
        lows = [p - 0.5 for p in prices]

    bars = []
    for i, (price, high, low) in enumerate(zip(prices, highs, lows)):
        bar = Bar(
            symbol=symbol,
            timeframe=timeframe,
            open=price - 0.2,
            high=high,
            low=low,
            close=price,
            volume=1000.0,
            timestamp=_BASE_TIME + timedelta(minutes=i * 5),
        )
        bars.append(bar)
    return bars


class TestBreakoutStrategy:
    """Test BreakoutStrategy with known consolidation and breakout patterns."""

    def _make_strategy(
        self,
        data_hub: FakeDataHub,
        consolidation_period: int = 5,
        breakout_atr_multiple: float = 1.0,
        symbols: list[str] | None = None,
    ) -> BreakoutStrategy:
        config = make_strategy_config(
            symbols=symbols or ["AAPL"],
            parameters={
                "consolidation_period": consolidation_period,
                "breakout_atr_multiple": breakout_atr_multiple,
            },
        )
        return BreakoutStrategy(config=config, data_hub=data_hub)

    @pytest.mark.asyncio
    async def test_upside_breakout_generates_long_signal(self):
        """Price breaking above consolidation range + ATR generates LONG."""
        # Need consolidation_period(5) + 14 + 1 = 20 bars
        # First 19 bars: consolidation around 100 with range [99, 101]
        # Last bar: breakout above 101 + ATR*multiple
        consolidation_prices = [100.0] * 19
        consolidation_highs = [101.0] * 19
        consolidation_lows = [99.0] * 19

        # ATR with high=101, low=99 → TR = 2.0 each bar, ATR = 2.0
        # Breakout threshold = range_high(101) + ATR(2.0) * multiple(1.0) = 103.0
        # Set last bar price above 103.0
        breakout_price = 104.0
        all_prices = consolidation_prices + [breakout_price]
        all_highs = consolidation_highs + [breakout_price + 0.5]
        all_lows = consolidation_lows + [breakout_price - 0.5]

        bars = make_breakout_bars("AAPL", all_prices, all_highs, all_lows)
        data_hub = FakeDataHub()
        data_hub.set_bars("AAPL", Timeframe.FIVE_MIN, bars)

        strategy = self._make_strategy(
            data_hub, consolidation_period=5, breakout_atr_multiple=1.0
        )
        signals = await strategy.evaluate()

        assert len(signals) == 1
        assert signals[0].direction == SignalDirection.LONG
        assert signals[0].metadata["breakout_type"] == "upside"

    @pytest.mark.asyncio
    async def test_downside_breakout_generates_short_signal(self):
        """Price breaking below consolidation range - ATR generates SHORT."""
        # Consolidation around 100, range [99, 101], ATR = 2.0
        # Breakout threshold = range_low(99) - ATR(2.0) * multiple(1.0) = 97.0
        consolidation_prices = [100.0] * 19
        consolidation_highs = [101.0] * 19
        consolidation_lows = [99.0] * 19

        breakout_price = 96.0
        all_prices = consolidation_prices + [breakout_price]
        all_highs = consolidation_highs + [breakout_price + 0.5]
        all_lows = consolidation_lows + [breakout_price - 0.5]

        bars = make_breakout_bars("AAPL", all_prices, all_highs, all_lows)
        data_hub = FakeDataHub()
        data_hub.set_bars("AAPL", Timeframe.FIVE_MIN, bars)

        strategy = self._make_strategy(
            data_hub, consolidation_period=5, breakout_atr_multiple=1.0
        )
        signals = await strategy.evaluate()

        assert len(signals) == 1
        assert signals[0].direction == SignalDirection.SHORT
        assert signals[0].metadata["breakout_type"] == "downside"

    @pytest.mark.asyncio
    async def test_no_signal_within_consolidation_range(self):
        """No signal when price stays within consolidation range + ATR."""
        # Price stays at 100, well within range [99, 101] + ATR buffer
        consolidation_prices = [100.0] * 20
        consolidation_highs = [101.0] * 20
        consolidation_lows = [99.0] * 20

        bars = make_breakout_bars("AAPL", consolidation_prices, consolidation_highs, consolidation_lows)
        data_hub = FakeDataHub()
        data_hub.set_bars("AAPL", Timeframe.FIVE_MIN, bars)

        strategy = self._make_strategy(
            data_hub, consolidation_period=5, breakout_atr_multiple=1.0
        )
        signals = await strategy.evaluate()

        assert len(signals) == 0

    @pytest.mark.asyncio
    async def test_no_signal_with_insufficient_bars(self):
        """No signal when there aren't enough bars."""
        prices = [100.0, 102.0, 104.0]
        bars = make_breakout_bars("AAPL", prices)
        data_hub = FakeDataHub()
        data_hub.set_bars("AAPL", Timeframe.FIVE_MIN, bars)

        strategy = self._make_strategy(data_hub, consolidation_period=5)
        signals = await strategy.evaluate()

        assert len(signals) == 0

    @pytest.mark.asyncio
    async def test_higher_atr_multiple_requires_larger_breakout(self):
        """Higher ATR multiple means price must move further to trigger."""
        # With ATR=2.0 and multiple=2.0, threshold = 101 + 4.0 = 105.0
        # Price at 104 should NOT trigger with multiple=2.0
        consolidation_prices = [100.0] * 19
        consolidation_highs = [101.0] * 19
        consolidation_lows = [99.0] * 19

        breakout_price = 104.0  # Above 103 (1x ATR) but below 105 (2x ATR)
        all_prices = consolidation_prices + [breakout_price]
        all_highs = consolidation_highs + [breakout_price + 0.5]
        all_lows = consolidation_lows + [breakout_price - 0.5]

        bars = make_breakout_bars("AAPL", all_prices, all_highs, all_lows)
        data_hub = FakeDataHub()
        data_hub.set_bars("AAPL", Timeframe.FIVE_MIN, bars)

        strategy = self._make_strategy(
            data_hub, consolidation_period=5, breakout_atr_multiple=2.0
        )
        signals = await strategy.evaluate()

        assert len(signals) == 0

    @pytest.mark.asyncio
    async def test_required_indicators(self):
        """required_indicators returns range and ATR indicator names."""
        data_hub = FakeDataHub()
        strategy = self._make_strategy(data_hub, consolidation_period=20)
        assert strategy.required_indicators() == ["HIGH_20", "LOW_20", "ATR_14"]
