"""Options Chain Provider — fetches and caches option chains from IBKR.

Provides OptionContract dataclass and OptionsChainProvider class for
retrieving option chain data including greeks via ib_async.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Any, Protocol

import structlog

logger = structlog.get_logger(__name__)


class ConnectionProtocol(Protocol):
    """Protocol for the IBKR connection interface used by OptionsChainProvider.

    Mirrors the minimal interface needed from ib_async.IB to keep this module
    testable without importing the full ib_async dependency in tests.
    """

    async def reqSecDefOptParams(
        self,
        underlyingSymbol: str,
        futFopExchange: str,
        underlyingSecType: str,
        underlyingConId: int,
    ) -> list[Any]: ...

    async def reqTickers(self, *contracts: Any) -> list[Any]: ...

    async def qualifyContracts(self, *contracts: Any) -> list[Any]: ...


@dataclass
class OptionContract:
    """Represents a single option contract with greeks and pricing."""

    symbol: str
    underlying: str
    strike: Decimal
    expiration: date
    right: str  # "P" or "C"
    delta: float
    gamma: float
    theta: float
    vega: float
    implied_vol: float
    bid: Decimal
    ask: Decimal
    mid: Decimal


class OptionsChainProvider:
    """Fetches and caches option chains from IBKR.

    Uses ib_async.reqSecDefOptParams to discover available expirations/strikes,
    then ib_async.reqTickers on filtered contracts to get live greeks.
    Results are cached with a configurable TTL (default 5 minutes).
    """

    def __init__(self, connection: ConnectionProtocol, cache_ttl: int = 300) -> None:
        """Initialize the OptionsChainProvider.

        Args:
            connection: An object implementing ConnectionProtocol (typically ib_async.IB).
            cache_ttl: Cache time-to-live in seconds (default 300 = 5 minutes).
        """
        self._connection = connection
        self._cache_ttl = cache_ttl
        # Cache structure: {cache_key: (timestamp, list[OptionContract])}
        self._cache: dict[str, tuple[float, list[OptionContract]]] = {}
        # VIX cache: (timestamp, value)
        self._vix_cache: tuple[float, float] | None = None

    async def get_chain(
        self,
        underlying: str,
        right: str = "P",
        min_dte: int = 30,
        max_dte: int = 45,
    ) -> list[OptionContract]:
        """Fetch option chain for an underlying filtered by right and DTE range.

        Args:
            underlying: The underlying ticker symbol (e.g. "AAPL").
            right: Option right - "P" for puts, "C" for calls.
            min_dte: Minimum days to expiration (inclusive).
            max_dte: Maximum days to expiration (inclusive).

        Returns:
            List of OptionContract objects with greeks populated.
        """
        cache_key = f"{underlying}:{right}:{min_dte}:{max_dte}"

        # Check cache
        cached = self._cache.get(cache_key)
        if cached is not None:
            cache_time, contracts = cached
            if time.time() - cache_time < self._cache_ttl:
                logger.debug("options_chain_cache_hit", underlying=underlying, right=right)
                return contracts

        logger.info(
            "fetching_options_chain",
            underlying=underlying,
            right=right,
            min_dte=min_dte,
            max_dte=max_dte,
        )

        try:
            contracts = await self._fetch_chain(underlying, right, min_dte, max_dte)
            self._cache[cache_key] = (time.time(), contracts)
            return contracts
        except Exception as exc:
            logger.warning(
                "options_chain_fetch_error",
                underlying=underlying,
                error=str(exc),
            )
            # Return stale cache if available
            if cached is not None:
                logger.info("returning_stale_cache", underlying=underlying)
                return cached[1]
            return []

    async def _fetch_chain(
        self,
        underlying: str,
        right: str,
        min_dte: int,
        max_dte: int,
    ) -> list[OptionContract]:
        """Internal method to fetch chain from IBKR.

        Discovers available expirations/strikes via reqSecDefOptParams,
        filters by DTE range, then fetches greeks via reqTickers.
        """
        # Get option parameters (available expirations and strikes)
        opt_params_list = await self._connection.reqSecDefOptParams(
            underlyingSymbol=underlying,
            futFopExchange="",
            underlyingSecType="STK",
            underlyingConId=0,
        )

        if not opt_params_list:
            logger.info("no_option_params", underlying=underlying)
            return []

        today = date.today()
        min_exp = today + timedelta(days=min_dte)
        max_exp = today + timedelta(days=max_dte)

        # Collect valid expirations and strikes from all param sets
        valid_expirations: set[str] = set()
        valid_strikes: set[float] = set()

        for opt_params in opt_params_list:
            # opt_params has .expirations (set of str "YYYYMMDD") and .strikes (set of float)
            expirations = getattr(opt_params, "expirations", set())
            strikes = getattr(opt_params, "strikes", set())

            for exp_str in expirations:
                try:
                    exp_date = _parse_expiration(exp_str)
                    if min_exp <= exp_date <= max_exp:
                        valid_expirations.add(exp_str)
                except ValueError:
                    continue

            valid_strikes.update(strikes)

        if not valid_expirations or not valid_strikes:
            logger.info(
                "no_valid_contracts_in_dte_range",
                underlying=underlying,
                min_dte=min_dte,
                max_dte=max_dte,
            )
            return []

        # Build option contracts for reqTickers
        option_contracts = []
        try:
            from ib_async import Option
        except ImportError:
            logger.error("ib_async_not_available")
            return []

        for exp_str in sorted(valid_expirations):
            for strike in sorted(valid_strikes):
                contract = Option(
                    symbol=underlying,
                    lastTradeDateOrExpiry=exp_str,
                    strike=strike,
                    right=right,
                    exchange="SMART",
                )
                option_contracts.append(contract)

        if not option_contracts:
            return []

        # Qualify contracts to get conIds
        qualified = await self._connection.qualifyContracts(*option_contracts)

        # Fetch tickers with greeks
        if not qualified:
            return []

        tickers = await self._connection.reqTickers(*qualified)

        # Build OptionContract dataclass instances
        result: list[OptionContract] = []
        for ticker in tickers:
            contract = ticker.contract
            greeks = getattr(ticker, "modelGreeks", None) or getattr(ticker, "lastGreeks", None)

            if greeks is None:
                continue

            delta = getattr(greeks, "delta", None)
            if delta is None:
                continue

            exp_date = _parse_expiration(contract.lastTradeDateOrExpiry)
            bid = Decimal(str(ticker.bid)) if ticker.bid and ticker.bid > 0 else Decimal("0")
            ask = Decimal(str(ticker.ask)) if ticker.ask and ticker.ask > 0 else Decimal("0")
            mid = (bid + ask) / 2 if bid > 0 and ask > 0 else Decimal("0")

            result.append(
                OptionContract(
                    symbol=f"{underlying}{contract.lastTradeDateOrExpiry}{right}{contract.strike}",
                    underlying=underlying,
                    strike=Decimal(str(contract.strike)),
                    expiration=exp_date,
                    right=right,
                    delta=float(greeks.delta) if greeks.delta is not None else 0.0,
                    gamma=float(greeks.gamma) if greeks.gamma is not None else 0.0,
                    theta=float(greeks.theta) if greeks.theta is not None else 0.0,
                    vega=float(greeks.vega) if greeks.vega is not None else 0.0,
                    implied_vol=float(greeks.impliedVol) if greeks.impliedVol is not None else 0.0,
                    bid=bid,
                    ask=ask,
                    mid=mid,
                )
            )

        logger.info(
            "options_chain_fetched",
            underlying=underlying,
            right=right,
            contracts_count=len(result),
        )
        return result

    async def get_vix(self) -> float | None:
        """Fetch the current VIX index level.

        Returns:
            The current VIX level as a float, or None if unavailable.
        """
        # Check cache
        if self._vix_cache is not None:
            cache_time, value = self._vix_cache
            if time.time() - cache_time < self._cache_ttl:
                return value

        try:
            from ib_async import Index

            vix_contract = Index("VIX", "CBOE")
            qualified = await self._connection.qualifyContracts(vix_contract)
            if not qualified:
                logger.warning("vix_contract_qualification_failed")
                return self._vix_cache[1] if self._vix_cache else None

            tickers = await self._connection.reqTickers(*qualified)
            if tickers and tickers[0].last:
                vix_value = float(tickers[0].last)
                self._vix_cache = (time.time(), vix_value)
                logger.debug("vix_fetched", value=vix_value)
                return vix_value
            elif tickers and tickers[0].close:
                vix_value = float(tickers[0].close)
                self._vix_cache = (time.time(), vix_value)
                logger.debug("vix_fetched_from_close", value=vix_value)
                return vix_value

            logger.warning("vix_no_price_data")
            return self._vix_cache[1] if self._vix_cache else None

        except ImportError:
            logger.error("ib_async_not_available_for_vix")
            return self._vix_cache[1] if self._vix_cache else None
        except Exception as exc:
            logger.warning("vix_fetch_error", error=str(exc))
            return self._vix_cache[1] if self._vix_cache else None

    def invalidate_cache(self, underlying: str) -> None:
        """Invalidate all cached chain data for a given underlying.

        Args:
            underlying: The underlying ticker symbol to invalidate.
        """
        keys_to_remove = [key for key in self._cache if key.startswith(f"{underlying}:")]
        for key in keys_to_remove:
            del self._cache[key]
        logger.debug("cache_invalidated", underlying=underlying, keys_removed=len(keys_to_remove))


def _parse_expiration(exp_str: str) -> date:
    """Parse an expiration string in YYYYMMDD format to a date object."""
    return date(int(exp_str[:4]), int(exp_str[4:6]), int(exp_str[6:8]))
