"""Process-local sliding-window rate limiting for sensitive endpoints.

For multi-instance deployments use a shared Redis/Valkey backend. The current
implementation is intentionally dependency-free and suitable for one Render
instance; counters reset when the process restarts.
"""
import os
import time
from collections import defaultdict, deque
from threading import Lock

from fastapi import HTTPException, Request

_hits: dict[str, deque] = defaultdict(deque)
_lock = Lock()


def get_client_ip(request: Request) -> str:
    """Use Render's trusted client-IP header when available."""
    if os.environ.get("RENDER") or os.environ.get("RENDER_SERVICE_ID"):
        render_ip = request.headers.get("x-render-client-ip")
        if render_ip:
            return render_ip.strip()
    # Do not trust arbitrary X-Forwarded-For values outside Render.
    return request.client.host if request.client else "127.0.0.1"


_client_ip = get_client_ip


def _trim(key: str, now: float, window_seconds: int) -> deque:
    hits = _hits[key]
    cutoff = now - window_seconds
    while hits and hits[0] <= cutoff:
        hits.popleft()
    return hits


def _record(key: str, now: float) -> None:
    _hits[key].append(now)


def rate_limit(bucket: str, max_calls: int = 1000, window_seconds: int = 60):
    """FastAPI dependency that caps calls per client IP in a sliding window."""
    def _dep(request: Request):
        if os.environ.get("TESTING", "").lower() == "true":
            return
        ip = get_client_ip(request)
        key = f"request:{bucket}:{ip}"
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
            _record(key, now)
    return _dep


def check_login_rate_limit(username: str, ip: str, window_seconds: int = 300,
                           max_user_failures: int = 5, max_ip_failures: int = 40):
    """Reject login attempts after repeated failures for an account or IP."""
    if os.environ.get("TESTING", "").lower() == "true":
        return
    now = time.monotonic()
    user_key = f"login_fail:user:{username.strip().lower()}:{ip}"
    ip_key = f"login_fail:ip:{ip}"
    with _lock:
        user_hits = _trim(user_key, now, window_seconds)
        ip_hits = _trim(ip_key, now, window_seconds)
        if len(user_hits) >= max_user_failures or len(ip_hits) >= max_ip_failures:
            retry_after = max(
                1,
                int(window_seconds - (now - min(user_hits[0] if user_hits else now,
                                                ip_hits[0] if ip_hits else now))),
            )
            raise HTTPException(
                status_code=429,
                detail="Too many failed login attempts. Please wait 5 minutes before trying again.",
                headers={"Retry-After": str(retry_after)},
            )


def record_login_failure(username: str, ip: str):
    """Record a failed login attempt."""
    if os.environ.get("TESTING", "").lower() == "true":
        return
    now = time.monotonic()
    user_key = f"login_fail:user:{username.strip().lower()}:{ip}"
    ip_key = f"login_fail:ip:{ip}"
    with _lock:
        _record(user_key, now)
        _record(ip_key, now)


def clear_login_failures(username: str, ip: str):
    """Clear account-specific failures after a successful login."""
    user_key = f"login_fail:user:{username.strip().lower()}:{ip}"
    with _lock:
        _hits.pop(user_key, None)
