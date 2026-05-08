"""Tests for MeanReversionStrategy signal generation."""

from __future__ import annotations

import pytest

from src.data.bar_builder import Timeframe
from src.strategies.implementations.mean_reversion import MeanReversionStrategy
from src.strategies.signals import SignalDirection

from .conftest import FakeDataHub, make_bars, make_strategy_config


class TestMeanReversionStrategy:
    """Test MeanReversionStrategy with known price patterns."""

    def _make_strategy(
        self,
        data_hub: FakeDataHub,
        lookback_period: int = 10,
        z_score_threshold: float = 2.0,
        symbols: list[str] | None = None,
    ) -> MeanReversionStrategy:
        config = make_strategy_config(
            symbols=symbols or ["AAPL"],
            parameters={
                "lookback_period": lookback_period,
                "z_score_threshold": z_score_threshold,
            },
        )
        return MeanReversionStrategy(config=config, data_hub=data_hub)

    @pytest.mark.asyncio
    async def test_long_signal_when_price_below_mean(self):
        """Z-score below negative threshold generates a LONG signal."""
        # 9 bars at 100, then a sharp drop to 80 — z-score will be very negative
        prices = [100.0] * 9 + [80.0]
        bars = make_bars("AAPL", prices)
        data_hub = FakeDataHub()
        data_hub.set_bars("AAPL", Timeframe.FIVE_MIN, bars)

        strategy = self._make_strategy(data_hub, lookback_period=10, z_score_threshold=2.0)
        signals = await strategy.evaluate()

        assert len(signals) == 1
        assert signals[0].direction == SignalDirection.LONG
        assert signals[0].symbol == "AAPL"
        assert signals[0].metadata["z_score"] < -2.0

    @pytest.mark.asyncio
    async def test_short_signal_when_price_above_mean(self):
        """Z-score above positive threshold generates a SHORT signal."""
        # 9 bars at 100, then a sharp spike to 120 — z-score will be very positive
        prices = [100.0] * 9 + [120.0]
        bars = make_bars("AAPL", prices)
        data_hub = FakeDataHub()
        data_hub.set_bars("AAPL", Timeframe.FIVE_MIN, bars)

        strategy = self._make_strategy(data_hub, lookback_period=10, z_score_threshold=2.0)
        signals = await strategy.evaluate()

        assert len(signals) == 1
        assert signals[0].direction == SignalDirection.SHORT
        assert signals[0].symbol == "AAPL"
        assert signals[0].metadata["z_score"] > 2.0

    @pytest.mark.asyncio
    async def test_no_signal_when_z_score_within_threshold(self):
        """No signal when z-score is within threshold bounds."""
        # Prices hover around 100 with small variation
        prices = [99.0, 100.0, 101.0, 100.0, 99.5, 100.5, 100.0, 99.8, 100.2, 100.0]
        bars = make_bars("AAPL", prices)
        data_hub = FakeDataHub()
        data_hub.set_bars("AAPL", Timeframe.FIVE_MIN, bars)

        strategy = self._make_strategy(data_hub, lookback_period=10, z_score_threshold=2.0)
        signals = await strategy.evaluate()

        assert len(signals) == 0

    @pytest.mark.asyncio
    async def test_no_signal_with_insufficient_bars(self):
        """No signal when there aren't enough bars for lookback."""
        prices = [100.0, 80.0]  # Only 2 bars, need 10
        bars = make_bars("AAPL", prices)
        data_hub = FakeDataHub()
        data_hub.set_bars("AAPL", Timeframe.FIVE_MIN, bars)

        strategy = self._make_strategy(data_hub, lookback_period=10, z_score_threshold=2.0)
        signals = await strategy.evaluate()

        assert len(signals) == 0

    @pytest.mark.asyncio
    async def test_no_signal_with_zero_std_dev(self):
        """No signal when all prices are identical (std_dev = 0)."""
        prices = [100.0] * 10
        bars = make_bars("AAPL", prices)
        data_hub = FakeDataHub()
        data_hub.set_bars("AAPL", Timeframe.FIVE_MIN, bars)

        strategy = self._make_strategy(data_hub, lookback_period=10, z_score_threshold=2.0)
        signals = await strategy.evaluate()

        assert len(signals) == 0

    @pytest.mark.asyncio
    async def test_required_indicators(self):
        """required_indicators returns the z-score indicator name."""
        data_hub = FakeDataHub()
        strategy = self._make_strategy(data_hub, lookback_period=20)
        assert strategy.required_indicators() == ["ZSCORE_20"]
