"""Backtesting engine for strategy simulation against historical data.

Provides single-strategy and multi-strategy portfolio backtests with:
- Realistic execution simulation (slippage, commissions, market impact)
- Look-ahead bias prevention (strategies only see data at time <= current)
- Performance metrics (Sharpe, Sortino, max drawdown, win rate, profit factor)
- Database storage of results for comparison
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import structlog

from src.backtesting.simulator import SimulatedExecution, SimulatedFill
from src.config.settings import BacktestConfig
from src.data.bar_builder import Bar, Timeframe
from src.data.historical import load_historical_csv
from src.portfolio.capital_allocator import AllocationMode, CapitalAllocator
from src.risk.manager import RiskManager
from src.strategies.base import BaseStrategy
from src.strategies.signals import Signal, SignalDirection

logger = structlog.get_logger(__name__)


@dataclass
class BacktestTrade:
    """Record of a single trade executed during a backtest.

    Attributes:
        symbol: Ticker symbol traded.
        direction: Trade direction (LONG, SHORT, CLOSE).
        entry_price: Price at which the position was opened.
        exit_price: Price at which the position was closed (None if still open).
        quantity: Number of shares.
        entry_time: Timestamp of entry.
        exit_time: Timestamp of exit (None if still open).
        pnl: Realized profit/loss for this trade.
        commission: Total commission paid.
        strategy_name: Name of the strategy that generated the trade.
    """

    symbol: str
    direction: SignalDirection
    entry_price: Decimal
    exit_price: Decimal | None
    quantity: Decimal
    entry_time: datetime
    exit_time: datetime | None
    pnl: Decimal
    commission: Decimal
    strategy_name: str


@dataclass
class BacktestResult:
    """Complete results from a backtest run.

    Attributes:
        strategy_name: Name of the strategy tested.
        start_date: Start of the backtest period.
        end_date: End of the backtest period.
        total_return: Total return as a decimal (e.g. 0.15 = 15%).
        annualized_return: Annualized return.
        sharpe_ratio: Annualized Sharpe ratio (risk-free rate = 0).
        sortino_ratio: Annualized Sortino ratio.
        max_drawdown: Maximum peak-to-trough drawdown as decimal.
        win_rate: Fraction of trades that were profitable.
        profit_factor: Gross profit / gross loss.
        avg_trade_duration: Average duration of closed trades.
        total_trades: Total number of completed trades.
        trades: List of all trade records.
        equity_curve: Series of portfolio values over time.
        parameters: Strategy parameters used.
    """

    strategy_name: str
    start_date: date
    end_date: date
    total_return: Decimal
    annualized_return: Decimal
    sharpe_ratio: float
    sortino_ratio: float
    max_drawdown: Decimal
    win_rate: float
    profit_factor: float
    avg_trade_duration: timedelta
    total_trades: int
    trades: list[BacktestTrade] = field(default_factory=list)
    equity_curve: pd.Series = field(default_factory=lambda: pd.Series(dtype=float))
    parameters: dict[str, Any] = field(default_factory=dict)


@dataclass
class PortfolioBacktestResult:
    """Results from a multi-strategy portfolio backtest.

    Attributes:
        start_date: Start of the backtest period.
        end_date: End of the backtest period.
        total_return: Combined portfolio return.
        annualized_return: Annualized portfolio return.
        sharpe_ratio: Portfolio Sharpe ratio.
        sortino_ratio: Portfolio Sortino ratio.
        max_drawdown: Portfolio max drawdown.
        strategy_results: Per-strategy results.
        equity_curve: Combined portfolio equity curve.
    """

    start_date: date
    end_date: date
    total_return: Decimal
    annualized_return: Decimal
    sharpe_ratio: float
    sortino_ratio: float
    max_drawdown: Decimal
    strategy_results: dict[str, BacktestResult] = field(default_factory=dict)
    equity_curve: pd.Series = field(default_factory=lambda: pd.Series(dtype=float))


class _LookAheadGuard:
    """Wraps a DataFrame to prevent access to data beyond the current time.

    This ensures strategies cannot peek at future data during backtesting.
    """

    def __init__(self, data: pd.DataFrame) -> None:
        self._full_data = data
        self._current_idx: int = 0

    def set_position(self, idx: int) -> None:
        """Set the current time position. Data beyond this index is hidden."""
        self._current_idx = idx

    def get_visible_data(self) -> pd.DataFrame:
        """Return only data up to and including the current position."""
        return self._full_data.iloc[: self._current_idx + 1]

    def get_current_bar(self) -> pd.Series:
        """Return the current bar (row at current index)."""
        return self._full_data.iloc[self._current_idx]


class _BacktestDataHub:
    """Minimal data hub that provides historical data to strategies during backtest.

    Implements just enough of the MarketDataHub interface for strategies to
    call get_history() and get_latest_bar() without look-ahead bias.
    """

    def __init__(self, guard: _LookAheadGuard, symbol: str) -> None:
        self._guard = guard
        self._symbol = symbol

    def get_latest_bar(self, symbol: str, timeframe: Timeframe) -> Bar | None:
        """Return the current bar without look-ahead."""
        try:
            row = self._guard.get_current_bar()
            return Bar(
                symbol=symbol,
                timeframe=timeframe,
                open=float(row["open"]),
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
                volume=float(row["volume"]),
                timestamp=row.name if isinstance(row.name, datetime) else datetime.now(timezone.utc),
            )
        except (IndexError, KeyError):
            return None

    def get_history(self, symbol: str, timeframe: Timeframe, periods: int) -> pd.DataFrame:
        """Return historical data up to current time only (no look-ahead)."""
        visible = self._guard.get_visible_data()
        return visible.tail(periods)

    async def subscribe(self, symbol: str, asset_class: str = "equity") -> None:
        """No-op for backtest."""

    async def get_latest_bar_async(self, symbol: str, timeframe: Timeframe) -> Bar | None:
        """Async version of get_latest_bar."""
        return self.get_latest_bar(symbol, timeframe)

    async def get_history_async(
        self, symbol: str, timeframe: Timeframe, periods: int
    ) -> pd.DataFrame:
        """Async version of get_history."""
        return self.get_history(symbol, timeframe, periods)


class BacktestEngine:
    """Engine for running strategy backtests against historical data.

    Supports single-strategy and multi-strategy portfolio backtests with
    realistic execution simulation and look-ahead bias prevention.

    Args:
        config: BacktestConfig with simulation parameters.
    """

    def __init__(self, config: BacktestConfig | None = None) -> None:
        if config is None:
            config = BacktestConfig()
        self._config = config
        self._simulator = SimulatedExecution(
            slippage_bps=config.slippage_bps,
            commission_per_share=config.commission_per_share,
            market_impact_bps=config.market_impact_bps,
        )

    @property
    def config(self) -> BacktestConfig:
        """Backtest configuration."""
        return self._config

    @property
    def simulator(self) -> SimulatedExecution:
        """Execution simulator."""
        return self._simulator

    async def run(
        self,
        strategy: BaseStrategy,
        data: pd.DataFrame,
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> BacktestResult:
        """Run a single strategy backtest against historical OHLCV data.

        The strategy's evaluate() method is called for each bar. Look-ahead
        bias is prevented by only providing data up to the current bar.

        Args:
            strategy: Strategy instance to backtest.
            data: DataFrame with OHLCV data indexed by datetime.
            start_date: Optional start date filter.
            end_date: Optional end date filter.

        Returns:
            BacktestResult with performance metrics and trade history.
        """
        # Filter data by date range
        filtered_data = self._filter_data(data, start_date, end_date)
        if filtered_data.empty:
            return self._empty_result(strategy.name, start_date, end_date)

        # Set up look-ahead guard
        guard = _LookAheadGuard(filtered_data)
        symbol = self._get_symbol(filtered_data, strategy)
        backtest_hub = _BacktestDataHub(guard, symbol)

        # Replace strategy's data hub with backtest hub
        original_hub = strategy._data_hub
        strategy._data_hub = backtest_hub  # type: ignore[assignment]

        try:
            trades, equity_curve = await self._simulate_strategy(
                strategy, filtered_data, guard, symbol
            )
        finally:
            # Restore original data hub
            strategy._data_hub = original_hub

        # Calculate metrics
        actual_start = filtered_data.index[0].date() if hasattr(filtered_data.index[0], 'date') else start_date or date.today()
        actual_end = filtered_data.index[-1].date() if hasattr(filtered_data.index[-1], 'date') else end_date or date.today()

        result = self._calculate_metrics(
            strategy_name=strategy.name,
            trades=trades,
            equity_curve=equity_curve,
            start_date=actual_start,
            end_date=actual_end,
            parameters=strategy.config.parameters if strategy.config else {},
        )

        logger.info(
            "backtest_complete",
            strategy=strategy.name,
            total_return=str(result.total_return),
            sharpe=result.sharpe_ratio,
            max_drawdown=str(result.max_drawdown),
            total_trades=result.total_trades,
        )

        return result

    async def run_portfolio(
        self,
        strategies: list[BaseStrategy],
        allocations: dict[str, Decimal],
        data: dict[str, pd.DataFrame],
        start_date: date | None = None,
        end_date: date | None = None,
        risk_config: Any | None = None,
    ) -> PortfolioBacktestResult:
        """Run a multi-strategy portfolio backtest with capital allocation and risk rules.

        Each strategy is run independently with its allocated capital. Risk
        management rules are applied across the portfolio.

        Args:
            strategies: List of strategy instances.
            allocations: Map of strategy name to allocated capital.
            data: Map of symbol to OHLCV DataFrame.
            start_date: Optional start date filter.
            end_date: Optional end date filter.
            risk_config: Optional RiskConfig for risk management.

        Returns:
            PortfolioBacktestResult with combined and per-strategy metrics.
        """
        total_capital = sum(allocations.values())
        capital_allocator = CapitalAllocator(total_capital)

        # Allocate capital to each strategy
        for strategy in strategies:
            if strategy.name in allocations:
                capital_allocator.allocate(
                    strategy.name,
                    allocations[strategy.name],
                    AllocationMode.FIXED_AMOUNT,
                )

        # Run each strategy independently
        strategy_results: dict[str, BacktestResult] = {}
        all_equity_points: list[pd.Series] = []

        for strategy in strategies:
            # Get data for this strategy's symbols
            strategy_data = self._get_strategy_data(strategy, data)
            if strategy_data.empty:
                continue

            result = await self.run(strategy, strategy_data, start_date, end_date)
            strategy_results[strategy.name] = result

            if not result.equity_curve.empty:
                all_equity_points.append(result.equity_curve)

        # Combine equity curves
        if all_equity_points:
            combined_equity = pd.concat(all_equity_points, axis=1).sum(axis=1)
        else:
            combined_equity = pd.Series(dtype=float)

        # Calculate portfolio-level metrics
        actual_start = start_date or date.today()
        actual_end = end_date or date.today()

        if not combined_equity.empty:
            portfolio_return = Decimal(str(
                (combined_equity.iloc[-1] - combined_equity.iloc[0]) / combined_equity.iloc[0]
            )) if combined_equity.iloc[0] != 0 else Decimal("0")
        else:
            portfolio_return = Decimal("0")

        days = (actual_end - actual_start).days or 1
        ann_return = self._annualize_return(portfolio_return, days)
        sharpe = self._calculate_sharpe(combined_equity)
        sortino = self._calculate_sortino(combined_equity)
        max_dd = self._calculate_max_drawdown(combined_equity)

        return PortfolioBacktestResult(
            start_date=actual_start,
            end_date=actual_end,
            total_return=portfolio_return,
            annualized_return=ann_return,
            sharpe_ratio=sharpe,
            sortino_ratio=sortino,
            max_drawdown=max_dd,
            strategy_results=strategy_results,
            equity_curve=combined_equity,
        )

    async def load_data(
        self,
        symbol: str,
        source: str | None = None,
        filepath: Path | str | None = None,
    ) -> pd.DataFrame:
        """Load historical data from IBKR API or local CSV.

        Args:
            symbol: Ticker symbol.
            source: Data source ("ibkr" or "csv"). Defaults to config.
            filepath: Explicit file path for CSV loading.

        Returns:
            DataFrame with OHLCV data indexed by datetime.
        """
        source = source or self._config.data_source

        if source == "csv":
            if filepath:
                return load_historical_csv(filepath, symbol=symbol)
            # Look in configured CSV directory
            csv_dir = Path(self._config.csv_directory)
            csv_path = csv_dir / f"{symbol}.csv"
            return load_historical_csv(csv_path, symbol=symbol)
        else:
            # IBKR source - delegate to historical module
            from src.data.historical import load_historical_ibkr
            return await load_historical_ibkr(symbol)

    def store_result(self, result: BacktestResult, filepath: Path | None = None) -> Path:
        """Store backtest results to a JSON file for comparison.

        Args:
            result: BacktestResult to store.
            filepath: Optional explicit path. Defaults to results_directory.

        Returns:
            Path where the result was stored.
        """
        if filepath is None:
            results_dir = Path(self._config.results_directory)
            results_dir.mkdir(parents=True, exist_ok=True)
            timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            filepath = results_dir / f"{result.strategy_name}_{timestamp}.json"

        result_dict = {
            "strategy_name": result.strategy_name,
            "start_date": str(result.start_date),
            "end_date": str(result.end_date),
            "total_return": str(result.total_return),
            "annualized_return": str(result.annualized_return),
            "sharpe_ratio": result.sharpe_ratio,
            "sortino_ratio": result.sortino_ratio,
            "max_drawdown": str(result.max_drawdown),
            "win_rate": result.win_rate,
            "profit_factor": result.profit_factor,
            "avg_trade_duration_seconds": result.avg_trade_duration.total_seconds(),
            "total_trades": result.total_trades,
            "parameters": result.parameters,
            "trades": [
                {
                    "symbol": t.symbol,
                    "direction": t.direction.value,
                    "entry_price": str(t.entry_price),
                    "exit_price": str(t.exit_price) if t.exit_price else None,
                    "quantity": str(t.quantity),
                    "entry_time": t.entry_time.isoformat(),
                    "exit_time": t.exit_time.isoformat() if t.exit_time else None,
                    "pnl": str(t.pnl),
                    "commission": str(t.commission),
                    "strategy_name": t.strategy_name,
                }
                for t in result.trades
            ],
        }

        filepath.parent.mkdir(parents=True, exist_ok=True)
        with open(filepath, "w") as f:
            json.dump(result_dict, f, indent=2)

        logger.info("backtest_result_stored", filepath=str(filepath))
        return filepath

    # -------------------------------------------------------------------------
    # Private helpers
    # -------------------------------------------------------------------------

    async def _simulate_strategy(
        self,
        strategy: BaseStrategy,
        data: pd.DataFrame,
        guard: _LookAheadGuard,
        symbol: str,
    ) -> tuple[list[BacktestTrade], pd.Series]:
        """Run strategy evaluation bar-by-bar with look-ahead prevention."""
        trades: list[BacktestTrade] = []
        open_positions: dict[str, dict] = {}  # symbol -> position info
        initial_capital = Decimal("100000")  # Default backtest capital
        cash = initial_capital
        equity_points: list[tuple[datetime, float]] = []

        for idx in range(len(data)):
            guard.set_position(idx)
            row = data.iloc[idx]
            current_time = row.name if isinstance(row.name, datetime) else datetime.now(timezone.utc)
            current_price = Decimal(str(row["close"]))

            # Evaluate strategy
            try:
                signals = await strategy.evaluate()
            except Exception as e:
                logger.debug("strategy_evaluate_error", error=str(e), idx=idx)
                signals = []

            # Process signals
            for signal in signals:
                if signal.direction == SignalDirection.CLOSE:
                    # Close existing position
                    pos_key = signal.symbol
                    if pos_key in open_positions:
                        pos = open_positions[pos_key]
                        fill = self._simulator.simulate_fill(
                            price=current_price,
                            quantity=pos["quantity"],
                            direction=SignalDirection.CLOSE,
                        )
                        # Calculate P&L
                        if pos["direction"] == SignalDirection.LONG:
                            pnl = (fill.fill_price - pos["entry_price"]) * pos["quantity"]
                        else:
                            pnl = (pos["entry_price"] - fill.fill_price) * pos["quantity"]
                        pnl -= fill.total_cost

                        cash += pos["entry_price"] * pos["quantity"] + pnl
                        trade = BacktestTrade(
                            symbol=signal.symbol,
                            direction=pos["direction"],
                            entry_price=pos["entry_price"],
                            exit_price=fill.fill_price,
                            quantity=pos["quantity"],
                            entry_time=pos["entry_time"],
                            exit_time=current_time,
                            pnl=pnl,
                            commission=fill.commission + pos["entry_commission"],
                            strategy_name=strategy.name,
                        )
                        trades.append(trade)
                        del open_positions[pos_key]

                elif signal.direction in (SignalDirection.LONG, SignalDirection.SHORT):
                    pos_key = signal.symbol
                    if pos_key not in open_positions:
                        # Calculate quantity from suggested size
                        if current_price > 0:
                            quantity = signal.suggested_size / current_price
                        else:
                            continue

                        fill = self._simulator.simulate_fill(
                            price=current_price,
                            quantity=quantity,
                            direction=signal.direction,
                            order_type=signal.order_type,
                            limit_price=signal.limit_price,
                        )

                        # Check if we have enough cash
                        required = fill.fill_price * quantity
                        if required > cash:
                            continue

                        cash -= required
                        open_positions[pos_key] = {
                            "direction": signal.direction,
                            "entry_price": fill.fill_price,
                            "quantity": quantity,
                            "entry_time": current_time,
                            "entry_commission": fill.commission,
                        }

            # Calculate equity (cash + market value of open positions)
            equity = float(cash)
            for pos_key, pos in open_positions.items():
                if pos["direction"] == SignalDirection.LONG:
                    equity += float(current_price * pos["quantity"])
                else:
                    # Short: profit when price goes down
                    equity += float(
                        pos["entry_price"] * pos["quantity"]
                        + (pos["entry_price"] - current_price) * pos["quantity"]
                    )

            equity_points.append((current_time, equity))

        # Build equity curve
        if equity_points:
            times, values = zip(*equity_points)
            equity_curve = pd.Series(values, index=pd.DatetimeIndex(times))
        else:
            equity_curve = pd.Series(dtype=float)

        return trades, equity_curve

    def _filter_data(
        self, data: pd.DataFrame, start_date: date | None, end_date: date | None
    ) -> pd.DataFrame:
        """Filter DataFrame by date range."""
        if data.empty:
            return data

        filtered = data.copy()
        if start_date:
            start_dt = pd.Timestamp(start_date)
            filtered = filtered[filtered.index >= start_dt]
        if end_date:
            end_dt = pd.Timestamp(end_date) + pd.Timedelta(days=1) - pd.Timedelta(seconds=1)
            filtered = filtered[filtered.index <= end_dt]

        return filtered

    def _get_symbol(self, data: pd.DataFrame, strategy: BaseStrategy) -> str:
        """Extract symbol from data or strategy config."""
        if "symbol" in data.columns:
            return str(data["symbol"].iloc[0])
        if strategy.config and strategy.config.symbols:
            return strategy.config.symbols[0]
        return "UNKNOWN"

    def _get_strategy_data(
        self, strategy: BaseStrategy, data: dict[str, pd.DataFrame]
    ) -> pd.DataFrame:
        """Get combined data for a strategy's symbols."""
        if not strategy.config or not strategy.config.symbols:
            # Return first available data
            if data:
                return next(iter(data.values()))
            return pd.DataFrame()

        # Get data for the first symbol the strategy trades
        for symbol in strategy.config.symbols:
            if symbol in data:
                return data[symbol]

        return pd.DataFrame()

    def _calculate_metrics(
        self,
        strategy_name: str,
        trades: list[BacktestTrade],
        equity_curve: pd.Series,
        start_date: date,
        end_date: date,
        parameters: dict[str, Any],
    ) -> BacktestResult:
        """Calculate performance metrics from trades and equity curve."""
        closed_trades = [t for t in trades if t.exit_price is not None]
        total_trades = len(closed_trades)

        # Total return
        if not equity_curve.empty and equity_curve.iloc[0] != 0:
            total_return = Decimal(str(
                (equity_curve.iloc[-1] - equity_curve.iloc[0]) / equity_curve.iloc[0]
            ))
        else:
            total_return = Decimal("0")

        # Annualized return
        days = (end_date - start_date).days or 1
        ann_return = self._annualize_return(total_return, days)

        # Sharpe ratio
        sharpe = self._calculate_sharpe(equity_curve)

        # Sortino ratio
        sortino = self._calculate_sortino(equity_curve)

        # Max drawdown
        max_dd = self._calculate_max_drawdown(equity_curve)

        # Win rate
        if total_trades > 0:
            winning_trades = [t for t in closed_trades if t.pnl > 0]
            win_rate = len(winning_trades) / total_trades
        else:
            win_rate = 0.0

        # Profit factor
        gross_profit = sum(float(t.pnl) for t in closed_trades if t.pnl > 0)
        gross_loss = abs(sum(float(t.pnl) for t in closed_trades if t.pnl < 0))
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf") if gross_profit > 0 else 0.0

        # Average trade duration
        durations = []
        for t in closed_trades:
            if t.exit_time and t.entry_time:
                durations.append(t.exit_time - t.entry_time)
        avg_duration = (
            sum(durations, timedelta()) / len(durations)
            if durations
            else timedelta()
        )

        return BacktestResult(
            strategy_name=strategy_name,
            start_date=start_date,
            end_date=end_date,
            total_return=total_return,
            annualized_return=ann_return,
            sharpe_ratio=sharpe,
            sortino_ratio=sortino,
            max_drawdown=max_dd,
            win_rate=win_rate,
            profit_factor=profit_factor,
            avg_trade_duration=avg_duration,
            total_trades=total_trades,
            trades=trades,
            equity_curve=equity_curve,
            parameters=parameters,
        )

    def _annualize_return(self, total_return: Decimal, days: int) -> Decimal:
        """Annualize a total return over a given number of days."""
        if days <= 0:
            return Decimal("0")
        years = Decimal(str(days)) / Decimal("365.25")
        if years <= 0:
            return Decimal("0")
        # (1 + total_return)^(1/years) - 1
        try:
            base = float(Decimal("1") + total_return)
            if base <= 0:
                return Decimal("-1")
            ann = base ** (1.0 / float(years)) - 1.0
            return Decimal(str(round(ann, 6)))
        except (OverflowError, ValueError):
            return Decimal("0")

    def _calculate_sharpe(self, equity_curve: pd.Series, risk_free: float = 0.0) -> float:
        """Calculate annualized Sharpe ratio from equity curve."""
        if equity_curve.empty or len(equity_curve) < 2:
            return 0.0

        returns = equity_curve.pct_change().dropna()
        if returns.empty or returns.std() == 0:
            return 0.0

        excess_returns = returns - risk_free / 252
        sharpe = float(excess_returns.mean() / returns.std()) * np.sqrt(252)
        return round(sharpe, 4)

    def _calculate_sortino(self, equity_curve: pd.Series, risk_free: float = 0.0) -> float:
        """Calculate annualized Sortino ratio from equity curve."""
        if equity_curve.empty or len(equity_curve) < 2:
            return 0.0

        returns = equity_curve.pct_change().dropna()
        if returns.empty:
            return 0.0

        excess_returns = returns - risk_free / 252
        downside_returns = returns[returns < 0]

        if downside_returns.empty or downside_returns.std() == 0:
            return float("inf") if excess_returns.mean() > 0 else 0.0

        sortino = float(excess_returns.mean() / downside_returns.std()) * np.sqrt(252)
        return round(sortino, 4)

    def _calculate_max_drawdown(self, equity_curve: pd.Series) -> Decimal:
        """Calculate maximum drawdown from equity curve."""
        if equity_curve.empty or len(equity_curve) < 2:
            return Decimal("0")

        peak = equity_curve.expanding(min_periods=1).max()
        drawdown = (equity_curve - peak) / peak
        max_dd = drawdown.min()

        return Decimal(str(round(abs(float(max_dd)), 6)))

    def _empty_result(
        self, strategy_name: str, start_date: date | None, end_date: date | None
    ) -> BacktestResult:
        """Return an empty result when no data is available."""
        return BacktestResult(
            strategy_name=strategy_name,
            start_date=start_date or date.today(),
            end_date=end_date or date.today(),
            total_return=Decimal("0"),
            annualized_return=Decimal("0"),
            sharpe_ratio=0.0,
            sortino_ratio=0.0,
            max_drawdown=Decimal("0"),
            win_rate=0.0,
            profit_factor=0.0,
            avg_trade_duration=timedelta(),
            total_trades=0,
        )
