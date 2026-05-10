"""Adaptive meta-strategy implementation.

A "strategy of strategies" that dynamically selects the best-performing
child strategy for each symbol based on rolling performance tracking.

On each evaluation:
1. Runs all child strategies to collect their signals.
2. Tracks a rolling performance score per child strategy per symbol.
3. Only forwards signals from the currently best-performing strategy for each symbol.

Performance is measured as a win-rate score over a configurable lookback window:
    score = (wins - losses) / total_signals

Strategy selection is re-evaluated every `rebalance_period` bars.
"""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
from typing import TYPE_CHECKING

import structlog

from src.data.bar_builder import Timeframe
from src.strategies.base import BaseStrategy
from src.strategies.signals import Signal, SignalDirection

if TYPE_CHECKING:
    from src.config.settings import StrategyConfig
    from src.data.market_data_hub import MarketDataHub

logger = structlog.get_logger(__name__)


@dataclass
class SignalRecord:
    """Record of a past signal for performance tracking."""

    direction: SignalDirection
    price_at_signal: float
    resolved: bool = False
    profitable: bool | None = None


class AdaptiveStrategy(BaseStrategy):
    """Adaptive meta-strategy that selects the best child strategy per symbol.

    Internally instantiates all available child strategies and on each
    evaluate() call, runs them all to collect signals. Only forwards signals
    from whichever child strategy has the best recent track record for each
    symbol.

    Parameters (from config.parameters):
        lookback_window: int = 60 — bars to evaluate performance over.
        rebalance_period: int = 20 — bars between strategy re-selection.
        min_sharpe_threshold: float = 0.0 — minimum score to forward any signal.
    """

    # Child strategies to instantiate (excludes pairs_trading and market_making
    # which have different symbol semantics, and wheel which needs extra deps)
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
        self._lookback_window: int = int(config.parameters.get("lookback_window", 60))
        self._rebalance_period: int = int(config.parameters.get("rebalance_period", 20))
        self._min_sharpe_threshold: float = float(
            config.parameters.get("min_sharpe_threshold", 0.0)
        )

        # Rolling performance scores: strategy_name -> symbol -> deque of outcomes (True/False)
        self._strategy_scores: dict[str, dict[str, deque]] = defaultdict(
            lambda: defaultdict(lambda: deque(maxlen=self._lookback_window))
        )

        # Pending signal records: strategy_name -> symbol -> list of SignalRecord
        self._pending_signals: dict[str, dict[str, list[SignalRecord]]] = defaultdict(
            lambda: defaultdict(list)
        )

        # Currently selected best strategy per symbol
        self._best_strategy: dict[str, str] = {}

        # Bar counter for rebalance timing
        self._bar_count: int = 0

        # Child strategy instances
        self._child_strategies: dict[str, BaseStrategy] = {}
        self._instantiate_children(config, data_hub)

    def _instantiate_children(self, config: StrategyConfig, data_hub: MarketDataHub) -> None:
        """Instantiate all child strategy classes."""
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

        # Create a child config that mirrors the adaptive config (same symbols, frequency, etc.)
        # but uses default parameters for each child strategy
        from src.config.settings import StrategyConfig as SC

        for name, cls in child_classes.items():
            try:
                child_config = SC(
                    enabled=True,
                    frequency=config.frequency,
                    symbols=config.symbols,
                    asset_classes=config.asset_classes,
                    parameters={},  # Use child strategy defaults
                )
                self._child_strategies[name] = cls(config=child_config, data_hub=data_hub)
                logger.debug("adaptive_child_instantiated", child=name)
            except Exception as exc:
                logger.warning(
                    "adaptive_child_instantiation_failed",
                    child=name,
                    error=str(exc),
                )

        logger.info(
            "adaptive_children_ready",
            count=len(self._child_strategies),
            children=list(self._child_strategies.keys()),
        )

    @property
    def lookback_window(self) -> int:
        return self._lookback_window

    @property
    def rebalance_period(self) -> int:
        return self._rebalance_period

    @property
    def min_sharpe_threshold(self) -> float:
        return self._min_sharpe_threshold

    def update_parameters(self, parameters: dict) -> None:
        """Update adaptive parameters at runtime for hot-reload support."""
        super().update_parameters(parameters)
        self._lookback_window = int(parameters.get("lookback_window", self._lookback_window))
        self._rebalance_period = int(parameters.get("rebalance_period", self._rebalance_period))
        self._min_sharpe_threshold = float(
            parameters.get("min_sharpe_threshold", self._min_sharpe_threshold)
        )

    def required_indicators(self) -> list[str]:
        """Aggregate required indicators from all child strategies."""
        indicators: set[str] = set()
        for child in self._child_strategies.values():
            indicators.update(child.required_indicators())
        return sorted(indicators)

    async def evaluate(self) -> list[Signal]:
        """Evaluate all child strategies and forward signals from the best one per symbol.

        Steps:
        1. Resolve pending signals from previous bar (check if they were profitable).
        2. Run all child strategies to get their current signals.
        3. Record new signals for future performance tracking.
        4. Re-select best strategy per symbol if rebalance period reached.
        5. Forward only signals from the best strategy for each symbol.

        Returns:
            List of signals from the best-performing strategy per symbol.
        """
        self._bar_count += 1

        # Sync data_hub to children (backtest engine may have replaced it)
        for child in self._child_strategies.values():
            child._data_hub = self._data_hub

        # Step 1: Resolve pending signals from previous evaluations
        self._resolve_pending_signals()

        # Step 2: Run all child strategies
        child_signals: dict[str, list[Signal]] = {}
        for name, strategy in self._child_strategies.items():
            try:
                signals = await strategy.evaluate()
                child_signals[name] = signals
            except Exception as exc:
                logger.warning(
                    "adaptive_child_evaluate_failed",
                    child=name,
                    error=str(exc),
                )
                child_signals[name] = []

        # Step 3: Record new signals for performance tracking
        self._record_signals(child_signals)

        # Step 4: Re-select best strategy per symbol on rebalance
        if self._bar_count % self._rebalance_period == 0:
            self._rebalance()

        # Step 5: Forward signals from the best strategy per symbol
        forwarded_signals: list[Signal] = []
        for symbol in self._config.symbols:
            best = self._best_strategy.get(symbol)
            if best is None:
                # No best strategy selected yet — skip
                continue

            # Check minimum score threshold
            score = self._compute_score(best, symbol)
            if score < self._min_sharpe_threshold:
                logger.debug(
                    "adaptive_signal_suppressed",
                    symbol=symbol,
                    strategy=best,
                    score=round(score, 4),
                    threshold=self._min_sharpe_threshold,
                )
                continue

            # Forward signals from the best strategy for this symbol
            for sig in child_signals.get(best, []):
                if sig.symbol == symbol:
                    # Re-tag the signal with the adaptive strategy name
                    forwarded_signals.append(
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
                                "selected_strategy": best,
                                "strategy_score": round(score, 4),
                            },
                            option_params=sig.option_params,
                        )
                    )

        return forwarded_signals

    def _resolve_pending_signals(self) -> None:
        """Check pending signals against current prices to determine profitability.

        For each unresolved signal, get the current price and check if the
        price moved in the signal's direction.
        """
        timeframe = self._resolve_timeframe()

        for strategy_name in list(self._pending_signals.keys()):
            for symbol in list(self._pending_signals[strategy_name].keys()):
                records = self._pending_signals[strategy_name][symbol]
                if not records:
                    continue

                # Get current price
                bars = self._data_hub.get_history(symbol, timeframe, 1)
                if not bars:
                    continue
                current_price = bars[-1].close

                # Resolve unresolved signals
                resolved_indices: list[int] = []
                for i, record in enumerate(records):
                    if record.resolved:
                        resolved_indices.append(i)
                        continue

                    # Determine if the signal was profitable
                    if record.direction == SignalDirection.LONG:
                        profitable = current_price > record.price_at_signal
                    elif record.direction == SignalDirection.SHORT:
                        profitable = current_price < record.price_at_signal
                    else:
                        # CLOSE signals — consider profitable if price didn't move against
                        profitable = True

                    record.resolved = True
                    record.profitable = profitable
                    resolved_indices.append(i)

                    # Update rolling score
                    self._strategy_scores[strategy_name][symbol].append(profitable)

                # Remove resolved signals
                self._pending_signals[strategy_name][symbol] = [
                    r for i, r in enumerate(records) if i not in resolved_indices
                ]

    def _record_signals(self, child_signals: dict[str, list[Signal]]) -> None:
        """Record new signals from child strategies for future performance tracking."""
        timeframe = self._resolve_timeframe()

        for strategy_name, signals in child_signals.items():
            for sig in signals:
                # Get current price for the signal's symbol
                bars = self._data_hub.get_history(sig.symbol, timeframe, 1)
                if not bars:
                    continue
                price_at_signal = bars[-1].close

                record = SignalRecord(
                    direction=sig.direction,
                    price_at_signal=price_at_signal,
                )
                self._pending_signals[strategy_name][sig.symbol].append(record)

    def _rebalance(self) -> None:
        """Re-evaluate which strategy is best for each symbol."""
        for symbol in self._config.symbols:
            best_name: str | None = None
            best_score: float = -float("inf")

            for strategy_name in self._child_strategies:
                score = self._compute_score(strategy_name, symbol)
                if score > best_score:
                    best_score = score
                    best_name = strategy_name

            previous_best = self._best_strategy.get(symbol)

            if best_name is not None:
                self._best_strategy[symbol] = best_name

                if previous_best != best_name:
                    logger.info(
                        "adaptive_strategy_selection_changed",
                        symbol=symbol,
                        previous=previous_best,
                        new=best_name,
                        score=round(best_score, 4),
                    )

            logger.info(
                "adaptive_rebalance",
                symbol=symbol,
                best_strategy=best_name,
                score=round(best_score, 4),
                bar_count=self._bar_count,
            )

    def _compute_score(self, strategy_name: str, symbol: str) -> float:
        """Compute rolling performance score for a strategy on a symbol.

        Score = (wins - losses) / total_signals over the lookback window.
        Returns 0.0 if no signals have been recorded.
        """
        outcomes = self._strategy_scores[strategy_name][symbol]
        if not outcomes:
            return 0.0

        total = len(outcomes)
        wins = sum(1 for o in outcomes if o)
        losses = total - wins

        return (wins - losses) / total

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
