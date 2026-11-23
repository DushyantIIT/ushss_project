"""Distributed rate limiting backed by the USHSS Supabase database.

The RPC performs an atomic fixed-window increment, so limits are shared across
Vercel serverless instances and survive cold starts. Local SQLite development
keeps a bounded in-process fallback. Production fails closed if the shared
security counter cannot be reached.
"""
import hashlib
import logging
import os
import time
from collections import defaultdict, deque
from threading import Lock

from fastapi import HTTPException, Request

from app.database import sb, USE_SQLITE_FALLBACK

logger = logging.getLogger(__name__)
_hits: dict[str, deque] = defaultdict(deque)
_lock = Lock()


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", "replace")).hexdigest()


def _shared_limit(key: str, max_calls: int, window_seconds: int,
                  increment: bool = True, reset: bool = False) -> dict:
    """Call the restricted SECURITY DEFINER RPC using the server-only client."""
    try:
        response = sb.rpc(
            "ushss_rate_limit",
            {
                "p_key_hash": _digest(key),
                "p_max_calls": max_calls,
                "p_window_seconds": window_seconds,
                "p_increment": increment,
                "p_reset": reset,
            },
        ).execute()
        rows = response.data or []
        if not rows:
            raise RuntimeError("Rate-limit RPC returned no result")
        return rows[0]
    except Exception as exc:
        logger.error("Shared rate-limit store unavailable: %s", type(exc).__name__)
        raise HTTPException(
            status_code=503,
            detail="Security rate-limit service temporarily unavailable. Please retry shortly.",
            headers={"Retry-After": "5"},
        ) from exc


def get_client_ip(request: Request) -> str:
    """Use platform-provided client IP headers, with socket fallback."""
    if os.environ.get("VERCEL"):
        vercel_ip = request.headers.get("x-real-ip") or request.headers.get("x-forwarded-for")
        if vercel_ip:
            return vercel_ip.split(",")[0].strip()
    if os.environ.get("RENDER") or os.environ.get("RENDER_SERVICE_ID"):
        render_ip = request.headers.get("x-render-client-ip")
        if render_ip:
            return render_ip.strip()
    return request.client.host if request.client else "127.0.0.1"


_client_ip = get_client_ip


def _trim(key: str, now: float, window_seconds: int) -> deque:
    hits = _hits[key]
    cutoff = now - window_seconds
    while hits and hits[0] <= cutoff:
        hits.popleft()
    if not hits:
        _hits.pop(key, None)
        return deque()
    return hits


def _local_rate_limit(key: str, max_calls: int, window_seconds: int) -> None:
    now = time.monotonic()
    with _lock:
        hits = _trim(key, now, window_seconds)
        if len(hits) >= max_calls:
            retry_after = max(1, int(window_seconds - (now - hits[0])))
            raise HTTPException(
                status_code=429,
                detail="Too many requests. Please wait and try again.",
                headers={"Retry-After": str(retry_after)},
            )
        _hits[key].append(now)


def rate_limit(bucket: str, max_calls: int = 1000, window_seconds: int = 60):
    """FastAPI dependency that caps calls per client IP across all instances."""
    def _dep(request: Request):
        if os.environ.get("TESTING", "").lower() == "true":
            return
        ip = get_client_ip(request)
        key = f"request:{bucket}:{ip}"
        if not USE_SQLITE_FALLBACK:
            result = _shared_limit(key, max_calls, window_seconds, increment=True)
            if not result.get("allowed", False):
                raise HTTPException(
                    status_code=429,
                    detail="Too many requests. Please wait and try again.",
                    headers={"Retry-After": str(result.get("retry_after_seconds", window_seconds))},
                )
            return
        _local_rate_limit(key, max_calls, window_seconds)
    return _dep


def _login_keys(username: str, ip: str) -> tuple[str, str]:
    normalized = username.strip().lower()
    return (
        f"login:user:{normalized}:{ip}",
        f"login:ip:{ip}",
    )


def check_login_rate_limit(username: str, ip: str, window_seconds: int = 300,
                           max_user_failures: int = 5, max_ip_failures: int = 40):
    """Reject repeated failed logins per account+IP and per IP."""
    if os.environ.get("TESTING", "").lower() == "true":
        return
    user_key, ip_key = _login_keys(username, ip)
    if not USE_SQLITE_FALLBACK:
        user_hits = _shared_limit(user_key, max_user_failures, window_seconds, increment=False)
        ip_hits = _shared_limit(ip_key, max_ip_failures, window_seconds, increment=False)
        if not user_hits.get("allowed", True) or not ip_hits.get("allowed", True):
            retry_after = max(
                int(user_hits.get("retry_after_seconds", 1)),
                int(ip_hits.get("retry_after_seconds", 1)),
            )
            raise HTTPException(
                status_code=429,
                detail="Too many failed login attempts. Please wait 5 minutes before trying again.",
                headers={"Retry-After": str(retry_after)},
            )
        return

    now = time.monotonic()
    with _lock:
        user_hits = _trim("login_fail:" + user_key, now, window_seconds)
        ip_hits = _trim("login_fail:" + ip_key, now, window_seconds)
        if len(user_hits) >= max_user_failures or len(ip_hits) >= max_ip_failures:
            oldest = min(user_hits[0] if user_hits else now, ip_hits[0] if ip_hits else now)
            retry_after = max(1, int(window_seconds - (now - oldest)))
            raise HTTPException(
                status_code=429,
                detail="Too many failed login attempts. Please wait 5 minutes before trying again.",
                headers={"Retry-After": str(retry_after)},
            )


def record_login_failure(username: str, ip: str, window_seconds: int = 300):
    """Record a failed login attempt in the shared store or local fallback."""
    if os.environ.get("TESTING", "").lower() == "true":
        return
    user_key, ip_key = _login_keys(username, ip)
    if not USE_SQLITE_FALLBACK:
        _shared_limit(user_key, 1_000_000_000, window_seconds, increment=True)
        _shared_limit(ip_key, 1_000_000_000, window_seconds, increment=True)
        return
    now = time.monotonic()
    with _lock:
        _hits["login_fail:" + user_key].append(now)
        _hits["login_fail:" + ip_key].append(now)


def clear_login_failures(username: str, ip: str):
    """Clear account-specific failures after a successful login."""
    user_key, _ = _login_keys(username, ip)
    if not USE_SQLITE_FALLBACK:
        _shared_limit(user_key, 1, 300, increment=False, reset=True)
        return
    with _lock:
        _hits.pop("login_fail:" + user_key, None)
