"""Tests for Market Data Hub: bar builder aggregation, stale detection, history retrieval."""

from __future__ import annotations

import time
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from src.data.bar_builder import Bar, BarBuilder, Timeframe, TIMEFRAME_SECONDS
from src.data.market_data_hub import MarketDataHub


# ---------------------------------------------------------------------------
# BarBuilder Tests
# ---------------------------------------------------------------------------


class TestBarBuilderTick:
    """Test BarBuilder with TICK timeframe — every tick emits a bar."""

    def test_tick_timeframe_emits_bar_per_tick(self):
        builder = BarBuilder(symbol="AAPL", timeframe=Timeframe.TICK)
        bar = builder.on_tick(price=150.0, volume=100, tick_time=1000.0)

        assert bar is not None
        assert bar.symbol == "AAPL"
        assert bar.timeframe == Timeframe.TICK
        assert bar.open == 150.0
        assert bar.high == 150.0
        assert bar.low == 150.0
        assert bar.close == 150.0
        assert bar.volume == 100

    def test_tick_timeframe_multiple_ticks(self):
        builder = BarBuilder(symbol="AAPL", timeframe=Timeframe.TICK)
        bar1 = builder.on_tick(price=150.0, volume=100, tick_time=1000.0)
        bar2 = builder.on_tick(price=151.0, volume=200, tick_time=1001.0)

        assert bar1 is not None
        assert bar2 is not None
        assert bar1.close == 150.0
        assert bar2.close == 151.0
        assert len(builder.completed_bars) == 2

    def test_tick_timeframe_calls_on_bar_complete(self):
        callback = MagicMock()
        builder = BarBuilder(symbol="AAPL", timeframe=Timeframe.TICK, on_bar_complete=callback)
        builder.on_tick(price=150.0, volume=100, tick_time=1000.0)

        callback.assert_called_once()
        bar = callback.call_args[0][0]
        assert bar.close == 150.0


class TestBarBuilderOneMin:
    """Test BarBuilder with ONE_MIN timeframe."""

    def test_first_tick_does_not_emit_bar(self):
        builder = BarBuilder(symbol="AAPL", timeframe=Timeframe.ONE_MIN)
        # Tick at t=60 (start of minute 1)
        bar = builder.on_tick(price=150.0, volume=100, tick_time=60.0)
        assert bar is None

    def test_ticks_within_same_minute_accumulate(self):
        builder = BarBuilder(symbol="AAPL", timeframe=Timeframe.ONE_MIN)
        builder.on_tick(price=150.0, volume=100, tick_time=60.0)
        builder.on_tick(price=152.0, volume=50, tick_time=80.0)
        builder.on_tick(price=149.0, volume=75, tick_time=100.0)

        current = builder.current_bar
        assert current is not None
        assert current.open == 150.0
        assert current.high == 152.0
        assert current.low == 149.0
        assert current.close == 149.0
        assert current.volume == 225  # 100 + 50 + 75

    def test_crossing_minute_boundary_emits_bar(self):
        builder = BarBuilder(symbol="AAPL", timeframe=Timeframe.ONE_MIN)
        # Ticks in minute 1 (60-119)
        builder.on_tick(price=150.0, volume=100, tick_time=60.0)
        builder.on_tick(price=152.0, volume=50, tick_time=90.0)
        builder.on_tick(price=151.0, volume=75, tick_time=110.0)

        # Tick in minute 2 (120+) — should complete minute 1 bar
        bar = builder.on_tick(price=153.0, volume=200, tick_time=120.0)

        assert bar is not None
        assert bar.open == 150.0
        assert bar.high == 152.0
        assert bar.low == 150.0
        assert bar.close == 151.0
        assert bar.volume == 225
        assert bar.timestamp == datetime.fromtimestamp(60.0, tz=timezone.utc)

    def test_multiple_bars_completed(self):
        builder = BarBuilder(symbol="AAPL", timeframe=Timeframe.ONE_MIN)
        # Minute 0
        builder.on_tick(price=100.0, volume=10, tick_time=0.0)
        # Minute 1 — completes minute 0
        builder.on_tick(price=101.0, volume=20, tick_time=60.0)
        # Minute 2 — completes minute 1
        builder.on_tick(price=102.0, volume=30, tick_time=120.0)

        assert len(builder.completed_bars) == 2
        assert builder.completed_bars[0].close == 100.0
        assert builder.completed_bars[1].close == 101.0


