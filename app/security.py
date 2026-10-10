"""
app/security.py
───────────────
JWT helpers for USHSS.

Passwords are never hashed or verified here — every account's credential
lives in Supabase Auth (see routers/auth.py and routers/admin.py). This
module only issues and decodes the backend's own authorization token.
"""

import os
from datetime import datetime, timedelta, timezone
from typing import Optional

from jose import JWTError, jwt

SECRET_KEY = os.environ.get("SECRET_KEY", "").strip()
IS_PROD = bool(os.environ.get("RENDER") or os.environ.get("RENDER_SERVICE_ID") or os.environ.get("ENVIRONMENT") == "production")
IS_DEV = not IS_PROD or os.environ.get("DEBUG", "false").lower() == "true" or os.environ.get("TESTING", "false").lower() == "true"

if not SECRET_KEY:
    if IS_DEV:
        SECRET_KEY = "dev-secret-change-in-production-must-be-32-chars"
    else:
        raise RuntimeError(
            "FATAL: SECRET_KEY environment variable is not set. "
            "Set a random 32+ character SECRET_KEY in your environment/Render dashboard."
        )
elif SECRET_KEY == "dev-secret-change-in-production-must-be-32-chars" and IS_PROD:
    raise RuntimeError(
        "FATAL: Default development SECRET_KEY detected in production. "
        "Set a unique, strong SECRET_KEY."
    )

ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.environ.get("ACCESS_TOKEN_EXPIRE_MINUTES", 480))  # 8 h


# ── JWT ───────────────────────────────────────────────────────────────────────

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    payload = data.copy()
    expire  = datetime.now(timezone.utc) + (
        expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    )
    payload.update({"exp": expire})
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def decode_token(token: str) -> dict:
    """Raises jose.JWTError on invalid / expired tokens."""
    return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
