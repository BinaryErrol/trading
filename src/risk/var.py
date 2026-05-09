"""Value at Risk (VaR) calculation using historical simulation.

Provides portfolio-level VaR estimation by computing the percentile of
historical returns at the specified confidence level.

VaR at 95% confidence = 5th percentile of historical return distribution.
"""

from __future__ import annotations

from decimal import Decimal

import numpy as np
import structlog

logger = structlog.get_logger(__name__)


def calculate_var(
    returns: list[float] | np.ndarray,
    confidence: float = 0.95,
    portfolio_value: float | Decimal | None = None,
) -> float:
    """Calculate Value at Risk using historical simulation.

    Sorts the historical returns and takes the percentile at (1 - confidence).
    For example, at 95% confidence, this is the 5th percentile of returns.

    The result is expressed as a positive loss amount (or fraction if
    portfolio_value is not provided).

    Args:
        returns: Array of historical daily returns (as decimals, e.g. -0.02 = -2%).
        confidence: Confidence level (e.g. 0.95 for 95% VaR).
        portfolio_value: If provided, VaR is scaled to dollar amount.

    Returns:
        VaR as a positive number. If portfolio_value is given, returns dollar
        VaR; otherwise returns the fractional VaR (e.g. 0.023 = 2.3% loss).

    Raises:
        ValueError: If returns is empty or confidence is not in (0, 1).
    """
    if confidence <= 0 or confidence >= 1:
        raise ValueError(f"confidence must be between 0 and 1 (exclusive), got {confidence}")

    arr = np.asarray(returns, dtype=np.float64)

    if arr.size == 0:
        raise ValueError("returns array must not be empty")

    # Remove NaN/Inf values
    arr = arr[np.isfinite(arr)]
    if arr.size == 0:
        return 0.0

    # VaR percentile: for 95% confidence, we want the 5th percentile
    percentile_level = (1 - confidence) * 100  # e.g. 5.0 for 95% confidence
    var_return = np.percentile(arr, percentile_level)

    # VaR is expressed as a positive loss
    var_value = -float(var_return)

    if portfolio_value is not None:
        pv = float(portfolio_value) if isinstance(portfolio_value, Decimal) else portfolio_value
        var_value = var_value * pv

    logger.debug(
        "var_calculated",
        confidence=confidence,
        percentile_level=percentile_level,
        var_return=float(var_return),
        var_value=var_value,
        num_observations=arr.size,
    )

    return var_value


def check_var_limit(
    returns: list[float] | np.ndarray,
    confidence: float,
    portfolio_value: float | Decimal,
    var_limit: float | Decimal,
) -> tuple[bool, float]:
    """Check if portfolio VaR exceeds the configured limit.

    Args:
        returns: Historical daily returns.
        confidence: VaR confidence level (e.g. 0.95).
        portfolio_value: Current portfolio value.
        var_limit: Maximum acceptable VaR as a dollar amount.

    Returns:
        Tuple of (within_limit, var_value):
        - within_limit: True if VaR is at or below the limit.
        - var_value: The computed VaR dollar amount.
    """
    var_value = calculate_var(
        returns=returns,
        confidence=confidence,
        portfolio_value=portfolio_value,
    )

    limit = float(var_limit) if isinstance(var_limit, Decimal) else var_limit
    within_limit = var_value <= limit

    if not within_limit:
        logger.warning(
            "var_limit_breached",
            var_value=var_value,
            var_limit=limit,
            confidence=confidence,
        )

    return within_limit, var_value
