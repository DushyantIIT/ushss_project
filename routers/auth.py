"""
routers/auth.py

Authentication  login returns a JWT.

  POST /api/login
  POST /api/register
"""

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, EmailStr, Field

from app.database import sb
from app.security import verify_password, hash_password, create_access_token

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


class RegisterRequest(BaseModel):
    username:      str      = Field(..., min_length=1)
    password:      str      = Field(..., min_length=6)
    role:          str      = Field(default="student")
    full_name:     str      = Field(..., min_length=1)
    email:         EmailStr
    phone:         Optional[str] = None
    enrollment_no: Optional[str] = None
    department:    Optional[str] = None
    programme:     Optional[str] = None
    batch:         Optional[str] = None
    designation:   Optional[str] = None
    model_config = {"str_strip_whitespace": True}


@router.post("/register", status_code=201, summary="Self-register a new portal user")
def register(body: RegisterRequest):
    ALLOWED_ROLES = ("student", "faculty", "cr")
    if body.role not in ALLOWED_ROLES:
        raise HTTPException(400, f"Self-registration is only allowed for: {ALLOWED_ROLES}")

    # check duplicate username + role
    dup = (
        sb.table("users")
        .select("id")
        .eq("username", body.username)
        .eq("role", body.role)
        .execute()
    )
    if dup.data:
        raise HTTPException(409, "Username already exists for this role")

    # check duplicate email
    dup_email = sb.table("users").select("id").eq("email", body.email).execute()
    if dup_email.data:
        raise HTTPException(409, "Email address is already registered")

    row = {
        "username":      body.username,
        "role":          body.role,
        "full_name":     body.full_name,
        "email":         body.email,
        "password_hash": hash_password(body.password),
        "phone":         body.phone,
        "enrollment_no": body.enrollment_no or body.username,
        "department":    body.department,
        "programme":     body.programme,
        "batch":         body.batch,
        "designation":   body.designation,
        "is_active":     True,
    }
    res = sb.table("users").insert(row).execute()

    sb.table("audit_log").insert({
        "user_id": res.data[0]["id"],
        "action":  "SELF_REGISTER",
        "detail":  f"{body.role} '{body.username}' self-registered",
    }).execute()

    new_user = res.data[0]
    new_user.pop("password_hash", None)
    return {"success": True, "message": "Registration successful. You can now log in.", "user": new_user}
