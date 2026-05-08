"""Tests for RSIDivergenceStrategy signal generation."""

from __future__ import annotations

import pytest

from src.data.bar_builder import Timeframe
from src.strategies.implementations.rsi_divergence import RSIDivergenceStrategy
from src.strategies.signals import SignalDirection

from .conftest import FakeDataHub, make_bars, make_strategy_config


class TestRSIDivergenceStrategy:
    """Test RSIDivergenceStrategy with known price patterns."""

    def _make_strategy(
        self,
        data_hub: FakeDataHub,
        rsi_period: int = 5,
        overbought: int = 70,
        oversold: int = 30,
        symbols: list[str] | None = None,
    ) -> RSIDivergenceStrategy:
        config = make_strategy_config(
            symbols=symbols or ["AAPL"],
            parameters={
                "rsi_period": rsi_period,
                "overbought": overbought,
                "oversold": oversold,
            },
        )
        return RSIDivergenceStrategy(config=config, data_hub=data_hub)

    @pytest.mark.asyncio
    async def test_long_signal_when_oversold(self):
        """RSI below oversold level generates a LONG signal."""
        # Consistent downtrend — RSI should be very low
        prices = [100.0, 95.0, 90.0, 85.0, 80.0, 75.0]
        bars = make_bars("AAPL", prices)
        data_hub = FakeDataHub()
        data_hub.set_bars("AAPL", Timeframe.FIVE_MIN, bars)

        strategy = self._make_strategy(data_hub, rsi_period=5, oversold=30)
        signals = await strategy.evaluate()

        assert len(signals) == 1
        assert signals[0].direction == SignalDirection.LONG
        assert signals[0].symbol == "AAPL"
        assert signals[0].metadata["rsi"] < 30

    @pytest.mark.asyncio
    async def test_short_signal_when_overbought(self):
        """RSI above overbought level generates a SHORT signal."""
        # Consistent uptrend — RSI should be very high
        prices = [100.0, 105.0, 110.0, 115.0, 120.0, 125.0]
        bars = make_bars("AAPL", prices)
        data_hub = FakeDataHub()
        data_hub.set_bars("AAPL", Timeframe.FIVE_MIN, bars)

        strategy = self._make_strategy(data_hub, rsi_period=5, overbought=70)
        signals = await strategy.evaluate()

        assert len(signals) == 1
        assert signals[0].direction == SignalDirection.SHORT
        assert signals[0].symbol == "AAPL"
        assert signals[0].metadata["rsi"] > 70

    @pytest.mark.asyncio
    async def test_no_signal_when_rsi_neutral(self):
        """No signal when RSI is between oversold and overbought."""
        # Mixed movement — RSI should be around 50
        prices = [100.0, 102.0, 99.0, 101.0, 100.0, 101.0]
        bars = make_bars("AAPL", prices)
        data_hub = FakeDataHub()
        data_hub.set_bars("AAPL", Timeframe.FIVE_MIN, bars)

        strategy = self._make_strategy(data_hub, rsi_period=5, overbought=70, oversold=30)
        signals = await strategy.evaluate()

        assert len(signals) == 0

    @pytest.mark.asyncio
    async def test_no_signal_with_insufficient_bars(self):
        """No signal when there aren't enough bars for RSI calculation."""
        prices = [100.0, 75.0]  # Only 2 bars, need rsi_period + 1 = 6
        bars = make_bars("AAPL", prices)
        data_hub = FakeDataHub()
        data_hub.set_bars("AAPL", Timeframe.FIVE_MIN, bars)

        strategy = self._make_strategy(data_hub, rsi_period=5, oversold=30)
        signals = await strategy.evaluate()

        assert len(signals) == 0

    @pytest.mark.asyncio
    async def test_custom_overbought_oversold_levels(self):
        """Custom overbought/oversold levels are respected."""
        # Consistent uptrend — RSI = 100
        prices = [100.0, 105.0, 110.0, 115.0, 120.0, 125.0]
        bars = make_bars("AAPL", prices)
        data_hub = FakeDataHub()
        data_hub.set_bars("AAPL", Timeframe.FIVE_MIN, bars)

        # With overbought at 90, RSI of 100 should still trigger
        strategy = self._make_strategy(data_hub, rsi_period=5, overbought=90)
        signals = await strategy.evaluate()

        assert len(signals) == 1
        assert signals[0].direction == SignalDirection.SHORT

    @pytest.mark.asyncio
    async def test_required_indicators(self):
        """required_indicators returns the RSI indicator name."""
        data_hub = FakeDataHub()
        strategy = self._make_strategy(data_hub, rsi_period=14)
        assert strategy.required_indicators() == ["RSI_14"]
