"""Authentication routes."""

from datetime import datetime, timezone
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Request
from jose import JWTError
from pydantic import BaseModel, EmailStr, Field
from app.database import sb
from app.deps import oauth2_scheme
from app.security import create_access_token, decode_token, IS_DEV
from app.rate_limit import (
    rate_limit,
    get_client_ip,
    check_login_rate_limit,
    record_login_failure,
    clear_login_failures,
)

# Retry only idempotent Supabase reads. Never retry writes or authentication
# attempts, because their side effects/semantics may not be safe to repeat.
def _execute_read_with_retry(query, attempts: int = 3):
    last_error = None
    for attempt in range(attempts):
        try:
            return query.execute()
        except Exception as error:
            last_error = error
            if attempt < attempts - 1:
                import time
                time.sleep(0.2 * (attempt + 1))
    print(
        f"AUTH DATABASE READ FAILED after {attempts} attempts: "
        f"{type(last_error).__name__}: {str(last_error)[:180]}"
    )
    raise HTTPException(
        status_code=503,
        detail="Authentication service temporarily unavailable. Please retry.",
        headers={"Retry-After": "3"},
    )

router = APIRouter(tags=["Auth"])
VALID_ROLES = ("student", "faculty", "cr", "admin")
SELF_REGISTER_ROLES = ("student", "faculty")
ENROLLMENT_REQUIRED_ROLES = ("student", "cr")
ROLE_REDIRECTS = {"admin": "/dashboard/admin", "faculty": "/dashboard/faculty", "cr": "/dashboard/cr", "student": "/dashboard/student"}

class LoginRequest(BaseModel):
    username: str = Field(..., min_length=1)
    password: str = Field(..., min_length=1)
    role: Optional[str] = None
    model_config = {"str_strip_whitespace": True}

class LoginResponse(BaseModel):
    success: bool
    token: str
    token_type: str = "bearer"
    redirect_url: str
    user: dict

