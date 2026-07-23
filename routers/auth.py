"""
routers/auth.py

Authentication — login returns a JWT.

  POST /api/login                 login (legacy password_hash OR Supabase Auth accounts)
  POST /api/register              self-register via Supabase Auth (email verification + approval)
  GET  /api/check-username        live username-availability check for the registration form
  GET  /api/registration-status   poll pending/rejected/approved status after registering

Registration / approval workflow
─────────────────────────────────
Self-registration no longer stores or hashes a password in this app. The
password is created directly in Supabase Auth (`sb.auth.sign_up`), which also
sends the account's email verification message — nothing here re-implements
that. We link the two records with `users.supabase_uid`.

Every self-registered profile starts with `status = 'pending'`. An admin (or,
for the `admin` role itself, only a SuperAdmin) must approve the request
before the account can log in — see routers/admin.py for the approval panel
(added separately). Existing/legacy accounts (created by an admin, or created
before this migration) have no `supabase_uid` and keep authenticating exactly
as before via `password_hash` + bcrypt, and default to `status = 'approved'`,
so nothing about today's login behaviour changes for them.
"""

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from jose import JWTError
from pydantic import BaseModel, EmailStr, Field

from app.database import sb
from app.deps import oauth2_scheme
from app.security import verify_password, create_access_token, decode_token
from app.rate_limit import rate_limit

router = APIRouter(tags=["Auth"])

VALID_ROLES = ("student", "faculty", "cr", "admin")

# Roles selectable on the public self-registration form. SuperAdmin is never
# offered here, and "admin" self-registration (if ever enabled) would still
# require a SuperAdmin's approval — enforced server-side, not by this list.
SELF_REGISTER_ROLES = ("student", "faculty", "cr")

# Roles that must supply an enrollment number to register.
ENROLLMENT_REQUIRED_ROLES = ("student", "cr")

ROLE_REDIRECTS = {
    "admin":   "/dashboard/admin",
    "faculty": "/dashboard/faculty",
    "cr":      "/dashboard/cr",
    "student": "/dashboard/student",
}


# ── Login ────────────────────────────────────────────────────────────────────

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


@router.post(
    "/login", response_model=LoginResponse, summary="Login and get JWT",
    dependencies=[Depends(rate_limit("login", max_calls=10, window_seconds=300))],
)
def login(body: LoginRequest):
    if body.role not in VALID_ROLES:
        raise HTTPException(400, f"Invalid role. Must be one of: {VALID_ROLES}")

    res = (
        sb.table("users")
        .select("*")
        .eq("username", body.username)
        .eq("role", body.role)
        .limit(1)
        .execute()
    )

    if not res.data:
        raise HTTPException(401, "Invalid username, role, or password")

    user = res.data[0]

    if not user.get("is_active", True):
        raise HTTPException(401, "Invalid username, role, or password")

    supabase_uid = user.get("supabase_uid")

    if supabase_uid:
        # New-flow account — Supabase Auth owns the password, verify there.
        try:
            auth_res = sb.auth.sign_in_with_password({
                "email": user["email"],
                "password": body.password,
            })
            if not getattr(auth_res, "session", None):
                raise HTTPException(401, "Invalid username, role, or password")
        except HTTPException:
            raise
        except Exception:
            raise HTTPException(401, "Invalid username, role, or password")
    else:
        # Legacy account — unchanged bcrypt check.
        if not user.get("password_hash") or not verify_password(body.password, user["password_hash"]):
            raise HTTPException(401, "Invalid username, role, or password")

    status_val = user.get("status") or "approved"

    if status_val == "pending":
        raise HTTPException(status_code=403, detail={
            "status": "pending",
            "message": "Your registration is still awaiting approval.",
            "redirect_url": "/waiting",
        })

    if status_val == "rejected":
        raise HTTPException(status_code=403, detail={
            "status": "rejected",
            "message": "Your registration request was rejected.",
            "reason": user.get("rejection_reason"),
            "redirect_url": "/rejected",
        })

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


# ── Registration ─────────────────────────────────────────────────────────────

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


class RegisterResponse(BaseModel):
    success:      bool
    message:      str
    token:        str
    redirect_url: str = "/waiting"


