"""Unit tests for the RiskManager module.

Tests each risk check individually, the combined check_order flow, and halt logic.
"""

from __future__ import annotations

from decimal import Decimal
from datetime import datetime, timezone

import pytest

from src.config.settings import RiskConfig
from src.risk.manager import RiskCheckResult, RiskManager
from src.strategies.signals import OrderType, Signal, SignalDirection


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


class FakePortfolioMonitor:
    """Fake PortfolioMonitor for testing risk checks."""

    def __init__(
        self,
        total_value: Decimal = Decimal("100000"),
        peak_equity: Decimal = Decimal("100000"),
        positions: dict | None = None,
    ):
        self._total_value = total_value
        self._peak_equity = peak_equity
        self._positions = positions or {}

    def get_total_value(self) -> Decimal:
        return self._total_value

    def get_peak_equity(self) -> Decimal:
        return self._peak_equity

    @property
    def positions(self) -> dict:
        return self._positions


def make_signal(
    symbol: str = "AAPL",
    suggested_size: Decimal = Decimal("5000"),
    sector: str = "Technology",
    strategy_name: str = "momentum",
) -> Signal:
    """Create a test signal with sensible defaults."""
    return Signal(
        strategy_name=strategy_name,
        symbol=symbol,
        direction=SignalDirection.LONG,
        confidence=0.8,
        suggested_size=suggested_size,
        order_type=OrderType.MARKET,
        metadata={"sector": sector},
        timestamp=datetime.now(timezone.utc),
    )


@pytest.fixture
def risk_config() -> RiskConfig:
    """Default risk configuration for tests."""
    return RiskConfig(
        max_position_pct=0.05,
        max_drawdown_pct=0.10,
        max_daily_loss_pct=0.02,
        max_sector_concentration=0.25,
        max_correlation=0.7,
    )


@pytest.fixture
def portfolio() -> FakePortfolioMonitor:
    """Portfolio with $100k total value and peak equity."""
    return FakePortfolioMonitor(
        total_value=Decimal("100000"),
        peak_equity=Decimal("100000"),
    )


@pytest.fixture
def risk_manager(risk_config: RiskConfig, portfolio: FakePortfolioMonitor) -> RiskManager:
    """RiskManager with default config and fake portfolio."""
    return RiskManager(config=risk_config, portfolio=portfolio)


# ---------------------------------------------------------------------------
# Test: check_position_size
# ---------------------------------------------------------------------------


class TestCheckPositionSize:
    """Tests for the position size check."""

    def test_within_limit(self, risk_manager: RiskManager):
        """Position at exactly 5% of portfolio should pass."""
        signal = make_signal(suggested_size=Decimal("5000"))  # 5% of 100k
        assert risk_manager.check_position_size(signal) is True

    def test_exceeds_limit(self, risk_manager: RiskManager):
        """Position exceeding 5% of portfolio should fail."""
        signal = make_signal(suggested_size=Decimal("6000"))  # 6% of 100k
        assert risk_manager.check_position_size(signal) is False

    def test_zero_portfolio_value(self, risk_config: RiskConfig):
        """Zero portfolio value should fail the check."""
        portfolio = FakePortfolioMonitor(total_value=Decimal("0"))
        rm = RiskManager(config=risk_config, portfolio=portfolio)
        signal = make_signal(suggested_size=Decimal("100"))
        assert rm.check_position_size(signal) is False

    def test_small_position_passes(self, risk_manager: RiskManager):
        """Very small position should pass easily."""
        signal = make_signal(suggested_size=Decimal("100"))  # 0.1% of 100k
        assert risk_manager.check_position_size(signal) is True


# ---------------------------------------------------------------------------
# Test: check_drawdown
# ---------------------------------------------------------------------------


