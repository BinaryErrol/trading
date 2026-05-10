"""Strategy implementations for the IBKR trading bot.

Includes trend-based, mean-reversion, statistical, market making, adaptive meta, and options strategy families.
"""

from src.strategies.implementations.adaptive import AdaptiveStrategy
from src.strategies.implementations.bandit import BanditStrategy
from src.strategies.implementations.best_per_symbol import BestPerSymbolStrategy
from src.strategies.implementations.bollinger import BollingerStrategy
from src.strategies.implementations.breakout import BreakoutStrategy
from src.strategies.implementations.ma_crossover import MACrossoverStrategy
from src.strategies.implementations.market_making import MarketMakingStrategy
from src.strategies.implementations.mean_reversion import MeanReversionStrategy
from src.strategies.implementations.momentum import MomentumStrategy
from src.strategies.implementations.pairs_trading import PairsTradingStrategy
from src.strategies.implementations.regime_hmm import RegimeHMMStrategy
from src.strategies.implementations.rsi_divergence import RSIDivergenceStrategy
from src.strategies.implementations.trend_following import TrendFollowingStrategy
from src.strategies.implementations.vwap import VWAPStrategy
from src.strategies.wheel import WheelStrategy

__all__ = [
    "AdaptiveStrategy",
    "BanditStrategy",
    "BestPerSymbolStrategy",
    "BollingerStrategy",
    "BreakoutStrategy",
    "MACrossoverStrategy",
    "MarketMakingStrategy",
    "MeanReversionStrategy",
    "MomentumStrategy",
    "PairsTradingStrategy",
    "RegimeHMMStrategy",
    "RSIDivergenceStrategy",
    "TrendFollowingStrategy",
    "VWAPStrategy",
    "WheelStrategy",
]
