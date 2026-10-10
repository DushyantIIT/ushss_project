"""
app/rate_limit.py
──────────────────
A small in-memory rate limiter for the endpoints most worth protecting
from brute-forcing / spam: login and self-registration (Task 13).

This is intentionally simple — a per-process sliding window keyed by
client IP — with no new dependency and no schema change. It's a real
mitigation for a single-instance deployment (which is what this project
runs on today — see render.yaml), but it resets on restart and does not
share state across multiple worker processes/instances. If this app is
ever horizontally scaled, swap this for a shared store (e.g. Redis) —
noted here rather than silently pretending this is production-grade
distributed rate limiting.
"""

import os
import time
from collections import defaultdict, deque

from fastapi import HTTPException, Request

_hits: dict[str, deque] = defaultdict(deque)


def get_client_ip(request: Request) -> str:
    """Extracts client IP reliably.
    When behind Render, uses X-Render-Client-IP if on Render environment,
    or the originating client from X-Forwarded-For (first entry)."""
    is_render = bool(os.environ.get("RENDER") or os.environ.get("RENDER_SERVICE_ID"))
    if is_render:
        render_ip = request.headers.get("x-render-client-ip")
        if render_ip:
            return render_ip.strip()

    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        ips = [ip.strip() for ip in fwd.split(",") if ip.strip()]
        if ips:
            return ips[0]

    return request.client.host if request.client else "127.0.0.1"


_client_ip = get_client_ip


def rate_limit(bucket: str, max_calls: int, window_seconds: int):
    """Returns a FastAPI dependency that rejects with 429 once an IP
    exceeds `max_calls` within `window_seconds` for this named bucket."""

    def _dep(request: Request):
        if os.environ.get("TESTING", "").lower() == "true":
            return
        key = f"{bucket}:{get_client_ip(request)}"
        now = time.monotonic()
        q = _hits[key]

        while q and now - q[0] > window_seconds:
            q.popleft()

        if len(q) >= max_calls:
            raise HTTPException(
                status_code=429,
                detail="Too many attempts. Please wait a bit and try again.",
            )

        q.append(now)

    return _dep


def check_login_rate_limit(username: str, ip: str, window_seconds: int = 300, max_user_failures: int = 5, max_ip_failures: int = 40):
    """Check if failed login threshold has been exceeded.
    Keyed on username+IP so campus Wi-Fi sharing one IP does not lock out all students.
    Also has a higher IP-wide threshold to protect against dictionary attacks."""
    if os.environ.get("TESTING", "").lower() == "true":
        return

    now = time.monotonic()
    user_key = f"login_fail:user:{username.strip().lower()}:{ip}"
    q_user = _hits[user_key]
    while q_user and now - q_user[0] > window_seconds:
        q_user.popleft()
    if len(q_user) >= max_user_failures:
        raise HTTPException(
            status_code=429,
            detail="Too many failed login attempts for this account. Please wait 5 minutes and try again.",
        )

    ip_key = f"login_fail:ip:{ip}"
    q_ip = _hits[ip_key]
    while q_ip and now - q_ip[0] > window_seconds:
        q_ip.popleft()
    if len(q_ip) >= max_ip_failures:
        raise HTTPException(
            status_code=429,
            detail="Too many failed login attempts from this network. Please wait 5 minutes and try again.",
        )


def record_login_failure(username: str, ip: str):
    """Record a failed login attempt."""
    if os.environ.get("TESTING", "").lower() == "true":
        return
    now = time.monotonic()
    user_key = f"login_fail:user:{username.strip().lower()}:{ip}"
    _hits[user_key].append(now)
    ip_key = f"login_fail:ip:{ip}"
    _hits[ip_key].append(now)


def clear_login_failures(username: str, ip: str):
    """Clear failed login records upon successful login."""
    user_key = f"login_fail:user:{username.strip().lower()}:{ip}"
    _hits.pop(user_key, None)

