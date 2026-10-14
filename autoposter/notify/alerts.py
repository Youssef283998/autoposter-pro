"""Notification alerts: Telegram, SMTP email, generic webhook.

Alerts are rate-limited per channel so a platform outage cannot flood you.
"""

from __future__ import annotations

import asyncio
import json
import smtplib
import time
from email.mime.text import MIMEText

import httpx

from autoposter.config.schema import AlertChannel
from autoposter.config.settings import SecretSettings
from autoposter.utils.logging import get_logger

logger = get_logger("notify.alerts")


class AlertManager:
    """Sends alerts through configured channels with rate limiting."""

    def __init__(
        self,
        channels: list[AlertChannel],
        *,
        secrets: SecretSettings,
        bot_token: str | None = None,
    ) -> None:
        self._channels = channels
        self._secrets = secrets
        self._bot_token = bot_token
        self._last_sent: dict[str, float] = {}

    async def send(self, kind: str, message: str) -> None:
        """Send an alert through all configured channels."""
        for channel in self._channels:
            key = f"{channel.kind}:{kind}"
            now = time.monotonic()
            last = self._last_sent.get(key, 0)
            if now - last < channel.min_interval_seconds:
                logger.debug("Alert rate-limited: %s", key)
                continue
            self._last_sent[key] = now
            try:
                if channel.kind == "telegram":
                    await self._send_telegram(message)
                elif channel.kind == "smtp":
                    await self._send_smtp(message)
                elif channel.kind == "webhook":
                    await self._send_webhook(message)
            except Exception as exc:
                logger.warning("Alert failed (%s): %s", channel.kind, exc)

    async def _send_telegram(self, message: str) -> None:
        """Send a Telegram message via the bot API."""
        chat_id = self._secrets.alert_telegram_chat_id
        if not chat_id or not self._bot_token:
            return
        url = f"https://api.telegram.org/bot{self._bot_token}/sendMessage"
        async with httpx.AsyncClient(timeout=10.0) as client:
            await client.post(url, json={"chat_id": chat_id, "text": message})

    async def _send_smtp(self, message: str) -> None:
        """Send an email via SMTP."""
        host = self._secrets.smtp_host
        if not host:
            return
        port = self._secrets.smtp_port or 587
        username = self._secrets.smtp_username
        password = self._secrets.smtp_password
        from_addr = self._secrets.smtp_from or username
        if not from_addr:
            return

        msg = MIMEText(message)
        msg["Subject"] = "AutoPoster Alert"
        msg["From"] = from_addr
        msg["To"] = from_addr

        def _send() -> None:
            with smtplib.SMTP(host, port, timeout=10) as server:
                server.starttls()
                if username and password:
                    server.login(username, password)
                server.send_message(msg)

        await asyncio.to_thread(_send)

    async def _send_webhook(self, message: str) -> None:
        """POST a JSON payload to a generic webhook."""
        url = self._secrets.alert_webhook_url
        if not url:
            return
        payload = json.dumps({"text": message, "source": "autoposter"})
        async with httpx.AsyncClient(timeout=10.0) as client:
            await client.post(url, content=payload, headers={"Content-Type": "application/json"})
