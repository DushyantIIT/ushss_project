"""Authentication routes."""

from datetime import datetime, timezone
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from jose import JWTError
from pydantic import BaseModel, EmailStr, Field
from app.database import sb
from app.deps import oauth2_scheme
from app.security import create_access_token, decode_token
from app.rate_limit import rate_limit
from app.seed import DEMO_USERS

router = APIRouter(tags=["Auth"])
VALID_ROLES = ("student", "faculty", "cr", "admin")
SELF_REGISTER_ROLES = ("student", "faculty", "cr")
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

@router.post("/login", response_model=LoginResponse, dependencies=[Depends(rate_limit("login", max_calls=10, window_seconds=300))])
def login(body: LoginRequest):
    if body.role is not None and body.role not in VALID_ROLES:
        raise HTTPException(400, f"Invalid role. Must be one of: {VALID_ROLES}")
    pending_res = sb.table("users").select("*").eq("enrollment_no", body.username).eq("status", "pending").limit(1).execute()
    if pending_res.data:
        u = pending_res.data[0]
        raise HTTPException(403, detail={"status":"pending","redirect_url":"/waiting","pending_token":create_access_token({"sub":u["username"],"id":u["id"],"role":u["role"]}),"message":"Your registration request is still pending approval."})
    res = sb.table("users").select("*").eq("username", body.username).limit(1).execute()
    if not res.data: raise HTTPException(401, "Invalid username or password")
    user = res.data[0]; user_role = user.get("role")
    if not user_role: raise HTTPException(500, "User role missing in profile")
    if body.role is not None and body.role != user_role: raise HTTPException(401, "Invalid role for this user")
    if not user.get("is_active", True): raise HTTPException(401, "Invalid username or password")
    status_val = user.get("status") or "approved"
    if status_val == "pending": raise HTTPException(403, detail={"status":"pending","redirect_url":"/waiting","message":"Account registration is pending approval."})
    if status_val == "rejected": raise HTTPException(403, detail={"status":"rejected","redirect_url":"/rejected","reason":user.get("rejection_reason"),"message":"Account registration was rejected."})
    if status_val != "approved": raise HTTPException(401, "Invalid username, role, or password")

    # Verification is intentionally disabled for this portal. For every linked
    # account, confirm both identifiers before password sign-in so Supabase Auth
    # remains the actual authentication provider without email/SMS verification.
    auth_uid = user.get("supabase_uid")
    if auth_uid:
        try:
            sb.auth.admin.update_user_by_id(auth_uid, {"email_confirm": True, "phone_confirm": True})
        except Exception as e:
            print(f"LOGIN AUTH CONFIRM SYNC FAILED: {type(e).__name__}: {str(e)[:160]}")

    authenticated=False
    try:
        ar=sb.auth.sign_in_with_password({"email":user["email"],"password":body.password})
        authenticated=bool(getattr(ar,"session",None))
    except Exception as e:
        print(f"SUPABASE SIGNIN ERROR: username={body.username!r} type={type(e).__name__} detail={str(e)[:240]!r}")
        if auth_uid:
            try:
                sb.auth.admin.update_user_by_id(auth_uid, {"email_confirm": True, "phone_confirm": True})
                rr=sb.auth.sign_in_with_password({"email":user["email"],"password":body.password})
                authenticated=bool(getattr(rr,"session",None))
            except Exception as repair: print(f"LOGIN AUTH REPAIR FAILED: {type(repair).__name__}")
        if not authenticated:
            demo=next((d for d in DEMO_USERS if d["username"]==body.username and d["role"]==user_role and d["password"]==body.password),None)
            if demo:
                try:
                    uid=user.get("supabase_uid")
                    if uid: sb.auth.admin.update_user_by_id(uid,{"password":demo["password"],"email_confirm":True,"phone_confirm":True})
                    rr=sb.auth.sign_in_with_password({"email":demo["email"],"password":demo["password"]})
                    authenticated=bool(getattr(rr,"session",None))
                except Exception as de: print(f"DEMO LOGIN REPAIR FAILED: {type(de).__name__}")
        if not authenticated and user.get("password_hash"):
            try:
                import bcrypt
                authenticated=bcrypt.checkpw(body.password.encode(),user["password_hash"].encode())
            except Exception: pass
    if not authenticated: raise HTTPException(401,"Invalid username or password")
    try: sb.table("users").update({"last_login":datetime.now(timezone.utc).isoformat()}).eq("id",user["id"]).execute()
    except Exception as e: print(f"LOGIN WARNING: {e!r}")
    try: sb.table("audit_log").insert({"user_id":user["id"],"action":"LOGIN","detail":f"{user['role']} '{user['username']}' logged in"}).execute()
    except Exception as e: print(f"LOGIN WARNING: audit log: {e!r}")
    token=create_access_token({"sub":user["username"],"id":user["id"],"role":user["role"]}); user.pop("password_hash",None)
    return LoginResponse(success=True,token=token,redirect_url=ROLE_REDIRECTS.get(user_role,"/"),user=user)

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
    username: str = Field(..., min_length=1); password: str = Field(..., min_length=6); role: str = Field(default="student"); full_name: str = Field(..., min_length=1); email: EmailStr; phone: str; enrollment_no: Optional[str]=None; department: Optional[str]=None; programme: Optional[str]=None; batch: Optional[str]=None; semester: Optional[str]=None; designation: Optional[str]=None
    model_config={"str_strip_whitespace":True}
