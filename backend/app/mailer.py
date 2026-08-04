"""Best-effort transactional email over SMTP, stdlib only.

Password reset needs to reach the account holder somehow. There is no vetted
mail-sending SaaS wired in here — that would be its own credential and billing
decision, not a code decision — so this sends over plain SMTP when SMTP_HOST is
configured and otherwise logs the message. Logging is still useful on a
self-hosted, single-operator instance: the operator has log access, so no mail
step is actually required to complete their own reset.
"""
from __future__ import annotations

import logging
import smtplib
from email.message import EmailMessage

from app.config import settings

logger = logging.getLogger("legend.mailer")


def send_mail(to: str, subject: str, body: str) -> bool:
    """Send `body` to `to`. Returns whether an SMTP send was attempted and succeeded.

    A `False` return does not mean the message was lost — it was logged instead
    so a self-hosted instance without SMTP configured is still usable.
    """
    if not settings.smtp_host:
        logger.warning(
            "SMTP is not configured (SMTP_HOST unset) — logging the message instead of "
            "sending it. to=%s subject=%r\n%s",
            to, subject, body,
        )
        return False

    message = EmailMessage()
    message["From"] = settings.smtp_from
    message["To"] = to
    message["Subject"] = subject
    message.set_content(body)

    try:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=10) as client:
            if settings.smtp_use_tls:
                client.starttls()
            if settings.smtp_username:
                client.login(settings.smtp_username, settings.smtp_password)
            client.send_message(message)
        return True
    except (smtplib.SMTPException, OSError) as exc:
        logger.error("failed to send mail to %s: %s", to, exc)
        return False
