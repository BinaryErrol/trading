"""Risk Manager — pre-trade risk checks and trading halt logic.

Runs position size, drawdown, daily loss, sector concentration, and correlation
checks before approving any order. Halts trading when thresholds are breached.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Callable, Protocol

import structlog

from src.config.settings import RiskConfig
from src.strategies.signals import Signal

logger = structlog.get_logger(__name__)


class PortfolioMonitorProtocol(Protocol):
    """Protocol for the PortfolioMonitor dependency."""

    def get_total_value(self) -> Decimal: ...

    def get_peak_equity(self) -> Decimal: ...

    @property
    def positions(self) -> dict[str, Any]: ...


@dataclass
class RiskCheckResult:
    """Result of running pre-trade risk checks on a signal.

    Attributes:
        approved: Whether the order passed all risk checks.
        reason: Human-readable rejection reason (None if approved).
        risk_metrics: Dictionary of computed risk metrics at check time.
    """

    approved: bool
    reason: str | None = None
    risk_metrics: dict[str, Any] = field(default_factory=dict)


class RiskManager:
    """Pre-trade risk checks and trading halt enforcement.

    Checks run by check_order():
    1. Trading halted — reject immediately if halted
    2. Position size — signal size vs max % of portfolio
    3. Drawdown — portfolio drawdown from peak
    4. Daily loss — cumulative daily P&L vs limit
    5. Sector concentration — sector exposure limit
    6. Correlation — correlated position limit

    Args:
        config: RiskConfig with threshold values.
        portfolio: PortfolioMonitor for equity/position data.
        on_halt: Optional callback invoked when trading is halted.
    """

    def __init__(
        self,
        config: RiskConfig,
        portfolio: PortfolioMonitorProtocol,
        on_halt: Callable[[str], None] | None = None,
    ):
        self._config = config
        self._portfolio = portfolio
        self._on_halt = on_halt
        self._halted: bool = False
        self._halt_reason: str | None = None
        self._daily_pnl: Decimal = Decimal("0")
        self._sector_positions: dict[str, Decimal] = {}
        self._correlation_map: dict[str, list[str]] = {}

    @property
    def is_halted(self) -> bool:
        """Whether trading is currently halted."""
        return self._halted

    @property
    def halt_reason(self) -> str | None:
        """Reason trading was halted, or None."""
        return self._halt_reason

    def update_daily_pnl(self, pnl: Decimal) -> None:
        """Update the cumulative daily P&L tracker.

        Args:
            pnl: The P&L amount to add (negative for losses).
        """
        self._daily_pnl += pnl

    def reset_daily_pnl(self) -> None:
        """Reset daily P&L at start of new trading day."""
        self._daily_pnl = Decimal("0")
        # Also unhalt if halted due to daily loss
        if self._halted and self._halt_reason and "daily loss" in self._halt_reason:
            self._halted = False
            self._halt_reason = None
            logger.info("trading_unhalted", reason="new trading day")

    def set_sector(self, symbol: str, sector: str) -> None:
        """Register a symbol's sector for concentration checks.

        Args:
            symbol: Ticker symbol.
            sector: Sector name (e.g. "Technology", "Healthcare").
        """
        if sector not in self._sector_positions:
            self._sector_positions[sector] = Decimal("0")

    def update_sector_exposure(self, sector: str, value: Decimal) -> None:
        """Update the total exposure for a sector.

        Args:
            sector: Sector name.
            value: Total market value of positions in this sector.
        """
        self._sector_positions[sector] = value

    def set_correlation(self, symbol: str, correlated_symbols: list[str]) -> None:
        """Register correlated symbols for a given instrument.

        Args:
            symbol: The base symbol.
            correlated_symbols: Symbols with correlation > threshold to base.
        """
        self._correlation_map[symbol] = correlated_symbols

    async def check_order(self, signal: Signal) -> RiskCheckResult:
        """Run all pre-trade risk checks on a signal.

        Checks are run in order; the first failure short-circuits and returns
        the rejection reason. If all pass, returns approved=True.

        Args:
            signal: The trading signal to validate.

        Returns:
            RiskCheckResult with approval status and metrics.
        """
        total_value = self._portfolio.get_total_value()
        peak_equity = self._portfolio.get_peak_equity()

        risk_metrics: dict[str, Any] = {
            "total_value": str(total_value),
            "peak_equity": str(peak_equity),
            "daily_pnl": str(self._daily_pnl),
            "halted": self._halted,
        }

        # Check 1: Trading halted
        if self._halted:
            logger.warning(
                "order_rejected_halted",
                symbol=signal.symbol,
                reason=self._halt_reason,
            )
            return RiskCheckResult(
                approved=False,
                reason=f"Trading halted: {self._halt_reason}",
                risk_metrics=risk_metrics,
            )

        # Check 2: Position size
        if not self.check_position_size(signal):
            position_pct = (
                float(signal.suggested_size / total_value) if total_value > 0 else 0
            )
            risk_metrics["position_pct"] = position_pct
            reason = (
                f"Position size {position_pct:.1%} exceeds max "
                f"{self._config.max_position_pct:.1%}"
            )
            logger.warning(
                "order_rejected_position_size",
                symbol=signal.symbol,
                position_pct=position_pct,
                max_pct=self._config.max_position_pct,
            )
            return RiskCheckResult(
                approved=False, reason=reason, risk_metrics=risk_metrics
            )

        # Check 3: Drawdown
        if not self.check_drawdown():
            drawdown_pct = (
                float((peak_equity - total_value) / peak_equity)
                if peak_equity > 0
                else 0
            )
            risk_metrics["drawdown_pct"] = drawdown_pct
            reason = (
                f"Portfolio drawdown {drawdown_pct:.1%} exceeds max "
                f"{self._config.max_drawdown_pct:.1%}"
            )
            self.halt_trading(reason)
            return RiskCheckResult(
                approved=False, reason=reason, risk_metrics=risk_metrics
            )

        # Check 4: Daily loss
        if not self.check_daily_loss():
            daily_loss_pct = (
                float(abs(self._daily_pnl) / total_value) if total_value > 0 else 0
            )
            risk_metrics["daily_loss_pct"] = daily_loss_pct
            reason = (
                f"Daily loss {daily_loss_pct:.1%} exceeds max "
                f"{self._config.max_daily_loss_pct:.1%}"
            )
            self.halt_trading(reason)
            return RiskCheckResult(
                approved=False, reason=reason, risk_metrics=risk_metrics
            )

        # Check 5: Sector concentration
        if not self.check_sector_concentration(signal):
            reason = (
                f"Sector concentration would exceed "
                f"{self._config.max_sector_concentration:.0%} limit"
            )
            logger.warning(
                "order_rejected_sector_concentration",
                symbol=signal.symbol,
            )
            return RiskCheckResult(
                approved=False, reason=reason, risk_metrics=risk_metrics
            )

        # Check 6: Correlation
        if not self.check_correlation(signal):
            reason = (
                f"Adding {signal.symbol} would exceed correlation threshold "
                f"({self._config.max_correlation})"
            )
            logger.warning(
                "order_rejected_correlation",
                symbol=signal.symbol,
            )
            return RiskCheckResult(
                approved=False, reason=reason, risk_metrics=risk_metrics
            )

        # All checks passed
        logger.info(
            "order_approved",
            symbol=signal.symbol,
            strategy=signal.strategy_name,
            size=str(signal.suggested_size),
        )
        return RiskCheckResult(
            approved=True, reason=None, risk_metrics=risk_metrics
        )

    def check_position_size(self, signal: Signal) -> bool:
        """Check if position size is within the max % of portfolio.

        Returns True if the position is acceptable, False if it exceeds the limit.

        Rule: signal.suggested_size / portfolio_total_value <= max_position_pct
        """
        total_value = self._portfolio.get_total_value()
        if total_value <= 0:
            return False

        position_pct = signal.suggested_size / total_value
        return float(position_pct) <= self._config.max_position_pct

    def check_drawdown(self) -> bool:
        """Check if portfolio drawdown is within acceptable limits.

        Returns True if drawdown is acceptable, False if it exceeds the threshold.

        Rule: (peak_equity - current_equity) / peak_equity < max_drawdown_pct
        """
        peak_equity = self._portfolio.get_peak_equity()
        if peak_equity <= 0:
            return True

        current_value = self._portfolio.get_total_value()
        drawdown = (peak_equity - current_value) / peak_equity
        return float(drawdown) < self._config.max_drawdown_pct

    def check_daily_loss(self) -> bool:
        """Check if daily loss is within acceptable limits.

        Returns True if daily loss is acceptable, False if it exceeds the limit.

        Rule: abs(daily_pnl) / portfolio_value < max_daily_loss_pct (when pnl is negative)
        """
        if self._daily_pnl >= 0:
            return True

        total_value = self._portfolio.get_total_value()
        if total_value <= 0:
            return False

        daily_loss_pct = abs(self._daily_pnl) / total_value
        return float(daily_loss_pct) < self._config.max_daily_loss_pct

    def check_sector_concentration(self, signal: Signal) -> bool:
        """Check if adding this position would exceed sector concentration limits.

        Returns True if sector exposure is acceptable, False if it would exceed the limit.

        Rule: (sector_exposure + signal_size) / total_value <= max_sector_concentration
        """
        total_value = self._portfolio.get_total_value()
        if total_value <= 0:
            return False

        # Get the sector for this symbol from metadata
        sector = signal.metadata.get("sector", "Unknown")

        current_sector_value = self._sector_positions.get(sector, Decimal("0"))
        new_sector_value = current_sector_value + signal.suggested_size
        concentration = new_sector_value / total_value

        return float(concentration) <= self._config.max_sector_concentration

    def check_correlation(self, signal: Signal) -> bool:
        """Check if adding this position would exceed correlation limits.

        Returns True if correlation exposure is acceptable, False otherwise.

        Simplified check: if the signal's symbol has correlated symbols that
        are already in the portfolio, reject if adding would create too many
        correlated positions.
        """
        correlated = self._correlation_map.get(signal.symbol, [])
        if not correlated:
            return True

        # Check how many correlated symbols are already in portfolio positions
        current_positions = self._portfolio.positions
        correlated_in_portfolio = [
            sym for sym in correlated if sym in current_positions
        ]

        # If we already have positions in correlated symbols, reject
        # This is a simplified check — a more sophisticated version would
        # compute actual portfolio correlation
        if len(correlated_in_portfolio) >= 2:
            logger.debug(
                "correlation_check_failed",
                symbol=signal.symbol,
                correlated_positions=correlated_in_portfolio,
            )
            return False

        return True

    def halt_trading(self, reason: str) -> None:
        """Halt all trading and invoke the on_halt callback.

        Args:
            reason: Human-readable reason for the halt.
        """
        self._halted = True
        self._halt_reason = reason
        logger.critical("trading_halted", reason=reason)

        if self._on_halt is not None:
            self._on_halt(reason)

    def resume_trading(self) -> None:
        """Resume trading after a halt (manual intervention)."""
        if self._halted:
            logger.info(
                "trading_resumed",
                previous_reason=self._halt_reason,
            )
            self._halted = False
            self._halt_reason = None
