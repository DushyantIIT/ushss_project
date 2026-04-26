"""
routers/password_reset.py
──────────────────────────
Password reset — ADMIN ONLY.
Only admins can reset any user's password.

  POST /api/reset/request   — admin generates a reset token for a username
  POST /api/reset/confirm   — admin confirms with token + new password
"""

from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, status
from jose import JWTError
from pydantic import BaseModel, Field

from app.database import sb
from app.deps import require_admin
from app.security import create_access_token, decode_token, hash_password

router = APIRouter(prefix="/reset", tags=["Password Reset (Admin Only)"])

RESET_TOKEN_EXPIRE_MINUTES = 30


class ResetRequestBody(BaseModel):
    username: str = Field(..., min_length=1)
    model_config = {"str_strip_whitespace": True}


class ResetConfirmBody(BaseModel):
    token:            str = Field(..., min_length=1)
    new_password:     str = Field(..., min_length=6)
    confirm_password: str = Field(..., min_length=6)


@router.post("/request", summary="[Admin] Generate a password reset token")
def request_reset(body: ResetRequestBody, admin: dict = Depends(require_admin)):
    res = (
        sb.table("users")
        .select("id, username, full_name")
        .eq("username", body.username)
        .eq("is_active", True)
        .single()
        .execute()
    )
    if not res.data:
        raise HTTPException(404, "No active user with that username")

    user = res.data
    token = create_access_token(
        data={"sub": user["username"], "id": user["id"], "purpose": "password_reset"},
        expires_delta=timedelta(minutes=RESET_TOKEN_EXPIRE_MINUTES),
    )

    print("\n" + "=" * 60)
    print("  PASSWORD RESET TOKEN  (Admin-initiated)")
    print(f"  Requested by : {admin['username']} (admin)")
    print(f"  Target user  : {user['full_name']} ({user['username']})")
    print(f"  Token        : {token}")
    print(f"  Expires      : {RESET_TOKEN_EXPIRE_MINUTES} minutes")
    print("  Endpoint     : POST /api/reset/confirm")
    print("=" * 60 + "\n")

    sb.table("audit_log").insert({
        "user_id": admin["id"],
        "action":  "PASSWORD_RESET_REQUESTED",
        "detail":  f"Admin {admin['username']} requested reset for {user['username']}",
    }).execute()

    return {"message": f"Reset token generated for '{user['username']}'. Check server logs."}


@router.post("/confirm", summary="[Admin] Confirm a password reset")
def confirm_reset(body: ResetConfirmBody, admin: dict = Depends(require_admin)):
    if body.new_password != body.confirm_password:
        raise HTTPException(400, "Passwords do not match")

    invalid_exc = HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid or expired reset token")
    try:
        payload = decode_token(body.token)
    except JWTError:
        raise invalid_exc

    if payload.get("purpose") != "password_reset":
        raise invalid_exc

    user_id = payload.get("id")
    if not user_id:
        raise invalid_exc

    res = (
        sb.table("users")
        .select("id, username")
        .eq("id", user_id)
        .eq("is_active", True)
        .single()
        .execute()
    )
    if not res.data:
        raise invalid_exc

    user = res.data
    sb.table("users").update({"password_hash": hash_password(body.new_password)}).eq("id", user_id).execute()

    sb.table("audit_log").insert({
        "user_id": admin["id"],
        "action":  "PASSWORD_RESET_COMPLETE",
        "detail":  f"Admin {admin['username']} reset password for {user['username']}",
    }).execute()

    return {"message": f"Password for '{user['username']}' has been reset successfully."}
