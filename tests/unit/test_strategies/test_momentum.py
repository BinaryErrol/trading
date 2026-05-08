"""Tests for MomentumStrategy signal generation."""

from __future__ import annotations

import pytest

from src.data.bar_builder import Timeframe
from src.strategies.implementations.momentum import MomentumStrategy
from src.strategies.signals import SignalDirection

from .conftest import FakeDataHub, make_bars, make_strategy_config


class TestMomentumStrategy:
    """Test MomentumStrategy with known price patterns."""

    def _make_strategy(
        self,
        data_hub: FakeDataHub,
        lookback_period: int = 5,
        momentum_threshold: float = 0.03,
        symbols: list[str] | None = None,
    ) -> MomentumStrategy:
        config = make_strategy_config(
            symbols=symbols or ["AAPL"],
            parameters={
                "lookback_period": lookback_period,
                "momentum_threshold": momentum_threshold,
            },
        )
        return MomentumStrategy(config=config, data_hub=data_hub)

    @pytest.mark.asyncio
    async def test_long_signal_on_strong_uptrend(self):
        """Momentum above threshold generates a LONG signal."""
        # Price goes from 100 to 110 over 6 bars (10% momentum > 3% threshold)
        prices = [100.0, 102.0, 104.0, 106.0, 108.0, 110.0]
        bars = make_bars("AAPL", prices)
        data_hub = FakeDataHub()
        data_hub.set_bars("AAPL", Timeframe.FIVE_MIN, bars)

        strategy = self._make_strategy(data_hub, lookback_period=5, momentum_threshold=0.03)
        signals = await strategy.evaluate()

        assert len(signals) == 1
        assert signals[0].direction == SignalDirection.LONG
        assert signals[0].symbol == "AAPL"
        assert signals[0].metadata["momentum"] == pytest.approx(0.10, rel=1e-2)

    @pytest.mark.asyncio
    async def test_short_signal_on_strong_downtrend(self):
        """Momentum below negative threshold generates a SHORT signal."""
        # Price goes from 100 to 90 over 6 bars (-10% momentum < -3% threshold)
        prices = [100.0, 98.0, 96.0, 94.0, 92.0, 90.0]
        bars = make_bars("AAPL", prices)
        data_hub = FakeDataHub()
        data_hub.set_bars("AAPL", Timeframe.FIVE_MIN, bars)

        strategy = self._make_strategy(data_hub, lookback_period=5, momentum_threshold=0.03)
        signals = await strategy.evaluate()

        assert len(signals) == 1
        assert signals[0].direction == SignalDirection.SHORT
        assert signals[0].symbol == "AAPL"
        assert signals[0].metadata["momentum"] == pytest.approx(-0.10, rel=1e-2)

    @pytest.mark.asyncio
    async def test_no_signal_when_momentum_within_threshold(self):
        """No signal when momentum is within threshold bounds."""
        # Price barely moves: 100 to 101 (1% < 3% threshold)
        prices = [100.0, 100.2, 100.4, 100.6, 100.8, 101.0]
        bars = make_bars("AAPL", prices)
        data_hub = FakeDataHub()
        data_hub.set_bars("AAPL", Timeframe.FIVE_MIN, bars)

        strategy = self._make_strategy(data_hub, lookback_period=5, momentum_threshold=0.03)
        signals = await strategy.evaluate()

        assert len(signals) == 0

    @pytest.mark.asyncio
    async def test_no_signal_with_insufficient_bars(self):
        """No signal when there aren't enough bars for lookback."""
        prices = [100.0, 105.0]  # Only 2 bars, need 6 (lookback=5 + 1)
        bars = make_bars("AAPL", prices)
        data_hub = FakeDataHub()
        data_hub.set_bars("AAPL", Timeframe.FIVE_MIN, bars)

        strategy = self._make_strategy(data_hub, lookback_period=5, momentum_threshold=0.03)
        signals = await strategy.evaluate()

        assert len(signals) == 0

    @pytest.mark.asyncio
    async def test_multiple_symbols(self):
        """Strategy evaluates all configured symbols independently."""
        # AAPL trending up, MSFT trending down
        aapl_prices = [100.0, 102.0, 104.0, 106.0, 108.0, 110.0]
        msft_prices = [200.0, 196.0, 192.0, 188.0, 184.0, 180.0]

        data_hub = FakeDataHub()
        data_hub.set_bars("AAPL", Timeframe.FIVE_MIN, make_bars("AAPL", aapl_prices))
        data_hub.set_bars("MSFT", Timeframe.FIVE_MIN, make_bars("MSFT", msft_prices))

        strategy = self._make_strategy(
            data_hub, lookback_period=5, momentum_threshold=0.03, symbols=["AAPL", "MSFT"]
        )
        signals = await strategy.evaluate()

        assert len(signals) == 2
        aapl_signal = next(s for s in signals if s.symbol == "AAPL")
        msft_signal = next(s for s in signals if s.symbol == "MSFT")
        assert aapl_signal.direction == SignalDirection.LONG
        assert msft_signal.direction == SignalDirection.SHORT

    @pytest.mark.asyncio
    async def test_required_indicators(self):
        """required_indicators returns the momentum indicator name."""
        data_hub = FakeDataHub()
        strategy = self._make_strategy(data_hub, lookback_period=14)
        assert strategy.required_indicators() == ["MOMENTUM_14"]
