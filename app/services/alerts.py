from __future__ import annotations

"""Email + Slack delivery. Gmail SMTP is the default for the Sadiq tenant."""
import logging
import smtplib
from email.message import EmailMessage

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


def format_alert_message(comparison: dict) -> str:
    return (
        f"{comparison.get('title')}\n"
        f"Our price: Rs.{comparison['our_price']:,.0f}\n"
        f"{comparison['competitor']} price: Rs.{comparison['competitor_price']:,.0f}\n"
        f"Gap: {comparison['gap_pct']:.1f}% cheaper elsewhere\n"
        f"{comparison.get('competitor_url') or ''}"
    )


async def deliver(tenant: dict, comparison: dict) -> None:
    text = format_alert_message(comparison)
    alerts = tenant.get("alerts") or {}
    webhook = alerts.get("slack_webhook_url") or settings.SLACK_WEBHOOK_URL
    if webhook:
        await send_slack_alert(webhook, text)
    await send_email_alert(
        subject=f"[PriceIntel] {comparison.get('title', 'Product')} undercut by {comparison['gap_pct']:.1f}%",
        body=text,
        from_addr=alerts.get("email_from") or settings.ALERT_EMAIL_FROM,
        to_addr=alerts.get("email_to") or settings.ALERT_EMAIL_TO,
    )


async def send_slack_alert(webhook: str, text: str) -> None:
    async with httpx.AsyncClient(timeout=10) as client:
        await client.post(webhook, json={"text": text})


async def send_email_alert(subject: str, body: str, from_addr: str, to_addr: str) -> None:
    if not settings.SMTP_PASSWORD or not to_addr:
        logger.debug("SMTP not configured, skip email: %s", subject)
        return

    msg = EmailMessage()
    msg["From"] = from_addr
    msg["To"] = to_addr
    msg["Subject"] = subject
    msg.set_content(body)

    try:
        with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=20) as smtp:
            if settings.SMTP_USE_TLS:
                smtp.starttls()
            smtp.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
            smtp.send_message(msg)
    except Exception:
        logger.exception("Failed to send alert email to %s", to_addr)
