"""Portfolio monitor for real-time tracking, metrics, and reporting.

Provides position reconciliation with IBKR, per-strategy performance metrics
(Sharpe, Sortino, max drawdown, win rate, profit factor), daily reports, and CSV export.
"""

from __future__ import annotations

import csv
import math
from collections import deque
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Protocol

import structlog

logger = structlog.get_logger(__name__)


class ConnectionProtocol(Protocol):
    """Protocol for IBKR connection used by PortfolioMonitor."""

    @property
    def ib(self) -> Any: ...

    @property
    def is_connected(self) -> bool: ...


@dataclass
class Position:
    """Represents a single portfolio position."""

    symbol: str
    asset_class: str
    strategy_name: str
    quantity: Decimal
    avg_entry_price: Decimal
    current_price: Decimal
    unrealized_pnl: Decimal
    realized_pnl: Decimal
    opened_at: datetime


@dataclass
class StrategyMetrics:
    """Performance metrics for a single strategy."""

    total_return: Decimal
    annualized_return: Decimal
    sharpe_ratio: float
    sortino_ratio: float
    max_drawdown: Decimal
    win_rate: float
    profit_factor: float
    total_trades: int


@dataclass
class Trade:
    """A completed trade record for metric calculation."""

    strategy_name: str
    symbol: str
    pnl: Decimal
    return_pct: float
    closed_at: datetime


@dataclass
class DailyReport:
    """End-of-day portfolio summary."""

    date: date
    total_equity: Decimal
    total_pnl: Decimal
    realized_pnl: Decimal
    unrealized_pnl: Decimal
    peak_equity: Decimal
    drawdown_pct: Decimal
    trades: list[Trade]
    strategy_metrics: dict[str, StrategyMetrics]


