"""Unit tests for the Alert Service.

Tests cover:
- Rate limiting (max 1 per event type per minute, except critical)
- Channel routing based on AlertConfig.routing
- Critical alert bypass of rate limits
- Channel delivery mechanics
"""

from __future__ import annotations

import time
from datetime import UTC, datetime

import pytest

from src.alerts.service import (
    Alert,
    AlertChannel,
    AlertEventType,
    AlertPriority,
    AlertRateLimiter,
    AlertService,
)
from src.config.settings import AlertChannelsConfig, AlertConfig, SlackConfig, WebhookConfig

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class FakeChannel(AlertChannel):
    """A fake channel for testing that records delivered alerts."""

    def __init__(self, channel_name: str = "fake", should_succeed: bool = True) -> None:
        self._name = channel_name
        self._should_succeed = should_succeed
        self.delivered: list[Alert] = []

    @property
    def name(self) -> str:
        return self._name

    async def deliver(self, alert: Alert) -> bool:
        self.delivered.append(alert)
        return self._should_succeed


class FailingChannel(AlertChannel):
    """A channel that raises an exception on delivery."""

    @property
    def name(self) -> str:
        return "failing"

    async def deliver(self, alert: Alert) -> bool:
        raise RuntimeError("Channel delivery failed")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def alert_config() -> AlertConfig:
    """Config with routing for trade_executed -> slack, risk_breach -> slack + email."""
    return AlertConfig(
        channels=AlertChannelsConfig(
            slack=SlackConfig(enabled=True, webhook_url="https://hooks.slack.com/test"),
            webhook=WebhookConfig(enabled=True, url="https://example.com/webhook"),
        ),
        routing={
            "trade_executed": ["slack"],
            "risk_breach": ["slack", "email"],
            "connection_lost": ["slack", "email", "webhook"],
            "error": ["slack"],
            "daily_report": ["email"],
        },
    )


@pytest.fixture
def alert_service(alert_config: AlertConfig) -> AlertService:
    """AlertService with fake channels registered."""
    service = AlertService(alert_config)
    return service


@pytest.fixture
def sample_alert() -> Alert:
    """A sample trade_executed alert."""
    return Alert(
        event_type=AlertEventType.TRADE_EXECUTED,
        priority=AlertPriority.MEDIUM,
        title="Trade Executed: AAPL",
        message="Bought 100 shares of AAPL at $150.00",
        metadata={"symbol": "AAPL", "quantity": 100, "price": 150.0},
        timestamp=datetime.now(UTC),
    )


# ---------------------------------------------------------------------------
# Rate Limiter Tests
# ---------------------------------------------------------------------------


class TestAlertRateLimiter:
    """Tests for AlertRateLimiter."""

    def test_first_send_is_allowed(self) -> None:
        limiter = AlertRateLimiter(max_per_type_per_minute=1)
        assert limiter.is_allowed(AlertEventType.TRADE_EXECUTED) is True

    def test_second_send_within_minute_is_blocked(self) -> None:
        limiter = AlertRateLimiter(max_per_type_per_minute=1)
        limiter.record_send(AlertEventType.TRADE_EXECUTED)
        assert limiter.is_allowed(AlertEventType.TRADE_EXECUTED) is False

    def test_different_event_types_are_independent(self) -> None:
        limiter = AlertRateLimiter(max_per_type_per_minute=1)
        limiter.record_send(AlertEventType.TRADE_EXECUTED)
        # Different event type should still be allowed
        assert limiter.is_allowed(AlertEventType.RISK_BREACH) is True

    def test_send_allowed_after_window_expires(self) -> None:
        limiter = AlertRateLimiter(max_per_type_per_minute=1)
        # Manually inject an old timestamp (61 seconds ago)
        old_time = time.time() - 61.0
        limiter._send_times[AlertEventType.TRADE_EXECUTED.value] = [old_time]
        assert limiter.is_allowed(AlertEventType.TRADE_EXECUTED) is True

    def test_reset_clears_all_state(self) -> None:
        limiter = AlertRateLimiter(max_per_type_per_minute=1)
        limiter.record_send(AlertEventType.TRADE_EXECUTED)
        limiter.record_send(AlertEventType.RISK_BREACH)
        limiter.reset()
        assert limiter.is_allowed(AlertEventType.TRADE_EXECUTED) is True
        assert limiter.is_allowed(AlertEventType.RISK_BREACH) is True

    def test_custom_max_per_minute(self) -> None:
        limiter = AlertRateLimiter(max_per_type_per_minute=3)
        limiter.record_send(AlertEventType.TRADE_EXECUTED)
        limiter.record_send(AlertEventType.TRADE_EXECUTED)
        assert limiter.is_allowed(AlertEventType.TRADE_EXECUTED) is True
        limiter.record_send(AlertEventType.TRADE_EXECUTED)
        assert limiter.is_allowed(AlertEventType.TRADE_EXECUTED) is False


# ---------------------------------------------------------------------------
# Channel Routing Tests
# ---------------------------------------------------------------------------