class TestBarBuilderFiveMin:
    """Test BarBuilder with FIVE_MIN timeframe."""

    def test_five_min_boundary(self):
        builder = BarBuilder(symbol="MSFT", timeframe=Timeframe.FIVE_MIN)
        # Ticks in first 5-min period (0-299)
        builder.on_tick(price=300.0, volume=100, tick_time=0.0)
        builder.on_tick(price=305.0, volume=50, tick_time=150.0)
        builder.on_tick(price=298.0, volume=75, tick_time=250.0)

        # Cross into next 5-min period
        bar = builder.on_tick(price=310.0, volume=200, tick_time=300.0)

        assert bar is not None
        assert bar.open == 300.0
        assert bar.high == 305.0
        assert bar.low == 298.0
        assert bar.close == 298.0
        assert bar.volume == 225


class TestBarBuilderHistory:
    """Test BarBuilder history retrieval."""

    def test_get_history_returns_last_n_bars(self):
        builder = BarBuilder(symbol="AAPL", timeframe=Timeframe.ONE_MIN)
        # Generate 5 completed bars
        for i in range(6):
            builder.on_tick(price=100.0 + i, volume=10, tick_time=i * 60.0)

        # 5 ticks crossing boundaries = 5 completed bars
        history = builder.get_history(3)
        assert len(history) == 3
        # Most recent last
        assert history[-1].close == 104.0

    def test_get_history_fewer_than_requested(self):
        builder = BarBuilder(symbol="AAPL", timeframe=Timeframe.ONE_MIN)
        builder.on_tick(price=100.0, volume=10, tick_time=0.0)
        builder.on_tick(price=101.0, volume=10, tick_time=60.0)

        history = builder.get_history(10)
        assert len(history) == 1

    def test_get_latest_completed_bar(self):
        builder = BarBuilder(symbol="AAPL", timeframe=Timeframe.ONE_MIN)
        assert builder.get_latest_completed_bar() is None

        builder.on_tick(price=100.0, volume=10, tick_time=0.0)
        builder.on_tick(price=101.0, volume=10, tick_time=60.0)

        latest = builder.get_latest_completed_bar()
        assert latest is not None
        assert latest.close == 100.0

    def test_reset_clears_state(self):
        builder = BarBuilder(symbol="AAPL", timeframe=Timeframe.ONE_MIN)
        builder.on_tick(price=100.0, volume=10, tick_time=0.0)
        builder.on_tick(price=101.0, volume=10, tick_time=60.0)

        builder.reset()
        assert builder.current_bar is None
        assert builder.completed_bars == []
        assert builder.get_latest_completed_bar() is None


# ---------------------------------------------------------------------------
# MarketDataHub Tests
# ---------------------------------------------------------------------------


class TestMarketDataHubSubscribe:
    """Test MarketDataHub subscription management."""

    def _make_hub(self) -> MarketDataHub:
        conn = MagicMock()
        conn.subscribe_market_data = MagicMock(return_value="ticker_obj")
        return MarketDataHub(connection=conn, redis=None)

    def test_subscribe_creates_bar_builders(self):
        hub = self._make_hub()
        hub.subscribe("AAPL", "STK")

        assert "AAPL" in hub.subscribed_symbols
        assert "AAPL" in hub._bar_builders
        assert len(hub._bar_builders["AAPL"]) == len(MarketDataHub.BAR_TIMEFRAMES)

    def test_subscribe_calls_connection_manager(self):
        conn = MagicMock()
        conn.subscribe_market_data = MagicMock(return_value="ticker_obj")
        hub = MarketDataHub(connection=conn, redis=None)

        hub.subscribe("AAPL", "STK")
        conn.subscribe_market_data.assert_called_once()

    def test_duplicate_subscribe_is_noop(self):
        conn = MagicMock()
        conn.subscribe_market_data = MagicMock(return_value="ticker_obj")
        hub = MarketDataHub(connection=conn, redis=None)

        hub.subscribe("AAPL", "STK")
        hub.subscribe("AAPL", "STK")

        conn.subscribe_market_data.assert_called_once()


class TestMarketDataHubOnTick:
    """Test MarketDataHub tick processing."""

    def _make_hub(self) -> MarketDataHub:
        conn = MagicMock()
        conn.subscribe_market_data = MagicMock(return_value="ticker_obj")
        return MarketDataHub(connection=conn, redis=None)

    def test_on_tick_updates_bar_builders(self):
        hub = self._make_hub()
        hub.subscribe("AAPL")

        completed = hub.on_tick("AAPL", price=150.0, volume=100, tick_time=0.0)
        # TICK timeframe always completes
        assert any(b.timeframe == Timeframe.TICK for b in completed)

    def test_on_tick_returns_completed_bars_on_boundary(self):
        hub = self._make_hub()
        hub.subscribe("AAPL")

        # First tick
        hub.on_tick("AAPL", price=150.0, volume=100, tick_time=0.0)
        # Cross 1-min boundary
        completed = hub.on_tick("AAPL", price=151.0, volume=50, tick_time=60.0)

        # Should have TICK bar + ONE_MIN bar completed
        timeframes = {b.timeframe for b in completed}
        assert Timeframe.TICK in timeframes
        assert Timeframe.ONE_MIN in timeframes

    def test_on_tick_for_unsubscribed_symbol_returns_empty(self):
        hub = self._make_hub()
        completed = hub.on_tick("UNKNOWN", price=100.0, volume=10, tick_time=0.0)
        assert completed == []

    def test_on_tick_caches_in_redis(self):
        conn = MagicMock()
        conn.subscribe_market_data = MagicMock(return_value="ticker_obj")
        redis_mock = MagicMock()
        hub = MarketDataHub(connection=conn, redis=redis_mock)

        hub.subscribe("AAPL")
        hub.on_tick("AAPL", price=155.5, volume=100, tick_time=0.0)

        redis_mock.set.assert_called_once_with("market:AAPL:last_price", "155.5", ex=300)


