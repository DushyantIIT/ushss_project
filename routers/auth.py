"""
routers/auth.py

Authentication  login returns a JWT.

  POST /api/login
"""

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.database import sb
from app.security import verify_password, create_access_token

router = APIRouter(tags=["Auth"])

ROLE_REDIRECTS = {
    "admin":   "/dashboard/admin",
    "faculty": "/dashboard/faculty",
    "cr":      "/dashboard/cr",
    "student": "/dashboard/student",
}


class LoginRequest(BaseModel):
    username: str = Field(..., min_length=1)
    password: str = Field(..., min_length=1)
    role:     str = Field(default="student")
    model_config = {"str_strip_whitespace": True}


class LoginResponse(BaseModel):
    success:      bool
    token:        str
    token_type:   str = "bearer"
    redirect_url: str
    user:         dict


@router.post("/login", response_model=LoginResponse, summary="Login and get JWT")
def login(body: LoginRequest):
    VALID_ROLES = ("student", "faculty", "cr", "admin")
    if body.role not in VALID_ROLES:
        raise HTTPException(400, f"Invalid role. Must be one of: {VALID_ROLES}")

    res = (
        sb.table("users")
        .select("*")
        .eq("username", body.username)
        .eq("role", body.role)
        .eq("is_active", True)
        .limit(1)
        .execute()
    )

    if not res.data:
        raise HTTPException(401, "Invalid username, role, or password")

    user = res.data[0]

    if not verify_password(body.password, user["password_hash"]):
        raise HTTPException(401, "Invalid username, role, or password")

    sb.table("users").update(
        {"last_login": datetime.now(timezone.utc).isoformat()}
    ).eq("id", user["id"]).execute()

    sb.table("audit_log").insert({
        "user_id": user["id"],
        "action":  "LOGIN",
        "detail":  f"{user['role']} '{user['username']}' logged in",
    }).execute()

    token = create_access_token({
        "sub":  user["username"],
        "id":   user["id"],
        "role": user["role"],
    })

    user.pop("password_hash", None)

    return LoginResponse(
        success=True,
        token=token,
        token_type="bearer",
        redirect_url=ROLE_REDIRECTS.get(user["role"], "/"),
        user=user,
    )
