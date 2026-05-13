"""Walk-forward optimization for strategy parameter tuning.

Splits historical data into in-sample (training) and out-of-sample (testing)
windows, optimizes parameters on in-sample data, then validates on out-of-sample.
This prevents overfitting by ensuring strategies are always tested on unseen data.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

import pandas as pd
import structlog

from src.backtesting.engine import BacktestEngine, BacktestResult
from src.strategies.base import BaseStrategy

logger = structlog.get_logger(__name__)


@dataclass
class WalkForwardFold:
    """Results from a single walk-forward fold.

    Attributes:
        fold_number: Index of this fold (0-based).
        in_sample_start: Start date of in-sample period.
        in_sample_end: End date of in-sample period.
        out_of_sample_start: Start date of out-of-sample period.
        out_of_sample_end: End date of out-of-sample period.
        best_parameters: Parameters selected during in-sample optimization.
        in_sample_result: Backtest result on in-sample data with best params.
        out_of_sample_result: Backtest result on out-of-sample data with best params.
    """

    fold_number: int
    in_sample_start: date
    in_sample_end: date
    out_of_sample_start: date
    out_of_sample_end: date
    best_parameters: dict[str, Any]
    in_sample_result: BacktestResult
    out_of_sample_result: BacktestResult


@dataclass
class WalkForwardResult:
    """Complete results from walk-forward optimization.

    Attributes:
        strategy_name: Name of the strategy optimized.
        num_folds: Number of walk-forward folds.
        in_sample_pct: Fraction of data used for in-sample.
        folds: Results for each fold.
        avg_in_sample_sharpe: Average Sharpe across in-sample periods.
        avg_out_of_sample_sharpe: Average Sharpe across out-of-sample periods.
        avg_out_of_sample_return: Average return across out-of-sample periods.
        degradation_ratio: Out-of-sample Sharpe / In-sample Sharpe (< 1 = overfitting).
        combined_out_of_sample: Combined out-of-sample equity curve.
    """

    strategy_name: str
    num_folds: int
    in_sample_pct: float
    folds: list[WalkForwardFold] = field(default_factory=list)
    avg_in_sample_sharpe: float = 0.0
    avg_out_of_sample_sharpe: float = 0.0
    avg_out_of_sample_return: Decimal = Decimal("0")
    degradation_ratio: float = 0.0
    combined_out_of_sample: pd.Series = field(
        default_factory=lambda: pd.Series(dtype=float)
    )


class WalkForwardOptimizer:
    """Walk-forward optimization engine.

    Splits data into sequential in-sample/out-of-sample windows and runs
    parameter optimization on each in-sample window, then validates on
    the subsequent out-of-sample window.

    Args:
        engine: BacktestEngine instance for running backtests.
        parameter_grid: Dict mapping parameter names to lists of values to try.
        objective: Function to maximize during optimization (default: Sharpe ratio).
    """

    def __init__(
        self,
        engine: BacktestEngine | None = None,
        parameter_grid: dict[str, list[Any]] | None = None,
        objective: Callable[[BacktestResult], float] | None = None,
    ) -> None:
        self._engine = engine or BacktestEngine()
        self._parameter_grid = parameter_grid or {}
        self._objective = objective or (lambda r: r.sharpe_ratio)

    @property
    def engine(self) -> BacktestEngine:
        """The backtest engine used for running simulations."""
        return self._engine

    async def run(
        self,
        strategy: BaseStrategy,
        data: pd.DataFrame,
        in_sample_pct: float = 0.7,
        num_folds: int = 5,
    ) -> WalkForwardResult:
        """Run walk-forward optimization.

        Data is split into num_folds sequential windows. For each fold:
        1. Use in_sample_pct of the window for parameter optimization
        2. Test the best parameters on the remaining out-of-sample data

        Args:
            strategy: Strategy to optimize.
            data: Full historical OHLCV DataFrame.
            in_sample_pct: Fraction of each fold used for in-sample (0.5 to 0.9).
            num_folds: Number of walk-forward folds.

        Returns:
            WalkForwardResult with per-fold and aggregate metrics.
        """
        if not (0.1 <= in_sample_pct <= 0.95):
            raise ValueError(f"in_sample_pct must be between 0.1 and 0.95, got {in_sample_pct}")
        if num_folds < 1:
            raise ValueError(f"num_folds must be >= 1, got {num_folds}")

        # Split data into folds
        folds_data = self._split_data(data, in_sample_pct, num_folds)

        folds: list[WalkForwardFold] = []
        oos_equity_parts: list[pd.Series] = []

        for fold_idx, (is_data, oos_data) in enumerate(folds_data):
            logger.info(
                "walk_forward_fold_start",
                fold=fold_idx,
                is_rows=len(is_data),
                oos_rows=len(oos_data),
            )

            # Optimize on in-sample data
            best_params, is_result = await self._optimize_in_sample(
                strategy, is_data
            )

            # Apply best parameters and test on out-of-sample
            strategy.update_parameters(best_params)
            oos_result = await self._engine.run(strategy, oos_data)

            # Record fold results
            fold = WalkForwardFold(
                fold_number=fold_idx,
                in_sample_start=is_data.index[0].date() if len(is_data) > 0 else date.today(),
                in_sample_end=is_data.index[-1].date() if len(is_data) > 0 else date.today(),
                out_of_sample_start=oos_data.index[0].date() if len(oos_data) > 0 else date.today(),
                out_of_sample_end=oos_data.index[-1].date() if len(oos_data) > 0 else date.today(),
                best_parameters=best_params,
                in_sample_result=is_result,
                out_of_sample_result=oos_result,
            )
            folds.append(fold)

            if not oos_result.equity_curve.empty:
                oos_equity_parts.append(oos_result.equity_curve)

            logger.info(
                "walk_forward_fold_complete",
                fold=fold_idx,
                is_sharpe=is_result.sharpe_ratio,
                oos_sharpe=oos_result.sharpe_ratio,
                best_params=best_params,
            )

        # Calculate aggregate metrics
        result = self._aggregate_results(
            strategy_name=strategy.name,
            folds=folds,
            in_sample_pct=in_sample_pct,
            num_folds=num_folds,
            oos_equity_parts=oos_equity_parts,
        )

        logger.info(
            "walk_forward_complete",
            strategy=strategy.name,
            avg_is_sharpe=result.avg_in_sample_sharpe,
            avg_oos_sharpe=result.avg_out_of_sample_sharpe,
            degradation=result.degradation_ratio,
        )

        return result

    def _split_data(
        self,
        data: pd.DataFrame,
        in_sample_pct: float,
        num_folds: int,
    ) -> list[tuple[pd.DataFrame, pd.DataFrame]]:
        """Split data into in-sample/out-of-sample pairs for each fold.

        Uses an anchored walk-forward approach where each fold's window
        advances through the data sequentially.
        """
        n = len(data)
        fold_size = n // num_folds
        folds = []

        for i in range(num_folds):
            start_idx = i * fold_size
            end_idx = min(start_idx + fold_size, n)

            if end_idx - start_idx < 10:
                continue

            split_idx = start_idx + int((end_idx - start_idx) * in_sample_pct)

            is_data = data.iloc[start_idx:split_idx]
            oos_data = data.iloc[split_idx:end_idx]

            if len(is_data) >= 5 and len(oos_data) >= 2:
                folds.append((is_data, oos_data))

        return folds

    async def _optimize_in_sample(
        self,
        strategy: BaseStrategy,
        data: pd.DataFrame,
    ) -> tuple[dict[str, Any], BacktestResult]:
        """Find the best parameters on in-sample data.

        If no parameter grid is provided, uses the strategy's current parameters.
        Otherwise, runs a grid search over all parameter combinations.

        Returns:
            Tuple of (best_parameters, best_result).
        """
        if not self._parameter_grid:
            # No grid: just run with current parameters
            result = await self._engine.run(strategy, data)
            current_params = (
                dict(strategy.config.parameters) if strategy.config else {}
            )
            return current_params, result

        # Grid search
        best_score = float("-inf")
        best_params: dict[str, Any] = {}
        best_result: BacktestResult | None = None

        param_combinations = self._generate_combinations(self._parameter_grid)

        for params in param_combinations:
            strategy.update_parameters(params)
            result = await self._engine.run(strategy, data)
            score = self._objective(result)

            if score > best_score:
                best_score = score
                best_params = dict(params)
                best_result = result

        if best_result is None:
            # Fallback: run with current params
            result = await self._engine.run(strategy, data)
            current_params = (
                dict(strategy.config.parameters) if strategy.config else {}
            )
            return current_params, result

        return best_params, best_result

    def _generate_combinations(
        self, grid: dict[str, list[Any]]
    ) -> list[dict[str, Any]]:
        """Generate all parameter combinations from a grid."""
        if not grid:
            return [{}]

        keys = list(grid.keys())
        values = list(grid.values())

        combinations: list[dict[str, Any]] = []
        self._cartesian_product(keys, values, 0, {}, combinations)
        return combinations

    def _cartesian_product(
        self,
        keys: list[str],
        values: list[list[Any]],
        idx: int,
        current: dict[str, Any],
        results: list[dict[str, Any]],
    ) -> None:
        """Recursively generate cartesian product of parameter values."""
        if idx == len(keys):
            results.append(dict(current))
            return

        for val in values[idx]:
            current[keys[idx]] = val
            self._cartesian_product(keys, values, idx + 1, current, results)

    def _aggregate_results(
        self,
        strategy_name: str,
        folds: list[WalkForwardFold],
        in_sample_pct: float,
        num_folds: int,
        oos_equity_parts: list[pd.Series],
    ) -> WalkForwardResult:
        """Aggregate per-fold results into a summary."""
        if not folds:
            return WalkForwardResult(
                strategy_name=strategy_name,
                num_folds=num_folds,
                in_sample_pct=in_sample_pct,
            )

        is_sharpes = [f.in_sample_result.sharpe_ratio for f in folds]
        oos_sharpes = [f.out_of_sample_result.sharpe_ratio for f in folds]
        oos_returns = [float(f.out_of_sample_result.total_return) for f in folds]

        avg_is_sharpe = sum(is_sharpes) / len(is_sharpes) if is_sharpes else 0.0
        avg_oos_sharpe = sum(oos_sharpes) / len(oos_sharpes) if oos_sharpes else 0.0
        avg_oos_return = sum(oos_returns) / len(oos_returns) if oos_returns else 0.0

        degradation = (
            avg_oos_sharpe / avg_is_sharpe if avg_is_sharpe != 0 else 0.0
        )

        # Combine out-of-sample equity curves
        if oos_equity_parts:
            combined_oos = pd.concat(oos_equity_parts)
        else:
            combined_oos = pd.Series(dtype=float)

        return WalkForwardResult(
            strategy_name=strategy_name,
            num_folds=num_folds,
            in_sample_pct=in_sample_pct,
            folds=folds,
            avg_in_sample_sharpe=round(avg_is_sharpe, 4),
            avg_out_of_sample_sharpe=round(avg_oos_sharpe, 4),
            avg_out_of_sample_return=Decimal(str(round(avg_oos_return, 6))),
            degradation_ratio=round(degradation, 4),
            combined_out_of_sample=combined_oos,
        )
