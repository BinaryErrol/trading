"""Market Data Hub — aggregates real-time market data, builds bars, detects stale data."""

from __future__ import annotations

import time
from typing import Any, Protocol

import structlog

from src.data.bar_builder import Bar, BarBuilder, Timeframe

logger = structlog.get_logger(__name__)


# Minimal protocol for ConnectionManager to keep this module testable without
# importing the full ib_async dependency in tests.
class ConnectionManagerProtocol(Protocol):
    """Protocol for the connection manager interface used by MarketDataHub."""

    def subscribe_market_data(self, contract: Any) -> Any: ...


# Minimal protocol for Redis client (async or sync)
class RedisCacheProtocol(Protocol):
    """Protocol for Redis cache operations used by MarketDataHub."""

    def set(self, key: str, value: str, ex: int | None = None) -> Any: ...
    def get(self, key: str) -> Any: ...


class MarketDataHub:
    """Central hub for real-time market data aggregation and distribution.

    Responsibilities:
    - Subscribe to market data for symbols via ConnectionManager
    - Aggregate ticks into OHLCV bars at multiple timeframes
    - Cache latest data in Redis for fast access
    - Detect stale data conditions
    """

    # All timeframes that bar builders are created for
    BAR_TIMEFRAMES: list[Timeframe] = [
        Timeframe.TICK,
        Timeframe.ONE_MIN,
        Timeframe.FIVE_MIN,
        Timeframe.FIFTEEN_MIN,
        Timeframe.ONE_HOUR,
        Timeframe.DAILY,
        Timeframe.WEEKLY,
    ]

    def __init__(
        self,
        connection: ConnectionManagerProtocol,
        redis: RedisCacheProtocol | None = None,
        stale_threshold_seconds: float = 60.0,
    ):
        self._connection = connection
        self._redis = redis
        self._stale_threshold = stale_threshold_seconds

        # symbol -> {timeframe -> BarBuilder}
        self._bar_builders: dict[str, dict[Timeframe, BarBuilder]] = {}
        # symbol -> last tick timestamp (unix)
        self._last_tick_time: dict[str, float] = {}
        # symbol -> subscription ticker object
        self._subscriptions: dict[str, Any] = {}

    def subscribe(self, symbol: str, asset_class: str = "STK") -> None:
        """Subscribe to market data for a symbol via ConnectionManager.

        Creates bar builders for all supported timeframes.

        Args:
            symbol: The ticker symbol (e.g. "AAPL").
            asset_class: The asset class (STK, OPT, FUT, FOREX). Default STK.
        """
        if symbol in self._subscriptions:
            logger.debug("already_subscribed", symbol=symbol)
            return

        # Create a contract-like object for the connection manager
        # In production this would be an ib_async Contract
        contract = _make_contract(symbol, asset_class)
        ticker = self._connection.subscribe_market_data(contract)
        self._subscriptions[symbol] = ticker

        # Initialize bar builders for all timeframes
        self._bar_builders[symbol] = {}
        for tf in self.BAR_TIMEFRAMES:
            self._bar_builders[symbol][tf] = BarBuilder(symbol=symbol, timeframe=tf)

        logger.info("subscribed_market_data", symbol=symbol, asset_class=asset_class)

    def on_tick(
        self,
        symbol: str,
        price: float,
        volume: float = 0.0,
        tick_time: float | None = None,
    ) -> list[Bar]:
        """Process an incoming tick, update all bar builders, cache in Redis.

        Args:
            symbol: The ticker symbol.
            price: Tick price.
            volume: Tick volume (default 0).
            tick_time: Unix timestamp of the tick. Defaults to current time.

        Returns:
            List of completed bars (if any timeframe boundaries were crossed).
        """
        if tick_time is None:
            tick_time = time.time()

        self._last_tick_time[symbol] = tick_time

        if symbol not in self._bar_builders:
            logger.warning("tick_for_unsubscribed_symbol", symbol=symbol)
            return []

        completed_bars: list[Bar] = []
        for tf, builder in self._bar_builders[symbol].items():
            bar = builder.on_tick(price, volume, tick_time)
            if bar is not None:
                completed_bars.append(bar)

        # Cache latest price in Redis
        if self._redis is not None:
            try:
                self._redis.set(
                    f"market:{symbol}:last_price",
                    str(price),
                    ex=300,  # 5 min TTL
                )
            except Exception as exc:
                logger.warning("redis_cache_error", symbol=symbol, error=str(exc))

        return completed_bars

    def get_latest_bar(self, symbol: str, timeframe: Timeframe) -> Bar | None:
        """Return the latest completed bar for a symbol/timeframe.

        Args:
            symbol: The ticker symbol.
            timeframe: The desired timeframe.

        Returns:
            The most recently completed bar, or None if no bars completed yet.
        """
        builders = self._bar_builders.get(symbol)
        if builders is None:
            return None
        builder = builders.get(timeframe)
        if builder is None:
            return None
        return builder.get_latest_completed_bar()

    def get_history(self, symbol: str, timeframe: Timeframe, periods: int = 20) -> list[Bar]:
        """Return N historical completed bars for a symbol/timeframe.

        Args:
            symbol: The ticker symbol.
            timeframe: The desired timeframe.
            periods: Number of bars to return (default 20).

        Returns:
            List of completed bars, most recent last. May be shorter than
            periods if fewer bars have been completed.
        """
        builders = self._bar_builders.get(symbol)
        if builders is None:
            return []
        builder = builders.get(timeframe)
        if builder is None:
            return []
        return builder.get_history(periods)

    def _detect_stale_data(self, symbol: str) -> bool:
        """Check if market data for a symbol is stale.

        Data is considered stale if no ticks have been received within
        the configured threshold (default 60 seconds).

        Args:
            symbol: The ticker symbol to check.

        Returns:
            True if data is stale (no recent updates), False otherwise.
        """
        last_tick = self._last_tick_time.get(symbol)
        if last_tick is None:
            # Never received a tick — considered stale
            return True

        elapsed = time.time() - last_tick
        is_stale = elapsed > self._stale_threshold

        if is_stale:
            logger.warning(
                "stale_data_detected",
                symbol=symbol,
                seconds_since_last_tick=round(elapsed, 1),
                threshold=self._stale_threshold,
            )

        return is_stale

    @property
    def subscribed_symbols(self) -> list[str]:
        """Return list of currently subscribed symbols."""
        return list(self._subscriptions.keys())

    def subscribe_qualified(
        self, symbol: str, ticker: Any
    ) -> None:
        """Register a pre-qualified ticker subscription.

        Used when the caller has already qualified the contract with IBKR
        and obtained a ticker via reqMktData. Creates bar builders for all
        supported timeframes.

        Args:
            symbol: The ticker symbol.
            ticker: The IBKR Ticker object from reqMktData.
        """
        self._subscriptions[symbol] = ticker
        if symbol not in self._bar_builders:
            self._bar_builders[symbol] = {}
            for tf in self.BAR_TIMEFRAMES:
                self._bar_builders[symbol][tf] = BarBuilder(
                    symbol=symbol, timeframe=tf
                )
        logger.info("subscribed_qualified", symbol=symbol)


def _make_contract(symbol: str, asset_class: str) -> Any:
    """Create a minimal contract object for subscription.

    In production, this would create an ib_async Contract.
    Here we use a simple namespace for testability.
    """
    try:
        from ib_async import Contract, Crypto, Forex

        if asset_class.upper() == "FOREX":
            return Forex(symbol)
        if asset_class.upper() == "CRYPTO":
            return Crypto(symbol, "PAXOS", "USD")
        contract = Contract()
        contract.symbol = symbol
        contract.secType = asset_class.upper()
        contract.exchange = "SMART"
        contract.currency = "USD"
        return contract
    except ImportError:
        # Fallback for testing without ib_async
        class _SimpleContract:
            def __init__(self, sym: str, sec_type: str):
                self.symbol = sym
                self.secType = sec_type
                self.exchange = "PAXOS" if sec_type == "CRYPTO" else "SMART"
                self.currency = "USD"

        return _SimpleContract(symbol, asset_class.upper())
