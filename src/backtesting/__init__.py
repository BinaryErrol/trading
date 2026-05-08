"""Backtesting engine for strategy simulation and walk-forward optimization.

Provides realistic execution simulation with slippage, commissions, and market impact.
Supports single-strategy and multi-strategy portfolio backtests with look-ahead bias prevention.
"""

from src.backtesting.engine import BacktestEngine, BacktestResult, BacktestTrade
from src.backtesting.simulator import SimulatedExecution, SimulatedFill
from src.backtesting.walk_forward import WalkForwardOptimizer, WalkForwardResult

__all__ = [
    "BacktestEngine",
    "BacktestResult",
    "BacktestTrade",
    "SimulatedExecution",
    "SimulatedFill",
    "WalkForwardOptimizer",
    "WalkForwardResult",
]
