"""Tests for VWAPStrategy signal generation."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.data.bar_builder import Bar, Timeframe
from src.strategies.implementations.vwap import VWAPStrategy
from src.strategies.signals import SignalDirection

from .conftest import FakeDataHub, make_bars, make_strategy_config

_BASE_TIME = datetime(2024, 1, 1, 9, 0, tzinfo=timezone.utc)


def make_vwap_bars(
    symbol: str,
    prices: list[float],
    volumes: list[float],
    timeframe: Timeframe = Timeframe.FIVE_MIN,
) -> list[Bar]:
    """Create bars with specific prices and volumes for VWAP testing."""
    bars = []
    for i, (price, volume) in enumerate(zip(prices, volumes)):
        bar = Bar(
            symbol=symbol,
            timeframe=timeframe,
            open=price,
            high=price + 0.5,
            low=price - 0.5,
            close=price,
            volume=volume,
            timestamp=_BASE_TIME + timedelta(minutes=i * 5),
        )
        bars.append(bar)
    return bars


class TestVWAPStrategy:
    """Test VWAPStrategy with known price/volume patterns."""

    def _make_strategy(
        self,
        data_hub: FakeDataHub,
        deviation_threshold: float = 0.02,
        session_type: str = "regular",
        symbols: list[str] | None = None,
    ) -> VWAPStrategy:
        config = make_strategy_config(
            symbols=symbols or ["AAPL"],
            parameters={
                "deviation_threshold": deviation_threshold,
                "session_type": session_type,
            },
        )
        return VWAPStrategy(config=config, data_hub=data_hub)

    @pytest.mark.asyncio
    async def test_long_signal_when_price_below_vwap(self):
        """Price below VWAP by more than threshold generates a LONG signal."""
        # VWAP will be around 100 (high volume at 100), last price drops to 95
        prices = [100.0, 100.0, 100.0, 100.0, 95.0]
        volumes = [10000.0, 10000.0, 10000.0, 10000.0, 1000.0]
        bars = make_vwap_bars("AAPL", prices, volumes)
        data_hub = FakeDataHub()
        data_hub.set_bars("AAPL", Timeframe.FIVE_MIN, bars)

        strategy = self._make_strategy(data_hub, deviation_threshold=0.02)
        signals = await strategy.evaluate()

        assert len(signals) == 1
        assert signals[0].direction == SignalDirection.LONG
        assert signals[0].symbol == "AAPL"
        assert signals[0].metadata["deviation"] < -0.02

    @pytest.mark.asyncio
    async def test_short_signal_when_price_above_vwap(self):
        """Price above VWAP by more than threshold generates a SHORT signal."""
        # VWAP will be around 100 (high volume at 100), last price spikes to 105
        prices = [100.0, 100.0, 100.0, 100.0, 105.0]
        volumes = [10000.0, 10000.0, 10000.0, 10000.0, 1000.0]
        bars = make_vwap_bars("AAPL", prices, volumes)
        data_hub = FakeDataHub()
        data_hub.set_bars("AAPL", Timeframe.FIVE_MIN, bars)

        strategy = self._make_strategy(data_hub, deviation_threshold=0.02)
        signals = await strategy.evaluate()

        assert len(signals) == 1
        assert signals[0].direction == SignalDirection.SHORT
        assert signals[0].symbol == "AAPL"
        assert signals[0].metadata["deviation"] > 0.02

    @pytest.mark.asyncio
    async def test_no_signal_when_price_near_vwap(self):
        """No signal when price is within threshold of VWAP."""
        # All prices at 100 with equal volume — price equals VWAP
        prices = [100.0, 100.0, 100.0, 100.0, 100.0]
        volumes = [1000.0, 1000.0, 1000.0, 1000.0, 1000.0]
        bars = make_vwap_bars("AAPL", prices, volumes)
        data_hub = FakeDataHub()
        data_hub.set_bars("AAPL", Timeframe.FIVE_MIN, bars)

        strategy = self._make_strategy(data_hub, deviation_threshold=0.02)
        signals = await strategy.evaluate()

        assert len(signals) == 0

    @pytest.mark.asyncio
    async def test_no_signal_with_insufficient_bars(self):
        """No signal when there's only 1 bar."""
        prices = [100.0]
        volumes = [1000.0]
        bars = make_vwap_bars("AAPL", prices, volumes)
        data_hub = FakeDataHub()
        data_hub.set_bars("AAPL", Timeframe.FIVE_MIN, bars)

        strategy = self._make_strategy(data_hub, deviation_threshold=0.02)
        signals = await strategy.evaluate()

        assert len(signals) == 0

    @pytest.mark.asyncio
    async def test_volume_weighting_affects_vwap(self):
        """Higher volume bars have more influence on VWAP."""
        # Heavy volume at 90, light volume at 110 — VWAP should be closer to 90
        # Last price at 110 should be above VWAP → SHORT
        prices = [90.0, 90.0, 90.0, 110.0]
        volumes = [10000.0, 10000.0, 10000.0, 100.0]
        bars = make_vwap_bars("AAPL", prices, volumes)
        data_hub = FakeDataHub()
        data_hub.set_bars("AAPL", Timeframe.FIVE_MIN, bars)

        strategy = self._make_strategy(data_hub, deviation_threshold=0.02)
        signals = await strategy.evaluate()

        assert len(signals) == 1
        assert signals[0].direction == SignalDirection.SHORT

    @pytest.mark.asyncio
    async def test_required_indicators(self):
        """required_indicators returns the VWAP indicator name."""
        data_hub = FakeDataHub()
        strategy = self._make_strategy(data_hub, session_type="full")
        assert strategy.required_indicators() == ["VWAP_full"]
