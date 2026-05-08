"""Alert service with rate limiting and configurable channel routing.

Provides notification delivery via email, Slack, and generic HTTP webhooks.
Rate limiting prevents notification flooding (max 1 per event type per minute),
with a bypass for critical alerts.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any

import structlog

from src.config.settings import AlertConfig

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class AlertEventType(Enum):
    """Types of events that can trigger alerts."""

    TRADE_EXECUTED = "trade_executed"
    RISK_BREACH = "risk_breach"
    CONNECTION_LOST = "connection_lost"
    ERROR = "error"
    DAILY_REPORT = "daily_report"
    STRATEGY_HALTED = "strategy_halted"
    DRAWDOWN_WARNING = "drawdown_warning"


class AlertPriority(Enum):
    """Priority levels for alerts."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


# ---------------------------------------------------------------------------
# Alert dataclass
# ---------------------------------------------------------------------------


@dataclass
class Alert:
    """Represents a notification to be delivered through configured channels."""

    event_type: AlertEventType
    priority: AlertPriority
    title: str
    message: str
    metadata: dict[str, Any] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))


# ---------------------------------------------------------------------------
# Abstract channel
# ---------------------------------------------------------------------------


class AlertChannel(ABC):
    """Abstract base class for notification channels."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Return the channel name identifier."""

    @abstractmethod
    async def deliver(self, alert: Alert) -> bool:
        """Deliver an alert through this channel.

        Returns True if delivery succeeded, False otherwise.
        """


# ---------------------------------------------------------------------------
# Rate limiter
# ---------------------------------------------------------------------------


class AlertRateLimiter:
    """Rate limiter that allows max 1 notification per event type per minute.

    Critical alerts bypass rate limiting entirely.
    """

    def __init__(self, max_per_type_per_minute: int = 1) -> None:
        self._max_per_minute = max_per_type_per_minute
        # Maps event_type -> list of timestamps (epoch seconds) of recent sends
        self._send_times: dict[str, list[float]] = {}

    def is_allowed(self, event_type: AlertEventType) -> bool:
        """Check if sending an alert for this event type is allowed."""
        now = time.time()
        key = event_type.value

        if key not in self._send_times:
            self._send_times[key] = []

        # Remove entries older than 60 seconds
        self._send_times[key] = [
            t for t in self._send_times[key] if now - t < 60.0
        ]

        return len(self._send_times[key]) < self._max_per_minute

    def record_send(self, event_type: AlertEventType) -> None:
        """Record that an alert was sent for this event type."""
        now = time.time()
        key = event_type.value

        if key not in self._send_times:
            self._send_times[key] = []

        self._send_times[key].append(now)

    def reset(self) -> None:
        """Clear all rate limit state."""
        self._send_times.clear()


# ---------------------------------------------------------------------------
# Alert Service
# ---------------------------------------------------------------------------


class AlertService:
    """Routes alerts to configured notification channels with rate limiting.

    Supports configurable event-to-channel routing via AlertConfig.routing.
    Rate limits non-critical alerts to prevent notification flooding.
    """

    def __init__(self, config: AlertConfig) -> None:
        self._config = config
        self._channels: dict[str, AlertChannel] = {}
        self._rate_limiter = AlertRateLimiter(max_per_type_per_minute=1)

    def register_channel(self, channel: AlertChannel) -> None:
        """Register a notification channel."""
        self._channels[channel.name] = channel
        logger.info("alert_channel_registered", channel=channel.name)

    def _get_channels_for_event(self, event_type: AlertEventType) -> list[AlertChannel]:
        """Get the list of channels configured for a given event type."""
        channel_names = self._config.routing.get(event_type.value, [])
        channels = []
        for name in channel_names:
            if name in self._channels:
                channels.append(self._channels[name])
        return channels

    async def send(self, alert: Alert) -> None:
        """Send alert through configured channels based on event type.

        Applies rate limiting — max 1 notification per event type per minute.
        Critical alerts should use send_critical() instead.
        """
        if not self._rate_limiter.is_allowed(alert.event_type):
            logger.debug(
                "alert_rate_limited",
                event_type=alert.event_type.value,
                title=alert.title,
            )
            return

        channels = self._get_channels_for_event(alert.event_type)
        if not channels:
            logger.debug(
                "alert_no_channels_configured",
                event_type=alert.event_type.value,
            )
            return

        self._rate_limiter.record_send(alert.event_type)

        delivered_channels: list[str] = []
        for channel in channels:
            try:
                success = await channel.deliver(alert)
                if success:
                    delivered_channels.append(channel.name)
            except Exception:
                logger.exception(
                    "alert_delivery_failed",
                    channel=channel.name,
                    event_type=alert.event_type.value,
                )

        logger.info(
            "alert_sent",
            event_type=alert.event_type.value,
            priority=alert.priority.value,
            title=alert.title,
            delivered_to=delivered_channels,
        )

    async def send_critical(self, alert: Alert) -> None:
        """Send critical alert immediately, bypassing rate limits.

        All configured channels for the event type receive the alert
        regardless of rate limiting state.
        """
        channels = self._get_channels_for_event(alert.event_type)
        if not channels:
            # For critical alerts, try all registered channels as fallback
            channels = list(self._channels.values())

        delivered_channels: list[str] = []
        for channel in channels:
            try:
                success = await channel.deliver(alert)
                if success:
                    delivered_channels.append(channel.name)
            except Exception:
                logger.exception(
                    "critical_alert_delivery_failed",
                    channel=channel.name,
                    event_type=alert.event_type.value,
                )

        logger.warning(
            "critical_alert_sent",
            event_type=alert.event_type.value,
            title=alert.title,
            delivered_to=delivered_channels,
        )
