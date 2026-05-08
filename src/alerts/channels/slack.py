"""Slack notification channel using incoming webhook URL."""

from __future__ import annotations

import httpx
import structlog

from src.alerts.service import Alert, AlertChannel

logger = structlog.get_logger(__name__)


class SlackChannel(AlertChannel):
    """Delivers alerts via Slack incoming webhook.

    Sends a formatted message to the configured Slack webhook URL
    using an httpx async POST request.
    """

    def __init__(self, webhook_url: str) -> None:
        self._webhook_url = webhook_url

    @property
    def name(self) -> str:
        return "slack"

    def _format_payload(self, alert: Alert) -> dict:
        """Format alert into Slack message payload."""
        priority_emoji = {
            "low": "ℹ️",
            "medium": "⚠️",
            "high": "🔴",
            "critical": "🚨",
        }
        emoji = priority_emoji.get(alert.priority.value, "📢")

        text = f"{emoji} *{alert.title}*\n{alert.message}"
        if alert.metadata:
            details = "\n".join(f"• {k}: {v}" for k, v in alert.metadata.items())
            text += f"\n\n{details}"

        return {
            "text": text,
            "username": "Trading Bot",
            "icon_emoji": ":chart_with_upwards_trend:",
        }

    async def deliver(self, alert: Alert) -> bool:
        """Send alert to Slack via webhook POST."""
        payload = self._format_payload(alert)

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(self._webhook_url, json=payload)
                if response.status_code == 200:
                    logger.debug("slack_alert_delivered", title=alert.title)
                    return True
                else:
                    logger.warning(
                        "slack_alert_failed",
                        status_code=response.status_code,
                        response_text=response.text,
                    )
                    return False
        except httpx.HTTPError as exc:
            logger.error("slack_alert_error", error=str(exc))
            return False