class TestMarketDataHubGetLatestBar:
    """Test MarketDataHub get_latest_bar."""

    def _make_hub(self) -> MarketDataHub:
        conn = MagicMock()
        conn.subscribe_market_data = MagicMock(return_value="ticker_obj")
        return MarketDataHub(connection=conn, redis=None)

    def test_get_latest_bar_returns_none_when_no_bars(self):
        hub = self._make_hub()
        hub.subscribe("AAPL")
        assert hub.get_latest_bar("AAPL", Timeframe.ONE_MIN) is None

    def test_get_latest_bar_returns_completed_bar(self):
        hub = self._make_hub()
        hub.subscribe("AAPL")

        hub.on_tick("AAPL", price=150.0, volume=100, tick_time=0.0)
        hub.on_tick("AAPL", price=151.0, volume=50, tick_time=60.0)

        bar = hub.get_latest_bar("AAPL", Timeframe.ONE_MIN)
        assert bar is not None
        assert bar.close == 150.0
        assert bar.timeframe == Timeframe.ONE_MIN

    def test_get_latest_bar_unknown_symbol_returns_none(self):
        hub = self._make_hub()
        assert hub.get_latest_bar("UNKNOWN", Timeframe.ONE_MIN) is None


class TestMarketDataHubGetHistory:
    """Test MarketDataHub get_history."""

    def _make_hub(self) -> MarketDataHub:
        conn = MagicMock()
        conn.subscribe_market_data = MagicMock(return_value="ticker_obj")
        return MarketDataHub(connection=conn, redis=None)

    def test_get_history_returns_bars(self):
        hub = self._make_hub()
        hub.subscribe("AAPL")

        # Generate multiple 1-min bars
        for i in range(5):
            hub.on_tick("AAPL", price=100.0 + i, volume=10, tick_time=i * 60.0)

        history = hub.get_history("AAPL", Timeframe.ONE_MIN, periods=3)
        assert len(history) == 3

    def test_get_history_unknown_symbol_returns_empty(self):
        hub = self._make_hub()
        assert hub.get_history("UNKNOWN", Timeframe.ONE_MIN, 10) == []


class TestMarketDataHubStaleDetection:
    """Test MarketDataHub stale data detection."""

    def _make_hub(self, threshold: float = 60.0) -> MarketDataHub:
        conn = MagicMock()
        conn.subscribe_market_data = MagicMock(return_value="ticker_obj")
        return MarketDataHub(connection=conn, redis=None, stale_threshold_seconds=threshold)

    def test_no_ticks_is_stale(self):
        hub = self._make_hub()
        hub.subscribe("AAPL")
        assert hub._detect_stale_data("AAPL") is True

    def test_recent_tick_is_not_stale(self):
        hub = self._make_hub(threshold=60.0)
        hub.subscribe("AAPL")
        hub.on_tick("AAPL", price=150.0, volume=100, tick_time=time.time())

        assert hub._detect_stale_data("AAPL") is False

    def test_old_tick_is_stale(self):
        hub = self._make_hub(threshold=60.0)
        hub.subscribe("AAPL")
        # Tick from 120 seconds ago
        hub.on_tick("AAPL", price=150.0, volume=100, tick_time=time.time() - 120)

        assert hub._detect_stale_data("AAPL") is True

    def test_custom_threshold(self):
        hub = self._make_hub(threshold=5.0)
        hub.subscribe("AAPL")
        # Tick from 3 seconds ago — within threshold
        hub.on_tick("AAPL", price=150.0, volume=100, tick_time=time.time() - 3)
        assert hub._detect_stale_data("AAPL") is False

        # Tick from 10 seconds ago — beyond threshold
        hub._last_tick_time["AAPL"] = time.time() - 10
        assert hub._detect_stale_data("AAPL") is True

    def test_unknown_symbol_is_stale(self):
        hub = self._make_hub()
        assert hub._detect_stale_data("UNKNOWN") is True