@router.post("/register", status_code=201, response_model=RegisterResponse,
             summary="Self-register a new portal user (Supabase Auth + approval workflow)",
             dependencies=[Depends(rate_limit("register", max_calls=5, window_seconds=600))])
def register(body: RegisterRequest):
    if body.role not in SELF_REGISTER_ROLES:
        raise HTTPException(400, f"Self-registration is only allowed for: {SELF_REGISTER_ROLES}")

    if body.role in ENROLLMENT_REQUIRED_ROLES and not (body.enrollment_no or body.username):
        raise HTTPException(400, "Enrollment number is required for this role")

    # duplicate username + role
    dup = (
        sb.table("users")
        .select("id")
        .eq("username", body.username)
        .eq("role", body.role)
        .execute()
    )
    if dup.data:
        raise HTTPException(409, "Username already exists for this role")

    # duplicate email in our profile table
    dup_email = sb.table("users").select("id").eq("email", body.email).execute()
    if dup_email.data:
        raise HTTPException(409, "Email address is already registered")

    # ── Create the credential in Supabase Auth (never hashed/stored by us) ──
    try:
        auth_res = sb.auth.sign_up({
            "email": body.email,
            "password": body.password,
            "options": {"data": {"full_name": body.full_name, "role": body.role}},
        })
    except Exception as e:
        msg = str(e).lower()
        if "already registered" in msg or "already exists" in msg or "user already" in msg:
            raise HTTPException(409, "Email address is already registered")
        raise HTTPException(502, "Could not create the authentication account. Please try again.")

    supabase_user = getattr(auth_res, "user", None)
    supabase_uid = getattr(supabase_user, "id", None) if supabase_user else None
    if not supabase_uid:
        # Supabase returns an empty user for an email that already exists,
        # to avoid leaking which emails are registered.
        raise HTTPException(409, "Email address is already registered")

    row = {
        "username":        body.username,
        "role":            body.role,
        "full_name":       body.full_name,
        "email":           body.email,
        "password_hash":   None,
        "phone":           body.phone,
        "enrollment_no":   body.enrollment_no or body.username,
        "department":      body.department,
        "programme":       body.programme,
        "batch":           body.batch,
        "designation":     body.designation,
        "is_active":       True,
        "status":          "pending",
        "supabase_uid":    supabase_uid,
    }
    res = sb.table("users").insert(row).execute()
    new_user = res.data[0]

    sb.table("audit_log").insert({
        "user_id": new_user["id"],
        "action":  "SELF_REGISTER",
        "detail":  f"{body.role} '{body.username}' self-registered — pending approval",
    }).execute()

    # Short-lived token so the Waiting page can poll /registration-status
    # even though the account isn't approved (and can't use /login) yet.
    token = create_access_token({
        "sub":  new_user["username"],
        "id":   new_user["id"],
        "role": new_user["role"],
    })

    return RegisterResponse(
        success=True,
        message=(
            "Account created. We've sent a verification email — please confirm it, "
            "then wait for admin approval before logging in."
        ),
        token=token,
        redirect_url="/waiting",
    )


@router.get("/check-username", summary="Check whether a username is available for a role")
def check_username(username: str, role: str):
    if role not in VALID_ROLES:
        raise HTTPException(400, f"Invalid role. Must be one of: {VALID_ROLES}")
    if not username.strip():
        return {"available": False}
    res = sb.table("users").select("id").eq("username", username.strip()).eq("role", role).execute()
    return {"available": not bool(res.data)}


@router.get("/registration-status", summary="Poll the status of a pending/rejected registration")
def registration_status(token: str = Depends(oauth2_scheme)):
    try:
        payload = decode_token(token)
        uid = payload.get("id")
        if not uid:
            raise HTTPException(401, "Invalid or expired token")
    except JWTError:
        raise HTTPException(401, "Invalid or expired token")

    res = sb.table("users").select(
        "status, rejection_reason, full_name, email, role"
    ).eq("id", uid).limit(1).execute()

    if not res.data:
        raise HTTPException(404, "Account not found")

    row = res.data[0]
    return {
        "status":           row.get("status") or "approved",
        "rejection_reason": row.get("rejection_reason"),
        "full_name":        row.get("full_name"),
        "email":            row.get("email"),
        "role":             row.get("role"),
    }
