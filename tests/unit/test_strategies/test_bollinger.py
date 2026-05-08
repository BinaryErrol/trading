"""Tests for BollingerStrategy signal generation."""

from __future__ import annotations

import pytest

from src.data.bar_builder import Timeframe
from src.strategies.implementations.bollinger import BollingerStrategy
from src.strategies.signals import SignalDirection

from .conftest import FakeDataHub, make_bars, make_strategy_config


class TestBollingerStrategy:
    """Test BollingerStrategy with known price patterns."""

    def _make_strategy(
        self,
        data_hub: FakeDataHub,
        bb_period: int = 10,
        bb_std: float = 2.0,
        entry_band: str = "both",
        symbols: list[str] | None = None,
    ) -> BollingerStrategy:
        config = make_strategy_config(
            symbols=symbols or ["AAPL"],
            parameters={
                "bb_period": bb_period,
                "bb_std": bb_std,
                "entry_band": entry_band,
            },
        )
        return BollingerStrategy(config=config, data_hub=data_hub)

    @pytest.mark.asyncio
    async def test_long_signal_on_lower_band_touch(self):
        """Price at or below lower band generates a LONG signal."""
        # 9 bars at 100, then drop to 80 — well below lower band
        prices = [100.0] * 9 + [80.0]
        bars = make_bars("AAPL", prices)
        data_hub = FakeDataHub()
        data_hub.set_bars("AAPL", Timeframe.FIVE_MIN, bars)

        strategy = self._make_strategy(data_hub, bb_period=10, bb_std=2.0)
        signals = await strategy.evaluate()

        assert len(signals) == 1
        assert signals[0].direction == SignalDirection.LONG
        assert signals[0].symbol == "AAPL"
        assert signals[0].metadata["band_touch"] == "lower"

    @pytest.mark.asyncio
    async def test_short_signal_on_upper_band_touch(self):
        """Price at or above upper band generates a SHORT signal."""
        # 9 bars at 100, then spike to 120 — well above upper band
        prices = [100.0] * 9 + [120.0]
        bars = make_bars("AAPL", prices)
        data_hub = FakeDataHub()
        data_hub.set_bars("AAPL", Timeframe.FIVE_MIN, bars)

        strategy = self._make_strategy(data_hub, bb_period=10, bb_std=2.0)
        signals = await strategy.evaluate()

        assert len(signals) == 1
        assert signals[0].direction == SignalDirection.SHORT
        assert signals[0].symbol == "AAPL"
        assert signals[0].metadata["band_touch"] == "upper"

    @pytest.mark.asyncio
    async def test_no_signal_when_price_within_bands(self):
        """No signal when price is between the bands."""
        # Prices hover around 100 with small variation
        prices = [99.0, 100.0, 101.0, 100.0, 99.5, 100.5, 100.0, 99.8, 100.2, 100.0]
        bars = make_bars("AAPL", prices)
        data_hub = FakeDataHub()
        data_hub.set_bars("AAPL", Timeframe.FIVE_MIN, bars)

        strategy = self._make_strategy(data_hub, bb_period=10, bb_std=2.0)
        signals = await strategy.evaluate()

        assert len(signals) == 0

    @pytest.mark.asyncio
    async def test_entry_band_lower_only(self):
        """Only lower band signals when entry_band='lower'."""
        # Price spikes above upper band
        prices = [100.0] * 9 + [120.0]
        bars = make_bars("AAPL", prices)
        data_hub = FakeDataHub()
        data_hub.set_bars("AAPL", Timeframe.FIVE_MIN, bars)

        strategy = self._make_strategy(data_hub, bb_period=10, bb_std=2.0, entry_band="lower")
        signals = await strategy.evaluate()

        # Should NOT generate a SHORT signal since entry_band is "lower"
        assert len(signals) == 0

    @pytest.mark.asyncio
    async def test_entry_band_upper_only(self):
        """Only upper band signals when entry_band='upper'."""
        # Price drops below lower band
        prices = [100.0] * 9 + [80.0]
        bars = make_bars("AAPL", prices)
        data_hub = FakeDataHub()
        data_hub.set_bars("AAPL", Timeframe.FIVE_MIN, bars)

        strategy = self._make_strategy(data_hub, bb_period=10, bb_std=2.0, entry_band="upper")
        signals = await strategy.evaluate()

        # Should NOT generate a LONG signal since entry_band is "upper"
        assert len(signals) == 0

    @pytest.mark.asyncio
    async def test_no_signal_with_insufficient_bars(self):
        """No signal when there aren't enough bars for the period."""
        prices = [100.0, 80.0]  # Only 2 bars, need 10
        bars = make_bars("AAPL", prices)
        data_hub = FakeDataHub()
        data_hub.set_bars("AAPL", Timeframe.FIVE_MIN, bars)

        strategy = self._make_strategy(data_hub, bb_period=10, bb_std=2.0)
        signals = await strategy.evaluate()

        assert len(signals) == 0

    @pytest.mark.asyncio
    async def test_required_indicators(self):
        """required_indicators returns the Bollinger Band indicator name."""
        data_hub = FakeDataHub()
        strategy = self._make_strategy(data_hub, bb_period=20, bb_std=2.5)
        assert strategy.required_indicators() == ["BB_20_2.5"]
