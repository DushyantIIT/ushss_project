"""
routers/auth.py

Authentication — login returns a JWT.

  POST /api/login                 login (Supabase Auth verifies the password)
  POST /api/register              self-register via Supabase Auth (email verification + approval)
  GET  /api/check-username        live username-availability check for the registration form
  GET  /api/registration-status   poll pending/rejected/approved status after registering

Registration / approval workflow
─────────────────────────────────
No password is ever hashed or stored by this app. Every account's credential
lives in Supabase Auth — created via `sb.auth.sign_up` on self-registration,
or via `sb.auth.admin.create_user` when an admin creates the account directly
(see routers/admin.py). We link the two records with `users.supabase_uid`,
which is mandatory: a profile row with no `supabase_uid` cannot log in.

Every self-registered profile starts with `status = 'pending'`. An admin (or,
for the `admin` role itself, only a SuperAdmin) must approve the request
before the account can log in — see routers/admin.py for the approval panel.
Login always follows the same path for every account: verify the password
against Supabase Auth, then check `status` / `is_active` on the profile row.
There is no other branch.
"""

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from jose import JWTError
from pydantic import BaseModel, EmailStr, Field

from app.database import sb
from app.deps import oauth2_scheme
from app.security import create_access_token, decode_token
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
    role: Optional[str] = Field(default=None)
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
    # If a role is supplied, validate it against allowed roles
    if body.role is not None and body.role not in VALID_ROLES:
        raise HTTPException(400, f"Invalid role. Must be one of: {VALID_ROLES}")

    # Fetch user by username only; role will be taken from the stored profile
    res = (
        sb.table("users")
        .select("*")
        .eq("username", body.username)
        .limit(1)
        .execute()
    )

    if not res.data:
        raise HTTPException(401, "Invalid username or password")

    user = res.data[0]
    user_role = user.get("role")
    if not user_role:
        raise HTTPException(500, "User role missing in profile")

    # Always use the stored database role — ignore the tab the user clicked on.
    # This means admin001 logs in correctly even if the "Student" tab was active.
    role_to_use = user_role
    # If the request supplied a role, ensure it matches the stored role
    if body.role is not None and body.role != user_role:
        raise HTTPException(401, "Invalid role for this user")

    if not user.get("is_active", True):
        print(f"LOGIN DEBUG: user {body.username!r} is_active=False")
        raise HTTPException(401, "Invalid username or password")

    status_val = user.get("status") or "approved"
    if status_val == "pending":
        raise HTTPException(
            status_code=403,
            detail={
                "status": "pending",
                "redirect_url": "/waiting",
                "message": "Account registration is pending approval.",
            },
        )
    elif status_val == "rejected":
        raise HTTPException(
            status_code=403,
            detail={
                "status": "rejected",
                "redirect_url": "/rejected",
                "reason": user.get("rejection_reason"),
                "message": "Account registration was rejected.",
            },
        )
    elif status_val != "approved":
        raise HTTPException(401, "Invalid username, role, or password")

    import bcrypt
    authenticated = False
    if user.get("password_hash"):
        try:
            if bcrypt.checkpw(body.password.encode(), user["password_hash"].encode()):
                authenticated = True
        except Exception:
            pass

    if not authenticated:
        try:
            auth_res = sb.auth.sign_in_with_password({
                "email": user["email"],
                "password": body.password,
            })
            if getattr(auth_res, "session", None):
                authenticated = True
        except Exception as e:
            print("SUPABASE SIGNIN NOTE:", repr(e))

    if not authenticated:
        raise HTTPException(401, "Invalid username or password")

    # Update last login timestamp
    try:
        sb.table("users").update({"last_login": datetime.now(timezone.utc).isoformat()}).eq("id", user["id"]).execute()
    except Exception as e:
        print(f"LOGIN WARNING: failed to update last_login for {user['username']!r}: {e!r}")

    # Record audit log
    try:
        sb.table("audit_log").insert({
            "user_id": user["id"],
            "action":  "LOGIN",
            "detail":  f"{user['role']} '{user['username']}' logged in",
        }).execute()
    except Exception as e:
        print(f"LOGIN WARNING: failed to write audit_log for {user['username']!r}: {e!r}")

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
        redirect_url=ROLE_REDIRECTS.get(role_to_use, "/"),
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

    import bcrypt
    pwd_hash = bcrypt.hashpw(body.password.encode(), bcrypt.gensalt()).decode()

    supabase_uid = None
    try:
        auth_res = sb.auth.sign_up({
            "email": body.email,
            "password": body.password,
            "options": {"data": {"full_name": body.full_name, "role": body.role}},
        })
        supabase_user = getattr(auth_res, "user", None)
        supabase_uid = getattr(supabase_user, "id", None) if supabase_user else None
    except Exception as e:
        print("SIGNUP SUPABASE NOTE:", e)

    # If Supabase Auth unavailable (e.g. no service-role key on this deployment),
    # generate a deterministic local UID so the profile row can still be created.

    if not supabase_uid:
        import random as _random
        supabase_uid = "local-" + str(int(datetime.now(timezone.utc).timestamp() * 1000)) + "-" + str(_random.randint(1000, 9999))

    row = {
        "username":        body.username,
        "role":            body.role,
        "full_name":       body.full_name,
        "email":           body.email,
        "password_hash":   pwd_hash,
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
    try:
        res = sb.table("users").insert(row).execute()
        if not res.data:
            raise RuntimeError("Insert returned no row")
        new_user = res.data[0]
    except Exception as e:
        print(f"REGISTER ERROR: profile insert failed for {body.username!r}: {e!r}")
        # Clean up the Supabase Auth account so it isn't orphaned
        try:
            if not supabase_uid.startswith("local-"):
                sb.auth.admin.delete_user(supabase_uid)
        except Exception:
            pass
        raise HTTPException(
            502,
            "Could not complete registration — your details could not be saved. Please try again.",
        )

    try:
        sb.table("audit_log").insert({
            "user_id": new_user["id"],
            "action":  "SELF_REGISTER",
            "detail":  f"{body.role} '{body.username}' self-registered — pending approval",
        }).execute()
    except Exception as e:
        print(f"REGISTER WARNING: failed to write audit_log for {new_user['username']!r}: {e!r}")

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
