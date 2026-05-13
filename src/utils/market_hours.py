"""Shared market hours utility.

Single source of truth for NYSE market hours checks used by both
the strategy engine and the stale data monitor.
"""

from __future__ import annotations

from datetime import datetime, time

# NYSE market hours in Eastern Time (ET)
MARKET_OPEN = time(9, 30)
MARKET_CLOSE = time(16, 0)


def is_market_open() -> bool:
    """Check if NYSE is currently open (9:30-16:00 ET, weekdays only).

    Uses zoneinfo for correct EST/EDT handling year-round.
    Does not account for market holidays.

    Returns:
        True if the market is currently open, False otherwise.
    """
    try:
        import zoneinfo

        et = zoneinfo.ZoneInfo("America/New_York")
    except (ImportError, KeyError):
        # Fallback: assume UTC-5 (EST) if zoneinfo unavailable
        from datetime import timedelta
        from datetime import timezone as tz

        et = tz(timedelta(hours=-5))

    now_et = datetime.now(et)

    # Weekday check: Monday=0, Friday=4
    if now_et.weekday() > 4:
        return False

    current_time = now_et.time()
    return MARKET_OPEN <= current_time < MARKET_CLOSE
