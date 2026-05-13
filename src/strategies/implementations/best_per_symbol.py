"""Best-per-symbol strategy implementation.

A static meta-strategy that routes each symbol to its historically
best-performing strategy based on backtest results. No learning or
adaptation — just uses proven assignments.

This is the "I know what works" strategy: hardcoded symbol-to-strategy
mapping derived from extensive backtesting.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import structlog

from src.data.bar_builder import Timeframe
from src.strategies.base import BaseStrategy
from src.strategies.signals import Signal

if TYPE_CHECKING:
    from src.config.settings import StrategyConfig
    from src.data.market_data_hub import MarketDataHub

logger = structlog.get_logger(__name__)

# Default symbol → best strategy mapping based on backtest results
DEFAULT_SYMBOL_STRATEGY_MAP: dict[str, str] = {
    "GOOGL": "momentum",
    "AMD": "trend_following",
    "DIS": "momentum",
    "META": "mean_reversion",
    "SPY": "mean_reversion",
    "JPM": "mean_reversion",
    "TSLA": "mean_reversion",
    "QQQ": "ma_crossover",
    "NFLX": "ma_crossover",
    "AAPL": "rsi_divergence",
    "MA": "rsi_divergence",
    "NVDA": "rsi_divergence",
    "MSFT": "momentum",
    "V": "vwap",
    "AMZN": "trend_following",
}


class BestPerSymbolStrategy(BaseStrategy):
    """Static meta-strategy that routes each symbol to its best strategy.

    Uses a hardcoded (or config-overridable) mapping of symbol → strategy
    based on historical backtest performance. Only instantiates the child
    strategies that are actually needed for the configured symbols.

    Parameters (from config.parameters):
        symbol_map: dict — override the default symbol-to-strategy mapping.
    """

    def __init__(self, config: StrategyConfig, data_hub: MarketDataHub) -> None:
        super().__init__(config, data_hub)

        # Build symbol map from config override or defaults
        config_map = config.parameters.get("symbol_map", {})
        if config_map and isinstance(config_map, dict):
            self._symbol_map: dict[str, str] = {**DEFAULT_SYMBOL_STRATEGY_MAP, **config_map}
        else:
            self._symbol_map = dict(DEFAULT_SYMBOL_STRATEGY_MAP)

        # Determine which child strategies we actually need
        needed_strategies: set[str] = set()
        for symbol in config.symbols:
            strategy_name = self._symbol_map.get(symbol)
            if strategy_name:
                needed_strategies.add(strategy_name)

        # Child strategy instances (only instantiate what's needed)
        self._child_strategies: dict[str, BaseStrategy] = {}
        self._instantiate_children(config, data_hub, needed_strategies)

        logger.info(
            "best_per_symbol_initialized",
            symbols=config.symbols,
            mappings={s: self._symbol_map.get(s, "none") for s in config.symbols},
            children=list(self._child_strategies.keys()),
        )

    def _instantiate_children(
        self,
        config: StrategyConfig,
        data_hub: MarketDataHub,
        needed: set[str],
    ) -> None:
        """Instantiate only the child strategies needed for configured symbols."""
        from src.config.settings import StrategyConfig as SC
        from src.strategies.implementations.bollinger import BollingerStrategy
        from src.strategies.implementations.breakout import BreakoutStrategy
        from src.strategies.implementations.ma_crossover import MACrossoverStrategy
        from src.strategies.implementations.mean_reversion import MeanReversionStrategy
        from src.strategies.implementations.momentum import MomentumStrategy
        from src.strategies.implementations.rsi_divergence import RSIDivergenceStrategy
        from src.strategies.implementations.trend_following import TrendFollowingStrategy
        from src.strategies.implementations.vwap import VWAPStrategy

        all_child_classes: dict[str, type[BaseStrategy]] = {
            "momentum": MomentumStrategy,
            "ma_crossover": MACrossoverStrategy,
            "mean_reversion": MeanReversionStrategy,
            "bollinger": BollingerStrategy,
            "rsi_divergence": RSIDivergenceStrategy,
            "trend_following": TrendFollowingStrategy,
            "breakout": BreakoutStrategy,
            "vwap": VWAPStrategy,
        }

        for name in needed:
            cls = all_child_classes.get(name)
            if cls is None:
                logger.warning("best_per_symbol_unknown_strategy", strategy=name)
                continue

            try:
                child_config = SC(
                    enabled=True,
                    frequency=config.frequency,
                    symbols=config.symbols,
                    asset_classes=config.asset_classes,
                    parameters={},
                )
                self._child_strategies[name] = cls(config=child_config, data_hub=data_hub)
                logger.debug("best_per_symbol_child_instantiated", child=name)
            except Exception as exc:
                logger.warning(
                    "best_per_symbol_child_instantiation_failed",
                    child=name,
                    error=str(exc),
                )

    @property
    def symbol_map(self) -> dict[str, str]:
        """Current symbol-to-strategy mapping."""
        return dict(self._symbol_map)

    def update_parameters(self, parameters: dict) -> None:
        """Update symbol map at runtime for hot-reload support."""
        super().update_parameters(parameters)
        config_map = parameters.get("symbol_map", {})
        if config_map and isinstance(config_map, dict):
            self._symbol_map = {**DEFAULT_SYMBOL_STRATEGY_MAP, **config_map}

    def required_indicators(self) -> list[str]:
        """Aggregate required indicators from instantiated child strategies."""
        indicators: set[str] = set()
        for child in self._child_strategies.values():
            indicators.update(child.required_indicators())
        return sorted(indicators)

    async def evaluate(self) -> list[Signal]:
        """Route each symbol to its designated strategy and collect signals.

        For each configured symbol:
        1. Look up the assigned strategy from the symbol map.
        2. Run that strategy's evaluate().
        3. Forward signals for the matching symbol.

        Returns:
            List of signals from each symbol's designated strategy.
        """
        # Sync data_hub to children (backtest engine may have replaced it)
        for child in self._child_strategies.values():
            child._data_hub = self._data_hub

        all_signals: list[Signal] = []

        for symbol in self._config.symbols:
            strategy_name = self._symbol_map.get(symbol)
            if strategy_name is None:
                logger.debug(
                    "best_per_symbol_no_mapping",
                    symbol=symbol,
                    msg="No strategy assigned, skipping",
                )
                continue

            child = self._child_strategies.get(strategy_name)
            if child is None:
                logger.debug(
                    "best_per_symbol_child_not_available",
                    symbol=symbol,
                    strategy=strategy_name,
                )
                continue

            try:
                signals = await child.evaluate()
                for sig in signals:
                    if sig.symbol == symbol:
                        # Re-tag with best_per_symbol strategy name
                        all_signals.append(
                            Signal(
                                strategy_name=self.name,
                                symbol=sig.symbol,
                                direction=sig.direction,
                                confidence=sig.confidence,
                                suggested_size=sig.suggested_size,
                                order_type=sig.order_type,
                                limit_price=sig.limit_price,
                                stop_price=sig.stop_price,
                                metadata={
                                    **sig.metadata,
                                    "assigned_strategy": strategy_name,
                                },
                                option_params=sig.option_params,
                            )
                        )
            except Exception as exc:
                logger.warning(
                    "best_per_symbol_child_evaluate_failed",
                    child=strategy_name,
                    symbol=symbol,
                    error=str(exc),
                )

        return all_signals

    def _resolve_timeframe(self) -> Timeframe:
        """Map the config frequency string to a Timeframe enum."""
        freq_map = {
            "tick": Timeframe.TICK,
            "1min": Timeframe.ONE_MIN,
            "5min": Timeframe.FIVE_MIN,
            "15min": Timeframe.FIFTEEN_MIN,
            "1hour": Timeframe.ONE_HOUR,
            "daily": Timeframe.DAILY,
            "weekly": Timeframe.WEEKLY,
        }
        return freq_map.get(self._config.frequency, Timeframe.FIVE_MIN)
