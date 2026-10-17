"""
app/deps.py
───────────
FastAPI JWT dependencies — role guards using Supabase.

  get_current_user  — any authenticated user (must be active AND approved)
  require_admin     — admin only  (full DB rights)
  require_faculty   — faculty or admin  (host attendance sessions)
  require_student   — student / cr / admin  (view timetable, mark attendance)
"""

import time

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError

from app.database import sb
from app.security import decode_token

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/login")


def get_current_user(token: str = Depends(oauth2_scheme)) -> dict:
    exc = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = decode_token(token)
        # Reset tokens or non-access tokens cannot act as login sessions
        if payload.get("purpose") and payload.get("purpose") != "access":
            raise exc
        user_id = payload.get("id")
        if not user_id:
            raise exc
    except JWTError:
        raise exc

    # Render logs show intermittent Supabase transport failures during this
    # lookup. Retry this read-only query briefly before reporting an outage.
    # Never turn a database error into an authentication success, and never
    # retry writes (which could duplicate changes).
    last_error = None
    res = None
    for attempt in range(3):
        try:
            res = (
                sb.table("users")
                .select("*")
                .eq("id", user_id)
                .eq("is_active", True)
                .limit(1)
                .execute()
            )
            last_error = None
            break
        except Exception as error:
            last_error = error
            if attempt < 2:
                time.sleep(0.2 * (attempt + 1))

    if last_error is not None:
        print(
            f"AUTH USER LOOKUP FAILED after retries: "
            f"{type(last_error).__name__}: {str(last_error)[:180]}"
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication service temporarily unavailable. Please retry.",
            headers={"Retry-After": "3"},
        )

    if not res or not res.data:
        raise exc

    user = res.data[0]

    # Registration/approval workflow: a token exists (e.g. the short-lived
    # one issued at self-registration for polling /registration-status)
    # doesn't mean the account may use any protected endpoint yet.
    account_status = user.get("status") or "approved"
    if account_status != "approved":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Account is {account_status}; access requires admin approval.",
        )

    return user


def require_admin(user: dict = Depends(get_current_user)) -> dict:
    if user["role"] != "admin":
        raise HTTPException(403, "Admin access required. Contact your administrator.")
    return user


def require_super_admin(user: dict = Depends(get_current_user)) -> dict:
    if user["role"] != "admin" or not user.get("is_super_admin", False):
        raise HTTPException(403, "Super Admin access required.")
    return user


def require_faculty(user: dict = Depends(get_current_user)) -> dict:
    if user["role"] not in ("faculty", "admin"):
        raise HTTPException(403, "Faculty access required.")
    return user


def require_student(user: dict = Depends(get_current_user)) -> dict:
    if user["role"] not in ("student", "cr", "admin"):
        raise HTTPException(403, "Student access required.")
    return user
