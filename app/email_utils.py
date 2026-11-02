"""SMTP delivery helpers for the USHSS portal.

Existing approval/rejection callers retain the best-effort send_email API.
Campaign delivery uses send_email_strict so an unconfigured or failing SMTP
server is recorded as a failed delivery rather than a false success.
"""
import os
import smtplib
from email.message import EmailMessage
from typing import Optional


def send_email_strict(to: str, subject: str, body: str, html_body: Optional[str] = None) -> None:
    host = os.environ.get("SMTP_HOST", "").strip()
    if not host:
        raise RuntimeError("SMTP is not configured. Set SMTP_HOST in the deployment environment.")

    port = int(os.environ.get("SMTP_PORT", "587"))
    username = os.environ.get("SMTP_USERNAME", "").strip()
    password = os.environ.get("SMTP_PASSWORD", "")
    sender = os.environ.get("SMTP_FROM", "USHSS Portal <no-reply@ushss.local>")
    use_ssl = os.environ.get("SMTP_USE_SSL", "false").lower() == "true"
    use_tls = os.environ.get("SMTP_USE_TLS", "true").lower() != "false"

    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = to
    msg["Subject"] = subject
    reply_to = os.environ.get("SMTP_REPLY_TO", "").strip()
    if reply_to:
        msg["Reply-To"] = reply_to
    msg.set_content(body or "Please view this email in an HTML-capable email client.")
    if html_body:
        msg.add_alternative(html_body, subtype="html")

    smtp_class = smtplib.SMTP_SSL if use_ssl else smtplib.SMTP
    with smtp_class(host, port, timeout=20) as server:
        if use_tls and not use_ssl:
            server.starttls()
        if username:
            server.login(username, password)
        server.send_message(msg)


def send_email(to: str, subject: str, body: str) -> bool:
    """Best-effort legacy sender; never blocks approval/rejection workflows."""
    try:
        send_email_strict(to, subject, body)
        return True
    except Exception as exc:
        print(f"EMAIL DELIVERY FAILED to {to}: {type(exc).__name__}: {str(exc)[:240]}")
        return False


def send_approval_email(to: str, full_name: str) -> bool:
    return send_email(
        to,
        "Your USHSS Portal account has been approved",
        f"Hi {full_name},\n\nYour account has been approved. You may now log in.\n\n— USHSS Portal",
    )


def send_rejection_email(to: str, full_name: str, reason: Optional[str] = None) -> bool:
    body = (
        f"Hi {full_name},\n\nYour registration request has been rejected."
        + (f"\n\nReason: {reason}" if reason else "")
        + "\n\n— USHSS Portal"
    )
    return send_email(to, "Your USHSS Portal registration request", body)
