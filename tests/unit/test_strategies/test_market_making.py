"""Tests for MarketMakingStrategy signal generation."""

from __future__ import annotations

from decimal import Decimal

import pytest

from src.data.bar_builder import Timeframe
from src.strategies.implementations.market_making import MarketMakingStrategy
from src.strategies.signals import OrderType, SignalDirection

from .conftest import FakeDataHub, make_bars, make_strategy_config


class TestMarketMakingStrategy:
    """Test MarketMakingStrategy bid/ask signal generation and inventory skew."""

    def _make_strategy(
        self,
        data_hub: FakeDataHub,
        spread_bps: float = 10.0,
        inventory_limit: int = 100,
        skew_factor: float = 0.5,
        atr_period: int = 14,
        current_inventory: int = 0,
        symbols: list[str] | None = None,
    ) -> MarketMakingStrategy:
        config = make_strategy_config(
            symbols=symbols or ["AAPL"],
            parameters={
                "spread_bps": spread_bps,
                "inventory_limit": inventory_limit,
                "skew_factor": skew_factor,
                "atr_period": atr_period,
                "current_inventory": current_inventory,
            },
        )
        return MarketMakingStrategy(config=config, data_hub=data_hub)

    def _make_bars_with_volatility(
        self,
        symbol: str,
        base_price: float = 100.0,
        count: int = 15,
        spread: float = 0.5,
    ) -> list:
        """Create bars with realistic high/low spread for ATR calculation."""
        return make_bars(symbol, [base_price] * count, spread=spread)

    @pytest.mark.asyncio
    async def test_generates_bid_and_ask_signals(self):
        """With zero inventory, generates both LONG (bid) and SHORT (ask) signals."""
        bars = self._make_bars_with_volatility("AAPL", base_price=100.0, count=16)
        data_hub = FakeDataHub()
        data_hub.set_bars("AAPL", Timeframe.FIVE_MIN, bars)

        strategy = self._make_strategy(data_hub, current_inventory=0)
        signals = await strategy.evaluate()

        assert len(signals) == 2
        # One LONG (bid) and one SHORT (ask)
        directions = {s.direction for s in signals}
        assert SignalDirection.LONG in directions
        assert SignalDirection.SHORT in directions

    @pytest.mark.asyncio
    async def test_bid_below_mid_and_ask_above_mid(self):
        """Bid price is below mid and ask price is above mid."""
        bars = self._make_bars_with_volatility("AAPL", base_price=100.0, count=16)
        data_hub = FakeDataHub()
        data_hub.set_bars("AAPL", Timeframe.FIVE_MIN, bars)

        strategy = self._make_strategy(data_hub, current_inventory=0, spread_bps=10.0)
        signals = await strategy.evaluate()

        mid_price = 100.0
        bid_signal = next(s for s in signals if s.direction == SignalDirection.LONG)
        ask_signal = next(s for s in signals if s.direction == SignalDirection.SHORT)

        assert float(bid_signal.limit_price) < mid_price
        assert float(ask_signal.limit_price) > mid_price

    @pytest.mark.asyncio
    async def test_uses_limit_orders(self):
        """Market making signals use LIMIT order type."""
        bars = self._make_bars_with_volatility("AAPL", base_price=100.0, count=16)
        data_hub = FakeDataHub()
        data_hub.set_bars("AAPL", Timeframe.FIVE_MIN, bars)

        strategy = self._make_strategy(data_hub)
        signals = await strategy.evaluate()

        for signal in signals:
            assert signal.order_type == OrderType.LIMIT
            assert signal.limit_price is not None

    @pytest.mark.asyncio
    async def test_inventory_skew_positive_inventory(self):
        """Positive inventory skews bid lower and ask lower (discourages buying)."""
        bars = self._make_bars_with_volatility("AAPL", base_price=100.0, count=16)
        data_hub = FakeDataHub()
        data_hub.set_bars("AAPL", Timeframe.FIVE_MIN, bars)

        # Zero inventory baseline
        strategy_zero = self._make_strategy(data_hub, current_inventory=0, skew_factor=0.5)
        signals_zero = await strategy_zero.evaluate()
        bid_zero = next(s for s in signals_zero if s.direction == SignalDirection.LONG)
        ask_zero = next(s for s in signals_zero if s.direction == SignalDirection.SHORT)

        # Positive inventory
        strategy_long = self._make_strategy(data_hub, current_inventory=50, skew_factor=0.5)
        signals_long = await strategy_long.evaluate()
        bid_long = next(s for s in signals_long if s.direction == SignalDirection.LONG)
        ask_long = next(s for s in signals_long if s.direction == SignalDirection.SHORT)

        # With positive inventory, both bid and ask should shift down
        assert float(bid_long.limit_price) < float(bid_zero.limit_price)
        assert float(ask_long.limit_price) < float(ask_zero.limit_price)

    @pytest.mark.asyncio
    async def test_inventory_skew_negative_inventory(self):
        """Negative inventory skews bid higher and ask higher (discourages selling)."""
        bars = self._make_bars_with_volatility("AAPL", base_price=100.0, count=16)
        data_hub = FakeDataHub()
        data_hub.set_bars("AAPL", Timeframe.FIVE_MIN, bars)

        # Zero inventory baseline
        strategy_zero = self._make_strategy(data_hub, current_inventory=0, skew_factor=0.5)
        signals_zero = await strategy_zero.evaluate()
        bid_zero = next(s for s in signals_zero if s.direction == SignalDirection.LONG)
        ask_zero = next(s for s in signals_zero if s.direction == SignalDirection.SHORT)

        # Negative inventory
        strategy_short = self._make_strategy(data_hub, current_inventory=-50, skew_factor=0.5)
        signals_short = await strategy_short.evaluate()
        bid_short = next(s for s in signals_short if s.direction == SignalDirection.LONG)
        ask_short = next(s for s in signals_short if s.direction == SignalDirection.SHORT)

        # With negative inventory, both bid and ask should shift up
        assert float(bid_short.limit_price) > float(bid_zero.limit_price)
        assert float(ask_short.limit_price) > float(ask_zero.limit_price)

    @pytest.mark.asyncio
    async def test_no_bid_at_positive_inventory_limit(self):
        """No LONG (bid) signal when inventory is at the positive limit."""
        bars = self._make_bars_with_volatility("AAPL", base_price=100.0, count=16)
        data_hub = FakeDataHub()
        data_hub.set_bars("AAPL", Timeframe.FIVE_MIN, bars)

        strategy = self._make_strategy(data_hub, current_inventory=100, inventory_limit=100)
        signals = await strategy.evaluate()

        directions = [s.direction for s in signals]
        assert SignalDirection.LONG not in directions
        assert SignalDirection.SHORT in directions

    @pytest.mark.asyncio
    async def test_no_ask_at_negative_inventory_limit(self):
        """No SHORT (ask) signal when inventory is at the negative limit."""
        bars = self._make_bars_with_volatility("AAPL", base_price=100.0, count=16)
        data_hub = FakeDataHub()
        data_hub.set_bars("AAPL", Timeframe.FIVE_MIN, bars)

        strategy = self._make_strategy(data_hub, current_inventory=-100, inventory_limit=100)
        signals = await strategy.evaluate()

        directions = [s.direction for s in signals]
        assert SignalDirection.SHORT not in directions
        assert SignalDirection.LONG in directions

    @pytest.mark.asyncio
    async def test_dynamic_spread_increases_with_volatility(self):
        """Higher volatility (larger ATR) results in wider spread."""
        # Low volatility bars (small high-low range)
        low_vol_bars = make_bars("AAPL", [100.0] * 16, spread=0.1)
        data_hub_low = FakeDataHub()
        data_hub_low.set_bars("AAPL", Timeframe.FIVE_MIN, low_vol_bars)

        strategy_low = self._make_strategy(data_hub_low, spread_bps=10.0)
        signals_low = await strategy_low.evaluate()

        # High volatility bars (large high-low range)
        high_vol_bars = make_bars("AAPL", [100.0] * 16, spread=5.0)
        data_hub_high = FakeDataHub()
        data_hub_high.set_bars("AAPL", Timeframe.FIVE_MIN, high_vol_bars)

        strategy_high = self._make_strategy(data_hub_high, spread_bps=10.0)
        signals_high = await strategy_high.evaluate()

        # Get the spread from metadata
        spread_low = signals_low[0].metadata["dynamic_spread"]
        spread_high = signals_high[0].metadata["dynamic_spread"]

        assert spread_high > spread_low

    @pytest.mark.asyncio
    async def test_no_signal_with_insufficient_bars(self):
        """No signal when there aren't enough bars for ATR calculation."""
        bars = make_bars("AAPL", [100.0] * 5)  # Only 5 bars, need 15
        data_hub = FakeDataHub()
        data_hub.set_bars("AAPL", Timeframe.FIVE_MIN, bars)

        strategy = self._make_strategy(data_hub, atr_period=14)
        signals = await strategy.evaluate()

        assert len(signals) == 0

    @pytest.mark.asyncio
    async def test_required_indicators(self):
        """required_indicators returns the ATR indicator name."""
        data_hub = FakeDataHub()
        strategy = self._make_strategy(data_hub, atr_period=14)
        assert strategy.required_indicators() == ["ATR_14"]

    @pytest.mark.asyncio
    async def test_metadata_contains_market_making_info(self):
        """Signal metadata includes mid_price, atr, spread, and inventory info."""
        bars = self._make_bars_with_volatility("AAPL", base_price=100.0, count=16)
        data_hub = FakeDataHub()
        data_hub.set_bars("AAPL", Timeframe.FIVE_MIN, bars)

        strategy = self._make_strategy(data_hub)
        signals = await strategy.evaluate()

        assert len(signals) >= 1
        meta = signals[0].metadata
        assert "mid_price" in meta
        assert "atr" in meta
        assert "dynamic_spread" in meta
        assert "inventory" in meta
        assert "bid_price" in meta
        assert "ask_price" in meta
