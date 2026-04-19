"""
app/routers/password_reset.py
──────────────────────────────
Token-based password reset flow (no email server required for dev).

Flow
────
1. Admin or the user calls POST /api/reset/request  →  gets a reset token (console-printed in dev)
2. User calls POST /api/reset/confirm with token + new password  →  password updated

In production, step 1 should email the token link. For dev, the token is
printed to the server console so you can test without an SMTP server.

Reset tokens
────────────
Tokens are short-lived JWTs (30 min) signed with the same SECRET_KEY but
with an extra claim `"purpose": "password_reset"` to prevent misuse.
"""
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, status
from jose import JWTError
from pydantic import BaseModel, Field, EmailStr
from sqlalchemy.orm import Session

# config removed — settings not needed in routers
from app.database import get_db
from app.security import create_access_token, decode_token, hash_password
from app.models import AuditLog, User
from app.schemas import MessageOut

router = APIRouter(prefix="/reset", tags=["Password Reset"])

RESET_TOKEN_EXPIRE_MINUTES = 30


# ── Request models ────────────────────────────────────────────────────────────

class ResetRequestBody(BaseModel):
    """Identify the user who needs a reset — by username or email."""
    username: str = Field(..., min_length=1)

    model_config = {"str_strip_whitespace": True}


class ResetConfirmBody(BaseModel):
    token:            str = Field(..., min_length=1)
    new_password:     str = Field(..., min_length=6)
    confirm_password: str = Field(..., min_length=6)


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post(
    "/request",
    response_model=MessageOut,
    summary="Request a password-reset token",
)
def request_reset(body: ResetRequestBody, db: Session = Depends(get_db)):
    """
    Looks up the user by username. If found, generates a 30-minute reset token.

    **Development**: The token is printed to the server console.
    **Production**: Replace the print() call with your email-sending logic.
    """
    user = db.query(User).filter(
        User.username == body.username,
        User.is_active == True,
    ).first()

    # Always return the same message to prevent username enumeration
    generic_msg = (
        "If an account with that username exists, a reset token has been generated. "
        "In production this would be emailed. Check server logs for the token (dev mode)."
    )

    if not user:
        return MessageOut(message=generic_msg)

    token = create_access_token(
        data={"sub": user.username, "id": user.id, "purpose": "password_reset"},
        expires_delta=timedelta(minutes=RESET_TOKEN_EXPIRE_MINUTES),
    )

    # ── DEV: print to console ──────────────────────────────────────────────
    print("\n" + "="*60)
    print("  PASSWORD RESET TOKEN (dev mode — email this in production)")
    print(f"  User     : {user.full_name} ({user.username})")
    print(f"  Token    : {token}")
    print(f"  Expires  : {RESET_TOKEN_EXPIRE_MINUTES} minutes")
    print("  Endpoint : POST /api/reset/confirm")
    print("="*60 + "\n")
    # ── END DEV ───────────────────────────────────────────────────────────

    db.add(AuditLog(
        user_id=user.id,
        action="PASSWORD_RESET_REQUESTED",
        detail=f"Reset token generated for {user.username}",
    ))
    db.commit()

    return MessageOut(message=generic_msg)


@router.post(
    "/confirm",
    response_model=MessageOut,
    summary="Confirm a password reset with the token",
)
def confirm_reset(body: ResetConfirmBody, db: Session = Depends(get_db)):
    """
    Validates the reset token and sets the new password.

    - Token must be valid and not expired
    - Token must have been issued specifically for password reset
    - New password and confirmation must match
    """
    # 1. Validate passwords match
    if body.new_password != body.confirm_password:
        raise HTTPException(status_code=400, detail="Passwords do not match")

    # 2. Decode and verify token
    invalid_exc = HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="Invalid or expired reset token",
    )
    try:
        payload = decode_token(body.token)
    except JWTError:
        raise invalid_exc

    # 3. Verify the purpose claim
    if payload.get("purpose") != "password_reset":
        raise invalid_exc

    user_id: int = payload.get("id")
    if not user_id:
        raise invalid_exc

    # 4. Load user
    user = db.query(User).filter(User.id == user_id, User.is_active == True).first()
    if not user:
        raise invalid_exc

    # 5. Update password
    user.password_hash = hash_password(body.new_password)

    db.add(AuditLog(
        user_id=user.id,
        action="PASSWORD_RESET_COMPLETE",
        detail=f"Password reset completed for {user.username}",
    ))
    db.commit()

    return MessageOut(message="Password has been reset successfully. You can now log in.")
