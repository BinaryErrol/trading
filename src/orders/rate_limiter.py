"""Token bucket rate limiter for IBKR message rate compliance.

IBKR enforces a 50 messages/second limit. We use 45 msg/sec as a buffer
to avoid hitting the hard limit and getting disconnected.
"""

import asyncio
import time

import structlog

logger = structlog.get_logger(__name__)


class RateLimiter:
    """Token bucket rate limiter.

    Maintains a bucket of tokens that refills at a constant rate.
    Each message consumes one token. If no tokens are available,
    callers must wait until a token is replenished.

    Args:
        max_per_second: Maximum messages per second (default 45, below IBKR's 50 limit).
        burst_size: Maximum burst capacity. Defaults to max_per_second.
    """

    def __init__(self, max_per_second: float = 45.0, burst_size: int | None = None):
        self._max_per_second = max_per_second
        self._burst_size = burst_size if burst_size is not None else int(max_per_second)
        self._tokens: float = float(self._burst_size)
        self._last_refill: float = time.monotonic()
        self._lock = asyncio.Lock()

    @property
    def max_per_second(self) -> float:
        """Maximum messages allowed per second."""
        return self._max_per_second

    @property
    def available_tokens(self) -> float:
        """Current number of available tokens (approximate, without lock)."""
        self._refill()
        return self._tokens

    def _refill(self) -> None:
        """Refill tokens based on elapsed time since last refill."""
        now = time.monotonic()
        elapsed = now - self._last_refill
        new_tokens = elapsed * self._max_per_second
        self._tokens = min(self._tokens + new_tokens, float(self._burst_size))
        self._last_refill = now

    def try_acquire(self) -> bool:
        """Try to acquire a token without waiting.

        Returns:
            True if a token was acquired, False if rate limit would be exceeded.
        """
        self._refill()
        if self._tokens >= 1.0:
            self._tokens -= 1.0
            return True
        return False

    async def acquire(self) -> None:
        """Acquire a token, waiting if necessary until one is available.

        This method is safe to call concurrently from multiple coroutines.
        """
        async with self._lock:
            self._refill()
            if self._tokens >= 1.0:
                self._tokens -= 1.0
                return

            # Calculate wait time for next token
            deficit = 1.0 - self._tokens
            wait_time = deficit / self._max_per_second
            logger.debug(
                "rate_limiter_waiting",
                wait_seconds=round(wait_time, 4),
                tokens_available=round(self._tokens, 2),
            )
            await asyncio.sleep(wait_time)
            self._refill()
            self._tokens -= 1.0

    def reset(self) -> None:
        """Reset the rate limiter to full capacity."""
        self._tokens = float(self._burst_size)
        self._last_refill = time.monotonic()