class TestCheckDrawdown:
    """Tests for the drawdown check."""

    def test_no_drawdown(self, risk_manager: RiskManager):
        """No drawdown (at peak) should pass."""
        assert risk_manager.check_drawdown() is True

    def test_within_limit(self, risk_config: RiskConfig):
        """Drawdown below threshold should pass."""
        portfolio = FakePortfolioMonitor(
            total_value=Decimal("92000"),  # 8% drawdown from 100k
            peak_equity=Decimal("100000"),
        )
        rm = RiskManager(config=risk_config, portfolio=portfolio)
        assert rm.check_drawdown() is True

    def test_exceeds_limit(self, risk_config: RiskConfig):
        """Drawdown at or above threshold should fail."""
        portfolio = FakePortfolioMonitor(
            total_value=Decimal("90000"),  # 10% drawdown from 100k
            peak_equity=Decimal("100000"),
        )
        rm = RiskManager(config=risk_config, portfolio=portfolio)
        assert rm.check_drawdown() is False

    def test_exceeds_limit_large_drawdown(self, risk_config: RiskConfig):
        """Large drawdown should fail."""
        portfolio = FakePortfolioMonitor(
            total_value=Decimal("80000"),  # 20% drawdown
            peak_equity=Decimal("100000"),
        )
        rm = RiskManager(config=risk_config, portfolio=portfolio)
        assert rm.check_drawdown() is False

    def test_zero_peak_equity(self, risk_config: RiskConfig):
        """Zero peak equity should pass (no history)."""
        portfolio = FakePortfolioMonitor(
            total_value=Decimal("0"),
            peak_equity=Decimal("0"),
        )
        rm = RiskManager(config=risk_config, portfolio=portfolio)
        assert rm.check_drawdown() is True


# ---------------------------------------------------------------------------
# Test: check_daily_loss
# ---------------------------------------------------------------------------


class TestCheckDailyLoss:
    """Tests for the daily loss check."""

    def test_no_loss(self, risk_manager: RiskManager):
        """No daily loss should pass."""
        assert risk_manager.check_daily_loss() is True

    def test_profit_passes(self, risk_manager: RiskManager):
        """Positive daily P&L should always pass."""
        risk_manager.update_daily_pnl(Decimal("5000"))
        assert risk_manager.check_daily_loss() is True

    def test_within_limit(self, risk_manager: RiskManager):
        """Daily loss below threshold should pass."""
        risk_manager.update_daily_pnl(Decimal("-1500"))  # 1.5% of 100k
        assert risk_manager.check_daily_loss() is True

    def test_exceeds_limit(self, risk_manager: RiskManager):
        """Daily loss at or above threshold should fail."""
        risk_manager.update_daily_pnl(Decimal("-2000"))  # 2% of 100k
        assert risk_manager.check_daily_loss() is False

    def test_cumulative_loss(self, risk_manager: RiskManager):
        """Multiple losses accumulating past threshold should fail."""
        risk_manager.update_daily_pnl(Decimal("-1000"))
        risk_manager.update_daily_pnl(Decimal("-1500"))  # Total: -2500 = 2.5%
        assert risk_manager.check_daily_loss() is False

    def test_reset_daily_pnl(self, risk_manager: RiskManager):
        """Resetting daily P&L should clear the loss."""
        risk_manager.update_daily_pnl(Decimal("-3000"))
        risk_manager.reset_daily_pnl()
        assert risk_manager.check_daily_loss() is True


# ---------------------------------------------------------------------------
# Test: check_sector_concentration
# ---------------------------------------------------------------------------


class TestCheckSectorConcentration:
    """Tests for the sector concentration check."""

    def test_within_limit(self, risk_manager: RiskManager):
        """Adding position within sector limit should pass."""
        signal = make_signal(
            suggested_size=Decimal("20000"), sector="Technology"
        )  # 20% of 100k
        assert risk_manager.check_sector_concentration(signal) is True

    def test_exceeds_limit(self, risk_manager: RiskManager):
        """Adding position that exceeds sector limit should fail."""
        # Set existing sector exposure
        risk_manager.update_sector_exposure("Technology", Decimal("22000"))
        signal = make_signal(
            suggested_size=Decimal("5000"), sector="Technology"
        )  # 22k + 5k = 27% > 25%
        assert risk_manager.check_sector_concentration(signal) is False

    def test_different_sector_passes(self, risk_manager: RiskManager):
        """Adding position in a different sector should pass."""
        risk_manager.update_sector_exposure("Technology", Decimal("24000"))
        signal = make_signal(
            suggested_size=Decimal("10000"), sector="Healthcare"
        )
        assert risk_manager.check_sector_concentration(signal) is True

    def test_at_exact_limit(self, risk_manager: RiskManager):
        """Position at exactly the sector limit should pass."""
        signal = make_signal(
            suggested_size=Decimal("25000"), sector="Energy"
        )  # Exactly 25%
        assert risk_manager.check_sector_concentration(signal) is True

    def test_zero_portfolio_value(self, risk_config: RiskConfig):
        """Zero portfolio value should fail sector check."""
        portfolio = FakePortfolioMonitor(total_value=Decimal("0"))
        rm = RiskManager(config=risk_config, portfolio=portfolio)
        signal = make_signal(suggested_size=Decimal("1000"), sector="Tech")
        assert rm.check_sector_concentration(signal) is False


