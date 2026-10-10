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
    res = q.limit(1).execute()
    if res.data and (res.data[0].get("status") or "approved") == "approved":
        user = res.data[0]
    elif body.role in ("student", "cr", None):
        # Also check approved accounts by enrollment_no
        q_enr = sb.table("users").select("*").eq("enrollment_no", body.username)
        if body.role is not None:
            q_enr = q_enr.eq("role", body.role)
        res_enr = q_enr.limit(1).execute()
        if res_enr.data and (res_enr.data[0].get("status") or "approved") == "approved":
            user = res_enr.data[0]

    # 2. If no approved account found, look for pending/rejected accounts
    if not user:
        q_other = sb.table("users").select("*").eq("username", body.username)
        if body.role is not None:
            q_other = q_other.eq("role", body.role)
        res_other = q_other.limit(1).execute()
        if not res_other.data and body.role in ("student", "cr", None):
            q_other = sb.table("users").select("*").eq("enrollment_no", body.username)
            if body.role is not None:
                q_other = q_other.eq("role", body.role)
            res_other = q_other.limit(1).execute()
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
    user.pop("password_hash", None)
    return LoginResponse(success=True, token=token, redirect_url=ROLE_REDIRECTS.get(user_role, "/"), user=user)

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
            raise HTTPException(400, "Enrollment number is required for this role")
        enr_clean = body.enrollment_no.strip()
        existing_enr = sb.table("users").select("id, status, supabase_uid").eq("enrollment_no", enr_clean).execute().data or []
        for row_enr in existing_enr:
            if row_enr.get("status") in ("approved", "pending"):
                raise HTTPException(409, "Enrollment number is already registered or pending approval")
            elif row_enr.get("status") == "rejected":
                old_uid = row_enr.get("supabase_uid")
                if old_uid and not str(old_uid).startswith("local_"):
                    try: sb.auth.admin.delete_user(old_uid)
                    except Exception: pass
                sb.table("users").delete().eq("id", row_enr["id"]).execute()

    existing_un = sb.table("users").select("id, status, supabase_uid").eq("username", body.username).eq("role", body.role).execute().data or []
    for r in existing_un:
        if r.get("status") in ("approved", "pending"):
            raise HTTPException(409, "Username already exists for this role")
        elif r.get("status") == "rejected":
            old_uid = r.get("supabase_uid")
            if old_uid and not str(old_uid).startswith("local_"):
                try: sb.auth.admin.delete_user(old_uid)
                except Exception: pass
            sb.table("users").delete().eq("id", r["id"]).execute()

    existing_em = sb.table("users").select("id, status, supabase_uid").eq("email", body.email).execute().data or []
    for r in existing_em:
        if r.get("status") in ("approved", "pending"):
            raise HTTPException(409, "Email address is already registered")
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
        created=sb.auth.admin.create_user(user_creds)
        au=getattr(created,"user",None); supabase_uid=getattr(au,"id",None) if au else None
    except Exception as e:
        print(f"REGISTER AUTH ADMIN ERROR: type={type(e).__name__} detail={str(e)[:300]!r}")
        if IS_DEV and "not configured" in str(e).lower():
            import bcrypt
            supabase_uid=f"local_{body.username}"
            pwd_hash=bcrypt.hashpw(body.password.encode(), bcrypt.gensalt()).decode()
        else:
            raise HTTPException(502,"Could not create the authentication account. Please try again.")
    if not supabase_uid: raise HTTPException(502,"Authentication account was not created. Please try again.")
    row={"username":body.username,"role":body.role,"full_name":body.full_name,"email":str(body.email),"phone":phone,"enrollment_no":body.enrollment_no or body.username,"department":body.department,"domain":body.domain,"programme":body.programme,"batch":body.batch,"semester":body.semester,"section":body.section,"designation":body.designation,"is_active":True,"status":"pending","email_verified":True,"phone_verified":True,"supabase_uid":supabase_uid,"password_hash":pwd_hash}
    try:
        res=sb.table("users").insert(row).execute()
        if not res.data: raise RuntimeError("Insert returned no row")
        new_user=res.data[0]
    except Exception as e:
        print(f"REGISTER ERROR: profile insert failed: {e!r}")
        try: sb.auth.admin.delete_user(supabase_uid)
        except Exception: pass
        raise HTTPException(502,"Could not complete registration — your details could not be saved. Please try again.")
    try: sb.table("audit_log").insert({"user_id":new_user["id"],"action":"SELF_REGISTER","detail":f"{body.role} '{body.username}' self-registered — pending approval","ip":client_ip}).execute()
    except Exception as e: print(f"REGISTER WARNING: {e!r}")
    token=create_access_token({"sub":new_user["username"],"id":new_user["id"],"role":new_user["role"],"purpose":"pending"})
    return RegisterResponse(success=True,message="Account created. Your registration request has been submitted and is awaiting admin approval.",token=token,redirect_url="/waiting")

@router.get("/check-username", dependencies=[Depends(rate_limit("check_username", max_calls=30, window_seconds=60))])
def check_username(username:str,role:str):
    if role not in VALID_ROLES: raise HTTPException(400,f"Invalid role. Must be one of: {VALID_ROLES}")
    return {"available":not bool(sb.table("users").select("id").eq("username",username.strip()).eq("role",role).execute().data)}

@router.get("/registration-status")
def registration_status(token:str=Depends(oauth2_scheme)):
    try:
        p=decode_token(token); uid=p.get("id")
        if not uid: raise HTTPException(401,"Invalid or expired token")
    except JWTError: raise HTTPException(401,"Invalid or expired token")
    res=sb.table("users").select("status,rejection_reason,full_name,email,role,email_verified,phone_verified").eq("id",uid).limit(1).execute()
    if not res.data: raise HTTPException(404,"Account not found")
    row=res.data[0]
    return row
