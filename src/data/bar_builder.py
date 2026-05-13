"""OHLCV bar builder that aggregates ticks into bars at configurable timeframes."""

from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum

import structlog

logger = structlog.get_logger(__name__)


class Timeframe(Enum):
    """Supported bar timeframes for aggregation."""

    TICK = "tick"
    ONE_MIN = "1min"
    FIVE_MIN = "5min"
    FIFTEEN_MIN = "15min"
    ONE_HOUR = "1hour"
    DAILY = "1day"
    WEEKLY = "1week"


# Mapping from timeframe to duration in seconds (TICK has no duration)
TIMEFRAME_SECONDS: dict[Timeframe, int] = {
    Timeframe.ONE_MIN: 60,
    Timeframe.FIVE_MIN: 300,
    Timeframe.FIFTEEN_MIN: 900,
    Timeframe.ONE_HOUR: 3600,
    Timeframe.DAILY: 86400,
    Timeframe.WEEKLY: 604800,
}


@dataclass
class Bar:
    """OHLCV bar representing price action over a timeframe."""

    symbol: str
    timeframe: Timeframe
    open: float
    high: float
    low: float
    close: float
    volume: float
    timestamp: datetime


@dataclass
class BarBuilder:
    """Aggregates incoming ticks into OHLCV bars based on time boundaries.

    For TICK timeframe, each tick is emitted as its own bar.
    For time-based timeframes, ticks are accumulated until the time boundary
    is crossed, at which point the completed bar is emitted.
    """

    symbol: str
    timeframe: Timeframe
    on_bar_complete: Callable[[Bar], None] | None = None

    # Internal state
    _open: float | None = field(default=None, init=False, repr=False)
    _high: float | None = field(default=None, init=False, repr=False)
    _low: float | None = field(default=None, init=False, repr=False)
    _close: float | None = field(default=None, init=False, repr=False)
    _volume: float = field(default=0.0, init=False, repr=False)
    _bar_start: float | None = field(default=None, init=False, repr=False)
    _completed_bars: deque[Bar] = field(
        default_factory=lambda: deque(maxlen=1000), init=False, repr=False
    )

    def _get_bar_boundary(self, tick_time: float) -> float:
        """Calculate the start of the current bar period for a given timestamp."""
        if self.timeframe == Timeframe.TICK:
            return tick_time
        seconds = TIMEFRAME_SECONDS[self.timeframe]
        return (tick_time // seconds) * seconds

    def on_tick(self, price: float, volume: float, tick_time: float | None = None) -> Bar | None:
        """Process an incoming tick and return a completed bar if boundary crossed.

        Args:
            price: The tick price.
            volume: The tick volume (can be 0 for quote ticks).
            tick_time: Unix timestamp of the tick. Defaults to current time.

        Returns:
            A completed Bar if a time boundary was crossed, otherwise None.
        """
        if tick_time is None:
            tick_time = time.time()

        # TICK timeframe: every tick is a bar
        if self.timeframe == Timeframe.TICK:
            bar = Bar(
                symbol=self.symbol,
                timeframe=self.timeframe,
                open=price,
                high=price,
                low=price,
                close=price,
                volume=volume,
                timestamp=datetime.fromtimestamp(tick_time, tz=UTC),
            )
            self._completed_bars.append(bar)
            if self.on_bar_complete:
                self.on_bar_complete(bar)
            return bar

        current_boundary = self._get_bar_boundary(tick_time)

        # First tick ever or new bar period
        if self._bar_start is None:
            self._bar_start = current_boundary
            self._open = price
            self._high = price
            self._low = price
            self._close = price
            self._volume = volume
            return None

        # Check if we crossed into a new bar period
        if current_boundary > self._bar_start:
            # Complete the current bar
            completed_bar = Bar(
                symbol=self.symbol,
                timeframe=self.timeframe,
                open=self._open,  # type: ignore[arg-type]
                high=self._high,  # type: ignore[arg-type]
                low=self._low,  # type: ignore[arg-type]
                close=self._close,  # type: ignore[arg-type]
                volume=self._volume,
                timestamp=datetime.fromtimestamp(self._bar_start, tz=UTC),
            )
            self._completed_bars.append(completed_bar)
            if self.on_bar_complete:
                self.on_bar_complete(completed_bar)

            # Start new bar
            self._bar_start = current_boundary
            self._open = price
            self._high = price
            self._low = price
            self._close = price
            self._volume = volume

            return completed_bar

        # Same bar period — update OHLCV
        if price > self._high:  # type: ignore[operator]
            self._high = price
        if price < self._low:  # type: ignore[operator]
            self._low = price
        self._close = price
        self._volume += volume

        return None

    @property
    def current_bar(self) -> Bar | None:
        """Return the in-progress bar (not yet completed), or None if no ticks received."""
        if self._open is None or self._bar_start is None:
            return None
        return Bar(
            symbol=self.symbol,
            timeframe=self.timeframe,
            open=self._open,
            high=self._high,  # type: ignore[arg-type]
            low=self._low,  # type: ignore[arg-type]
            close=self._close,  # type: ignore[arg-type]
            volume=self._volume,
            timestamp=datetime.fromtimestamp(self._bar_start, tz=UTC),
        )

    @property
    def completed_bars(self) -> list[Bar]:
        """Return all completed bars accumulated by this builder."""
        return list(self._completed_bars)

    def get_latest_completed_bar(self) -> Bar | None:
        """Return the most recently completed bar, or None."""
        if self._completed_bars:
            return self._completed_bars[-1]
        return None

    def get_history(self, periods: int) -> list[Bar]:
        """Return the last N completed bars.

        Args:
            periods: Number of bars to return.

        Returns:
            List of bars, most recent last. May be shorter than periods
            if fewer bars have been completed.
        """
        return list(self._completed_bars)[-periods:]

    def reset(self) -> None:
        """Reset the builder state, clearing all accumulated bars."""
        self._open = None
        self._high = None
        self._low = None
        self._close = None
        self._volume = 0.0
        self._bar_start = None
        self._completed_bars = deque(maxlen=1000)