class RegisterResponse(BaseModel):
    success: bool; message: str; token: str; redirect_url: str="/waiting"

@router.post("/register", status_code=201, response_model=RegisterResponse, dependencies=[Depends(rate_limit("register", max_calls=5, window_seconds=600))])
def register(body:RegisterRequest):
    if body.role not in SELF_REGISTER_ROLES: raise HTTPException(400,f"Self-registration is only allowed for: {SELF_REGISTER_ROLES}")
    if body.role in ENROLLMENT_REQUIRED_ROLES and not body.enrollment_no: raise HTTPException(400,"Enrollment number is required for this role")
    if sb.table("users").select("id").eq("username",body.username).eq("role",body.role).execute().data: raise HTTPException(409,"Username already exists for this role")
    if sb.table("users").select("id").eq("email",body.email).execute().data: raise HTTPException(409,"Email address is already registered")
    phone=body.phone.strip()
    if phone.isdigit() and len(phone)==10: phone="+91"+phone
    elif not phone.startswith("+") or len(phone)<10: raise HTTPException(400,"Please enter a valid mobile number with country code, e.g. +919876543210")
    supabase_uid=None
    try:
        # Create the Auth identity on the server and auto-confirm both identifiers.
        # This does not expose the service-role key and works independently of
        # Supabase's public signup/verification settings.
        created=sb.auth.admin.create_user({"email":str(body.email),"phone":phone,"password":body.password,"email_confirm":True,"phone_confirm":True,"user_metadata":{"full_name":body.full_name,"role":body.role}})
        au=getattr(created,"user",None); supabase_uid=getattr(au,"id",None) if au else None
    except Exception as e:
        print(f"REGISTER AUTH ADMIN ERROR: type={type(e).__name__} detail={str(e)[:300]!r}")
        raise HTTPException(502,"Could not create the authentication account. Please try again.")
    if not supabase_uid: raise HTTPException(502,"Authentication account was not created. Please try again.")
    row={"username":body.username,"role":body.role,"full_name":body.full_name,"email":str(body.email),"phone":phone,"enrollment_no":body.enrollment_no or body.username,"department":body.department,"programme":body.programme,"batch":body.batch,"semester":body.semester,"designation":body.designation,"is_active":True,"status":"pending","email_verified":True,"phone_verified":True,"supabase_uid":supabase_uid}
    try:
        res=sb.table("users").insert(row).execute()
        if not res.data: raise RuntimeError("Insert returned no row")
        new_user=res.data[0]
    except Exception as e:
        print(f"REGISTER ERROR: profile insert failed: {e!r}")
        try: sb.auth.admin.delete_user(supabase_uid)
        except Exception: pass
        raise HTTPException(502,"Could not complete registration — your details could not be saved. Please try again.")
    try: sb.table("audit_log").insert({"user_id":new_user["id"],"action":"SELF_REGISTER","detail":f"{body.role} '{body.username}' self-registered — pending approval"}).execute()
    except Exception as e: print(f"REGISTER WARNING: {e!r}")
    token=create_access_token({"sub":new_user["username"],"id":new_user["id"],"role":new_user["role"]})
    return RegisterResponse(success=True,message="Account created. Your registration request has been submitted and is awaiting admin approval.",token=token,redirect_url="/waiting")

@router.get("/check-username")
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
