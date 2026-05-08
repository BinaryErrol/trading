"""Tests for MACrossoverStrategy signal generation."""

from __future__ import annotations

import pytest

from src.data.bar_builder import Timeframe
from src.strategies.implementations.ma_crossover import MACrossoverStrategy
from src.strategies.signals import SignalDirection

from .conftest import FakeDataHub, make_bars, make_strategy_config


class TestMACrossoverStrategy:
    """Test MACrossoverStrategy with known crossover patterns."""

    def _make_strategy(
        self,
        data_hub: FakeDataHub,
        fast_period: int = 3,
        slow_period: int = 5,
        ma_type: str = "sma",
        symbols: list[str] | None = None,
    ) -> MACrossoverStrategy:
        config = make_strategy_config(
            symbols=symbols or ["AAPL"],
            parameters={
                "fast_period": fast_period,
                "slow_period": slow_period,
                "ma_type": ma_type,
            },
        )
        return MACrossoverStrategy(config=config, data_hub=data_hub)

    @pytest.mark.asyncio
    async def test_golden_cross_generates_long_signal(self):
        """Fast MA crossing above slow MA generates a LONG signal."""
        # Construct prices where fast MA crosses above slow MA on the last bar.
        # Slow period=5, fast period=3. Need slow_period+1=6 bars.
        # Bars: prices that start with fast < slow, then fast > slow at end.
        # Downtrend then sharp upturn:
        prices = [100.0, 98.0, 96.0, 94.0, 97.0, 102.0]
        # Previous (bars[:-1]=[100,98,96,94,97]):
        #   fast_ma_prev = avg(96,94,97) = 95.67
        #   slow_ma_prev = avg(100,98,96,94,97) = 97.0
        #   fast < slow ✓
        # Current (all bars=[100,98,96,94,97,102]):
        #   fast_ma_current = avg(94,97,102) = 97.67
        #   slow_ma_current = avg(98,96,94,97,102) = 97.4
        #   fast > slow ✓ → golden cross

        bars = make_bars("AAPL", prices)
        data_hub = FakeDataHub()
        data_hub.set_bars("AAPL", Timeframe.FIVE_MIN, bars)

        strategy = self._make_strategy(data_hub, fast_period=3, slow_period=5)
        signals = await strategy.evaluate()

        assert len(signals) == 1
        assert signals[0].direction == SignalDirection.LONG
        assert signals[0].metadata["crossover"] == "golden"

    @pytest.mark.asyncio
    async def test_death_cross_generates_short_signal(self):
        """Fast MA crossing below slow MA generates a SHORT signal."""
        # Uptrend then sharp downturn:
        prices = [94.0, 96.0, 98.0, 100.0, 97.0, 92.0]
        # Previous (bars[:-1]=[94,96,98,100,97]):
        #   fast_ma_prev = avg(98,100,97) = 98.33
        #   slow_ma_prev = avg(94,96,98,100,97) = 97.0
        #   fast > slow ✓
        # Current (all bars=[94,96,98,100,97,92]):
        #   fast_ma_current = avg(100,97,92) = 96.33
        #   slow_ma_current = avg(96,98,100,97,92) = 96.6
        #   fast < slow ✓ → death cross

        bars = make_bars("AAPL", prices)
        data_hub = FakeDataHub()
        data_hub.set_bars("AAPL", Timeframe.FIVE_MIN, bars)

        strategy = self._make_strategy(data_hub, fast_period=3, slow_period=5)
        signals = await strategy.evaluate()

        assert len(signals) == 1
        assert signals[0].direction == SignalDirection.SHORT
        assert signals[0].metadata["crossover"] == "death"

    @pytest.mark.asyncio
    async def test_no_signal_when_no_crossover(self):
        """No signal when MAs don't cross."""
        # Steady uptrend: fast always above slow
        prices = [100.0, 102.0, 104.0, 106.0, 108.0, 110.0]
        bars = make_bars("AAPL", prices)
        data_hub = FakeDataHub()
        data_hub.set_bars("AAPL", Timeframe.FIVE_MIN, bars)

        strategy = self._make_strategy(data_hub, fast_period=3, slow_period=5)
        signals = await strategy.evaluate()

        assert len(signals) == 0

    @pytest.mark.asyncio
    async def test_ema_crossover(self):
        """EMA crossover also generates signals correctly."""
        # Use same golden cross pattern but with EMA
        prices = [100.0, 98.0, 96.0, 94.0, 97.0, 102.0]
        bars = make_bars("AAPL", prices)
        data_hub = FakeDataHub()
        data_hub.set_bars("AAPL", Timeframe.FIVE_MIN, bars)

        strategy = self._make_strategy(data_hub, fast_period=3, slow_period=5, ma_type="ema")
        signals = await strategy.evaluate()

        # EMA is more responsive, should also detect the crossover
        assert len(signals) == 1
        assert signals[0].direction == SignalDirection.LONG
        assert signals[0].metadata["ma_type"] == "ema"

    @pytest.mark.asyncio
    async def test_insufficient_bars(self):
        """No signal when there aren't enough bars."""
        prices = [100.0, 102.0]  # Only 2 bars, need 6 (slow_period+1)
        bars = make_bars("AAPL", prices)
        data_hub = FakeDataHub()
        data_hub.set_bars("AAPL", Timeframe.FIVE_MIN, bars)

        strategy = self._make_strategy(data_hub, fast_period=3, slow_period=5)
        signals = await strategy.evaluate()

        assert len(signals) == 0

    @pytest.mark.asyncio
    async def test_required_indicators(self):
        """required_indicators returns the MA indicator names."""
        data_hub = FakeDataHub()
        strategy = self._make_strategy(data_hub, fast_period=10, slow_period=30, ma_type="sma")
        assert strategy.required_indicators() == ["SMA_10", "SMA_30"]

    @pytest.mark.asyncio
    async def test_required_indicators_ema(self):
        """required_indicators uses EMA prefix when configured."""
        data_hub = FakeDataHub()
        strategy = self._make_strategy(data_hub, fast_period=12, slow_period=26, ma_type="ema")
        assert strategy.required_indicators() == ["EMA_12", "EMA_26"]
