"""Unit tests for the Portfolio Monitor module.

Tests metric calculations, position reconciliation, and CSV export.
"""

import math
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, PropertyMock

import pytest

from src.portfolio.monitor import (
    DailyReport,
    PortfolioMonitor,
    Position,
    StrategyMetrics,
    Trade,
)


# --- Fixtures ---


@pytest.fixture
def monitor() -> PortfolioMonitor:
    """Create a PortfolioMonitor with no connection and 100k initial equity."""
    return PortfolioMonitor(connection=None, initial_equity=Decimal("100000"))


@pytest.fixture
def sample_trades() -> list[Trade]:
    """Create a set of sample trades for metric testing."""
    return [
        Trade(
            strategy_name="momentum",
            symbol="AAPL",
            pnl=Decimal("500"),
            return_pct=0.05,
            closed_at=datetime(2024, 1, 10, 15, 30),
        ),
        Trade(
            strategy_name="momentum",
            symbol="MSFT",
            pnl=Decimal("-200"),
            return_pct=-0.02,
            closed_at=datetime(2024, 1, 11, 15, 30),
        ),
        Trade(
            strategy_name="momentum",
            symbol="GOOG",
            pnl=Decimal("300"),
            return_pct=0.03,
            closed_at=datetime(2024, 1, 12, 15, 30),
        ),
        Trade(
            strategy_name="momentum",
            symbol="TSLA",
            pnl=Decimal("-100"),
            return_pct=-0.01,
            closed_at=datetime(2024, 1, 15, 15, 30),
        ),
        Trade(
            strategy_name="momentum",
            symbol="NVDA",
            pnl=Decimal("800"),
            return_pct=0.08,
            closed_at=datetime(2024, 1, 16, 15, 30),
        ),
    ]


@pytest.fixture
def monitor_with_trades(monitor: PortfolioMonitor, sample_trades: list[Trade]) -> PortfolioMonitor:
    """Create a monitor pre-loaded with sample trades."""
    for trade in sample_trades:
        monitor.record_trade(trade)
    return monitor


# --- Position Tests ---


class TestPositionDataclass:
    """Tests for the Position dataclass."""

    def test_position_creation(self):
        pos = Position(
            symbol="AAPL",
            asset_class="STK",
            strategy_name="momentum",
            quantity=Decimal("100"),
            avg_entry_price=Decimal("150.00"),
            current_price=Decimal("155.00"),
            unrealized_pnl=Decimal("500.00"),
            realized_pnl=Decimal("0"),
            opened_at=datetime(2024, 1, 1, 9, 30),
        )
        assert pos.symbol == "AAPL"
        assert pos.quantity == Decimal("100")
        assert pos.unrealized_pnl == Decimal("500.00")

    def test_position_with_negative_pnl(self):
        pos = Position(
            symbol="TSLA",
            asset_class="STK",
            strategy_name="mean_reversion",
            quantity=Decimal("50"),
            avg_entry_price=Decimal("200.00"),
            current_price=Decimal("190.00"),
            unrealized_pnl=Decimal("-500.00"),
            realized_pnl=Decimal("100.00"),
            opened_at=datetime(2024, 1, 5, 10, 0),
        )
        assert pos.unrealized_pnl == Decimal("-500.00")
        assert pos.realized_pnl == Decimal("100.00")


# --- Reconciliation Tests ---