class PortfolioMonitor:
    """Tracks portfolio positions, calculates metrics, and generates reports.

    Uses a ConnectionManager protocol for IBKR position synchronization.
    """

    def __init__(
        self,
        connection: ConnectionProtocol | None = None,
        initial_equity: Decimal = Decimal("0"),
    ):
        self._connection = connection
        self._positions: dict[str, Position] = {}
        self._peak_equity: Decimal = initial_equity
        self._total_equity: Decimal = initial_equity
        self._trades: list[Trade] = []
        self._daily_returns: list[float] = []
        self._equity_history: deque[Decimal] = deque(maxlen=2520)
        if initial_equity:
            self._equity_history.append(initial_equity)

    @property
    def positions(self) -> dict[str, Position]:
        """Return current positions keyed by symbol."""
        return dict(self._positions)

    async def sync_positions(self) -> None:
        """Reconcile internal state with IBKR account positions.

        Fetches current positions from IBKR and updates internal tracking.
        Positions not found in IBKR are removed; new IBKR positions are added.
        """
        if self._connection is None or not self._connection.is_connected:
            logger.warning("sync_positions_skipped", reason="not connected")
            return

        ib = self._connection.ib
        ibkr_positions = ib.positions()

        # Build a set of symbols from IBKR
        ibkr_symbols: set[str] = set()

        for pos in ibkr_positions:
            symbol = pos.contract.symbol
            ibkr_symbols.add(symbol)

            quantity = Decimal(str(pos.position))
            avg_cost = Decimal(str(pos.avgCost))

            if symbol in self._positions:
                # Update existing position
                existing = self._positions[symbol]
                existing.quantity = quantity
                existing.avg_entry_price = avg_cost
                logger.debug("position_updated", symbol=symbol, quantity=quantity)
            else:
                # Add new position from IBKR
                self._positions[symbol] = Position(
                    symbol=symbol,
                    asset_class=pos.contract.secType or "STK",
                    strategy_name="unknown",
                    quantity=quantity,
                    avg_entry_price=avg_cost,
                    current_price=avg_cost,  # Will be updated with market data
                    unrealized_pnl=Decimal("0"),
                    realized_pnl=Decimal("0"),
                    opened_at=datetime.now(),
                )
                logger.info("position_added_from_ibkr", symbol=symbol, quantity=quantity)

        # Remove positions no longer in IBKR
        stale_symbols = set(self._positions.keys()) - ibkr_symbols
        for symbol in stale_symbols:
            del self._positions[symbol]
            logger.info("position_removed", symbol=symbol, reason="not in IBKR")

        logger.info(
            "positions_synced",
            total_positions=len(self._positions),
            added=len(ibkr_symbols - set(self._positions.keys())),
            removed=len(stale_symbols),
        )

    def get_total_value(self) -> Decimal:
        """Get current total portfolio value (sum of position market values + equity)."""
        position_value = sum(
            pos.quantity * pos.current_price for pos in self._positions.values()
        )
        return self._total_equity + position_value

    def get_unrealized_pnl(self) -> Decimal:
        """Get total unrealized P&L across all positions."""
        return sum(pos.unrealized_pnl for pos in self._positions.values())

    def get_peak_equity(self) -> Decimal:
        """Get historical peak equity for drawdown calculation."""
        return self._peak_equity

    def update_equity(self, current_equity: Decimal) -> None:
        """Update current equity and track peak for drawdown calculation."""
        self._total_equity = current_equity
        if current_equity > self._peak_equity:
            self._peak_equity = current_equity
        self._equity_history.append(current_equity)

    def record_trade(self, trade: Trade) -> None:
        """Record a completed trade for metric calculation."""
        self._trades.append(trade)
        self._daily_returns.append(trade.return_pct)

    def calculate_strategy_metrics(self, strategy: str) -> StrategyMetrics:
        """Calculate performance metrics for a specific strategy.

        Metrics:
        - Sharpe ratio: mean(returns) / std(returns) * sqrt(252)
        - Sortino ratio: mean(returns) / downside_std(returns) * sqrt(252)
        - Max drawdown: maximum peak-to-trough decline
        - Win rate: winning_trades / total_trades
        - Profit factor: gross_profit / gross_loss
        """
        strategy_trades = [t for t in self._trades if t.strategy_name == strategy]
        total_trades = len(strategy_trades)

        if total_trades == 0:
            return StrategyMetrics(
                total_return=Decimal("0"),
                annualized_return=Decimal("0"),
                sharpe_ratio=0.0,
                sortino_ratio=0.0,
                max_drawdown=Decimal("0"),
                win_rate=0.0,
                profit_factor=0.0,
                total_trades=0,
            )

        # Returns for ratio calculations
        returns = [t.return_pct for t in strategy_trades]

        # Total return
        pnls = [t.pnl for t in strategy_trades]
        total_pnl = sum(pnls)
        total_return = total_pnl

        # Annualized return (assume 252 trading days)
        if len(strategy_trades) >= 2:
            first_trade = strategy_trades[0].closed_at
            last_trade = strategy_trades[-1].closed_at
            days = max((last_trade - first_trade).days, 1)
            years = days / 365.25
            if years > 0:
                annualized_return = Decimal(str(
                    float(total_return) * (252 / max(len(returns), 1))
                ))
            else:
                annualized_return = total_return
        else:
            annualized_return = total_return

        # Sharpe ratio: mean(returns) / std(returns) * sqrt(252)
        sharpe_ratio = self._calculate_sharpe(returns)

        # Sortino ratio: mean(returns) / downside_std(returns) * sqrt(252)
        sortino_ratio = self._calculate_sortino(returns)

        # Max drawdown
        max_drawdown = self._calculate_max_drawdown(pnls)

        # Win rate
        winning_trades = sum(1 for t in strategy_trades if t.pnl > 0)
        win_rate = winning_trades / total_trades

        # Profit factor: gross_profit / gross_loss
        profit_factor = self._calculate_profit_factor(pnls)

        return StrategyMetrics(
            total_return=total_return,
            annualized_return=annualized_return,
            sharpe_ratio=sharpe_ratio,
            sortino_ratio=sortino_ratio,
            max_drawdown=max_drawdown,
            win_rate=win_rate,
            profit_factor=profit_factor,
            total_trades=total_trades,
        )

    async def generate_daily_report(self) -> DailyReport:
        """Generate end-of-day summary with all trades, P&L, and risk metrics."""
        today = date.today()
        total_equity = self.get_total_value()
        unrealized = self.get_unrealized_pnl()
        realized = sum(pos.realized_pnl for pos in self._positions.values())
        total_pnl = unrealized + realized
        peak = self.get_peak_equity()

        # Drawdown percentage
        if peak > 0:
            drawdown_pct = ((peak - total_equity) / peak) * Decimal("100")
        else:
            drawdown_pct = Decimal("0")

        # Today's trades
        today_trades = [
            t for t in self._trades
            if t.closed_at.date() == today
        ]

        # Per-strategy metrics
        strategies = set(t.strategy_name for t in self._trades)
        strategy_metrics = {
            s: self.calculate_strategy_metrics(s) for s in strategies
        }

        report = DailyReport(
            date=today,
            total_equity=total_equity,
            total_pnl=total_pnl,
            realized_pnl=realized,
            unrealized_pnl=unrealized,
            peak_equity=peak,
            drawdown_pct=drawdown_pct,
            trades=today_trades,
            strategy_metrics=strategy_metrics,
        )

        logger.info(
            "daily_report_generated",
            date=str(today),
            total_equity=str(total_equity),
            total_pnl=str(total_pnl),
            drawdown_pct=str(drawdown_pct),
        )

        return report

    async def export_csv(self, start: date, end: date) -> Path:
        """Export trade history to CSV file for the given date range.

        Returns the path to the generated CSV file.
        """
        filtered_trades = [
            t for t in self._trades
            if start <= t.closed_at.date() <= end
        ]

        export_dir = Path("data")
        export_dir.mkdir(parents=True, exist_ok=True)
        filename = f"trades_{start.isoformat()}_{end.isoformat()}.csv"
        filepath = export_dir / filename

        with open(filepath, "w", newline="") as csvfile:
            writer = csv.writer(csvfile)
            writer.writerow(["strategy", "symbol", "pnl", "return_pct", "closed_at"])
            for trade in filtered_trades:
                writer.writerow([
                    trade.strategy_name,
                    trade.symbol,
                    str(trade.pnl),
                    f"{trade.return_pct:.6f}",
                    trade.closed_at.isoformat(),
                ])

        logger.info(
            "csv_exported",
            filepath=str(filepath),
            trade_count=len(filtered_trades),
            start=str(start),
            end=str(end),
        )

        return filepath

    @staticmethod
    def _calculate_sharpe(returns: list[float]) -> float:
        """Calculate Sharpe ratio: mean(returns) / std(returns) * sqrt(252)."""
        if len(returns) < 2:
            return 0.0

        mean_return = sum(returns) / len(returns)
        variance = sum((r - mean_return) ** 2 for r in returns) / (len(returns) - 1)
        std_return = math.sqrt(variance)

        if std_return == 0:
            return 0.0

        return (mean_return / std_return) * math.sqrt(252)

    @staticmethod
    def _calculate_sortino(returns: list[float]) -> float:
        """Calculate Sortino ratio: mean(returns) / downside_std(returns) * sqrt(252)."""
        if len(returns) < 2:
            return 0.0

        mean_return = sum(returns) / len(returns)

        # Downside deviation: only negative returns
        downside_returns = [r for r in returns if r < 0]
        if not downside_returns:
            return 0.0 if mean_return == 0 else float("inf")

        downside_variance = sum(r ** 2 for r in downside_returns) / len(returns)
        downside_std = math.sqrt(downside_variance)

        if downside_std == 0:
            return 0.0

        return (mean_return / downside_std) * math.sqrt(252)

    @staticmethod
    def _calculate_max_drawdown(pnls: list[Decimal]) -> Decimal:
        """Calculate maximum peak-to-trough decline from cumulative P&L series."""
        if not pnls:
            return Decimal("0")

        cumulative = Decimal("0")
        peak = Decimal("0")
        max_dd = Decimal("0")

        for pnl in pnls:
            cumulative += pnl
            if cumulative > peak:
                peak = cumulative
            drawdown = peak - cumulative
            if drawdown > max_dd:
                max_dd = drawdown

        return max_dd

    @staticmethod
    def _calculate_profit_factor(pnls: list[Decimal]) -> float:
        """Calculate profit factor: gross_profit / gross_loss."""
        gross_profit = sum(p for p in pnls if p > 0)
        gross_loss = abs(sum(p for p in pnls if p < 0))

        if gross_loss == 0:
            return float("inf") if gross_profit > 0 else 0.0

        return float(gross_profit / gross_loss)
