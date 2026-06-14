import asyncio
from typing import Any

import resend

from app.config import settings


def _get_email_config() -> tuple[str, str]:
    api_key = settings.EMAIL_API_KEY
    from_email = settings.FROM_EMAIL
    if not api_key or not from_email:
        raise ValueError("EMAIL_API_KEY and FROM_EMAIL must be set")
    return api_key, from_email


def _send_email_sync(params: dict[str, Any]) -> dict[str, Any]:
    api_key, _ = _get_email_config()
    resend.api_key = api_key
    return resend.Emails.send(params)


async def send_otp_email(to_email: str, otp: str) -> None:
    _, from_email = _get_email_config()
    params = {
        "from": from_email,
        "to": [to_email],
        "subject": "Your verification code",
        "html": (
            f"<h2>Email Verification</h2>"
            f"<p>Your one-time verification code is:</p>"
            f"<p style='font-size: 24px; font-weight: bold; letter-spacing: 4px;'>{otp}</p>"
            f"<p>This code expires in 10 minutes. Do not share it with anyone.</p>"
        ),
        "text": f"Your verification code is {otp}. It expires in 10 minutes.",
    }
    await asyncio.to_thread(_send_email_sync, params)


async def send_contact_email(
    *,
    name: str,
    email: str,
    subject: str,
    message: str,
    admin_email: str,
) -> None:
    _, from_email = _get_email_config()
    params = {
        "from": from_email,
        "to": [admin_email],
        "reply_to": email,
        "subject": f"[Contact Form] {subject}",
        "html": (
            f"<h2>New contact form submission</h2>"
            f"<p><strong>Name:</strong> {name}</p>"
            f"<p><strong>Email:</strong> {email}</p>"
            f"<p><strong>Subject:</strong> {subject}</p>"
            f"<p><strong>Message:</strong></p>"
            f"<p>{message.replace(chr(10), '<br>')}</p>"
        ),
        "text": (
            f"Name: {name}\n"
            f"Email: {email}\n"
            f"Subject: {subject}\n\n"
            f"Message:\n{message}"
        ),
    }
    await asyncio.to_thread(_send_email_sync, params)
