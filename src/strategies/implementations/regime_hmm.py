"""Regime detection strategy using a rule-based HMM approximation.

Classifies the current market into one of three regimes:
- TRENDING: High directional movement, low mean-reversion
- MEAN_REVERTING: Oscillating around mean, low trend
- VOLATILE: High volatility, unpredictable (crisis mode)

Based on the detected regime, delegates signal generation to the
appropriate child strategy:
- Trending → momentum or trend_following signals
- Mean-Reverting → mean_reversion or bollinger signals
- Volatile → no signals (sit out) or reduce size by 50%

Uses a simplified rule-based regime detector that approximates what a
Gaussian HMM would produce, without requiring hmmlearn:
- If rolling volatility > vol_threshold AND abs(rolling_return) < volatility/2 → VOLATILE
- If abs(rolling_return) > volatility → TRENDING
- Otherwise → MEAN_REVERTING
"""

from __future__ import annotations

from decimal import Decimal
from enum import Enum
from typing import TYPE_CHECKING

import numpy as np
import structlog

from src.data.bar_builder import Timeframe
from src.strategies.base import BaseStrategy
from src.strategies.signals import Signal

if TYPE_CHECKING:
    from src.config.settings import StrategyConfig
    from src.data.market_data_hub import MarketDataHub

logger = structlog.get_logger(__name__)


class MarketRegime(Enum):
    """Detected market regime states."""

    TRENDING = "trending"
    MEAN_REVERTING = "mean_reverting"
    VOLATILE = "volatile"