class TestSyncPositions:
    """Tests for position reconciliation with IBKR."""

    @pytest.fixture
    def mock_connection(self):
        """Create a mock connection with IBKR positions."""
        conn = MagicMock()
        type(conn).is_connected = PropertyMock(return_value=True)

        # Mock IBKR position objects
        pos1 = MagicMock()
        pos1.contract.symbol = "AAPL"
        pos1.contract.secType = "STK"
        pos1.position = 100
        pos1.avgCost = 150.0

        pos2 = MagicMock()
        pos2.contract.symbol = "MSFT"
        pos2.contract.secType = "STK"
        pos2.position = 50
        pos2.avgCost = 300.0

        conn.ib.positions.return_value = [pos1, pos2]
        return conn

    async def test_sync_adds_new_positions(self, mock_connection):
        monitor = PortfolioMonitor(connection=mock_connection, initial_equity=Decimal("100000"))
        await monitor.sync_positions()

        assert "AAPL" in monitor.positions
        assert "MSFT" in monitor.positions
        assert monitor.positions["AAPL"].quantity == Decimal("100")
        assert monitor.positions["MSFT"].quantity == Decimal("50")

    async def test_sync_updates_existing_positions(self, mock_connection):
        monitor = PortfolioMonitor(connection=mock_connection, initial_equity=Decimal("100000"))

        # Pre-populate with an existing position
        monitor._positions["AAPL"] = Position(
            symbol="AAPL",
            asset_class="STK",
            strategy_name="momentum",
            quantity=Decimal("75"),
            avg_entry_price=Decimal("145.00"),
            current_price=Decimal("150.00"),
            unrealized_pnl=Decimal("375.00"),
            realized_pnl=Decimal("0"),
            opened_at=datetime(2024, 1, 1),
        )

        await monitor.sync_positions()

        # Quantity should be updated from IBKR
        assert monitor.positions["AAPL"].quantity == Decimal("100")
        # Strategy name should be preserved
        assert monitor.positions["AAPL"].strategy_name == "momentum"

    async def test_sync_removes_stale_positions(self, mock_connection):
        monitor = PortfolioMonitor(connection=mock_connection, initial_equity=Decimal("100000"))

        # Pre-populate with a position not in IBKR
        monitor._positions["GOOG"] = Position(
            symbol="GOOG",
            asset_class="STK",
            strategy_name="breakout",
            quantity=Decimal("25"),
            avg_entry_price=Decimal("140.00"),
            current_price=Decimal("145.00"),
            unrealized_pnl=Decimal("125.00"),
            realized_pnl=Decimal("0"),
            opened_at=datetime(2024, 1, 1),
        )

        await monitor.sync_positions()

        # GOOG should be removed since it's not in IBKR
        assert "GOOG" not in monitor.positions
        # AAPL and MSFT from IBKR should be present
        assert "AAPL" in monitor.positions
        assert "MSFT" in monitor.positions

    async def test_sync_skipped_when_not_connected(self):
        conn = MagicMock()
        type(conn).is_connected = PropertyMock(return_value=False)
        monitor = PortfolioMonitor(connection=conn, initial_equity=Decimal("100000"))

        # Should not raise, just skip
        await monitor.sync_positions()
        assert len(monitor.positions) == 0

    async def test_sync_skipped_when_no_connection(self):
        monitor = PortfolioMonitor(connection=None, initial_equity=Decimal("100000"))
        await monitor.sync_positions()
        assert len(monitor.positions) == 0


# --- Metric Calculation Tests ---


