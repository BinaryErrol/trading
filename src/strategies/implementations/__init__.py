"""Strategy implementations for the IBKR trading bot.

Includes trend-based, mean-reversion, statistical, market making, and options strategy families.
"""

from src.strategies.implementations.bollinger import BollingerStrategy
from src.strategies.implementations.breakout import BreakoutStrategy
from src.strategies.implementations.ma_crossover import MACrossoverStrategy
from src.strategies.implementations.market_making import MarketMakingStrategy
from src.strategies.implementations.mean_reversion import MeanReversionStrategy
from src.strategies.implementations.momentum import MomentumStrategy
from src.strategies.implementations.pairs_trading import PairsTradingStrategy
from src.strategies.implementations.rsi_divergence import RSIDivergenceStrategy
from src.strategies.implementations.trend_following import TrendFollowingStrategy
from src.strategies.implementations.vwap import VWAPStrategy
from src.strategies.wheel import WheelStrategy

__all__ = [
    "BollingerStrategy",
    "BreakoutStrategy",
    "MACrossoverStrategy",
    "MarketMakingStrategy",
    "MeanReversionStrategy",
    "MomentumStrategy",
    "PairsTradingStrategy",
    "RSIDivergenceStrategy",
    "TrendFollowingStrategy",
    "VWAPStrategy",
    "WheelStrategy",
]
