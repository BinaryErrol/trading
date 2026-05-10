"""Unit tests for the backtesting engine.

Tests cover:
- Execution simulation (slippage, commissions, market impact)
- Look-ahead bias prevention
- Performance metric calculation (Sharpe, Sortino, max drawdown, win rate, profit factor)
- Walk-forward optimization
- Data loading from CSV
- Result storage
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from src.backtesting.engine import (
    BacktestEngine,
    BacktestResult,
    BacktestTrade,
    PortfolioBacktestResult,
    _BacktestDataHub,
    _LookAheadGuard,
)
from src.backtesting.simulator import SimulatedExecution, SimulatedFill
from src.backtesting.walk_forward import WalkForwardOptimizer, WalkForwardResult
from src.config.settings import BacktestConfig, StrategyConfig
from src.data.bar_builder import Timeframe
from src.strategies.base import BaseStrategy
from src.strategies.signals import OrderType, Signal, SignalDirection


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_ohlcv_data(
    num_bars: int = 100,
    start_price: float = 100.0,
    trend: float = 0.001,
    volatility: float = 0.02,
    start_date: datetime | None = None,
) -> pd.DataFrame:
    """Generate synthetic OHLCV data for testing."""
    if start_date is None:
        start_date = datetime(2023, 1, 2, tzinfo=timezone.utc)

    np.random.seed(42)
    dates = pd.date_range(start=start_date, periods=num_bars, freq="D")
    prices = [start_price]
    for _ in range(num_bars - 1):
        change = np.random.normal(trend, volatility)
        prices.append(prices[-1] * (1 + change))

    data = pd.DataFrame(
        {
            "open": [p * (1 - abs(np.random.normal(0, 0.005))) for p in prices],
            "high": [p * (1 + abs(np.random.normal(0, 0.01))) for p in prices],
            "low": [p * (1 - abs(np.random.normal(0, 0.01))) for p in prices],
            "close": prices,
            "volume": [np.random.randint(100000, 1000000) for _ in range(num_bars)],
        },
        index=dates,
    )
    return data



class _DummyStrategy(BaseStrategy):
    """A simple test strategy that buys on bar 5 and sells on bar 15."""

    def __init__(self, symbol: str = "TEST", buy_bar: int = 5, sell_bar: int = 15):
        config = StrategyConfig(
            enabled=True,
            frequency="daily",
            symbols=[symbol],
            asset_classes=["equity"],
            parameters={"buy_bar": buy_bar, "sell_bar": sell_bar},
        )
        # Pass None as data_hub; the engine will replace it
        super().__init__(config=config, data_hub=None)  # type: ignore[arg-type]
        self._symbol = symbol
        self._bar_count = 0

    @property
    def name(self) -> str:
        return "DummyStrategy"

    async def evaluate(self) -> list[Signal]:
        self._bar_count += 1
        buy_bar = self._config.parameters.get("buy_bar", 5)
        sell_bar = self._config.parameters.get("sell_bar", 15)

        if self._bar_count == buy_bar:
            return [
                Signal(
                    strategy_name=self.name,
                    symbol=self._symbol,
                    direction=SignalDirection.LONG,
                    confidence=0.8,
                    suggested_size=Decimal("10000"),
                    order_type=OrderType.MARKET,
                )
            ]
        elif self._bar_count == sell_bar:
            return [
                Signal(
                    strategy_name=self.name,
                    symbol=self._symbol,
                    direction=SignalDirection.CLOSE,
                    confidence=0.8,
                    suggested_size=Decimal("0"),
                    order_type=OrderType.MARKET,
                )
            ]
        return []

    def required_indicators(self) -> list[str]:
        return []


# ===========================================================================
# Tests: SimulatedExecution
# ===========================================================================


class TestSimulatedExecution:
    """Tests for the execution simulator (slippage, commissions, market impact)."""

    def test_default_parameters(self):
        """SimulatedExecution uses sensible defaults."""
        sim = SimulatedExecution()
        assert sim.slippage_bps == 5.0
        assert sim.commission_per_share == Decimal("0.005")
        assert sim.market_impact_bps == 2.0

    def test_custom_parameters(self):
        """SimulatedExecution accepts custom cost parameters."""
        sim = SimulatedExecution(
            slippage_bps=10.0,
            commission_per_share=Decimal("0.01"),
            market_impact_bps=3.0,
        )
        assert sim.slippage_bps == 10.0
        assert sim.commission_per_share == Decimal("0.01")
        assert sim.market_impact_bps == 3.0

    def test_long_fill_price_increases(self):
        """Buying (LONG) should result in a higher fill price due to slippage."""
        sim = SimulatedExecution(slippage_bps=10.0, market_impact_bps=5.0)
        price = Decimal("100.00")
        quantity = Decimal("100")

        fill = sim.simulate_fill(price, quantity, SignalDirection.LONG)

        # Price should be higher than market price (adverse for buyer)
        assert fill.fill_price > price
        # Expected: 100 * (1 + 10/10000 + 5/10000) = 100 * 1.0015 = 100.15
        expected_price = price * (Decimal("1") + Decimal("15") / Decimal("10000"))
        assert fill.fill_price == expected_price

    def test_short_fill_price_decreases(self):
        """Selling short should result in a lower fill price due to slippage."""
        sim = SimulatedExecution(slippage_bps=10.0, market_impact_bps=5.0)
        price = Decimal("100.00")
        quantity = Decimal("100")

        fill = sim.simulate_fill(price, quantity, SignalDirection.SHORT)

        # Price should be lower than market price (adverse for seller)
        assert fill.fill_price < price
        expected_price = price * (Decimal("1") - Decimal("15") / Decimal("10000"))
        assert fill.fill_price == expected_price

    def test_close_fill_price_decreases(self):
        """Closing a position (selling) should result in a lower fill price."""
        sim = SimulatedExecution(slippage_bps=10.0, market_impact_bps=5.0)
        price = Decimal("100.00")
        quantity = Decimal("50")

        fill = sim.simulate_fill(price, quantity, SignalDirection.CLOSE)

        assert fill.fill_price < price

    def test_commission_calculation(self):
        """Commission is calculated as per-share cost * quantity."""
        sim = SimulatedExecution(
            slippage_bps=0.0,
            commission_per_share=Decimal("0.01"),
            market_impact_bps=0.0,
        )
        price = Decimal("50.00")
        quantity = Decimal("200")

        fill = sim.simulate_fill(price, quantity, SignalDirection.LONG)

        # Commission = 0.01 * 200 = 2.00
        assert fill.commission == Decimal("2.00")

    def test_slippage_cost_calculation(self):
        """Slippage cost is price * slippage_factor * quantity."""
        sim = SimulatedExecution(
            slippage_bps=10.0,
            commission_per_share=Decimal("0"),
            market_impact_bps=0.0,
        )
        price = Decimal("100.00")
        quantity = Decimal("100")

        fill = sim.simulate_fill(price, quantity, SignalDirection.LONG)

        # Slippage cost = 100 * (10/10000) * 100 = 10.00
        expected_slippage = price * (Decimal("10") / Decimal("10000")) * quantity
        assert fill.slippage_cost == expected_slippage

    def test_market_impact_cost_calculation(self):
        """Market impact cost is price * impact_factor * quantity."""
        sim = SimulatedExecution(
            slippage_bps=0.0,
            commission_per_share=Decimal("0"),
            market_impact_bps=5.0,
        )
        price = Decimal("100.00")
        quantity = Decimal("100")

        fill = sim.simulate_fill(price, quantity, SignalDirection.LONG)

        # Market impact = 100 * (5/10000) * 100 = 5.00
        expected_impact = price * (Decimal("5") / Decimal("10000")) * quantity
        assert fill.market_impact_cost == expected_impact

    def test_total_cost_is_sum_of_components(self):
        """Total cost = commission + slippage_cost + market_impact_cost."""
        sim = SimulatedExecution(
            slippage_bps=10.0,
            commission_per_share=Decimal("0.005"),
            market_impact_bps=3.0,
        )
        price = Decimal("150.00")
        quantity = Decimal("50")

        fill = sim.simulate_fill(price, quantity, SignalDirection.LONG)

        assert fill.total_cost == fill.commission + fill.slippage_cost + fill.market_impact_cost

    def test_limit_order_uses_limit_price(self):
        """Limit orders use the limit price as the base for fill calculation."""
        sim = SimulatedExecution(slippage_bps=5.0, market_impact_bps=2.0)
        market_price = Decimal("100.00")
        limit_price = Decimal("99.50")
        quantity = Decimal("100")

        fill = sim.simulate_fill(
            market_price,
            quantity,
            SignalDirection.LONG,
            order_type=OrderType.LIMIT,
            limit_price=limit_price,
        )

        # Fill should be based on limit price, not market price
        expected = limit_price * (Decimal("1") + Decimal("7") / Decimal("10000"))
        assert fill.fill_price == expected

    def test_zero_slippage_and_impact(self):
        """With zero slippage and impact, fill price equals market price."""
        sim = SimulatedExecution(
            slippage_bps=0.0,
            commission_per_share=Decimal("0"),
            market_impact_bps=0.0,
        )
        price = Decimal("100.00")
        quantity = Decimal("100")

        fill = sim.simulate_fill(price, quantity, SignalDirection.LONG)

        assert fill.fill_price == price
        assert fill.slippage_cost == Decimal("0")
        assert fill.market_impact_cost == Decimal("0")
        assert fill.total_cost == Decimal("0")


# ===========================================================================
# Tests: _LookAheadGuard
# ===========================================================================


class TestLookAheadGuard:
    """Tests for look-ahead bias prevention."""

    def test_initial_position_shows_only_first_bar(self):
        """At position 0, only the first row is visible."""
        data = _make_ohlcv_data(num_bars=50)
        guard = _LookAheadGuard(data)
        guard.set_position(0)

        visible = guard.get_visible_data()
        assert len(visible) == 1
        assert visible.iloc[0]["close"] == data.iloc[0]["close"]

    def test_position_limits_visible_data(self):
        """Setting position to N shows only data up to index N (inclusive)."""
        data = _make_ohlcv_data(num_bars=50)
        guard = _LookAheadGuard(data)

        guard.set_position(9)
        visible = guard.get_visible_data()
        assert len(visible) == 10

        guard.set_position(24)
        visible = guard.get_visible_data()
        assert len(visible) == 25

    def test_future_data_is_hidden(self):
        """Data beyond the current position is not accessible."""
        data = _make_ohlcv_data(num_bars=50)
        guard = _LookAheadGuard(data)
        guard.set_position(10)

        visible = guard.get_visible_data()
        # The 11th bar (index 10) should be the last visible
        assert len(visible) == 11
        # Verify the last visible bar matches the data at index 10
        assert visible.iloc[-1]["close"] == data.iloc[10]["close"]

    def test_get_current_bar(self):
        """get_current_bar returns the row at the current position."""
        data = _make_ohlcv_data(num_bars=50)
        guard = _LookAheadGuard(data)
        guard.set_position(5)

        current = guard.get_current_bar()
        assert current["close"] == data.iloc[5]["close"]
        assert current["open"] == data.iloc[5]["open"]

    def test_full_data_visible_at_last_position(self):
        """At the last position, all data is visible."""
        data = _make_ohlcv_data(num_bars=20)
        guard = _LookAheadGuard(data)
        guard.set_position(19)

        visible = guard.get_visible_data()
        assert len(visible) == 20


# ===========================================================================
# Tests: _BacktestDataHub
# ===========================================================================


class TestBacktestDataHub:
    """Tests for the backtest data hub that wraps the look-ahead guard."""

    def test_get_latest_bar_returns_current(self):
        """get_latest_bar returns a Bar for the current position."""
        data = _make_ohlcv_data(num_bars=20)
        guard = _LookAheadGuard(data)
        guard.set_position(5)

        hub = _BacktestDataHub(guard, "TEST")
        bar = hub.get_latest_bar("TEST", Timeframe.DAILY)

        assert bar is not None
        assert bar.close == float(data.iloc[5]["close"])
        assert bar.symbol == "TEST"
        assert bar.timeframe == Timeframe.DAILY

    def test_get_history_respects_look_ahead(self):
        """get_history only returns data up to the current position."""
        data = _make_ohlcv_data(num_bars=50)
        guard = _LookAheadGuard(data)
        guard.set_position(10)

        hub = _BacktestDataHub(guard, "TEST")
        history = hub.get_history("TEST", Timeframe.DAILY, periods=100)

        # Even though we asked for 100 periods, only 11 are visible
        assert len(history) == 11

    def test_get_history_tail_periods(self):
        """get_history returns the last N periods of visible data."""
        data = _make_ohlcv_data(num_bars=50)
        guard = _LookAheadGuard(data)
        guard.set_position(20)

        hub = _BacktestDataHub(guard, "TEST")
        history = hub.get_history("TEST", Timeframe.DAILY, periods=5)

        # Should return last 5 bars of the 21 visible bars
        assert len(history) == 5
        assert history[-1].close == data.iloc[20]["close"]


# ===========================================================================
# Tests: BacktestEngine - Metrics Calculation
# ===========================================================================


class TestMetricsCalculation:
    """Tests for performance metric calculations."""

    def _make_engine(self) -> BacktestEngine:
        config = BacktestConfig()
        return BacktestEngine(config)

    def test_sharpe_ratio_positive_returns(self):
        """Sharpe ratio is positive for consistently positive returns."""
        engine = self._make_engine()
        # Create an equity curve with positive drift
        values = [100000 + i * 100 for i in range(252)]
        equity = pd.Series(values, index=pd.date_range("2023-01-01", periods=252, freq="D"))

        sharpe = engine._calculate_sharpe(equity)
        assert sharpe > 0

    def test_sharpe_ratio_zero_for_flat(self):
        """Sharpe ratio is 0 for a flat equity curve."""
        engine = self._make_engine()
        equity = pd.Series([100000] * 100, index=pd.date_range("2023-01-01", periods=100, freq="D"))

        sharpe = engine._calculate_sharpe(equity)
        assert sharpe == 0.0

    def test_sharpe_ratio_empty_curve(self):
        """Sharpe ratio is 0 for an empty equity curve."""
        engine = self._make_engine()
        equity = pd.Series(dtype=float)

        sharpe = engine._calculate_sharpe(equity)
        assert sharpe == 0.0

    def test_sortino_ratio_positive_returns(self):
        """Sortino ratio is positive for consistently positive returns."""
        engine = self._make_engine()
        values = [100000 + i * 100 for i in range(252)]
        equity = pd.Series(values, index=pd.date_range("2023-01-01", periods=252, freq="D"))

        sortino = engine._calculate_sortino(equity)
        # With no downside, sortino should be inf
        assert sortino == float("inf")

    def test_sortino_ratio_with_downside(self):
        """Sortino ratio accounts for downside volatility only."""
        engine = self._make_engine()
        # Create equity with some down days
        np.random.seed(123)
        returns = np.random.normal(0.001, 0.02, 252)
        values = [100000.0]
        for r in returns:
            values.append(values[-1] * (1 + r))
        equity = pd.Series(values, index=pd.date_range("2023-01-01", periods=253, freq="D"))

        sortino = engine._calculate_sortino(equity)
        assert sortino != 0.0
        # Sortino should be finite when there are down days
        assert np.isfinite(sortino)

    def test_max_drawdown_calculation(self):
        """Max drawdown correctly identifies the largest peak-to-trough decline."""
        engine = self._make_engine()
        # Equity goes up to 120, drops to 90, then recovers
        values = [100, 110, 120, 115, 100, 90, 95, 100, 110]
        equity = pd.Series(values, index=pd.date_range("2023-01-01", periods=9, freq="D"))

        max_dd = engine._calculate_max_drawdown(equity)

        # Max drawdown: (120 - 90) / 120 = 0.25
        assert max_dd == Decimal("0.25")

    def test_max_drawdown_no_drawdown(self):
        """Max drawdown is 0 for a monotonically increasing curve."""
        engine = self._make_engine()
        values = [100, 110, 120, 130, 140]
        equity = pd.Series(values, index=pd.date_range("2023-01-01", periods=5, freq="D"))

        max_dd = engine._calculate_max_drawdown(equity)
        assert max_dd == Decimal("0")

    def test_max_drawdown_empty_curve(self):
        """Max drawdown is 0 for an empty equity curve."""
        engine = self._make_engine()
        equity = pd.Series(dtype=float)

        max_dd = engine._calculate_max_drawdown(equity)
        assert max_dd == Decimal("0")

    def test_annualize_return(self):
        """Annualized return correctly scales a total return over a period."""
        engine = self._make_engine()

        # 10% return over 365 days = ~10% annualized
        ann = engine._annualize_return(Decimal("0.10"), 365)
        assert abs(float(ann) - 0.10) < 0.001

        # 10% return over 182 days ≈ ~21% annualized
        ann = engine._annualize_return(Decimal("0.10"), 182)
        assert float(ann) > 0.10

    def test_win_rate_calculation(self):
        """Win rate is correctly calculated from trades."""
        engine = self._make_engine()
        now = datetime.now(timezone.utc)

        trades = [
            BacktestTrade(
                symbol="TEST", direction=SignalDirection.LONG,
                entry_price=Decimal("100"), exit_price=Decimal("110"),
                quantity=Decimal("10"), entry_time=now, exit_time=now + timedelta(days=1),
                pnl=Decimal("100"), commission=Decimal("1"), strategy_name="test",
            ),
            BacktestTrade(
                symbol="TEST", direction=SignalDirection.LONG,
                entry_price=Decimal("100"), exit_price=Decimal("95"),
                quantity=Decimal("10"), entry_time=now, exit_time=now + timedelta(days=1),
                pnl=Decimal("-50"), commission=Decimal("1"), strategy_name="test",
            ),
            BacktestTrade(
                symbol="TEST", direction=SignalDirection.LONG,
                entry_price=Decimal("100"), exit_price=Decimal("105"),
                quantity=Decimal("10"), entry_time=now, exit_time=now + timedelta(days=1),
                pnl=Decimal("50"), commission=Decimal("1"), strategy_name="test",
            ),
        ]

        equity = pd.Series([100000, 100100, 100050, 100100],
                           index=pd.date_range("2023-01-01", periods=4, freq="D"))

        result = engine._calculate_metrics(
            strategy_name="test",
            trades=trades,
            equity_curve=equity,
            start_date=date(2023, 1, 1),
            end_date=date(2023, 4, 1),
            parameters={},
        )

        # 2 winning trades out of 3
        assert result.win_rate == pytest.approx(2 / 3, rel=1e-6)

    def test_profit_factor_calculation(self):
        """Profit factor = gross profit / gross loss."""
        engine = self._make_engine()
        now = datetime.now(timezone.utc)

        trades = [
            BacktestTrade(
                symbol="TEST", direction=SignalDirection.LONG,
                entry_price=Decimal("100"), exit_price=Decimal("110"),
                quantity=Decimal("10"), entry_time=now, exit_time=now + timedelta(days=1),
                pnl=Decimal("200"), commission=Decimal("1"), strategy_name="test",
            ),
            BacktestTrade(
                symbol="TEST", direction=SignalDirection.LONG,
                entry_price=Decimal("100"), exit_price=Decimal("90"),
                quantity=Decimal("10"), entry_time=now, exit_time=now + timedelta(days=1),
                pnl=Decimal("-100"), commission=Decimal("1"), strategy_name="test",
            ),
        ]

        equity = pd.Series([100000, 100200, 100100],
                           index=pd.date_range("2023-01-01", periods=3, freq="D"))

        result = engine._calculate_metrics(
            strategy_name="test",
            trades=trades,
            equity_curve=equity,
            start_date=date(2023, 1, 1),
            end_date=date(2023, 3, 1),
            parameters={},
        )

        # Profit factor = 200 / 100 = 2.0
        assert result.profit_factor == pytest.approx(2.0)


# ===========================================================================
# Tests: BacktestEngine.run()
# ===========================================================================


class TestBacktestEngineRun:
    """Tests for running a full backtest with a strategy."""

    @pytest.mark.asyncio
    async def test_run_with_dummy_strategy(self):
        """BacktestEngine.run() executes a strategy and returns results."""
        config = BacktestConfig(slippage_bps=5, market_impact_bps=2)
        engine = BacktestEngine(config)
        data = _make_ohlcv_data(num_bars=30)
        strategy = _DummyStrategy(symbol="TEST", buy_bar=5, sell_bar=15)

        result = await engine.run(strategy, data)

        assert isinstance(result, BacktestResult)
        assert result.strategy_name == "DummyStrategy"
        assert result.total_trades == 1  # One buy + one sell = 1 completed trade
        assert len(result.trades) == 1
        assert not result.equity_curve.empty

    @pytest.mark.asyncio
    async def test_run_produces_equity_curve(self):
        """The equity curve has one point per bar."""
        config = BacktestConfig(slippage_bps=0, market_impact_bps=0)
        engine = BacktestEngine(config)
        data = _make_ohlcv_data(num_bars=20)
        strategy = _DummyStrategy(symbol="TEST", buy_bar=5, sell_bar=15)

        result = await engine.run(strategy, data)

        assert len(result.equity_curve) == 20

    @pytest.mark.asyncio
    async def test_run_with_no_signals(self):
        """A strategy that generates no signals produces zero trades."""
        config = BacktestConfig()
        engine = BacktestEngine(config)
        data = _make_ohlcv_data(num_bars=10)
        # buy_bar and sell_bar beyond data length
        strategy = _DummyStrategy(symbol="TEST", buy_bar=50, sell_bar=60)

        result = await engine.run(strategy, data)

        assert result.total_trades == 0
        assert result.total_return == Decimal("0")

    @pytest.mark.asyncio
    async def test_run_empty_data(self):
        """Running with empty data returns an empty result."""
        engine = BacktestEngine()
        data = pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
        data.index = pd.DatetimeIndex([])
        strategy = _DummyStrategy()

        result = await engine.run(strategy, data)

        assert result.total_trades == 0
        assert result.total_return == Decimal("0")

    @pytest.mark.asyncio
    async def test_run_date_filtering(self):
        """Date range filtering limits the data used for backtesting."""
        config = BacktestConfig(slippage_bps=0, market_impact_bps=0)
        engine = BacktestEngine(config)
        # Use tz-naive data so date filtering works without tz mismatch
        data = _make_ohlcv_data(num_bars=60, start_date=None)
        # Remove timezone from the index (make it tz-naive)
        data.index = data.index.tz_localize(None)
        strategy = _DummyStrategy(symbol="TEST", buy_bar=5, sell_bar=15)

        result = await engine.run(
            strategy, data,
            start_date=date(2023, 1, 10),
            end_date=date(2023, 2, 10),
        )

        # The equity curve should be shorter than the full 60 bars
        assert len(result.equity_curve) < 60

    @pytest.mark.asyncio
    async def test_trade_records_entry_and_exit(self):
        """Completed trades have entry/exit prices and times."""
        config = BacktestConfig(slippage_bps=0, market_impact_bps=0, commission_per_share=Decimal("0"))
        engine = BacktestEngine(config)
        data = _make_ohlcv_data(num_bars=20)
        strategy = _DummyStrategy(symbol="TEST", buy_bar=3, sell_bar=10)

        result = await engine.run(strategy, data)

        assert result.total_trades == 1
        trade = result.trades[0]
        assert trade.symbol == "TEST"
        assert trade.direction == SignalDirection.LONG
        assert trade.entry_price is not None
        assert trade.exit_price is not None
        assert trade.entry_time is not None
        assert trade.exit_time is not None
        assert trade.exit_time > trade.entry_time


# ===========================================================================
# Tests: Data Loading from CSV
# ===========================================================================


class TestDataLoadingCSV:
    """Tests for loading historical data from CSV files."""

    @pytest.mark.asyncio
    async def test_load_csv_data(self, tmp_path: Path):
        """BacktestEngine.load_data loads OHLCV data from a CSV file."""
        # Create a test CSV
        csv_path = tmp_path / "TEST.csv"
        data = _make_ohlcv_data(num_bars=10)
        data.index.name = "date"
        data.to_csv(csv_path)

        config = BacktestConfig(data_source="csv", csv_directory=str(tmp_path))
        engine = BacktestEngine(config)

        loaded = await engine.load_data("TEST", source="csv", filepath=csv_path)

        assert len(loaded) == 10
        assert "open" in loaded.columns
        assert "close" in loaded.columns
        assert "volume" in loaded.columns

    @pytest.mark.asyncio
    async def test_load_csv_missing_file(self, tmp_path: Path):
        """Loading a non-existent CSV raises FileNotFoundError."""
        config = BacktestConfig(data_source="csv", csv_directory=str(tmp_path))
        engine = BacktestEngine(config)

        with pytest.raises(FileNotFoundError):
            await engine.load_data("NONEXISTENT", source="csv")


# ===========================================================================
# Tests: Result Storage
# ===========================================================================


class TestResultStorage:
    """Tests for storing backtest results to JSON."""

    def test_store_result_creates_json(self, tmp_path: Path):
        """store_result writes a JSON file with all metrics."""
        engine = BacktestEngine()
        now = datetime.now(timezone.utc)

        result = BacktestResult(
            strategy_name="TestStrategy",
            start_date=date(2023, 1, 1),
            end_date=date(2023, 12, 31),
            total_return=Decimal("0.15"),
            annualized_return=Decimal("0.15"),
            sharpe_ratio=1.5,
            sortino_ratio=2.0,
            max_drawdown=Decimal("0.08"),
            win_rate=0.6,
            profit_factor=1.8,
            avg_trade_duration=timedelta(days=5),
            total_trades=20,
            trades=[
                BacktestTrade(
                    symbol="AAPL",
                    direction=SignalDirection.LONG,
                    entry_price=Decimal("150"),
                    exit_price=Decimal("160"),
                    quantity=Decimal("10"),
                    entry_time=now,
                    exit_time=now + timedelta(days=3),
                    pnl=Decimal("100"),
                    commission=Decimal("1"),
                    strategy_name="TestStrategy",
                )
            ],
            parameters={"lookback": 20},
        )

        filepath = tmp_path / "test_result.json"
        stored_path = engine.store_result(result, filepath=filepath)

        assert stored_path == filepath
        assert filepath.exists()

        with open(filepath) as f:
            stored = json.load(f)

        assert stored["strategy_name"] == "TestStrategy"
        assert stored["total_return"] == "0.15"
        assert stored["sharpe_ratio"] == 1.5
        assert stored["total_trades"] == 20
        assert stored["win_rate"] == 0.6
        assert len(stored["trades"]) == 1
        assert stored["trades"][0]["symbol"] == "AAPL"

    def test_store_result_default_directory(self, tmp_path: Path):
        """store_result uses the configured results_directory when no path given."""
        config = BacktestConfig(results_directory=str(tmp_path / "results"))
        engine = BacktestEngine(config)

        result = BacktestResult(
            strategy_name="MyStrategy",
            start_date=date(2023, 1, 1),
            end_date=date(2023, 6, 30),
            total_return=Decimal("0.05"),
            annualized_return=Decimal("0.10"),
            sharpe_ratio=0.8,
            sortino_ratio=1.2,
            max_drawdown=Decimal("0.03"),
            win_rate=0.55,
            profit_factor=1.3,
            avg_trade_duration=timedelta(days=2),
            total_trades=10,
            parameters={},
        )

        stored_path = engine.store_result(result)

        assert stored_path.exists()
        assert "MyStrategy" in stored_path.name
        assert stored_path.suffix == ".json"
