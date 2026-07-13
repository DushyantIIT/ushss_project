"""
routers/password_reset.py
──────────────────────────
Password reset — ADMIN ONLY.

All admins can reset passwords for:
- Students
- Faculty
- CRs
- Other regular admins

The Super Admin account is protected:
- Regular admins cannot reset the Super Admin's password.
- The Super Admin can reset their own password.

Endpoints:
  POST /api/reset/request
  POST /api/reset/confirm
"""

from datetime import timedelta

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    status,
)
from jose import JWTError
from pydantic import BaseModel, Field

from app.database import sb
from app.deps import require_admin
from app.security import (
    create_access_token,
    decode_token,
    hash_password,
)


router = APIRouter(
    prefix="/reset",
    tags=["Password Reset (Admin Only)"],
)

RESET_TOKEN_EXPIRE_MINUTES = 30


class ResetRequestBody(BaseModel):
    username: str = Field(
        ...,
        min_length=1,
    )

    model_config = {
        "str_strip_whitespace": True
    }


class ResetConfirmBody(BaseModel):
    token: str = Field(
        ...,
        min_length=1,
    )

    new_password: str = Field(
        ...,
        min_length=6,
    )

    confirm_password: str = Field(
        ...,
        min_length=6,
    )


@router.post(
    "/request",
    summary="[Admin] Generate a password reset token",
)
def request_reset(
    body: ResetRequestBody,
    admin: dict = Depends(require_admin),
):
    res = (
        sb.table("users")
        .select(
            "id,username,full_name,is_super_admin"
        )
        .eq("username", body.username)
        .eq("is_active", True)
        .single()
        .execute()
    )

    if not res.data:
        raise HTTPException(
            status_code=404,
            detail=(
                "No active user with that username"
            ),
        )

    target = res.data

    # All admins can reset passwords for ordinary
    # users and other regular admins.
    #
    # Only the Super Admin account is protected.
    if (
        target.get("is_super_admin", False)
        and target["id"] != admin["id"]
    ):
        raise HTTPException(
            status_code=403,
            detail=(
                "The Super Admin's password cannot "
                "be reset by another admin."
            ),
        )

    token = create_access_token(
        data={
            "sub": target["username"],
            "id": target["id"],
            "purpose": "password_reset",
        },
        expires_delta=timedelta(
            minutes=RESET_TOKEN_EXPIRE_MINUTES
        ),
    )

    print("\n" + "=" * 60)
    print(
        "  PASSWORD RESET TOKEN "
        " (Admin-initiated)"
    )
    print(
        f"  Requested by : "
        f"{admin['username']} (admin)"
    )
    print(
        f"  Target user  : "
        f"{target['full_name']} "
        f"({target['username']})"
    )
    print(
        f"  Token        : {token}"
    )
    print(
        f"  Expires      : "
        f"{RESET_TOKEN_EXPIRE_MINUTES} minutes"
    )
    print(
        "  Endpoint     : "
        "POST /api/reset/confirm"
    )
    print("=" * 60 + "\n")

    (
        sb.table("audit_log")
        .insert({
            "user_id": admin["id"],
            "action": (
                "PASSWORD_RESET_REQUESTED"
            ),
            "detail": (
                f"Admin {admin['username']} "
                "requested password reset for "
                f"{target['username']}"
            ),
        })
        .execute()
    )

    return {
        "message": (
            "Reset token generated for "
            f"'{target['username']}'. "
            "Check server logs."
        )
    }


@router.post(
    "/confirm",
    summary="[Admin] Confirm a password reset",
)
def confirm_reset(
    body: ResetConfirmBody,
    admin: dict = Depends(require_admin),
):
    if (
        body.new_password
        != body.confirm_password
    ):
        raise HTTPException(
            status_code=400,
            detail="Passwords do not match",
        )

    invalid_exc = HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail=(
            "Invalid or expired reset token"
        ),
    )

    try:
        payload = decode_token(body.token)

    except JWTError:
        raise invalid_exc

    if (
        payload.get("purpose")
        != "password_reset"
    ):
        raise invalid_exc

    user_id = payload.get("id")

    if not user_id:
        raise invalid_exc

    res = (
        sb.table("users")
        .select(
            "id,username,is_super_admin"
        )
        .eq("id", user_id)
        .eq("is_active", True)
        .single()
        .execute()
    )

    if not res.data:
        raise invalid_exc

    target = res.data

    # Check protection again during confirmation.
    # Do not rely only on the request endpoint.
    if (
        target.get("is_super_admin", False)
        and target["id"] != admin["id"]
    ):
        raise HTTPException(
            status_code=403,
            detail=(
                "The Super Admin's password cannot "
                "be reset by another admin."
            ),
        )

    (
        sb.table("users")
        .update({
            "password_hash":
                hash_password(
                    body.new_password
                )
        })
        .eq("id", user_id)
        .execute()
    )

    (
        sb.table("audit_log")
        .insert({
            "user_id": admin["id"],
            "action": (
                "PASSWORD_RESET_COMPLETE"
            ),
            "detail": (
                f"Admin {admin['username']} "
                "reset the password for "
                f"{target['username']}"
            ),
        })
        .execute()
    )

    return {
        "message": (
            "Password for "
            f"'{target['username']}' "
            "has been reset successfully."
        )
    }