@router.post("/login", response_model=LoginResponse)
def login(body: LoginRequest, request: Request):
    client_ip = get_client_ip(request)
    check_login_rate_limit(body.username, client_ip)

    if body.role is not None and body.role not in VALID_ROLES:
        record_login_failure(body.username, client_ip)
        raise HTTPException(400, f"Invalid role. Must be one of: {VALID_ROLES}")

    user = None
    # 1. Look for approved account first by username
    q = sb.table("users").select("*").eq("username", body.username)
    if body.role is not None:
        q = q.eq("role", body.role)
    res = _execute_read_with_retry(q.limit(1))
    if res.data and (res.data[0].get("status") or "approved") == "approved":
        user = res.data[0]
    elif body.role in ("student", "cr", None):
        # Also check approved accounts by enrollment_no
        q_enr = sb.table("users").select("*").eq("enrollment_no", body.username)
        if body.role is not None:
            q_enr = q_enr.eq("role", body.role)
        res_enr = _execute_read_with_retry(q_enr.limit(1))
        if res_enr.data and (res_enr.data[0].get("status") or "approved") == "approved":
            user = res_enr.data[0]

    # 2. If no approved account found, look for pending/rejected accounts
    if not user:
        q_other = sb.table("users").select("*").eq("username", body.username)
        if body.role is not None:
            q_other = q_other.eq("role", body.role)
        res_other = _execute_read_with_retry(q_other.limit(1))
        if not res_other.data and body.role in ("student", "cr", None):
            q_other = sb.table("users").select("*").eq("enrollment_no", body.username)
            if body.role is not None:
                q_other = q_other.eq("role", body.role)
            res_other = _execute_read_with_retry(q_other.limit(1))
        if res_other.data:
            user = res_other.data[0]

    if not user:
        record_login_failure(body.username, client_ip)
        try:
            sb.table("audit_log").insert({
                "user_id": None,
                "action": "FAILED_LOGIN",
                "detail": f"Failed login for non-existent or unapproved username '{body.username}'",
                "ip": client_ip,
            }).execute()
        except Exception:
            pass
        raise HTTPException(401, "Invalid username or password")

    user_role = user.get("role")
    if not user_role:
        raise HTTPException(500, "User role missing in profile")
    if body.role is not None and body.role != user_role:
        record_login_failure(body.username, client_ip)
        raise HTTPException(401, "Invalid role for this user")
    if not user.get("is_active", True):
        record_login_failure(body.username, client_ip)
        raise HTTPException(401, "Invalid username or password")

    auth_uid = user.get("supabase_uid")

    # Authenticate password with Supabase Auth
    authenticated = False
    try:
        ar = sb.auth.sign_in_with_password({"email": user["email"], "password": body.password})
        authenticated = bool(getattr(ar, "session", None))
    except Exception as e:
        print(f"SUPABASE SIGNIN ERROR: username={body.username!r} type={type(e).__name__} detail={str(e)[:240]!r}")
        # If sign-in failed possibly due to unconfirmed email, attempt confirmed sign-in
        if auth_uid and "confirm" in str(e).lower():
            try:
                sb.auth.admin.update_user_by_id(auth_uid, {"email_confirm": True, "phone_confirm": True})
                rr = sb.auth.sign_in_with_password({"email": user["email"], "password": body.password})
                authenticated = bool(getattr(rr, "session", None))
            except Exception as repair:
                print(f"LOGIN AUTH REPAIR FAILED: {type(repair).__name__}")
        if IS_DEV and not authenticated and user.get("password_hash"):
            try:
                import bcrypt
                authenticated = bcrypt.checkpw(body.password.encode(), user["password_hash"].encode())
            except Exception:
                pass

    if not authenticated:
        record_login_failure(body.username, client_ip)
        try:
            sb.table("audit_log").insert({
                "user_id": user["id"],
                "action": "FAILED_LOGIN",
                "detail": f"Failed password for {user.get('role')} '{body.username}'",
                "ip": client_ip,
            }).execute()
        except Exception:
            pass
        raise HTTPException(401, "Invalid username or password")

    clear_login_failures(body.username, client_ip)

    # Credential is verified; now handle account status
    status_val = user.get("status") or "approved"
    if status_val == "pending":
        pending_token = create_access_token({"sub": user["username"], "id": user["id"], "role": user["role"], "purpose": "pending"})
        raise HTTPException(403, detail={"status": "pending", "redirect_url": "/waiting", "pending_token": pending_token, "message": "Your registration request is still pending approval."})
    if status_val == "rejected":
        raise HTTPException(403, detail={"status": "rejected", "redirect_url": "/rejected", "reason": user.get("rejection_reason"), "message": "Account registration was rejected."})
    if status_val != "approved":
        raise HTTPException(401, "Invalid username, role, or password")

    try:
        sb.table("users").update({"last_login": datetime.now(timezone.utc).isoformat()}).eq("id", user["id"]).execute()
    except Exception as e:
        print(f"LOGIN WARNING: {e!r}")

    try:
        sb.table("audit_log").insert({
            "user_id": user["id"],
            "action": "LOGIN",
            "detail": f"{user['role']} '{user['username']}' logged in",
            "ip": client_ip,
        }).execute()
    except Exception as e:
        print(f"LOGIN WARNING: audit log: {e!r}")

    token = create_access_token({"sub": user["username"], "id": user["id"], "role": user["role"], "purpose": "access"})
    # Return an explicit allowlist instead of the full database row. This keeps
    # internal fields such as supabase_uid, password_hash, and future columns
    # from leaking through the login response.
    safe_user_fields = (
        "id", "username", "role", "full_name", "email", "phone",
        "enrollment_no", "department", "programme", "batch", "semester",
        "section", "designation", "is_active",
    )
    safe_user = {key: user[key] for key in safe_user_fields if key in user}
    return LoginResponse(
        success=True,
        token=token,
        redirect_url=ROLE_REDIRECTS.get(user_role, "/"),
        user=safe_user,
    )

class ChangePasswordRequest(BaseModel):
    current_password: str = Field(..., min_length=1)
    new_password: str = Field(..., min_length=6)
    confirm_password: str = Field(..., min_length=6)

@router.post("/change-password")
def change_password(body: ChangePasswordRequest, token: str = Depends(oauth2_scheme)):
    try: payload=decode_token(token)
    except JWTError: raise HTTPException(401,"Your session has expired. Please log in again.")
    uid=payload.get("id")
    res=sb.table("users").select("id,username,email,is_active,status,supabase_uid").eq("id",uid).limit(1).execute()
    if not res.data: raise HTTPException(401,"User account not found")
    user=res.data[0]
    if not user.get("is_active",True) or (user.get("status") or "approved")!="approved": raise HTTPException(403,"Your account is not active")
    if not user.get("supabase_uid"): raise HTTPException(500,"This account has no linked Auth identity")
    if body.new_password!=body.confirm_password: raise HTTPException(400,"New passwords do not match")
    if body.current_password==body.new_password: raise HTTPException(400,"New password must be different from the current password")
    try:
        ar=sb.auth.sign_in_with_password({"email":user["email"],"password":body.current_password})
        if not getattr(ar,"session",None): raise ValueError()
    except Exception: raise HTTPException(400,"Current password is incorrect")
    try: sb.auth.admin.update_user_by_id(user["supabase_uid"],{"password":body.new_password})
    except Exception: raise HTTPException(502,"Could not update the password. Please try again.")
    return {"success":True,"message":"Password changed successfully."}