class RegimeHMMStrategy(BaseStrategy):
    """Regime-based meta-strategy using rule-based HMM approximation.

    Detects the current market regime and delegates to the appropriate
    child strategy for signal generation.

    Parameters (from config.parameters):
        hmm_lookback: int = 120 — bars of history for regime detection.
        volatility_window: int = 14 — rolling window for volatility calculation.
        trend_window: int = 14 — rolling window for return calculation.
        vol_threshold: float = 0.03 — volatility threshold for VOLATILE regime.
    """

    # Child strategies for each regime
    TRENDING_STRATEGIES = ["momentum"]
    MEAN_REVERTING_STRATEGIES = ["mean_reversion"]

    def __init__(self, config: StrategyConfig, data_hub: MarketDataHub) -> None:
        super().__init__(config, data_hub)

        # Parameters
        self._hmm_lookback: int = int(config.parameters.get("hmm_lookback", 120))
        self._volatility_window: int = int(config.parameters.get("volatility_window", 14))
        self._trend_window: int = int(config.parameters.get("trend_window", 14))
        self._vol_threshold: float = float(config.parameters.get("vol_threshold", 0.03))

        # Current regime per symbol
        self._current_regime: dict[str, MarketRegime] = {}

        # Child strategy instances
        self._child_strategies: dict[str, BaseStrategy] = {}
        self._instantiate_children(config, data_hub)

    def _instantiate_children(self, config: StrategyConfig, data_hub: MarketDataHub) -> None:
        """Instantiate child strategies for trending and mean-reverting regimes."""
        from src.config.settings import StrategyConfig as SC
        from src.strategies.implementations.mean_reversion import MeanReversionStrategy
        from src.strategies.implementations.momentum import MomentumStrategy

        child_classes: dict[str, type[BaseStrategy]] = {
            "momentum": MomentumStrategy,
            "mean_reversion": MeanReversionStrategy,
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
                logger.debug("regime_hmm_child_instantiated", child=name)
            except Exception as exc:
                logger.warning(
                    "regime_hmm_child_instantiation_failed",
                    child=name,
                    error=str(exc),
                )

        logger.info(
            "regime_hmm_children_ready",
            count=len(self._child_strategies),
            children=list(self._child_strategies.keys()),
        )

    @property
    def hmm_lookback(self) -> int:
        return self._hmm_lookback

    @property
    def volatility_window(self) -> int:
        return self._volatility_window

    @property
    def trend_window(self) -> int:
        return self._trend_window

    @property
    def vol_threshold(self) -> float:
        return self._vol_threshold

    def update_parameters(self, parameters: dict) -> None:
        """Update regime detection parameters at runtime."""
        super().update_parameters(parameters)
        self._hmm_lookback = int(parameters.get("hmm_lookback", self._hmm_lookback))
        self._volatility_window = int(parameters.get("volatility_window", self._volatility_window))
        self._trend_window = int(parameters.get("trend_window", self._trend_window))
        self._vol_threshold = float(parameters.get("vol_threshold", self._vol_threshold))

    def required_indicators(self) -> list[str]:
        """Aggregate required indicators from all child strategies."""
        indicators: set[str] = set()
        for child in self._child_strategies.values():
            indicators.update(child.required_indicators())
        return sorted(indicators)

    def detect_regime(self, symbol: str) -> MarketRegime:
        """Detect the current market regime for a symbol.

        Uses rolling returns and rolling volatility to classify the market:
        - VOLATILE: high volatility with low directional movement
        - TRENDING: strong directional movement relative to volatility
        - MEAN_REVERTING: moderate volatility with no clear trend

        Args:
            symbol: The ticker symbol to analyze.

        Returns:
            The detected MarketRegime.
        """
        timeframe = self._resolve_timeframe()
        bars = self._data_hub.get_history(symbol, timeframe, self._hmm_lookback)

        if len(bars) < max(self._volatility_window, self._trend_window) + 1:
            # Not enough data — default to mean-reverting (conservative)
            return MarketRegime.MEAN_REVERTING

        # Extract close prices
        closes = np.array([bar.close for bar in bars])

        # Calculate rolling returns over trend_window
        if len(closes) > self._trend_window:
            rolling_return = (closes[-1] - closes[-self._trend_window - 1]) / closes[-self._trend_window - 1]
        else:
            rolling_return = 0.0

        # Calculate rolling volatility (std of daily returns over volatility_window)
        if len(closes) > self._volatility_window:
            recent_closes = closes[-self._volatility_window - 1:]
            daily_returns = np.diff(recent_closes) / recent_closes[:-1]
            rolling_volatility = float(np.std(daily_returns))
        else:
            rolling_volatility = 0.0

        # Rule-based regime classification
        if rolling_volatility > self._vol_threshold and abs(rolling_return) < rolling_volatility / 2:
            regime = MarketRegime.VOLATILE
        elif abs(rolling_return) > rolling_volatility:
            regime = MarketRegime.TRENDING
        else:
            regime = MarketRegime.MEAN_REVERTING

        # Log regime changes
        previous_regime = self._current_regime.get(symbol)
        if previous_regime != regime:
            logger.info(
                "regime_change_detected",
                symbol=symbol,
                previous=previous_regime.value if previous_regime else "none",
                new=regime.value,
                rolling_return=round(rolling_return, 4),
                rolling_volatility=round(rolling_volatility, 4),
            )

        self._current_regime[symbol] = regime
        return regime

    async def evaluate(self) -> list[Signal]:
        """Evaluate market regime and delegate to appropriate child strategy.

        Steps:
        1. Detect regime for each symbol.
        2. Route to appropriate child strategies based on regime.
        3. For VOLATILE regime, suppress signals (sit out).

        Returns:
            List of signals from the regime-appropriate child strategies.
        """
        # Sync data_hub to children (backtest engine may have replaced it)
        for child in self._child_strategies.values():
            child._data_hub = self._data_hub

        all_signals: list[Signal] = []

        for symbol in self._config.symbols:
            regime = self.detect_regime(symbol)

            if regime == MarketRegime.VOLATILE:
                # Sit out during volatile/crisis periods
                logger.debug(
                    "regime_hmm_sitting_out",
                    symbol=symbol,
                    regime=regime.value,
                )
                continue

            elif regime == MarketRegime.TRENDING:
                strategy_names = self.TRENDING_STRATEGIES
            else:  # MEAN_REVERTING
                strategy_names = self.MEAN_REVERTING_STRATEGIES

            # Collect signals from appropriate child strategies
            for strategy_name in strategy_names:
                child = self._child_strategies.get(strategy_name)
                if child is None:
                    continue

                try:
                    signals = await child.evaluate()
                    for sig in signals:
                        if sig.symbol == symbol:
                            # Re-tag with regime_hmm strategy name and add regime metadata
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
                                        "regime": regime.value,
                                        "delegate_strategy": strategy_name,
                                    },
                                    option_params=sig.option_params,
                                )
                            )
                except Exception as exc:
                    logger.warning(
                        "regime_hmm_child_evaluate_failed",
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
