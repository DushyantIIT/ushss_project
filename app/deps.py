"""
app/deps.py
───────────
FastAPI JWT dependencies — role guards using Supabase.

  get_current_user  — any authenticated user (must be active AND approved)
  require_admin     — admin only  (full DB rights)
  require_faculty   — faculty or admin  (host attendance sessions)
  require_student   — student / cr / admin  (view timetable, mark attendance)
"""

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
        user_id = payload.get("id")
        if not user_id:
            raise exc
    except JWTError:
        raise exc

    try:
        # Use a list query instead of .single(): distinguish "no matching
        # account" from a database/network failure. Only the former means
        # the token's user is no longer valid.
        res = (
            sb.table("users")
            .select("*")
            .eq("id", user_id)
            .eq("is_active", True)
            .limit(1)
            .execute()
        )
    except Exception as error:
        # A temporary Supabase/database failure is a service problem, not an
        # invalid token. Returning 503 prevents clients from wiping sessions.
        print(f"AUTH USER LOOKUP FAILED: {type(error).__name__}: {str(error)[:180]}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication service temporarily unavailable. Please retry.",
            headers={"Retry-After": "3"},
        )

    if not res.data:
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

