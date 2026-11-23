"""Rate limiting for sensitive USHSS endpoints.

When Upstash Redis REST credentials are configured, counters are shared across
serverless instances and survive cold starts. Without them, a bounded
process-local fallback keeps local development usable; production logs a clear
warning so operators know that distributed protection is not active.
"""
import hashlib
import json
import logging
import os
import time
import urllib.error
import urllib.request
from collections import defaultdict, deque
from threading import Lock

from fastapi import HTTPException, Request

logger = logging.getLogger(__name__)
_hits: dict[str, deque] = defaultdict(deque)
_lock = Lock()
_warned_local = False

_UPSTASH_URL = os.environ.get("UPSTASH_REDIS_REST_URL", "").strip().rstrip("/")
_UPSTASH_TOKEN = os.environ.get("UPSTASH_REDIS_REST_TOKEN", "").strip()
_SHARED_CONFIGURED = bool(_UPSTASH_URL and _UPSTASH_TOKEN)

# Atomic fixed-window increment with expiry, preventing a counter from living
# forever if the process exits between INCR and EXPIRE.
_INCREMENT_SCRIPT = (
    "local n=redis.call('INCR',KEYS[1]); "
    "if n==1 then redis.call('EXPIRE',KEYS[1],ARGV[1]); end; "
    "return n"
)


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", "replace")).hexdigest()


def _upstash(command: list):
    """Run one Redis command through Upstash REST; fail closed if configured."""
    payload = json.dumps(command).encode("utf-8")
    request = urllib.request.Request(
        _UPSTASH_URL,
        data=payload,
        headers={
            "Authorization": f"Bearer {_UPSTASH_TOKEN}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=2.5) as response:
            result = json.loads(response.read().decode("utf-8"))
        if result.get("error") is not None:
            raise RuntimeError("Upstash returned a command error")
        return result.get("result")
    except Exception as exc:
        logger.error("Shared rate-limit store unavailable: %s", type(exc).__name__)
        raise HTTPException(
            status_code=503,
            detail="Security rate-limit service temporarily unavailable. Please retry shortly.",
            headers={"Retry-After": "5"},
        ) from exc


def _shared_increment(key: str, window_seconds: int) -> int:
    value = _upstash(["EVAL", _INCREMENT_SCRIPT, "1", key, str(window_seconds)])
    return int(value or 0)


def _shared_get(key: str) -> int:
    value = _upstash(["GET", key])
    return int(value or 0)


def _shared_delete(key: str) -> None:
    _upstash(["DEL", key])


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


def _record(key: str, now: float) -> None:
    _hits[key].append(now)


def _warn_if_local():
    global _warned_local
    if not _SHARED_CONFIGURED and not _warned_local:
        _warned_local = True
        if os.environ.get("VERCEL") or os.environ.get("RENDER") or os.environ.get("RENDER_SERVICE_ID") or os.environ.get("ENVIRONMENT") == "production":
            logger.warning(
                "Distributed rate limiting is NOT configured. Set "
                "UPSTASH_REDIS_REST_URL and UPSTASH_REDIS_REST_TOKEN for "
                "cross-instance protection."
            )


def rate_limit(bucket: str, max_calls: int = 1000, window_seconds: int = 60):
    """FastAPI dependency that caps calls per client IP."""
    def _dep(request: Request):
        if os.environ.get("TESTING", "").lower() == "true":
            return
        ip = get_client_ip(request)
        if _SHARED_CONFIGURED:
            key = f"ushss:rl:{_digest(bucket + ':' + ip)}"
            count = _shared_increment(key, window_seconds)
            if count > max_calls:
                raise HTTPException(
                    status_code=429,
                    detail="Too many requests. Please wait and try again.",
                    headers={"Retry-After": str(window_seconds)},
                )
            return
        _warn_if_local()
        key = f"request:{bucket}:{_digest(ip)}"
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


def _login_keys(username: str, ip: str) -> tuple[str, str]:
    normalized = username.strip().lower()
    return (
        f"ushss:login:user:{_digest(normalized + ':' + ip)}",
        f"ushss:login:ip:{_digest(ip)}",
    )


def check_login_rate_limit(username: str, ip: str, window_seconds: int = 300,
                           max_user_failures: int = 5, max_ip_failures: int = 40):
    """Reject repeated failed logins per account+IP and per IP."""
    if os.environ.get("TESTING", "").lower() == "true":
        return
    user_key, ip_key = _login_keys(username, ip)
    if _SHARED_CONFIGURED:
        user_hits = _shared_get(user_key)
        ip_hits = _shared_get(ip_key)
        if user_hits >= max_user_failures or ip_hits >= max_ip_failures:
            raise HTTPException(
                status_code=429,
                detail="Too many failed login attempts. Please wait 5 minutes before trying again.",
                headers={"Retry-After": str(window_seconds)},
            )
        return

    _warn_if_local()
    now = time.monotonic()
    local_user_key = "login_fail:" + user_key
    local_ip_key = "login_fail:" + ip_key
    with _lock:
        user_hits = _trim(local_user_key, now, window_seconds)
        ip_hits = _trim(local_ip_key, now, window_seconds)
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
    if _SHARED_CONFIGURED:
        _shared_increment(user_key, window_seconds)
        _shared_increment(ip_key, window_seconds)
        return
    _warn_if_local()
    now = time.monotonic()
    with _lock:
        _record("login_fail:" + user_key, now)
        _record("login_fail:" + ip_key, now)


def clear_login_failures(username: str, ip: str):
    """Clear account-specific failures after a successful login."""
    user_key, _ = _login_keys(username, ip)
    if _SHARED_CONFIGURED:
        _shared_delete(user_key)
        return
    with _lock:
        _hits.pop("login_fail:" + user_key, None)
