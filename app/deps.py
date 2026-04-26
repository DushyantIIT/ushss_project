"""
app/deps.py
───────────
FastAPI JWT dependencies — role guards using Supabase.

  get_current_user  — any authenticated user
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

    res = (
        sb.table("users")
        .select("*")
        .eq("id", user_id)
        .eq("is_active", True)
        .single()
        .execute()
    )
    if not res.data:
        raise exc
    return res.data


def require_admin(user: dict = Depends(get_current_user)) -> dict:
    if user["role"] != "admin":
        raise HTTPException(403, "Admin access required. Contact your administrator.")
    return user


def require_faculty(user: dict = Depends(get_current_user)) -> dict:
    if user["role"] not in ("faculty", "admin"):
        raise HTTPException(403, "Faculty access required.")
    return user


def require_student(user: dict = Depends(get_current_user)) -> dict:
    if user["role"] not in ("student", "cr", "admin"):
        raise HTTPException(403, "Student access required.")
    return user