class RegisterRequest(BaseModel):
    username: str = Field(..., min_length=1); password: str = Field(..., min_length=6); role: str = Field(default="student"); full_name: str = Field(..., min_length=1); email: EmailStr; phone: Optional[str]=None; enrollment_no: Optional[str]=None; department: Optional[str]=None; domain: Optional[str]=None; programme: Optional[str]=None; batch: Optional[str]=None; semester: Optional[str]=None; section: Optional[str]=None; designation: Optional[str]=None
    model_config={"str_strip_whitespace":True}
class RegisterResponse(BaseModel):
    success: bool; message: str; token: str; redirect_url: str="/waiting"

@router.post("/register", status_code=201, response_model=RegisterResponse, dependencies=[Depends(rate_limit("register", max_calls=5, window_seconds=600))])
def register(body: RegisterRequest, request: Request):
    client_ip = get_client_ip(request)
    if "<" in body.full_name or ">" in body.full_name:
        raise HTTPException(400, "Full name cannot contain HTML characters (< or >)")
    if "<" in body.username or ">" in body.username:
        raise HTTPException(400, "Username cannot contain HTML characters (< or >)")
    if body.role not in SELF_REGISTER_ROLES: raise HTTPException(400,f"Self-registration is only allowed for: {SELF_REGISTER_ROLES}")
    if body.role == "faculty" and not body.domain:
        raise HTTPException(400,"Faculty domain is required")
    if body.role in ENROLLMENT_REQUIRED_ROLES:
        if not body.enrollment_no:
            raise HTTPException(400, "Please enter your enrollment number.")
        enr_clean = body.enrollment_no.strip()
        if enr_clean.isdigit() and len(enr_clean) != 11:
            raise HTTPException(400, "Enrollment number must be exactly 11 digits (e.g. 02616401521).")
        if not (IS_DEV and enr_clean.startswith("stu")) and not (enr_clean.isdigit() and len(enr_clean) == 11):
            raise HTTPException(400, "Enrollment number must be exactly 11 digits (e.g. 02616401521).")

        # Course code is the 7th–9th digits in the 11-digit enrollment number.
        # Keep this server-side check authoritative; client-side validation is only UX.
        if body.role == "student":
            course_text = " ".join(
                part.strip().lower()
                for part in (body.department or "", body.programme or "")
                if part and part.strip()
            )
            required_code = None
            if "b.a." in course_text or "ba hons" in course_text or "ba (hons" in course_text:
                if "english" in course_text:
                    required_code = "617"
                elif "economics" in course_text or "eco" in course_text:
                    required_code = "616"
            elif "m.a." in course_text or course_text.startswith("ma ") or " ma " in f" {course_text} ":
                if "economics" in course_text or "eco" in course_text:
                    required_code = "307"
                elif "english" in course_text:
                    required_code = "109"

            if required_code and enr_clean[6:9] != required_code:
                raise HTTPException(400, "Invalid Enrollment Number")

        existing_enr = sb.table("users").select("id, status, supabase_uid").eq("enrollment_no", enr_clean).execute().data or []
        for row_enr in existing_enr:
            if row_enr.get("status") == "approved":
                raise HTTPException(409, f"Enrollment number '{enr_clean}' is already registered. Please go to Sign In.")
            elif row_enr.get("status") == "pending":
                raise HTTPException(409, f"An account with enrollment number '{enr_clean}' is already submitted and waiting for admin approval.")
            elif row_enr.get("status") == "rejected":
                old_uid = row_enr.get("supabase_uid")
                if old_uid and not str(old_uid).startswith("local_"):
                    try: sb.auth.admin.delete_user(old_uid)
                    except Exception: pass
                sb.table("users").delete().eq("id", row_enr["id"]).execute()

    existing_un = sb.table("users").select("id, status, supabase_uid").eq("username", body.username.strip()).eq("role", body.role).execute().data or []
    for r in existing_un:
        if r.get("status") == "approved":
            raise HTTPException(409, f"This {body.role} ID/username is already registered. Please sign in instead.")
        elif r.get("status") == "pending":
            raise HTTPException(409, f"This {body.role} ID/username is currently waiting for admin approval.")
        elif r.get("status") == "rejected":
            old_uid = r.get("supabase_uid")
            if old_uid and not str(old_uid).startswith("local_"):
                try: sb.auth.admin.delete_user(old_uid)
                except Exception: pass
            sb.table("users").delete().eq("id", r["id"]).execute()

    clean_email = str(body.email).strip().lower()
    existing_em = sb.table("users").select("id, status, supabase_uid, email").execute().data or []
    for r in existing_em:
        if str(r.get("email", "")).strip().lower() == clean_email:
            if r.get("status") == "approved":
                raise HTTPException(409, "This email address is already registered. Please sign in or use a different email.")
            elif r.get("status") == "pending":
                raise HTTPException(409, "An account with this email address is already waiting for admin approval.")
            elif r.get("status") == "rejected":
                old_uid = r.get("supabase_uid")
                if old_uid and not str(old_uid).startswith("local_"):
                    try: sb.auth.admin.delete_user(old_uid)
                    except Exception: pass
                sb.table("users").delete().eq("id", r["id"]).execute()
    phone = body.phone.strip() if body.phone else None
    if phone:
        if phone.isdigit() and len(phone)==10: phone="+91"+phone
        elif not phone.startswith("+") or len(phone)<10: raise HTTPException(400,"Please enter a valid mobile number with country code, e.g. +919876543210")
    supabase_uid=None
    pwd_hash=None
    try:
        user_meta={"full_name":body.full_name,"role":body.role}
        user_creds={"email":str(body.email),"password":body.password,"email_confirm":True,"phone_confirm":True,"user_metadata":user_meta}
        if phone: user_creds["phone"]=phone
        created = sb.auth.admin.create_user(user_creds)
        au = getattr(created, "user", None)
        supabase_uid = getattr(au, "id", None) if au else None
    except Exception as e:
        print(f"REGISTER AUTH ADMIN ERROR: type={type(e).__name__} detail={str(e)[:300]!r}")
        msg = str(e).lower()
        if "already registered" in msg or "already exists" in msg or "user already" in msg:
            raise HTTPException(409, "This email is already registered. Please sign in or use another email.")
        if IS_DEV:
            import bcrypt
            supabase_uid = f"local_{body.username}"
            pwd_hash = bcrypt.hashpw(body.password.encode(), bcrypt.gensalt()).decode()
        else:
            raise HTTPException(502, "Could not set up your login account right now. Please check your internet connection or try again in a few minutes.")
    if not supabase_uid:
        raise HTTPException(502, "Could not set up your login account right now. Please try again.")
    row = {
        "username": body.username,
        "role": body.role,
        "full_name": body.full_name,
        "email": str(body.email),
        "phone": phone,
        "enrollment_no": body.enrollment_no or body.username,
        "department": body.department,
        "domain": body.domain,
        "programme": body.programme,
        "batch": body.batch,
        "semester": body.semester,
        "section": body.section,
        "designation": body.designation,
        "is_active": True,
        "status": "pending",
        "email_verified": True,
        "phone_verified": True,
        "supabase_uid": supabase_uid,
        "password_hash": pwd_hash
    }
    try:
        res = sb.table("users").insert(row).execute()
        if not res.data:
            raise RuntimeError("Insert returned no row")
        new_user = res.data[0]
    except Exception as e:
        print(f"REGISTER ERROR: profile insert failed: {e!r}")
        try:
            sb.auth.admin.delete_user(supabase_uid)
        except Exception:
            pass
        raise HTTPException(502, "Could not save your registration details. Please make sure all details are correct and try again.")
    try: sb.table("audit_log").insert({"user_id":new_user["id"],"action":"SELF_REGISTER","detail":f"{body.role} '{body.username}' self-registered — pending approval","ip":client_ip}).execute()
    except Exception as e: print(f"REGISTER WARNING: {e!r}")
    token=create_access_token({"sub":new_user["username"],"id":new_user["id"],"role":new_user["role"],"purpose":"pending"})
    return RegisterResponse(success=True,message="Account created. Your registration request has been submitted and is awaiting admin approval.",token=token,redirect_url="/waiting")

