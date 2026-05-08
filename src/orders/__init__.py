"""Order management module for IBKR trading bot.

Provides order lifecycle management, rate limiting, and state tracking.
Signal and SignalDirection are re-exported from src.strategies.signals for
backward compatibility.
"""

from src.orders.manager import (
    ManagedOrder,
    OrderManager,
    OrderStatus,
)
from src.orders.rate_limiter import RateLimiter
from src.strategies.signals import OrderType, Signal, SignalDirection

__all__ = [
    "ManagedOrder",
    "OrderManager",
    "OrderStatus",
    "OrderType",
    "RateLimiter",
    "Signal",
    "SignalDirection",
]