class TestAlertServiceRouting:
    """Tests for event-to-channel routing."""

    async def test_alert_routed_to_configured_channel(
        self, alert_service: AlertService, sample_alert: Alert
    ) -> None:
        slack_channel = FakeChannel("slack")
        alert_service.register_channel(slack_channel)

        await alert_service.send(sample_alert)

        assert len(slack_channel.delivered) == 1
        assert slack_channel.delivered[0].title == "Trade Executed: AAPL"

    async def test_alert_routed_to_multiple_channels(
        self, alert_service: AlertService
    ) -> None:
        slack_channel = FakeChannel("slack")
        email_channel = FakeChannel("email")
        alert_service.register_channel(slack_channel)
        alert_service.register_channel(email_channel)

        alert = Alert(
            event_type=AlertEventType.RISK_BREACH,
            priority=AlertPriority.HIGH,
            title="Risk Breach",
            message="Max drawdown exceeded",
        )
        await alert_service.send(alert)

        assert len(slack_channel.delivered) == 1
        assert len(email_channel.delivered) == 1

    async def test_alert_not_sent_to_unconfigured_channel(
        self, alert_service: AlertService
    ) -> None:
        webhook_channel = FakeChannel("webhook")
        alert_service.register_channel(webhook_channel)

        # trade_executed only routes to "slack", not "webhook"
        alert = Alert(
            event_type=AlertEventType.TRADE_EXECUTED,
            priority=AlertPriority.MEDIUM,
            title="Trade",
            message="Trade executed",
        )
        await alert_service.send(alert)

        assert len(webhook_channel.delivered) == 0

    async def test_no_delivery_when_no_channels_registered(
        self, alert_service: AlertService, sample_alert: Alert
    ) -> None:
        # No channels registered — should not raise
        await alert_service.send(sample_alert)

    async def test_channel_failure_does_not_block_others(
        self, alert_service: AlertService
    ) -> None:
        failing = FailingChannel()
        email_channel = FakeChannel("email")
        # Register failing channel as "slack" to match routing
        alert_service._channels["slack"] = failing
        alert_service.register_channel(email_channel)

        alert = Alert(
            event_type=AlertEventType.RISK_BREACH,
            priority=AlertPriority.HIGH,
            title="Risk Breach",
            message="Drawdown exceeded",
        )
        await alert_service.send(alert)

        # Email should still receive the alert despite slack failing
        assert len(email_channel.delivered) == 1


# ---------------------------------------------------------------------------
# Rate Limiting Integration Tests
# ---------------------------------------------------------------------------


class TestAlertServiceRateLimiting:
    """Tests for rate limiting within AlertService.send()."""

    async def test_second_alert_same_type_is_rate_limited(
        self, alert_service: AlertService, sample_alert: Alert
    ) -> None:
        slack_channel = FakeChannel("slack")
        alert_service.register_channel(slack_channel)

        await alert_service.send(sample_alert)
        await alert_service.send(sample_alert)

        # Only the first should be delivered
        assert len(slack_channel.delivered) == 1

    async def test_different_event_types_not_rate_limited(
        self, alert_service: AlertService
    ) -> None:
        slack_channel = FakeChannel("slack")
        alert_service.register_channel(slack_channel)

        alert1 = Alert(
            event_type=AlertEventType.TRADE_EXECUTED,
            priority=AlertPriority.MEDIUM,
            title="Trade 1",
            message="First trade",
        )
        alert2 = Alert(
            event_type=AlertEventType.ERROR,
            priority=AlertPriority.HIGH,
            title="Error",
            message="Something went wrong",
        )

        await alert_service.send(alert1)
        await alert_service.send(alert2)

        # Both should be delivered (different event types)
        assert len(slack_channel.delivered) == 2


# ---------------------------------------------------------------------------
# Critical Alert Tests
# ---------------------------------------------------------------------------


class TestAlertServiceCritical:
    """Tests for send_critical() bypassing rate limits."""

    async def test_critical_bypasses_rate_limit(
        self, alert_service: AlertService
    ) -> None:
        slack_channel = FakeChannel("slack")
        alert_service.register_channel(slack_channel)

        alert = Alert(
            event_type=AlertEventType.TRADE_EXECUTED,
            priority=AlertPriority.CRITICAL,
            title="Critical Trade",
            message="Critical alert",
        )

        # Send a normal alert first to trigger rate limit
        await alert_service.send(alert)
        assert len(slack_channel.delivered) == 1

        # Normal send should be blocked
        await alert_service.send(alert)
        assert len(slack_channel.delivered) == 1

        # Critical send should bypass rate limit
        await alert_service.send_critical(alert)
        assert len(slack_channel.delivered) == 2

    async def test_critical_uses_all_channels_when_no_routing(self) -> None:
        """Critical alerts fall back to all registered channels if no routing configured."""
        config = AlertConfig(
            channels=AlertChannelsConfig(),
            routing={},  # No routing configured
        )
        service = AlertService(config)
        slack_channel = FakeChannel("slack")
        email_channel = FakeChannel("email")
        service.register_channel(slack_channel)
        service.register_channel(email_channel)

        alert = Alert(
            event_type=AlertEventType.CONNECTION_LOST,
            priority=AlertPriority.CRITICAL,
            title="Connection Lost",
            message="IBKR connection dropped",
        )

        await service.send_critical(alert)

        # Both channels should receive the critical alert
        assert len(slack_channel.delivered) == 1
        assert len(email_channel.delivered) == 1

    async def test_multiple_critical_alerts_all_delivered(
        self, alert_service: AlertService
    ) -> None:
        slack_channel = FakeChannel("slack")
        alert_service.register_channel(slack_channel)

        alert = Alert(
            event_type=AlertEventType.ERROR,
            priority=AlertPriority.CRITICAL,
            title="Critical Error",
            message="System failure",
        )

        # Send multiple critical alerts — all should be delivered
        await alert_service.send_critical(alert)
        await alert_service.send_critical(alert)
        await alert_service.send_critical(alert)

        assert len(slack_channel.delivered) == 3
