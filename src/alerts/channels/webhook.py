"""Generic HTTP webhook notification channel."""

from __future__ import annotations

import httpx
import structlog

from src.alerts.service import Alert, AlertChannel

logger = structlog.get_logger(__name__)


class WebhookChannel(AlertChannel):
    """Delivers alerts via generic HTTP webhook POST.

    Sends a JSON payload to the configured webhook URL containing
    the full alert details.
    """

    def __init__(self, url: str) -> None:
        self._url = url

    @property
    def name(self) -> str:
        return "webhook"

    def _format_payload(self, alert: Alert) -> dict:
        """Format alert into a JSON-serializable webhook payload."""
        return {
            "event_type": alert.event_type.value,
            "priority": alert.priority.value,
            "title": alert.title,
            "message": alert.message,
            "metadata": alert.metadata,
            "timestamp": alert.timestamp.isoformat(),
        }

    async def deliver(self, alert: Alert) -> bool:
        """Send alert to webhook endpoint via POST."""
        payload = self._format_payload(alert)

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(self._url, json=payload)
                if 200 <= response.status_code < 300:
                    logger.debug("webhook_alert_delivered", title=alert.title)
                    return True
                else:
                    logger.warning(
                        "webhook_alert_failed",
                        status_code=response.status_code,
                        response_text=response.text,
                    )
                    return False
        except httpx.HTTPError as exc:
            logger.error("webhook_alert_error", error=str(exc))
            return False