# ---------------------------------------------------------------------------
# Test: check_correlation
# ---------------------------------------------------------------------------


class TestCheckCorrelation:
    """Tests for the correlation check."""

    def test_no_correlated_symbols(self, risk_manager: RiskManager):
        """Symbol with no registered correlations should pass."""
        signal = make_signal(symbol="AAPL")
        assert risk_manager.check_correlation(signal) is True

    def test_correlated_but_not_in_portfolio(self, risk_config: RiskConfig):
        """Correlated symbols not in portfolio should pass."""
        portfolio = FakePortfolioMonitor(positions={})
        rm = RiskManager(config=risk_config, portfolio=portfolio)
        rm.set_correlation("AAPL", ["MSFT", "GOOG"])
        signal = make_signal(symbol="AAPL")
        assert rm.check_correlation(signal) is True

    def test_one_correlated_in_portfolio(self, risk_config: RiskConfig):
        """One correlated symbol in portfolio should still pass."""
        portfolio = FakePortfolioMonitor(positions={"MSFT": object()})
        rm = RiskManager(config=risk_config, portfolio=portfolio)
        rm.set_correlation("AAPL", ["MSFT", "GOOG"])
        signal = make_signal(symbol="AAPL")
        assert rm.check_correlation(signal) is True

    def test_two_correlated_in_portfolio_fails(self, risk_config: RiskConfig):
        """Two or more correlated symbols in portfolio should fail."""
        portfolio = FakePortfolioMonitor(
            positions={"MSFT": object(), "GOOG": object()}
        )
        rm = RiskManager(config=risk_config, portfolio=portfolio)
        rm.set_correlation("AAPL", ["MSFT", "GOOG"])
        signal = make_signal(symbol="AAPL")
        assert rm.check_correlation(signal) is False


# ---------------------------------------------------------------------------
# Test: halt_trading
# ---------------------------------------------------------------------------


class TestHaltTrading:
    """Tests for the trading halt mechanism."""

    def test_halt_sets_flag(self, risk_manager: RiskManager):
        """Halting should set the halted flag."""
        risk_manager.halt_trading("test halt")
        assert risk_manager.is_halted is True
        assert risk_manager.halt_reason == "test halt"

    def test_halt_calls_callback(self, risk_config: RiskConfig, portfolio: FakePortfolioMonitor):
        """Halting should invoke the on_halt callback."""
        callback_reasons: list[str] = []
        rm = RiskManager(
            config=risk_config,
            portfolio=portfolio,
            on_halt=lambda reason: callback_reasons.append(reason),
        )
        rm.halt_trading("drawdown exceeded")
        assert callback_reasons == ["drawdown exceeded"]

    def test_halt_without_callback(self, risk_manager: RiskManager):
        """Halting without a callback should not raise."""
        risk_manager.halt_trading("no callback")
        assert risk_manager.is_halted is True

    def test_resume_trading(self, risk_manager: RiskManager):
        """Resuming should clear the halted flag."""
        risk_manager.halt_trading("test")
        risk_manager.resume_trading()
        assert risk_manager.is_halted is False
        assert risk_manager.halt_reason is None


# ---------------------------------------------------------------------------
# Test: check_order (combined flow)
# ---------------------------------------------------------------------------


