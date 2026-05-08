"""Email notification channel using aiosmtplib."""

from __future__ import annotations

from email.message import EmailMessage

import aiosmtplib
import structlog

from src.alerts.service import Alert, AlertChannel

logger = structlog.get_logger(__name__)


class EmailChannel(AlertChannel):
    """Delivers alerts via SMTP email using aiosmtplib.

    Connects to the configured SMTP server and sends a formatted
    email to all configured recipients.
    """

    def __init__(
        self,
        smtp_host: str,
        smtp_port: int,
        smtp_user: str,
        smtp_password: str,
        from_email: str,
        to_emails: list[str],
    ) -> None:
        self._smtp_host = smtp_host
        self._smtp_port = smtp_port
        self._smtp_user = smtp_user
        self._smtp_password = smtp_password
        self._from_email = from_email
        self._to_emails = to_emails

    @property
    def name(self) -> str:
        return "email"

    def _build_message(self, alert: Alert) -> EmailMessage:
        """Build an EmailMessage from an Alert."""
        msg = EmailMessage()
        msg["Subject"] = f"[{alert.priority.value.upper()}] {alert.title}"
        msg["From"] = self._from_email
        msg["To"] = ", ".join(self._to_emails)

        body_parts = [
            f"Priority: {alert.priority.value.upper()}",
            f"Event Type: {alert.event_type.value}",
            f"Time: {alert.timestamp.isoformat()}",
            "",
            alert.message,
        ]

        if alert.metadata:
            body_parts.append("")
            body_parts.append("Details:")
            for key, value in alert.metadata.items():
                body_parts.append(f"  {key}: {value}")

        msg.set_content("\n".join(body_parts))
        return msg

    async def deliver(self, alert: Alert) -> bool:
        """Send alert via SMTP email."""
        if not self._to_emails:
            logger.warning("email_alert_no_recipients")
            return False

        message = self._build_message(alert)

        try:
            await aiosmtplib.send(
                message,
                hostname=self._smtp_host,
                port=self._smtp_port,
                username=self._smtp_user,
                password=self._smtp_password,
                start_tls=True,
            )
            logger.debug(
                "email_alert_delivered",
                title=alert.title,
                recipients=self._to_emails,
            )
            return True
        except (aiosmtplib.SMTPException, OSError) as exc:
            logger.error("email_alert_error", error=str(exc))
            return False
