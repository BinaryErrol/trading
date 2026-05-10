"""Options contract builder for converting signal params to ib_async contracts.

Provides a utility function to build an ib_async.Option contract from
OptionSignalParams, configured for SMART exchange routing.
"""

from __future__ import annotations

from typing import Any

from src.strategies.signals import OptionSignalParams


def build_option_contract(params: OptionSignalParams) -> Any:
    """Build an ib_async Option contract from signal params.

    Constructs an Option contract configured for SMART exchange routing,
    suitable for submission to OrderManager.

    Args:
        params: OptionSignalParams containing underlying, strike, expiration,
                and right (P/C).

    Returns:
        An ib_async.Option contract instance.
    """
    from ib_async import Option

    return Option(
        symbol=params.underlying,
        lastTradeDateOrExpiry=params.expiration.strftime("%Y%m%d"),
        strike=float(params.strike),
        right=params.right,
        exchange="SMART",
    )