class TestCheckOrder:
    """Tests for the combined check_order flow."""

    @pytest.mark.asyncio
    async def test_approved_order(self, risk_manager: RiskManager):
        """Order passing all checks should be approved."""
        signal = make_signal(suggested_size=Decimal("3000"))
        result = await risk_manager.check_order(signal)
        assert result.approved is True
        assert result.reason is None

    @pytest.mark.asyncio
    async def test_rejected_when_halted(self, risk_manager: RiskManager):
        """Order should be rejected when trading is halted."""
        risk_manager.halt_trading("manual halt")
        signal = make_signal(suggested_size=Decimal("1000"))
        result = await risk_manager.check_order(signal)
        assert result.approved is False
        assert "halted" in result.reason.lower()

    @pytest.mark.asyncio
    async def test_rejected_position_size(self, risk_manager: RiskManager):
        """Order exceeding position size should be rejected."""
        signal = make_signal(suggested_size=Decimal("10000"))  # 10% > 5%
        result = await risk_manager.check_order(signal)
        assert result.approved is False
        assert "position size" in result.reason.lower()

    @pytest.mark.asyncio
    async def test_rejected_drawdown(self, risk_config: RiskConfig):
        """Order should be rejected and trading halted on drawdown breach."""
        portfolio = FakePortfolioMonitor(
            total_value=Decimal("89000"),  # 11% drawdown
            peak_equity=Decimal("100000"),
        )
        rm = RiskManager(config=risk_config, portfolio=portfolio)
        signal = make_signal(suggested_size=Decimal("1000"))  # Small enough for position check
        result = await rm.check_order(signal)
        assert result.approved is False
        assert "drawdown" in result.reason.lower()
        assert rm.is_halted is True

    @pytest.mark.asyncio
    async def test_rejected_daily_loss(self, risk_manager: RiskManager):
        """Order should be rejected and trading halted on daily loss breach."""
        risk_manager.update_daily_pnl(Decimal("-2500"))  # 2.5% > 2%
        signal = make_signal(suggested_size=Decimal("1000"))
        result = await risk_manager.check_order(signal)
        assert result.approved is False
        assert "daily loss" in result.reason.lower()
        assert risk_manager.is_halted is True

    @pytest.mark.asyncio
    async def test_rejected_sector_concentration(self, risk_manager: RiskManager):
        """Order should be rejected when sector concentration is exceeded."""
        risk_manager.update_sector_exposure("Technology", Decimal("23000"))
        signal = make_signal(
            suggested_size=Decimal("3000"), sector="Technology"
        )  # 23k + 3k = 26% > 25%
        result = await risk_manager.check_order(signal)
        assert result.approved is False
        assert "sector" in result.reason.lower()

    @pytest.mark.asyncio
    async def test_rejected_correlation(self, risk_config: RiskConfig):
        """Order should be rejected when correlation limit is exceeded."""
        portfolio = FakePortfolioMonitor(
            positions={"MSFT": object(), "GOOG": object()}
        )
        rm = RiskManager(config=risk_config, portfolio=portfolio)
        rm.set_correlation("AAPL", ["MSFT", "GOOG"])
        signal = make_signal(symbol="AAPL", suggested_size=Decimal("3000"))
        result = await rm.check_order(signal)
        assert result.approved is False
        assert "correlation" in result.reason.lower()

    @pytest.mark.asyncio
    async def test_risk_metrics_included(self, risk_manager: RiskManager):
        """Result should include risk metrics regardless of approval."""
        signal = make_signal(suggested_size=Decimal("3000"))
        result = await risk_manager.check_order(signal)
        assert "total_value" in result.risk_metrics
        assert "peak_equity" in result.risk_metrics
        assert "daily_pnl" in result.risk_metrics

    @pytest.mark.asyncio
    async def test_first_failure_short_circuits(self, risk_config: RiskConfig):
        """check_order should return the first failure reason encountered."""
        # Both halted AND position too large — halted check comes first
        portfolio = FakePortfolioMonitor(total_value=Decimal("100000"))
        rm = RiskManager(config=risk_config, portfolio=portfolio)
        rm.halt_trading("test halt")
        signal = make_signal(suggested_size=Decimal("50000"))  # Also too large
        result = await rm.check_order(signal)
        assert "halted" in result.reason.lower()


# ---------------------------------------------------------------------------
# Test: RiskCheckResult dataclass
# ---------------------------------------------------------------------------


class TestRiskCheckResult:
    """Tests for the RiskCheckResult dataclass."""

    def test_approved_result(self):
        """Approved result should have correct defaults."""
        result = RiskCheckResult(approved=True)
        assert result.approved is True
        assert result.reason is None
        assert result.risk_metrics == {}

    def test_rejected_result(self):
        """Rejected result should carry reason and metrics."""
        result = RiskCheckResult(
            approved=False,
            reason="Position too large",
            risk_metrics={"position_pct": 0.08},
        )
        assert result.approved is False
        assert result.reason == "Position too large"
        assert result.risk_metrics["position_pct"] == 0.08
