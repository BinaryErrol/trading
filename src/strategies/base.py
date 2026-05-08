"""Base strategy abstract class for all trading strategy implementations.

Provides the contract that all strategies must implement: evaluate() to generate
signals and required_indicators() to declare data dependencies. Also provides
lifecycle state management and capital validation.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from decimal import Decimal
from enum import Enum
from typing import TYPE_CHECKING

import structlog

from src.strategies.signals import Signal

if TYPE_CHECKING:
    from src.config.settings import StrategyConfig
    from src.data.market_data_hub import MarketDataHub

logger = structlog.get_logger(__name__)


class StrategyState(Enum):
    """Lifecycle states for a strategy instance."""

    IDLE = "idle"
    RUNNING = "running"
    PAUSED = "paused"
    HALTED = "halted"


class BaseStrategy(ABC):
    """Abstract base class for all trading strategies.

    Subclasses must implement:
    - evaluate(): Analyze market data and return trading signals.
    - required_indicators(): Declare which indicators/data the strategy needs.

    Provides:
    - validate_capital(): Check if allocated capital meets minimum requirements.
    - State management via the state property.
    - Access to config and data_hub.
    """

    def __init__(self, config: StrategyConfig, data_hub: MarketDataHub) -> None:
        self._config = config
        self._data_hub = data_hub
        self._state: StrategyState = StrategyState.IDLE

    @property
    def name(self) -> str:
        """Strategy name derived from class name or config."""
        return self.__class__.__name__

    @property
    def config(self) -> StrategyConfig:
        """Strategy configuration."""
        return self._config

    @property
    def state(self) -> StrategyState:
        """Current lifecycle state."""
        return self._state

    @state.setter
    def state(self, value: StrategyState) -> None:
        """Set the lifecycle state."""
        previous = self._state
        self._state = value
        logger.info(
            "strategy_state_changed",
            strategy=self.name,
            previous=previous.value,
            new=value.value,
        )

    @property
    def data_hub(self) -> MarketDataHub:
        """Market data hub for accessing bars and history."""
        return self._data_hub

    @abstractmethod
    async def evaluate(self) -> list[Signal]:
        """Evaluate market conditions and generate trading signals.

        Called by the StrategyEngine at the configured frequency.
        Should return an empty list if no trading action is warranted.

        Returns:
            List of Signal objects representing trading intentions.
        """

    @abstractmethod
    def required_indicators(self) -> list[str]:
        """Return the list of indicators this strategy requires.

        Used by the engine to ensure required data is available
        before evaluation begins.

        Returns:
            List of indicator names (e.g., ['SMA_20', 'RSI_14']).
        """

    def validate_capital(self, allocated: Decimal) -> bool:
        """Check if allocated capital is sufficient for this strategy.

        Default implementation requires at least $1000. Subclasses can
        override for strategy-specific minimum requirements.

        Args:
            allocated: Amount of capital allocated to this strategy.

        Returns:
            True if capital is sufficient, False otherwise.
        """
        min_capital = Decimal("1000")
        if allocated < min_capital:
            logger.warning(
                "insufficient_capital",
                strategy=self.name,
                allocated=str(allocated),
                minimum=str(min_capital),
            )
            return False
        return True

    def update_parameters(self, parameters: dict) -> None:
        """Update strategy parameters at runtime for hot-reload support.

        Called by the StrategyEngine when the ConfigWatcher detects a config
        file change. Updates the internal config parameters dict and refreshes
        any cached parameter values.

        Subclasses should override this method to re-read their specific
        parameters from the updated dict.

        Args:
            parameters: New strategy-specific parameters dict from StrategyConfig.
        """
        self._config.parameters = parameters
        logger.info(
            "strategy_parameters_updated",
            strategy=self.name,
            parameters=parameters,
        )
