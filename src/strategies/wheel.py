"""Options Wheel strategy implementation.

Sells cash-secured puts on target underlyings, writes covered calls on
assigned shares, rolls expiring positions, and adapts to VIX regime changes.
"""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING

import structlog

from src.data.options_chain import OptionContract, OptionsChainProvider
from src.portfolio.monitor import PortfolioMonitor
from src.strategies.base import BaseStrategy
from src.strategies.signals import (
    OptionSignalParams,
    OrderType,
    Signal,
    SignalDirection,
)

if TYPE_CHECKING:
    from src.config.settings import StrategyConfig
    from src.data.market_data_hub import MarketDataHub

logger = structlog.get_logger(__name__)


class WheelStrategy(BaseStrategy):
    """Options Wheel strategy: sell puts, write calls on assignment, roll expiring.

    Lifecycle:
    1. Sell cash-secured puts on target underlyings at configured delta.
    2. If assigned, write covered calls on the resulting shares.
    3. Roll expiring positions forward for net credit when possible.
    4. Suppress new puts during high-VIX regimes (with hysteresis).

    Parameters (from config.parameters):
        target_delta: Target delta for strike selection (default 0.30).
        min_dte: Minimum days to expiration (default 30).
        max_dte: Maximum days to expiration (default 45).
        roll_dte_threshold: Days remaining to trigger roll evaluation (default 7).
        vix_high_threshold: VIX level to suppress new puts (default 30.0).
        vix_reentry_threshold: VIX level to resume puts (default 25.0).
        max_positions_per_symbol: Max concurrent positions per symbol (default 1).
    """

    # Default parameter constants
    DEFAULT_TARGET_DELTA: float = 0.30
    DEFAULT_MIN_DTE: int = 30
    DEFAULT_MAX_DTE: int = 45
    DEFAULT_ROLL_DTE_THRESHOLD: int = 7
    DEFAULT_VIX_HIGH_THRESHOLD: float = 30.0
    DEFAULT_VIX_REENTRY_THRESHOLD: float = 25.0
    DEFAULT_MAX_POSITIONS_PER_SYMBOL: int = 1

    def __init__(
        self,
        config: StrategyConfig,
        data_hub: MarketDataHub,
        options_chain: OptionsChainProvider,
        portfolio_monitor: PortfolioMonitor,
    ) -> None:
        super().__init__(config, data_hub)
        self._options_chain = options_chain
        self._portfolio_monitor = portfolio_monitor

        # Load parameters from config with defaults
        params = config.parameters
        self._target_delta: float = float(
            params.get("target_delta", self.DEFAULT_TARGET_DELTA)
        )
        self._min_dte: int = int(params.get("min_dte", self.DEFAULT_MIN_DTE))
        self._max_dte: int = int(params.get("max_dte", self.DEFAULT_MAX_DTE))
        self._roll_dte_threshold: int = int(
            params.get("roll_dte_threshold", self.DEFAULT_ROLL_DTE_THRESHOLD)
        )
        self._vix_high_threshold: float = float(
            params.get("vix_high_threshold", self.DEFAULT_VIX_HIGH_THRESHOLD)
        )
        self._vix_reentry_threshold: float = float(
            params.get("vix_reentry_threshold", self.DEFAULT_VIX_REENTRY_THRESHOLD)
        )
        self._max_positions_per_symbol: int = int(
            params.get("max_positions_per_symbol", self.DEFAULT_MAX_POSITIONS_PER_SYMBOL)
        )

        # VIX hysteresis state
        self._vix_suppressed: bool = True  # Suppress until first VIX reading
        self._last_vix: float | None = None

        # Capital allocation tracking
        self._allocated_capital: Decimal = Decimal("0")

    def required_indicators(self) -> list[str]:
        """Wheel strategy requires VIX data."""
        return ["VIX"]

    def validate_capital(self, allocated: Decimal) -> bool:
        """Require minimum $10,000 for options wheel strategy.

        Args:
            allocated: Amount of capital allocated to this strategy.

        Returns:
            True if capital is sufficient (>= $10,000), False otherwise.
        """
        min_capital = Decimal("10000")
        if allocated < min_capital:
            logger.warning(
                "insufficient_capital",
                strategy=self.name,
                allocated=str(allocated),
                minimum=str(min_capital),
            )
            return False
        self._allocated_capital = allocated
        return True

    def update_parameters(self, parameters: dict) -> None:
        """Update wheel strategy parameters at runtime for hot-reload support.

        Args:
            parameters: New strategy-specific parameters dict from config.
        """
        super().update_parameters(parameters)
        self._target_delta = float(
            parameters.get("target_delta", self._target_delta)
        )
        self._min_dte = int(parameters.get("min_dte", self._min_dte))
        self._max_dte = int(parameters.get("max_dte", self._max_dte))
        self._roll_dte_threshold = int(
            parameters.get("roll_dte_threshold", self._roll_dte_threshold)
        )
        self._vix_high_threshold = float(
            parameters.get("vix_high_threshold", self._vix_high_threshold)
        )
        self._vix_reentry_threshold = float(
            parameters.get("vix_reentry_threshold", self._vix_reentry_threshold)
        )
        self._max_positions_per_symbol = int(
            parameters.get("max_positions_per_symbol", self._max_positions_per_symbol)
        )
        logger.info(
            "wheel_parameters_updated",
            target_delta=self._target_delta,
            min_dte=self._min_dte,
            max_dte=self._max_dte,
            roll_dte_threshold=self._roll_dte_threshold,
            vix_high_threshold=self._vix_high_threshold,
            vix_reentry_threshold=self._vix_reentry_threshold,
        )

    async def evaluate(self) -> list[Signal]:
        """Evaluate market conditions and generate wheel strategy signals.

        Orchestration order:
        1. Get VIX level and update suppression state.
        2. Generate covered call signals for assigned shares.
        3. Generate roll signals for expiring positions.
        4. Generate put signals (if not VIX-suppressed).

        Returns:
            Combined list of all generated signals.
        """
        signals: list[Signal] = []

        # Step 1: VIX regime check
        await self._get_vix_level()

        # Step 2: Covered call signals (never suppressed by VIX)
        call_signals = await self._generate_call_signals()
        signals.extend(call_signals)

        # Step 3: Roll signals (never suppressed by VIX)
        roll_signals = await self._generate_roll_signals()
        signals.extend(roll_signals)

        # Step 4: Put signals (suppressed during high VIX)
        if not self._vix_suppressed:
            put_signals = await self._generate_put_signals()
            signals.extend(put_signals)
        else:
            logger.info(
                "put_signals_suppressed",
                strategy=self.name,
                vix_suppressed=self._vix_suppressed,
                last_vix=self._last_vix,
            )

        return signals

    async def _get_vix_level(self) -> float | None:
        """Fetch current VIX level and update suppression state with hysteresis.

        Suppresses puts when VIX > vix_high_threshold.
        Resumes puts when VIX < vix_reentry_threshold.
        If VIX unavailable and never received, suppresses puts.

        Returns:
            Current VIX level or None if unavailable.
        """
        vix = await self._options_chain.get_vix()

        if vix is None:
            logger.warning(
                "vix_unavailable",
                strategy=self.name,
                last_known=self._last_vix,
            )
            # If never received VIX, suppress puts
            if self._last_vix is None:
                self._vix_suppressed = True
            return self._last_vix

        self._last_vix = vix

        # Hysteresis logic
        if vix > self._vix_high_threshold:
            if not self._vix_suppressed:
                logger.info(
                    "vix_suppression_activated",
                    strategy=self.name,
                    vix=vix,
                    threshold=self._vix_high_threshold,
                )
            self._vix_suppressed = True
        elif vix < self._vix_reentry_threshold:
            if self._vix_suppressed:
                logger.info(
                    "vix_suppression_deactivated",
                    strategy=self.name,
                    vix=vix,
                    threshold=self._vix_reentry_threshold,
                )
            self._vix_suppressed = False
        # Between thresholds: maintain current state (hysteresis)

        return vix

    async def _generate_put_signals(self) -> list[Signal]:
        """Generate SELL PUT signals for configured symbols.

        For each symbol:
        - Skip if existing short put position exists.
        - Verify buying power is sufficient.
        - Select strike by target delta.
        - Validate DTE range.

        Returns:
            List of SELL PUT signals.
        """
        signals: list[Signal] = []

        for symbol in self._config.symbols:
            # Check for existing short put
            if self._has_existing_short_position(symbol, "P"):
                logger.debug(
                    "put_signal_skipped_existing_position",
                    symbol=symbol,
                )
                continue

            # Get option chain
            chain = await self._options_chain.get_chain(
                underlying=symbol,
                right="P",
                min_dte=self._min_dte,
                max_dte=self._max_dte,
            )

            if not chain:
                logger.info(
                    "put_signal_skipped_no_chain",
                    symbol=symbol,
                    min_dte=self._min_dte,
                    max_dte=self._max_dte,
                )
                continue

            # Select strike by delta
            contract = self._select_strike_by_delta(chain, self._target_delta)
            if contract is None:
                logger.info(
                    "put_signal_skipped_no_valid_strike",
                    symbol=symbol,
                    target_delta=self._target_delta,
                )
                continue

            # Validate DTE range
            if not self._is_within_dte_range(contract.expiration):
                logger.info(
                    "put_signal_skipped_dte_out_of_range",
                    symbol=symbol,
                    expiration=str(contract.expiration),
                )
                continue

            # Verify buying power
            cash_required = self._calculate_cash_required(contract.strike, 1)
            if cash_required > self._allocated_capital:
                logger.info(
                    "put_signal_skipped_insufficient_capital",
                    symbol=symbol,
                    required=str(cash_required),
                    available=str(self._allocated_capital),
                )
                continue

            # Generate signal
            signal = Signal(
                strategy_name=self.name,
                symbol=contract.symbol,
                direction=SignalDirection.SHORT,
                confidence=0.7,
                suggested_size=cash_required,
                order_type=OrderType.LIMIT,
                limit_price=contract.mid,
                metadata={
                    "underlying": symbol,
                    "strike": str(contract.strike),
                    "expiration": str(contract.expiration),
                    "delta": contract.delta,
                    "implied_vol": contract.implied_vol,
                },
                option_params=OptionSignalParams(
                    underlying=symbol,
                    strike=contract.strike,
                    expiration=contract.expiration,
                    right="P",
                    action="SELL_TO_OPEN",
                ),
            )
            signals.append(signal)
            logger.info(
                "put_signal_generated",
                symbol=symbol,
                strike=str(contract.strike),
                expiration=str(contract.expiration),
                delta=contract.delta,
                premium=str(contract.mid),
            )

        return signals

    async def _generate_call_signals(self) -> list[Signal]:
        """Generate SELL CALL signals for assigned shares.

        Checks portfolio for symbols with >= 100 shares and generates
        covered call signals for complete lots.

        Returns:
            List of SELL CALL signals.
        """
        signals: list[Signal] = []
        assigned_shares = self._get_assigned_shares()

        for symbol, qty in assigned_shares.items():
            # Skip if existing short call
            if self._has_existing_short_position(symbol, "C"):
                logger.debug(
                    "call_signal_skipped_existing_position",
                    symbol=symbol,
                )
                continue

            # Number of contracts = floor(shares / 100)
            num_contracts = qty // 100
            if num_contracts <= 0:
                continue

            # Get call chain
            chain = await self._options_chain.get_chain(
                underlying=symbol,
                right="C",
                min_dte=self._min_dte,
                max_dte=self._max_dte,
            )

            if not chain:
                logger.info(
                    "call_signal_skipped_no_chain",
                    symbol=symbol,
                    min_dte=self._min_dte,
                    max_dte=self._max_dte,
                )
                continue

            # Select strike by delta
            contract = self._select_strike_by_delta(chain, self._target_delta)
            if contract is None:
                logger.info(
                    "call_signal_skipped_no_valid_strike",
                    symbol=symbol,
                    target_delta=self._target_delta,
                )
                continue

            # Generate signal
            signal = Signal(
                strategy_name=self.name,
                symbol=contract.symbol,
                direction=SignalDirection.SHORT,
                confidence=0.7,
                suggested_size=Decimal(str(num_contracts * 100)) * contract.strike,
                order_type=OrderType.LIMIT,
                limit_price=contract.mid,
                metadata={
                    "underlying": symbol,
                    "strike": str(contract.strike),
                    "expiration": str(contract.expiration),
                    "delta": contract.delta,
                    "num_contracts": num_contracts,
                },
                option_params=OptionSignalParams(
                    underlying=symbol,
                    strike=contract.strike,
                    expiration=contract.expiration,
                    right="C",
                    action="SELL_TO_OPEN",
                ),
            )
            signals.append(signal)
            logger.info(
                "call_signal_generated",
                symbol=symbol,
                strike=str(contract.strike),
                expiration=str(contract.expiration),
                delta=contract.delta,
                contracts=num_contracts,
            )

        return signals

    async def _generate_roll_signals(self) -> list[Signal]:
        """Generate roll signals for positions near expiry.

        For each short option position within roll_dte_threshold:
        - Get new chain for the same underlying/right.
        - Calculate net credit (new premium - close cost).
        - Only generate roll if net credit > 0.
        - Generate paired BUY_TO_CLOSE + SELL_TO_OPEN signals.

        Returns:
            List of paired roll signals.
        """
        signals: list[Signal] = []
        today = date.today()

        positions = self._portfolio_monitor.positions
        for _key, pos in positions.items():
            # Only process option positions from this strategy
            if pos.strategy_name != self.name:
                continue
            if pos.asset_class not in ("OPT", "option"):
                continue

            # Parse expiration from symbol
            expiration = self._get_position_expiration(pos)
            if expiration is None:
                continue

            dte = (expiration - today).days
            if dte > self._roll_dte_threshold:
                continue

            # Determine right from position
            right = self._get_position_right(pos)
            if right is None:
                continue

            underlying = self._get_position_underlying(pos)

            # Get new chain
            chain = await self._options_chain.get_chain(
                underlying=underlying,
                right=right,
                min_dte=self._min_dte,
                max_dte=self._max_dte,
            )

            if not chain:
                logger.info(
                    "roll_skipped_no_chain",
                    symbol=pos.symbol,
                    underlying=underlying,
                )
                continue

            # Select new strike
            new_contract = self._select_strike_by_delta(chain, self._target_delta)
            if new_contract is None:
                logger.info(
                    "roll_skipped_no_valid_strike",
                    symbol=pos.symbol,
                )
                continue

            # Calculate net credit
            # Close cost = current price of existing position (ask to buy back)
            close_cost = pos.current_price if pos.current_price else Decimal("0")
            new_premium = new_contract.mid

            net_credit = new_premium - close_cost

            if net_credit <= 0:
                logger.info(
                    "roll_skipped_net_debit",
                    symbol=pos.symbol,
                    close_cost=str(close_cost),
                    new_premium=str(new_premium),
                    net_credit=str(net_credit),
                )
                continue

            # Generate BUY_TO_CLOSE signal
            close_signal = Signal(
                strategy_name=self.name,
                symbol=pos.symbol,
                direction=SignalDirection.LONG,
                confidence=0.8,
                suggested_size=close_cost * 100,
                order_type=OrderType.LIMIT,
                limit_price=close_cost,
                metadata={
                    "roll_action": "BUY_TO_CLOSE",
                    "underlying": underlying,
                    "net_credit": str(net_credit),
                },
                option_params=OptionSignalParams(
                    underlying=underlying,
                    strike=Decimal(str(pos.avg_entry_price)),
                    expiration=expiration,
                    right=right,
                    action="BUY_TO_CLOSE",
                ),
            )
            signals.append(close_signal)

            # Generate SELL_TO_OPEN signal
            open_signal = Signal(
                strategy_name=self.name,
                symbol=new_contract.symbol,
                direction=SignalDirection.SHORT,
                confidence=0.8,
                suggested_size=new_contract.strike * 100,
                order_type=OrderType.LIMIT,
                limit_price=new_contract.mid,
                metadata={
                    "roll_action": "SELL_TO_OPEN",
                    "underlying": underlying,
                    "net_credit": str(net_credit),
                    "new_expiration": str(new_contract.expiration),
                },
                option_params=OptionSignalParams(
                    underlying=underlying,
                    strike=new_contract.strike,
                    expiration=new_contract.expiration,
                    right=right,
                    action="SELL_TO_OPEN",
                ),
            )
            signals.append(open_signal)

            logger.info(
                "roll_signal_generated",
                symbol=pos.symbol,
                new_symbol=new_contract.symbol,
                close_cost=str(close_cost),
                new_premium=str(new_premium),
                net_credit=str(net_credit),
            )

        return signals

    def _select_strike_by_delta(
        self, chain: list[OptionContract], target_delta: float
    ) -> OptionContract | None:
        """Select the contract with delta closest to target.

        Args:
            chain: List of available option contracts.
            target_delta: Target delta value (e.g. 0.30).

        Returns:
            The contract with minimum |delta - target_delta|, or None if chain is empty.
        """
        if not chain:
            return None

        return min(chain, key=lambda c: abs(abs(c.delta) - target_delta))

    def _is_within_dte_range(self, expiration: date) -> bool:
        """Check if an expiration date falls within the configured DTE range.

        Args:
            expiration: The option expiration date.

        Returns:
            True if min_dte <= DTE <= max_dte.
        """
        today = date.today()
        dte = (expiration - today).days
        return self._min_dte <= dte <= self._max_dte

    def _has_existing_short_position(self, symbol: str, right: str) -> bool:
        """Check if there's an existing short option position for a symbol.

        Args:
            symbol: The underlying symbol.
            right: "P" for put, "C" for call.

        Returns:
            True if a short position exists for this symbol and right.
        """
        positions = self._portfolio_monitor.positions
        for _key, pos in positions.items():
            if pos.strategy_name != self.name:
                continue
            if pos.quantity >= 0:
                continue  # Not short
            # Check if position matches underlying and right
            pos_underlying = self._get_position_underlying(pos)
            pos_right = self._get_position_right(pos)
            if pos_underlying == symbol and pos_right == right:
                return True
        return False

    def _get_assigned_shares(self) -> dict[str, int]:
        """Get symbols with assigned shares (qty >= 100) from portfolio.

        Returns:
            Dict mapping symbol to share quantity for symbols with >= 100 shares.
        """
        result: dict[str, int] = {}
        positions = self._portfolio_monitor.positions
        for _key, pos in positions.items():
            # Only stock positions
            if pos.asset_class not in ("STK", "equity"):
                continue
            qty = int(pos.quantity)
            if qty >= 100:
                result[pos.symbol] = qty
        return result

    def _calculate_cash_required(self, strike: Decimal, quantity: int) -> Decimal:
        """Calculate cash required to secure a put position.

        Args:
            strike: The put strike price.
            quantity: Number of contracts.

        Returns:
            Cash required = strike * 100 * quantity.
        """
        return strike * Decimal("100") * Decimal(str(quantity))

    def _get_position_expiration(self, pos) -> date | None:
        """Extract expiration date from a position.

        Attempts to parse from position symbol.
        Convention: option symbols contain YYYYMMDD expiration.
        """
        symbol = pos.symbol
        match = re.search(r"(\d{8})", symbol)
        if match:
            date_str = match.group(1)
            try:
                return date(int(date_str[:4]), int(date_str[4:6]), int(date_str[6:8]))
            except ValueError:
                pass
        return None

    def _get_position_right(self, pos) -> str | None:
        """Extract option right (P/C) from a position symbol."""
        symbol = pos.symbol.upper()
        match = re.search(r"\d{8}([PC])", symbol)
        if match:
            return match.group(1)
        return None

    def _get_position_underlying(self, pos) -> str:
        """Extract underlying symbol from a position.

        For option positions, strips the option-specific suffix.
        For stock positions, returns the symbol as-is.
        """
        symbol = pos.symbol
        match = re.match(r"([A-Z]+)\d{8}", symbol)
        if match:
            return match.group(1)
        return symbol