@router.get("/check-username", dependencies=[Depends(rate_limit("check_username", max_calls=30, window_seconds=60))])
def check_username(username:str,role:str):
    if role not in VALID_ROLES: raise HTTPException(400,f"Invalid role. Must be one of: {VALID_ROLES}")
    return {"available":not bool(sb.table("users").select("id").eq("username",username.strip()).eq("role",role).execute().data)}

@router.get("/registration-status")
def registration_status(token: str = Depends(oauth2_scheme)):
    try:
        payload = decode_token(token)
    except JWTError:
        raise HTTPException(401, "Invalid or expired token")

    # This endpoint is used by the waiting/registration flow only. Do not let
    # reset or ordinary access tokens query registration metadata.
    if payload.get("purpose") != "pending":
        raise HTTPException(401, "A pending-registration token is required.")
    if payload.get("role") not in SELF_REGISTER_ROLES:
        raise HTTPException(403, "Registration status is not available for this account.")

    uid = payload.get("id")
    if not uid:
        raise HTTPException(401, "Invalid or expired token")

    res = _execute_read_with_retry(
        sb.table("users")
        .select("status,rejection_reason,full_name,email,role,email_verified,phone_verified")
        .eq("id", uid)
        .limit(1)
    )
    if not res.data:
        raise HTTPException(404, "Account not found")
    row = res.data[0]
    # Prevent accidental cross-account disclosure if a malformed token is issued.
    if row.get("role") not in SELF_REGISTER_ROLES:
        raise HTTPException(403, "Registration status is not available for this account.")
    return row
