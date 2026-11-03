"""Outgoing email over SMTP. Only the ``mail`` job calls this."""

from __future__ import annotations

import asyncio
import smtplib
from email.message import EmailMessage
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.settings import Settings


def compose(settings: Settings, *, to: str, subject: str, body: str) -> EmailMessage:
    message = EmailMessage()
    message['From'] = settings.mail_from
    message['To'] = to
    message['Subject'] = subject
    message.set_content(body)
    return message


def _send(settings: Settings, message: EmailMessage) -> None:
    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=10) as smtp:
        smtp.send_message(message)


async def send(settings: Settings, message: EmailMessage) -> None:
    await asyncio.to_thread(_send, settings, message)