class TestStrategyMetrics:
    """Tests for strategy metric calculations."""

    def test_sharpe_ratio_calculation(self, monitor_with_trades: PortfolioMonitor):
        metrics = monitor_with_trades.calculate_strategy_metrics("momentum")

        # Sharpe = mean(returns) / std(returns) * sqrt(252)
        returns = [0.05, -0.02, 0.03, -0.01, 0.08]
        mean_r = sum(returns) / len(returns)
        var_r = sum((r - mean_r) ** 2 for r in returns) / (len(returns) - 1)
        std_r = math.sqrt(var_r)
        expected_sharpe = (mean_r / std_r) * math.sqrt(252)

        assert abs(metrics.sharpe_ratio - expected_sharpe) < 1e-6

    def test_sortino_ratio_calculation(self, monitor_with_trades: PortfolioMonitor):
        metrics = monitor_with_trades.calculate_strategy_metrics("momentum")

        # Sortino = mean(returns) / downside_std(returns) * sqrt(252)
        returns = [0.05, -0.02, 0.03, -0.01, 0.08]
        mean_r = sum(returns) / len(returns)
        downside = [r for r in returns if r < 0]
        downside_var = sum(r ** 2 for r in downside) / len(returns)
        downside_std = math.sqrt(downside_var)
        expected_sortino = (mean_r / downside_std) * math.sqrt(252)

        assert abs(metrics.sortino_ratio - expected_sortino) < 1e-6

    def test_max_drawdown_calculation(self, monitor_with_trades: PortfolioMonitor):
        metrics = monitor_with_trades.calculate_strategy_metrics("momentum")

        # Cumulative PnL: 500, 300, 600, 500, 1300
        # Peak:           500, 500, 600, 600, 1300
        # Drawdown:       0,   200, 0,   100, 0
        # Max drawdown = 200
        assert metrics.max_drawdown == Decimal("200")

    def test_win_rate_calculation(self, monitor_with_trades: PortfolioMonitor):
        metrics = monitor_with_trades.calculate_strategy_metrics("momentum")

        # 3 winning trades out of 5
        assert metrics.win_rate == pytest.approx(0.6)

    def test_profit_factor_calculation(self, monitor_with_trades: PortfolioMonitor):
        metrics = monitor_with_trades.calculate_strategy_metrics("momentum")

        # Gross profit: 500 + 300 + 800 = 1600
        # Gross loss: 200 + 100 = 300
        # Profit factor: 1600 / 300 = 5.333...
        expected_pf = 1600.0 / 300.0
        assert abs(metrics.profit_factor - expected_pf) < 1e-6

    def test_total_trades_count(self, monitor_with_trades: PortfolioMonitor):
        metrics = monitor_with_trades.calculate_strategy_metrics("momentum")
        assert metrics.total_trades == 5

    def test_total_return(self, monitor_with_trades: PortfolioMonitor):
        metrics = monitor_with_trades.calculate_strategy_metrics("momentum")
        # 500 - 200 + 300 - 100 + 800 = 1300
        assert metrics.total_return == Decimal("1300")

    def test_empty_strategy_returns_zeros(self, monitor: PortfolioMonitor):
        metrics = monitor.calculate_strategy_metrics("nonexistent")

        assert metrics.total_return == Decimal("0")
        assert metrics.sharpe_ratio == 0.0
        assert metrics.sortino_ratio == 0.0
        assert metrics.max_drawdown == Decimal("0")
        assert metrics.win_rate == 0.0
        assert metrics.profit_factor == 0.0
        assert metrics.total_trades == 0

    def test_single_trade_strategy(self, monitor: PortfolioMonitor):
        monitor.record_trade(Trade(
            strategy_name="single",
            symbol="AAPL",
            pnl=Decimal("100"),
            return_pct=0.01,
            closed_at=datetime(2024, 1, 10),
        ))
        metrics = monitor.calculate_strategy_metrics("single")

        assert metrics.total_trades == 1
        assert metrics.total_return == Decimal("100")
        assert metrics.win_rate == 1.0
        # Sharpe/Sortino undefined with single trade
        assert metrics.sharpe_ratio == 0.0

    def test_all_winning_trades(self, monitor: PortfolioMonitor):
        for i in range(5):
            monitor.record_trade(Trade(
                strategy_name="winner",
                symbol=f"SYM{i}",
                pnl=Decimal("100"),
                return_pct=0.01,
                closed_at=datetime(2024, 1, 10 + i),
            ))
        metrics = monitor.calculate_strategy_metrics("winner")

        assert metrics.win_rate == 1.0
        assert metrics.max_drawdown == Decimal("0")
        # Profit factor is inf when no losses
        assert metrics.profit_factor == float("inf")

    def test_all_losing_trades(self, monitor: PortfolioMonitor):
        for i in range(5):
            monitor.record_trade(Trade(
                strategy_name="loser",
                symbol=f"SYM{i}",
                pnl=Decimal("-100"),
                return_pct=-0.01,
                closed_at=datetime(2024, 1, 10 + i),
            ))
        metrics = monitor.calculate_strategy_metrics("loser")

        assert metrics.win_rate == 0.0
        assert metrics.profit_factor == 0.0
        assert metrics.max_drawdown == Decimal("500")


# --- Portfolio Value Tests ---


