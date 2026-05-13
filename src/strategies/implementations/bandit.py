"""Multi-Armed Bandit strategy using EXP3 algorithm.

Implements the EXP3 (Exponential-weight algorithm for Exploration and
Exploitation) for dynamic strategy selection. Each "arm" is a child
strategy, and the algorithm balances exploration of new strategies with
exploitation of known good ones.

Key difference from AdaptiveStrategy: uses probability-weighted selection
with a switching cost penalty to prevent flip-flopping between strategies.
"""

from __future__ import annotations

import random
from collections import defaultdict
from typing import TYPE_CHECKING

import numpy as np
import structlog

from src.data.bar_builder import Timeframe
from src.strategies.base import BaseStrategy
from src.strategies.signals import Signal, SignalDirection

if TYPE_CHECKING:
    from src.config.settings import StrategyConfig
    from src.data.market_data_hub import MarketDataHub

logger = structlog.get_logger(__name__)


class BanditStrategy(BaseStrategy):
    """Multi-Armed Bandit meta-strategy using EXP3 algorithm.

    Selects among child strategies using probability-weighted sampling
    with exploration, and penalizes switching to prevent flip-flopping.

    Parameters (from config.parameters):
        gamma: float = 0.02 — exploration rate (higher = more exploration).
        switching_cost: float = 0.02 — penalty for changing strategy.
        min_rounds: int = 30 — minimum bars before switching is allowed.
    """

    # Same 8 child strategies as adaptive
    CHILD_STRATEGY_NAMES: list[str] = [
        "momentum",
        "ma_crossover",
        "mean_reversion",
        "bollinger",
        "rsi_divergence",
        "trend_following",
        "breakout",
        "vwap",
    ]

    def __init__(self, config: StrategyConfig, data_hub: MarketDataHub) -> None:
        super().__init__(config, data_hub)

        # Parameters
        self._gamma: float = float(config.parameters.get("gamma", 0.02))
        self._switching_cost: float = float(config.parameters.get("switching_cost", 0.02))
        self._min_rounds: int = int(config.parameters.get("min_rounds", 30))

        # EXP3 weights per symbol: symbol -> {strategy_name: weight}
        self._weights: dict[str, dict[str, float]] = defaultdict(
            lambda: {name: 1.0 for name in self.CHILD_STRATEGY_NAMES}
        )

        # Currently selected strategy per symbol
        self._current_strategy: dict[str, str] = {}

        # Bar counter per symbol (for min_rounds enforcement)
        self._bars_since_switch: dict[str, int] = defaultdict(int)

        # Previous signal price per symbol for reward calculation
        self._last_signal_price: dict[str, float] = {}
        self._last_signal_direction: dict[str, SignalDirection | None] = defaultdict(lambda: None)

        # Overall bar counter
        self._bar_count: int = 0

        # Child strategy instances
        self._child_strategies: dict[str, BaseStrategy] = {}
        self._instantiate_children(config, data_hub)

    def _instantiate_children(self, config: StrategyConfig, data_hub: MarketDataHub) -> None:
        """Instantiate all child strategy classes."""
        from src.config.settings import StrategyConfig as SC
        from src.strategies.implementations.bollinger import BollingerStrategy
        from src.strategies.implementations.breakout import BreakoutStrategy
        from src.strategies.implementations.ma_crossover import MACrossoverStrategy
        from src.strategies.implementations.mean_reversion import MeanReversionStrategy
        from src.strategies.implementations.momentum import MomentumStrategy
        from src.strategies.implementations.rsi_divergence import RSIDivergenceStrategy
        from src.strategies.implementations.trend_following import TrendFollowingStrategy
        from src.strategies.implementations.vwap import VWAPStrategy

        child_classes: dict[str, type[BaseStrategy]] = {
            "momentum": MomentumStrategy,
            "ma_crossover": MACrossoverStrategy,
            "mean_reversion": MeanReversionStrategy,
            "bollinger": BollingerStrategy,
            "rsi_divergence": RSIDivergenceStrategy,
            "trend_following": TrendFollowingStrategy,
            "breakout": BreakoutStrategy,
            "vwap": VWAPStrategy,
        }

        for name, cls in child_classes.items():
            try:
                child_config = SC(
                    enabled=True,
                    frequency=config.frequency,
                    symbols=config.symbols,
                    asset_classes=config.asset_classes,
                    parameters={},
                )
                self._child_strategies[name] = cls(config=child_config, data_hub=data_hub)
                logger.debug("bandit_child_instantiated", child=name)
            except Exception as exc:
                logger.warning(
                    "bandit_child_instantiation_failed",
                    child=name,
                    error=str(exc),
                )

        logger.info(
            "bandit_children_ready",
            count=len(self._child_strategies),
            children=list(self._child_strategies.keys()),
        )

    @property
    def gamma(self) -> float:
        return self._gamma

    @property
    def switching_cost(self) -> float:
        return self._switching_cost

    @property
    def min_rounds(self) -> int:
        return self._min_rounds

    def update_parameters(self, parameters: dict) -> None:
        """Update bandit parameters at runtime."""
        super().update_parameters(parameters)
        self._gamma = float(parameters.get("gamma", self._gamma))
        self._switching_cost = float(parameters.get("switching_cost", self._switching_cost))
        self._min_rounds = int(parameters.get("min_rounds", self._min_rounds))

    def required_indicators(self) -> list[str]:
        """Aggregate required indicators from all child strategies."""
        indicators: set[str] = set()
        for child in self._child_strategies.values():
            indicators.update(child.required_indicators())
        return sorted(indicators)

    def _compute_probabilities(self, symbol: str) -> dict[str, float]:
        """Compute EXP3 probability distribution over strategies for a symbol.

        The probability of selecting arm i is:
            p_i = (1 - gamma) * (w_i / sum(w)) + gamma / K

        where K is the number of arms, gamma is the exploration rate,
        and w_i is the weight of arm i.

        Args:
            symbol: The ticker symbol.

        Returns:
            Dict mapping strategy name to selection probability.
        """
        weights = self._weights[symbol]
        k = len(weights)
        total_weight = sum(weights.values())

        if total_weight == 0:
            # Uniform distribution if all weights are zero
            return {name: 1.0 / k for name in weights}

        probabilities = {}
        for name, w in weights.items():
            p = (1 - self._gamma) * (w / total_weight) + self._gamma / k
            probabilities[name] = p

        return probabilities

    def _select_strategy(self, symbol: str) -> str:
        """Select a strategy for a symbol using EXP3 probability distribution.

        Samples from the probability distribution, but respects min_rounds
        constraint to prevent excessive switching.

        Args:
            symbol: The ticker symbol.

        Returns:
            Name of the selected strategy.
        """
        probabilities = self._compute_probabilities(symbol)
        current = self._current_strategy.get(symbol)

        # If we haven't waited min_rounds since last switch, stick with current
        if current and self._bars_since_switch[symbol] < self._min_rounds:
            self._bars_since_switch[symbol] += 1
            return current

        # Sample from the distribution
        names = list(probabilities.keys())
        probs = [probabilities[n] for n in names]

        # Normalize probabilities (they should sum to 1, but ensure it)
        total = sum(probs)
        probs = [p / total for p in probs]

        selected = random.choices(names, weights=probs, k=1)[0]

        # Track switching
        if current and selected != current:
            self._bars_since_switch[symbol] = 0
            logger.info(
                "bandit_strategy_switch",
                symbol=symbol,
                previous=current,
                new=selected,
                probability=round(probabilities[selected], 4),
            )
        else:
            self._bars_since_switch[symbol] += 1

        self._current_strategy[symbol] = selected
        return selected

    def _update_weights(self, symbol: str, strategy_name: str, reward: float) -> None:
        """Update EXP3 weights based on observed reward.

        The weight update rule for EXP3:
            w_i *= exp(gamma * reward_hat / K)

        where reward_hat = reward / p_i (importance-weighted reward).

        If the strategy was switched to, apply switching cost penalty.

        Args:
            symbol: The ticker symbol.
            strategy_name: The strategy that generated the signal.
            reward: The observed reward (positive = profitable, negative = loss).
        """
        probabilities = self._compute_probabilities(symbol)
        k = len(self._weights[symbol])
        p_i = probabilities.get(strategy_name, 1.0 / k)

        # Importance-weighted reward estimate
        if p_i > 0:
            reward_hat = reward / p_i
        else:
            reward_hat = 0.0

        # Clip reward_hat to prevent numerical overflow
        reward_hat = max(-10.0, min(10.0, reward_hat))

        # Update weight
        update_factor = np.exp(self._gamma * reward_hat / k)
        self._weights[symbol][strategy_name] *= update_factor

        # Prevent weights from becoming too small or too large
        max_weight = max(self._weights[symbol].values())
        if max_weight > 1e6:
            # Normalize weights to prevent overflow
            for name in self._weights[symbol]:
                self._weights[symbol][name] /= max_weight

    async def evaluate(self) -> list[Signal]:
        """Evaluate using EXP3 bandit algorithm for strategy selection.

        Steps:
        1. Update weights based on previous signal outcomes.
        2. Select strategy for each symbol using EXP3 probabilities.
        3. Forward signals from the selected strategy.

        Returns:
            List of signals from the bandit-selected strategies.
        """
        self._bar_count += 1

        # Sync data_hub to children (backtest engine may have replaced it)
        for child in self._child_strategies.values():
            child._data_hub = self._data_hub

        # Step 1: Update weights based on previous signal outcomes
        self._update_from_previous_signals()

        # Step 2 & 3: Select strategy and forward signals for each symbol
        all_signals: list[Signal] = []

        for symbol in self._config.symbols:
            selected_strategy = self._select_strategy(symbol)
            child = self._child_strategies.get(selected_strategy)
            if child is None:
                continue

            try:
                signals = await child.evaluate()
                for sig in signals:
                    if sig.symbol == symbol:
                        # Record signal price for reward calculation next bar
                        timeframe = self._resolve_timeframe()
                        bars = self._data_hub.get_history(symbol, timeframe, 1)
                        if bars:
                            self._last_signal_price[symbol] = bars[-1].close
                            self._last_signal_direction[symbol] = sig.direction

                        # Re-tag with bandit strategy name
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
                                    "selected_strategy": selected_strategy,
                                    "selection_probability": round(
                                        self._compute_probabilities(symbol).get(
                                            selected_strategy, 0
                                        ),
                                        4,
                                    ),
                                },
                                option_params=sig.option_params,
                            )
                        )
            except Exception as exc:
                logger.warning(
                    "bandit_child_evaluate_failed",
                    child=selected_strategy,
                    symbol=symbol,
                    error=str(exc),
                )

        return all_signals

    def _update_from_previous_signals(self) -> None:
        """Update weights based on whether previous signals were profitable."""
        timeframe = self._resolve_timeframe()

        for symbol in list(self._last_signal_price.keys()):
            prev_price = self._last_signal_price.get(symbol)
            prev_direction = self._last_signal_direction.get(symbol)
            current_strategy = self._current_strategy.get(symbol)

            if prev_price is None or prev_direction is None or current_strategy is None:
                continue

            # Get current price
            bars = self._data_hub.get_history(symbol, timeframe, 1)
            if not bars:
                continue
            current_price = bars[-1].close

            # Calculate reward based on direction
            if prev_direction == SignalDirection.LONG:
                reward = (current_price - prev_price) / prev_price
            elif prev_direction == SignalDirection.SHORT:
                reward = (prev_price - current_price) / prev_price
            else:
                reward = 0.0

            # Update weights for the strategy that generated the signal
            self._update_weights(symbol, current_strategy, reward)

            # Clear the signal record
            del self._last_signal_price[symbol]
            self._last_signal_direction[symbol] = None

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
