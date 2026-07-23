"""
app/email_utils.py
───────────────────
Sends the two account-status emails the registration/approval workflow
needs (Task 11):

  - Account Approved  → "Your account has been approved. You may now log in."
  - Account Rejected  → "Your registration request has been rejected."
                         (+ reason, if one was given)

There was no existing email infrastructure in this project — password
reset (routers/password_reset.py) only prints its token to the server
log — so this adds a small, self-contained SMTP sender using only the
Python standard library (no new dependency).

Configuration is via environment variables, all optional:

  SMTP_HOST        e.g. smtp.gmail.com / smtp.sendgrid.net
  SMTP_PORT        default 587
  SMTP_USERNAME
  SMTP_PASSWORD
  SMTP_FROM        default "USHSS Portal <no-reply@ushss.local>"
  SMTP_USE_TLS     default "true"

If SMTP_HOST isn't set, sending degrades to printing the email to the
server console (same pattern already used for password-reset tokens),
so a deployment without SMTP configured keeps working instead of
crashing the approve/reject request.
"""

import os
import smtplib
from email.message import EmailMessage
from typing import Optional

SMTP_HOST      = os.environ.get("SMTP_HOST")
SMTP_PORT      = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USERNAME  = os.environ.get("SMTP_USERNAME")
SMTP_PASSWORD  = os.environ.get("SMTP_PASSWORD")
SMTP_FROM      = os.environ.get("SMTP_FROM", "USHSS Portal <no-reply@ushss.local>")
SMTP_USE_TLS   = os.environ.get("SMTP_USE_TLS", "true").lower() != "false"


def send_email(to: str, subject: str, body: str) -> bool:
    """Best-effort send. Never raises — a notification failure should
    never block an approval/rejection from completing. Returns whether
    it was actually sent over SMTP (False means it was only logged)."""
    if not SMTP_HOST:
        print("\n" + "─" * 60)
        print("  EMAIL (SMTP not configured — logging instead)")
        print(f"  To      : {to}")
        print(f"  Subject : {subject}")
        print(f"  Body    : {body}")
        print("─" * 60 + "\n")
        return False

    msg = EmailMessage()
    msg["From"] = SMTP_FROM
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)

    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=10) as server:
            if SMTP_USE_TLS:
                server.starttls()
            if SMTP_USERNAME and SMTP_PASSWORD:
                server.login(SMTP_USERNAME, SMTP_PASSWORD)
            server.send_message(msg)
        return True
    except Exception as e:
        # Log and swallow — see docstring above.
        print(f"  ✗ Failed to send email to {to}: {e}")
        return False


def send_approval_email(to: str, full_name: str) -> bool:
    subject = "Your USHSS Portal account has been approved"
    body = (
        f"Hi {full_name},\n\n"
        "Your account has been approved. You may now log in.\n\n"
        "— USHSS Portal"
    )
    return send_email(to, subject, body)


def send_rejection_email(to: str, full_name: str, reason: Optional[str] = None) -> bool:
    subject = "Your USHSS Portal registration request"
    body = (
        f"Hi {full_name},\n\n"
        "Your registration request has been rejected."
        + (f"\n\nReason: {reason}" if reason else "")
        + "\n\n— USHSS Portal"
    )
    return send_email(to, subject, body)
