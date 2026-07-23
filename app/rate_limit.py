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

import time
from collections import defaultdict, deque

from fastapi import HTTPException, Request

_hits: dict[str, deque] = defaultdict(deque)


def _client_ip(request: Request) -> str:
    # Respect a reverse proxy's forwarded header (Render sits behind one),
    # falling back to the direct connection.
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def rate_limit(bucket: str, max_calls: int, window_seconds: int):
    """Returns a FastAPI dependency that rejects with 429 once an IP
    exceeds `max_calls` within `window_seconds` for this named bucket."""

    def _dep(request: Request):
        key = f"{bucket}:{_client_ip(request)}"
        now = time.monotonic()
        q = _hits[key]

        while q and now - q[0] > window_seconds:
            q.popleft()

        if len(q) >= max_calls:
            raise HTTPException(
                status_code=429,
                detail=f"Too many attempts. Please wait a bit and try again.",
            )

        q.append(now)

    return _dep
