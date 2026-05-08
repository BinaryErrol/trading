"""Strategy engine module for the IBKR trading bot.

Provides the strategy orchestration engine, base strategy class, and signal models.
"""

from src.strategies.base import BaseStrategy, StrategyState
from src.strategies.engine import StrategyEngine
from src.strategies.signals import OrderType, Signal, SignalDirection

__all__ = [
    "BaseStrategy",
    "OrderType",
    "Signal",
    "SignalDirection",
    "StrategyEngine",
    "StrategyState",
]