class TestPortfolioValue:
    """Tests for portfolio value and P&L calculations."""

    def test_get_total_value_no_positions(self, monitor: PortfolioMonitor):
        # With no positions, total value is just equity
        assert monitor.get_total_value() == Decimal("100000")

    def test_get_total_value_with_positions(self, monitor: PortfolioMonitor):
        monitor._positions["AAPL"] = Position(
            symbol="AAPL",
            asset_class="STK",
            strategy_name="momentum",
            quantity=Decimal("100"),
            avg_entry_price=Decimal("150.00"),
            current_price=Decimal("155.00"),
            unrealized_pnl=Decimal("500.00"),
            realized_pnl=Decimal("0"),
            opened_at=datetime(2024, 1, 1),
        )
        # 100000 + (100 * 155) = 115500
        assert monitor.get_total_value() == Decimal("115500.00")

    def test_get_unrealized_pnl(self, monitor: PortfolioMonitor):
        monitor._positions["AAPL"] = Position(
            symbol="AAPL",
            asset_class="STK",
            strategy_name="momentum",
            quantity=Decimal("100"),
            avg_entry_price=Decimal("150.00"),
            current_price=Decimal("155.00"),
            unrealized_pnl=Decimal("500.00"),
            realized_pnl=Decimal("0"),
            opened_at=datetime(2024, 1, 1),
        )
        monitor._positions["MSFT"] = Position(
            symbol="MSFT",
            asset_class="STK",
            strategy_name="momentum",
            quantity=Decimal("50"),
            avg_entry_price=Decimal("300.00"),
            current_price=Decimal("295.00"),
            unrealized_pnl=Decimal("-250.00"),
            realized_pnl=Decimal("0"),
            opened_at=datetime(2024, 1, 1),
        )
        assert monitor.get_unrealized_pnl() == Decimal("250.00")

    def test_peak_equity_tracking(self, monitor: PortfolioMonitor):
        assert monitor.get_peak_equity() == Decimal("100000")

        monitor.update_equity(Decimal("105000"))
        assert monitor.get_peak_equity() == Decimal("105000")

        # Equity drops but peak stays
        monitor.update_equity(Decimal("102000"))
        assert monitor.get_peak_equity() == Decimal("105000")

        # New peak
        monitor.update_equity(Decimal("110000"))
        assert monitor.get_peak_equity() == Decimal("110000")


# --- CSV Export Tests ---


class TestCSVExport:
    """Tests for CSV export functionality."""

    async def test_export_csv_creates_file(self, monitor_with_trades: PortfolioMonitor, tmp_path, monkeypatch):
        # Monkeypatch the export directory
        monkeypatch.chdir(tmp_path)

        filepath = await monitor_with_trades.export_csv(
            start=date(2024, 1, 1),
            end=date(2024, 1, 31),
        )

        assert filepath.exists()
        assert filepath.suffix == ".csv"

    async def test_export_csv_content(self, monitor_with_trades: PortfolioMonitor, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)

        filepath = await monitor_with_trades.export_csv(
            start=date(2024, 1, 1),
            end=date(2024, 1, 31),
        )

        with open(filepath) as f:
            lines = f.readlines()

        # Header + 5 trades
        assert len(lines) == 6
        assert "strategy" in lines[0]
        assert "symbol" in lines[0]
        assert "pnl" in lines[0]

    async def test_export_csv_date_filtering(self, monitor_with_trades: PortfolioMonitor, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)

        # Only trades from Jan 10-11
        filepath = await monitor_with_trades.export_csv(
            start=date(2024, 1, 10),
            end=date(2024, 1, 11),
        )

        with open(filepath) as f:
            lines = f.readlines()

        # Header + 2 trades (Jan 10 and Jan 11)
        assert len(lines) == 3

    async def test_export_csv_empty_range(self, monitor_with_trades: PortfolioMonitor, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)

        filepath = await monitor_with_trades.export_csv(
            start=date(2025, 1, 1),
            end=date(2025, 1, 31),
        )

        with open(filepath) as f:
            lines = f.readlines()

        # Header only
        assert len(lines) == 1


# --- Daily Report Tests ---


class TestDailyReport:
    """Tests for daily report generation."""

    async def test_generate_daily_report_structure(self, monitor_with_trades: PortfolioMonitor):
        report = await monitor_with_trades.generate_daily_report()

        assert isinstance(report, DailyReport)
        assert isinstance(report.date, date)
        assert isinstance(report.total_equity, Decimal)
        assert isinstance(report.strategy_metrics, dict)

    async def test_daily_report_includes_strategy_metrics(self, monitor_with_trades: PortfolioMonitor):
        report = await monitor_with_trades.generate_daily_report()

        assert "momentum" in report.strategy_metrics
        metrics = report.strategy_metrics["momentum"]
        assert isinstance(metrics, StrategyMetrics)
        assert metrics.total_trades == 5

    async def test_daily_report_drawdown_calculation(self):
        monitor = PortfolioMonitor(connection=None, initial_equity=Decimal("100000"))
        monitor.update_equity(Decimal("110000"))  # New peak
        monitor.update_equity(Decimal("99000"))   # Drop below initial

        report = await monitor.generate_daily_report()

        # Peak is 110000, current equity is 99000
        # Drawdown = (110000 - total_value) / 110000 * 100
        # total_value = 99000 (no positions)
        expected_dd = ((Decimal("110000") - Decimal("99000")) / Decimal("110000")) * Decimal("100")
        assert report.drawdown_pct == expected_dd
