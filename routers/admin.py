"""
routers/admin.py

ADMIN ONLY  full rights over every table.

Users
  GET    /api/admin/users
  POST   /api/admin/users
  GET    /api/admin/users/{uid}
  PUT    /api/admin/users/{uid}
  DELETE /api/admin/users/{uid}
  PATCH  /api/admin/users/{uid}/toggle

Timetable  (admin owns the schedule)
  GET    /api/admin/timetable
  POST   /api/admin/timetable
  PUT    /api/admin/timetable/{slot_id}
  DELETE /api/admin/timetable/{slot_id}

Attendance oversight
  GET    /api/admin/attendance/sessions
  PATCH  /api/admin/attendance/sessions/{sid}/close
  DELETE /api/admin/attendance/sessions/{sid}
  PATCH  /api/admin/attendance/records/{rid}       override a student's status
  GET    /api/admin/attendance/report              full report

Events & News
  POST/PUT/DELETE /api/admin/events/{eid}
  POST/PUT/DELETE /api/admin/news/{nid}

Faculty Directory
  POST/PUT/DELETE /api/admin/faculty-directory/{fid}

System
  GET /api/admin/stats
  GET /api/admin/audit
  GET /api/admin/messages
  PATCH /api/admin/messages/{mid}/read
  POST /api/admin/reset-password               reset any user's password
"""

from datetime import date as DateType, datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, EmailStr, Field

from app.database import sb
from app.deps import require_admin, require_super_admin
from app.email_utils import send_approval_email, send_rejection_email

router = APIRouter(prefix="/admin", tags=["Admin"])

VALID_ROLES = ("student", "faculty", "cr", "admin")

def _audit(admin_id: int, action: str, detail: str, ip: str = None):
    row = {"user_id": admin_id, "action": action, "detail": detail}
    if ip:
        row["ip"] = ip
    try:
        sb.table("audit_log").insert(row).execute()
    except Exception as e:
        print(f"AUDIT LOG WARNING: {e!r}")


# 
#  USERS
# 

class UserCreate(BaseModel):
    username:      str
    password:      str = Field(..., min_length=6)
    role:          str
    full_name:     str
    email:         EmailStr
    phone:         Optional[str] = None
    designation:   Optional[str] = None
    enrollment_no: Optional[str] = None
    department:    Optional[str] = None
    domain:        Optional[str] = None
    programme:     Optional[str] = None
    batch:         Optional[str] = None
    semester:      Optional[str] = None
    is_active:     bool = True


def _validate_programme_batch(programme: Optional[str], batch: Optional[str], role: Optional[str] = None):
    if role not in (None, "student", "cr"):
        return
    if not programme or not batch:
        return
    p=programme.lower()
    duration = 4 if ("b.a." in p or "b.a " in p or "ba english" in p or "ba economics" in p) else 2 if ("m.a." in p or "m.a " in p or "ma english" in p or "ma economics" in p) else None
    if duration is None:
        return
    import re
    m=re.fullmatch(r"(\d{4})-(\d{4})", str(batch).strip())
    if not m or int(m.group(2))-int(m.group(1)) != duration:
        raise HTTPException(400, f"Batch must span exactly {duration} years for {programme}.")

class UserUpdate(BaseModel):
    full_name:     Optional[str]      = None
    email:         Optional[EmailStr] = None
    phone:         Optional[str]      = None
    designation:   Optional[str]      = None
    enrollment_no: Optional[str]      = None
    department:    Optional[str]      = None
    programme:     Optional[str]      = None
    batch:         Optional[str]      = None
    semester:      Optional[str]      = None
    domain:         Optional[str]      = None
    is_active:     Optional[bool]     = None
    password:      Optional[str]      = Field(None, min_length=6)
    role:          Optional[str]      = None   # SuperAdmin only — see update_user()


@router.get("/users", summary="List all users")
def list_users(
    role:      Optional[str]  = Query(None),
    search:    Optional[str]  = Query(None),
    is_active: Optional[bool] = Query(None),
    admin: dict = Depends(require_admin),
):
    q = sb.table("users").select("*,full_name")
    # Admin management lists all accounts, including Super Admins.
    # Protected-account permissions are enforced separately by update/delete endpoints.
    if role:      q = q.eq("role", role)
    if is_active is not None: q = q.eq("is_active", is_active)
    res = q.order("created_at", desc=True).execute()
    users = res.data or []

    # text search (supabase-py doesn't chain ilike easily across cols  filter in Python)
    if search:
        s = search.lower()
        users = [u for u in users if
                 s in (u.get("full_name") or "").lower() or
                 s in (u.get("username") or "").lower() or
                 s in (u.get("email") or "").lower()]
    return {"users": users, "total": len(users)}


@router.get("/users/{uid}", summary="Get single user")
def get_user(uid: int, admin: dict = Depends(require_admin)):
    res = sb.table("users").select("*").eq("id", uid).limit(1).execute()
    if not res.data:
        raise HTTPException(404, "User not found")
    return res.data[0]


@router.post("/users", status_code=201, summary="Create a user")
def create_user(body: UserCreate, admin: dict = Depends(require_admin)):
    # Class Representatives are existing students promoted by Admin; they cannot be created as new accounts.
    if body.role == "cr":
        raise HTTPException(400, "A Class Representative must be assigned from an existing Student account.")
    # Only Super Admin can create other admin accounts
    if body.role == "admin" and not admin.get("is_super_admin", False):
        raise HTTPException(403, "Only the Super Admin can create new administrators.")
    _validate_programme_batch(body.programme, body.batch, body.role)
    if body.role not in VALID_ROLES:
        raise HTTPException(400, f"Invalid role. Must be one of: {VALID_ROLES}")
    if "<" in body.full_name or ">" in body.full_name:
        raise HTTPException(400, "Full name cannot contain HTML characters (< or >)")

    # check duplicate username+role
    dup = sb.table("users").select("id").eq("username", body.username).eq("role", body.role).execute()
    if dup.data:
        raise HTTPException(409, "Username already exists for this role")

    # check duplicate email
    dup_email = sb.table("users").select("id").eq("email", body.email).execute()
    if dup_email.data:
        raise HTTPException(409, "Email already in use")

    try:
        auth_res = sb.auth.admin.create_user({
            "email":         body.email,
            "password":      body.password,
            "email_confirm": True,
            "user_metadata": {"full_name": body.full_name, "role": body.role},
        })
    except Exception as e:
        msg = str(e).lower()
        if "already registered" in msg or "already exists" in msg or "user already" in msg:
            raise HTTPException(409, "Email address is already registered")
        raise HTTPException(502, "Could not create the authentication account. Please try again.")

    supabase_user = getattr(auth_res, "user", None)
    supabase_uid = getattr(supabase_user, "id", None) if supabase_user else None
    if not supabase_uid:
        raise HTTPException(502, "Could not create the authentication account. Please try again.")

    row = {
        "username":      body.username,
        "role":          body.role,
        "full_name":     body.full_name,
        "email":         body.email,
        "password_hash": None,
        "phone":         body.phone,
        "designation":   body.designation,
        "enrollment_no": body.enrollment_no,
        "department":    body.department,
        "domain":        body.domain,
        "programme":     body.programme,
        "batch":         body.batch,
        "semester":      body.semester,
        "is_active":     body.is_active,
        "supabase_uid":  supabase_uid,
    }
    try:
        res = sb.table("users").insert(row).execute()
        if not res.data:
            raise RuntimeError("Insert returned no row")
    except Exception:
        try:
            sb.auth.admin.delete_user(supabase_uid)
        except Exception:
            pass
        raise HTTPException(502, "Could not complete user creation. Please try again.")

    _audit(admin["id"], "CREATE_USER", f"Created {body.role} '{body.username}'")
    return res.data[0]


@router.put("/users/{uid}", summary="Update a user")
def update_user(uid: int, body: UserUpdate, admin: dict = Depends(require_admin)):
    existing = sb.table("users").select("id,username,role,is_super_admin,supabase_uid,programme,batch,domain,email").eq("id", uid).limit(1).execute()
    if not existing.data:
        raise HTTPException(
            404,
            "User not found"
        )

    target = existing.data[0]

    # A regular admin cannot modify the Super Admin.
    # The Super Admin can still modify their own account.
    if (
        target.get("is_super_admin", False)
        and uid != admin["id"]
    ):
        raise HTTPException(
            status_code=403,
            detail="The Super Admin account cannot "
                   "be modified by another admin."
        )

    updates = body.model_dump(exclude_none=True)
    effective_programme = updates.get("programme", target.get("programme"))
    effective_batch = updates.get("batch", target.get("batch"))
    _validate_programme_batch(effective_programme, effective_batch, target.get("role"))

    if "full_name" in updates and ("<" in str(updates["full_name"]) or ">" in str(updates["full_name"])):
        raise HTTPException(400, "Full name cannot contain HTML characters (< or >)")

    if "role" in updates:
        new_role = updates["role"]
        current_role = target.get("role")
        if new_role not in VALID_ROLES:
            raise HTTPException(400, f"Invalid role. Must be one of: {VALID_ROLES}")
        if not admin.get("is_super_admin", False):
            if not ({current_role, new_role} <= {"student", "cr"}):
                raise HTTPException(403, "Only the Super Admin can make this role change.")

    if "email" in updates:
        dup = sb.table("users").select("id").eq("email", updates["email"]).neq("id", uid).execute()
        if dup.data:
            raise HTTPException(409, "Email already in use")
        if target.get("supabase_uid"):
            try:
                sb.auth.admin.update_user_by_id(target["supabase_uid"], {"email": updates["email"], "email_confirm": True})
            except Exception as e:
                print(f"SUPABASE AUTH EMAIL UPDATE FAILED: {e}")
                raise HTTPException(502, "Could not update login email in Auth service. Please try again.")

    new_password = updates.pop("password", None)
    if new_password:
        if not target.get("supabase_uid"):
            raise HTTPException(500, "This account has no linked Auth identity; cannot set a password.")
        try:
            sb.auth.admin.update_user_by_id(target["supabase_uid"], {"password": new_password})
        except Exception:
            raise HTTPException(502, "Could not update the password. Please try again.")

    if "is_active" in updates and not updates["is_active"] and uid == admin["id"]:
        raise HTTPException(400, "Cannot deactivate your own account")

    if not updates and not new_password:
        raise HTTPException(400, "No fields to update")

    if updates:
        res = sb.table("users").update(updates).eq("id", uid).execute()
        row = res.data[0]
    else:
        row = sb.table("users").select("*").eq("id", uid).limit(1).execute().data[0]

    _audit(admin["id"], "UPDATE_USER", f"Updated user id={uid}")
    return row


@router.delete("/users/{uid}",summary="Delete a user")
def delete_user(
    uid: int,
    admin: dict = Depends(require_admin)
):
    # No admin should accidentally delete
    # the account they are currently using.
    if uid == admin["id"]:
        raise HTTPException(
            status_code=400,
            detail="You cannot delete your own account."
        )

    existing = (
        sb.table("users")
        .select(
            "id,username,role,is_super_admin,supabase_uid"
        )
        .eq("id", uid)
        .limit(1)
        .execute()
    )

    if not existing.data:
        raise HTTPException(
            status_code=404,
            detail="User not found."
        )

    target = existing.data[0]

    # Nobody can delete the permanent Super Admin.
    if target.get("is_super_admin", False):
        raise HTTPException(
            status_code=403,
            detail="The Super Admin cannot be deleted."
        )

    # Preserve attendance history: if user has recorded attendance, deactivate instead of deleting
    has_records = bool(sb.table("attendance_records").select("id").eq("student_id", uid).limit(1).execute().data)
    has_sessions = bool(sb.table("attendance_sessions").select("id").eq("faculty_id", uid).limit(1).execute().data)
    if has_records or has_sessions:
        sb.table("users").update({"is_active": False}).eq("id", uid).execute()
        _audit(admin["id"], "DEACTIVATE_USER", f"Deactivated {target['role']} '{target['username']}' (attendance history preserved)")
        return {
            "message": f"User '{target['username']}' has attendance history and was deactivated instead of permanently deleted to preserve academic records."
        }

    sb.table("users") \
        .delete() \
        .eq("id", uid) \
        .execute()

    # Only delete the Auth account here — at the point of an explicit
    # admin delete, never during approval/rejection.
    if target.get("supabase_uid"):
        try:
            sb.auth.admin.delete_user(target["supabase_uid"])
        except Exception:
            pass

    _audit(
        admin["id"],
        "DELETE_USER",
        (
            f"Deleted {target['role']} "
            f"'{target['username']}'"
        )
    )

    return {
        "message": "User deleted successfully"
    }


@router.patch(
    "/users/{uid}/toggle",
    summary="Toggle active/inactive"
)
def toggle_user(
    uid: int,
    admin: dict = Depends(require_admin)
):
    if uid == admin["id"]:
        raise HTTPException(
            status_code=400,
            detail=(
                "You cannot deactivate "
                "your own account."
            )
        )

    result = (
        sb.table("users")
        .select(
            "id,username,is_active,"
            "is_super_admin"
        )
        .eq("id", uid)
        .limit(1)
        .execute()
    )

    if not result.data:
        raise HTTPException(
            status_code=404,
            detail="User not found."
        )

    target = result.data[0]

    # The permanent account must always remain active.
    if target.get("is_super_admin", False):
        raise HTTPException(
            status_code=403,
            detail=(
                "The Super Admin cannot "
                "be deactivated."
            )
        )

    new_status = not target["is_active"]

    (
        sb.table("users")
        .update({
            "is_active": new_status
        })
        .eq("id", uid)
        .execute()
    )

    action = (
        "ACTIVATE_USER"
        if new_status
        else "DEACTIVATE_USER"
    )

    _audit(
        admin["id"],
        action,
        (
            f"{action.replace('_', ' ').title()}: "
            f"'{target['username']}'"
        )
    )

    return {
        "message": (
            "User activated successfully"
            if new_status
            else "User deactivated successfully"
        ),
        "is_active": new_status
    }

# ═══════════════════════════════════════════════
#  REGISTRATION APPROVAL  (pending self-registrations)
# ═══════════════════════════════════════════════

class RejectBody(BaseModel):
    rejection_reason: str = Field(..., min_length=1)


@router.get("/pending-requests", summary="List pending registration requests")
def list_pending_requests(
    role: Optional[str] = Query(None),
    admin: dict = Depends(require_admin),
):
    q = (
        sb.table("users")
        .select(
            "id, username, full_name, email, role, enrollment_no, "
            "department, programme, batch, designation, email_verified, phone_verified, created_at"
        )
        .eq("status", "pending")
    )
    if role:
        q = q.eq("role", role)
    res = q.order("created_at", desc=True).execute()
    requests = res.data or []

    # A regular admin may act on student/cr/faculty requests only — admin
    # requests are still visible (for transparency) but only a Super
    # Admin can approve/reject them; the frontend uses this flag to
    # disable those actions, and the endpoints re-check it server-side.
    for r in requests:
        r["actionable_by_current_admin"] = (
            r["role"] != "admin" or admin.get("is_super_admin", False)
        )

    return {"requests": requests, "total": len(requests)}


@router.post("/pending-requests/{uid}/approve", summary="Approve a pending registration")
def approve_request(uid: int, admin: dict = Depends(require_admin)):
    existing = (
        sb.table("users")
        .select(
            "id, username, role, status, email, full_name, "
            "email_verified, phone_verified, supabase_uid"
        )
        .eq("id", uid)
        .limit(1)
        .execute()
    )
    if not existing.data:
        raise HTTPException(404, "Registration request not found")

    target = existing.data[0]
    if target["status"] != "pending":
        raise HTTPException(400, f"This request has already been {target['status']}")

    # Backend-enforced rule: only a Super Admin may approve an Admin
    # registration. Never trust a role check done on the frontend.
    if target["role"] == "admin" and not admin.get("is_super_admin", False):
        raise HTTPException(403, "Only the Super Admin can approve Admin registrations.")

    # Approval is the final gate. The Supabase Auth identity must also be
    # confirmed before the profile is marked approved. This makes the
    # portal's verification state and Supabase Auth state agree.
    auth_uid = target.get("supabase_uid")
    if not auth_uid:
        raise HTTPException(
            400,
            "This account has no linked Supabase Auth identity. "
            "The registration must be repaired before approval."
        )

    try:
        # Email and phone verification are disabled for this portal.
        # Auto-confirm both identifiers so Supabase password authentication works.
        confirm_fields = {"email_confirm": True, "phone_confirm": True}
        sb.auth.admin.update_user_by_id(auth_uid, confirm_fields)
        print(
            f"APPROVAL AUTH SYNC: username={target['username']!r} "
            "email_confirm=True phone_confirm=True"
        )
    except Exception as e:
        print(
            f"APPROVAL AUTH SYNC FAILED: username={target['username']!r} "
            f"type={type(e).__name__} detail={str(e)[:200]!r}"
        )
        if not ("not configured" in str(e).lower() and str(auth_uid).startswith("local_")):
            raise HTTPException(
                502,
                "The Supabase Auth account could not be verified. "
                "The registration was not approved; please try again."
            )

    sb.table("users").update({
            "status":           "approved",
            "is_active":        True,
            "approved_by":      admin["id"],
            "approved_at":      datetime.now(timezone.utc).isoformat(),
            "rejection_reason": None,    }).eq("id", uid).execute()

    _audit(admin["id"], "APPROVE_REGISTRATION", f"Approved {target['role']} '{target['username']}'")
    send_approval_email(target["email"], target["full_name"])
    return {"message": f"'{target['username']}' has been approved and can now log in."}


@router.post("/pending-requests/{uid}/reject", summary="Reject a pending registration")
def reject_request(uid: int, body: RejectBody, admin: dict = Depends(require_admin)):
    existing = (
        sb.table("users")
        .select("id, username, role, status, email, full_name, supabase_uid")
        .eq("id", uid)
        .limit(1)
        .execute()
    )
    if not existing.data:
        raise HTTPException(404, "Registration request not found")

    target = existing.data[0]
    if target["status"] != "pending":
        raise HTTPException(400, f"This request has already been {target['status']}")

    # Same backend-enforced rule as approval.
    if target["role"] == "admin" and not admin.get("is_super_admin", False):
        raise HTTPException(403, "Only the Super Admin can reject Admin registrations.")

    auth_uid = target.get("supabase_uid")
    if auth_uid and not str(auth_uid).startswith("local_"):
        try:
            sb.auth.admin.delete_user(auth_uid)
        except Exception as e:
            print(f"REJECT: could not delete Supabase Auth user {auth_uid}: {e}")

    sb.table("users").update({
        "status":           "rejected",
        "approved_by":      admin["id"],
        "approved_at":      datetime.now(timezone.utc).isoformat(),
        "rejection_reason": body.rejection_reason,
        "supabase_uid":     None,
    }).eq("id", uid).execute()

    _audit(
        admin["id"], "REJECT_REGISTRATION",
        f"Rejected {target['role']} '{target['username']}': {body.rejection_reason}",
    )
    send_rejection_email(target["email"], target["full_name"], body.rejection_reason)
    return {"message": f"'{target['username']}' has been rejected."}


# 
#  PASSWORD RESET  (admin resets any user's password)
# 

class PasswordResetBody(BaseModel):
    username:     str
    new_password: str = Field(..., min_length=6)


@router.post(
    "/reset-password",
    summary="Reset any user's password"
)
def reset_password(
    body: PasswordResetBody,
    admin: dict = Depends(require_admin)
):
    res = (
        sb.table("users")
        .select(
            "id,full_name,username,is_super_admin,supabase_uid"
        )
        .eq("username", body.username)
        .eq("is_active", True)
        .limit(1)
        .execute()
    )

    if not res.data:
        raise HTTPException(
            status_code=404,
            detail="No active user with that username"
        )

    u = res.data[0]

    # Another admin cannot reset the
    # Super Admin's password.
    if (
        u.get("is_super_admin", False)
        and u["id"] != admin["id"]
    ):
        raise HTTPException(
            status_code=403,
            detail=(
                "The Super Admin's password "
                "cannot be reset by another admin."
            )
        )

    if not u.get("supabase_uid"):
        raise HTTPException(500, "This account has no linked Auth identity; cannot reset its password.")

    # Runs only when the request is permitted. Supabase Auth owns the
    # password — nothing is written to the profile row.
    try:
        sb.auth.admin.update_user_by_id(u["supabase_uid"], {"password": body.new_password})
    except Exception:
        raise HTTPException(502, "Could not reset the password. Please try again.")

    _audit(
        admin["id"],
        "PASSWORD_RESET",
        (
            "Admin reset password for "
            f"'{u['username']}'"
        )
    )

    return {
        "message": (
            f"Password for '{u['username']}' "
            "has been reset."
        )
    }

# 
#  SUBJECTS OFFERED (admin-managed subject catalogue)
# 

class SubjectOfferedCreate(BaseModel):
    subject: str = Field(min_length=1, max_length=200)
    course_code: str = Field(min_length=1, max_length=50)
    paper_id: str = Field(min_length=1, max_length=80)
    paper_category: str = Field(min_length=1, max_length=30)
    programme: str = Field(min_length=1, max_length=30)
    course: str = Field(min_length=1, max_length=50)
    batch: Optional[str] = None
    semester: str = Field(min_length=1, max_length=20)
    section: Optional[str] = None


@router.get("/subjects-offered", summary="List offered subjects")
def list_subjects_offered(admin: dict = Depends(require_admin)):
    return sb.table("subjects_offered").select("*").order("programme").order("course").order("batch").order("semester").order("subject").execute().data or []


@router.post("/subjects-offered", status_code=201, summary="Add an offered subject")
def create_subject_offered(body: SubjectOfferedCreate, admin: dict = Depends(require_admin)):
    payload = body.model_dump()
    payload["subject"] = payload["subject"].strip()
    payload["course_code"] = payload["course_code"].strip()
    payload["paper_id"] = payload["paper_id"].strip()
    payload["paper_category"] = payload["paper_category"].strip()
    valid_categories = {"B.A.": {"DSC", "FC", "MSC", "IDC", "SEC", "AEC", "VAC"}, "M.A.": {"DSC", "MSC", "Elective", "Minors (Elective)", "AEC", "SEC"}}
    if payload["paper_category"] not in valid_categories.get(payload["programme"], set()):
        raise HTTPException(status_code=400, detail="Invalid paper category")
    payload["programme"] = payload["programme"].strip()
    payload["course"] = payload["course"].strip()
    payload["batch"] = (payload.get("batch") or "").strip() or "General"
    payload["semester"] = payload["semester"].strip()
    payload["section"] = (payload.get("section") or "").strip() or None
    if not all(payload[key] for key in ("subject", "course_code", "paper_id", "paper_category", "programme", "course", "semester")):
        raise HTTPException(400, "Subject name, course code and all academic details are required.")
    payload["created_by"] = admin.get("id")
    try:
        result = sb.table("subjects_offered").insert(payload).execute()
    except Exception as exc:
        message = str(exc).lower()
        if "subjects_offered_unique_scope" in message or "duplicate key" in message or "23505" in message:
            raise HTTPException(409, "This subject is already listed for the selected programme, course, batch, semester and section.")
        raise HTTPException(502, "Could not save the subject. Please try again.")
    _audit(admin["id"], "CREATE_SUBJECT_OFFERED", f"{payload['subject']} [{payload['programme']} / {payload['course']} / {payload['batch']} / Sem {payload['semester']}]")
    return result.data[0]


@router.delete("/subjects-offered/{subject_id}", summary="Remove an offered subject")
def delete_subject_offered(subject_id: int, admin: dict = Depends(require_admin)):
    existing = sb.table("subjects_offered").select("id,subject").eq("id", subject_id).limit(1).execute().data or []
    if not existing:
        raise HTTPException(404, "Subject not found.")
    sb.table("subjects_offered").delete().eq("id", subject_id).execute()
    _audit(admin["id"], "DELETE_SUBJECT_OFFERED", f"Removed '{existing[0].get('subject')}' from Subjects Offered")
    return {"message": "Subject removed."}


# 
#  TIMETABLE  (admin creates / manages the schedule)
# 

class TimetableCreate(BaseModel):
    subject:     str
    day_of_week: str
    start_time:  str    # HH:MM
    end_time:    str    # HH:MM
    programme:   str
    course:      Optional[str] = None
    batch:       str
    semester:    Optional[str] = None
    section:     Optional[str] = None
    room:        Optional[str] = None
    department:  Optional[str] = None
    faculty_id:  Optional[int] = None
    faculty_ids: list[int] = Field(default_factory=list)


class TimetableUpdate(BaseModel):
    subject:     Optional[str] = None
    day_of_week: Optional[str] = None
    start_time:  Optional[str] = None
    end_time:    Optional[str] = None
    programme:   Optional[str] = None
    course:      Optional[str] = None
    batch:       Optional[str] = None
    semester:    Optional[str] = None
    section:     Optional[str] = None
    room:        Optional[str] = None
    department:  Optional[str] = None
    faculty_id:  Optional[int] = None
    faculty_ids: Optional[list[int]] = None


# Timetable times are fixed; admins may edit class details but cannot create custom periods.
FIXED_TIMETABLE_PAIRS = {
    ("09:00", "10:00"), ("10:00", "11:00"),
    ("11:00", "12:00"), ("12:00", "13:00"),
    ("13:30", "14:30"), ("14:30", "15:30"),
    ("15:30", "16:30"), ("16:30", "17:30"),
}


def _validate_timetable_slot(start_time: str, end_time: str, day_of_week: str) -> None:
    day = (day_of_week or "").strip().lower()
    if day not in {"monday", "tuesday", "wednesday", "thursday", "friday"}:
        raise HTTPException(400, "Timetable day must be Monday through Friday.")

    start = str(start_time or "")[:5]
    end = str(end_time or "")[:5]
    if (start, end) not in FIXED_TIMETABLE_PAIRS:
        raise HTTPException(
            400,
            "Please use one of the fixed timetable slots. Custom class times are not allowed."
        )


def _time_minutes(value: str) -> int:
    parsed = datetime.strptime(str(value)[:5], "%H:%M")
    return parsed.hour * 60 + parsed.minute


def _timetable_conflict(existing_slots, start_time, end_time, faculty_id=None, faculty_ids=None,
                        room=None, programme=None, course=None, batch=None,
                        semester=None, section=None, exclude_id=None):
    """Prevent resource clashes while allowing different classes to run in parallel."""
    start_min, end_min = _time_minutes(start_time), _time_minutes(end_time)
    assigned_faculty = {str(x) for x in (faculty_ids or []) if x}
    if faculty_id:
        assigned_faculty.add(str(faculty_id))
    for slot in existing_slots:
        if exclude_id is not None and str(slot.get("id")) == str(exclude_id):
            continue
        slot_start = _time_minutes(slot.get("start_time"))
        slot_end = _time_minutes(slot.get("end_time"))
        if start_min >= slot_end or slot_start >= end_min:
            continue
        existing_faculty = {str(x) for x in (slot.get("faculty_ids") or []) if x}
        if slot.get("faculty_id"):
            existing_faculty.add(str(slot.get("faculty_id")))
        if assigned_faculty.intersection(existing_faculty):
            return f"One of the assigned professors is already teaching '{slot.get('subject')}' during this time."
        if room and (slot.get("room") or "").strip().lower() == room.strip().lower():
            return f"Room '{room}' is already booked for '{slot.get('subject')}' during this time."
        # A cohort is defined by its programme, course, batch, semester and section.
        # Different cohorts may have classes at the same time; the same cohort may not.
        same_class = all(
            str(slot.get(field) or "").strip().casefold()
            == str(value or "").strip().casefold()
            for field, value in (
                ("programme", programme), ("course", course), ("batch", batch),
                ("semester", semester), ("section", section)
            )
        )
        if same_class:
            return f"This class overlaps with '{slot.get('subject')}' in the timetable."
    return None


@router.get("/timetable", summary="List all timetable slots")
def list_timetable(
    programme: Optional[str] = Query(None),
    batch:     Optional[str] = Query(None),
    admin: dict = Depends(require_admin),
):
    q = sb.table("timetable_slots").select("*, users(full_name, email)").eq("is_active", True)
    if programme: q = q.ilike("programme", f"%{programme}%")
    if batch:     q = q.eq("batch", batch)
    res = q.order("day_of_week").order("start_time").execute()
    rows = res.data or []
    all_ids = {int(fid) for row in rows for fid in (row.get("faculty_ids") or []) if str(fid).isdigit()}
    all_ids.update(int(row["faculty_id"]) for row in rows if row.get("faculty_id"))
    names = {}
    if all_ids:
        users = sb.table("users").select("id,full_name,username").in_("id", list(all_ids)).execute().data or []
        names = {int(u["id"]): (u.get("full_name") or u.get("username") or "") for u in users}
    for row in rows:
        ids = row.get("faculty_ids") or ([row["faculty_id"]] if row.get("faculty_id") else [])
        row["faculty_ids"] = ids
        row["faculty_names"] = [names[int(fid)] for fid in ids if str(fid).isdigit() and int(fid) in names]
    return rows


@router.post("/timetable", status_code=201, summary="Create a timetable slot")
def create_slot(body: TimetableCreate, admin: dict = Depends(require_admin)):
    _validate_timetable_slot(body.start_time, body.end_time, body.day_of_week)
    faculty_ids = list(dict.fromkeys([int(x) for x in (body.faculty_ids or []) if x]))
    if body.faculty_id and int(body.faculty_id) not in faculty_ids:
        faculty_ids.insert(0, int(body.faculty_id))
    if faculty_ids:
        fac = sb.table("users").select("id").eq("role", "faculty").in_("id", faculty_ids).execute()
        found_ids = {int(x["id"]) for x in (fac.data or [])}
        if found_ids != set(faculty_ids):
            raise HTTPException(404, "One or more assigned professors could not be found.")
    primary_faculty_id = faculty_ids[0] if faculty_ids else None

    day_slots = (
        sb.table("timetable_slots").select("*")
        .eq("is_active", True).eq("day_of_week", body.day_of_week).execute().data or []
    )
    conflict = _timetable_conflict(
        day_slots, body.start_time, body.end_time, faculty_id=primary_faculty_id, faculty_ids=faculty_ids,
        room=body.room, programme=body.programme, course=body.course,
        batch=body.batch, semester=body.semester, section=body.section
    )
    if conflict:
        raise HTTPException(409, conflict)

    payload = body.model_dump(exclude={"faculty_id", "faculty_ids"})
    payload.update({"faculty_id": primary_faculty_id, "faculty_ids": faculty_ids})
    res = sb.table("timetable_slots").insert(payload).execute()
    _audit(admin["id"], "CREATE_TIMETABLE",
           f"{body.subject} {body.day_of_week} {body.start_time} [{body.programme} {body.batch}]")
    return res.data[0]


class TimetablePublish(BaseModel):
    programme: str
    group: str


@router.post("/timetable/publish", summary="Publish a timetable group to students")
def publish_timetable_group(body: TimetablePublish, admin: dict = Depends(require_admin)):
    programme = (body.programme or "").strip()
    group = (body.group or "").strip()
    if not programme or not group:
        raise HTTPException(400, "Programme and timetable section are required.")

    rows = sb.table("timetable_slots").select(
        "id, programme, course, batch, semester, section, published"
    ).eq("programme", programme).eq("is_active", True).execute().data or []

    def group_label(slot):
        if slot.get("course") and slot.get("batch") and slot.get("semester"):
            return (
                str(slot["course"]) + " · " + str(slot["batch"])
                + " · Sem " + str(slot["semester"])
                + " · Section " + str(slot.get("section") or "None")
            )
        return slot.get("section") or slot.get("batch") or "General"

    matching = [row for row in rows if str(group_label(row)).strip() == group]
    if not matching:
        raise HTTPException(404, "This timetable has no saved class slots yet. Add classes before publishing.")

    # Publish every saved slot in this exact programme + semester/section group.
    for row in matching:
        sb.table("timetable_slots").update({"published": True}).eq("id", row["id"]).execute()

    _audit(
        admin["id"],
        "PUBLISH_TIMETABLE",
        f"Published {programme} / {group} ({len(matching)} slots)"
    )
    return {"message": f"Timetable published for {programme} / {group}.", "published": True, "slots": len(matching)}


@router.put("/timetable/{slot_id}", summary="Update a timetable slot")
def update_slot(slot_id: int, body: TimetableUpdate, admin: dict = Depends(require_admin)):
    existing = sb.table("timetable_slots").select("*").eq("id", slot_id).limit(1).execute()
    if not existing.data:
        raise HTTPException(404, "Timetable slot not found")
    current = existing.data[0]

    updates = body.model_dump(exclude_none=True)
    if not updates:
        raise HTTPException(400, "No fields to update")

    eff_start = updates.get("start_time", current.get("start_time"))
    eff_end = updates.get("end_time", current.get("end_time"))
    eff_day = updates.get("day_of_week", current.get("day_of_week"))
    _validate_timetable_slot(eff_start, eff_end, eff_day)

    # Validate the assigned faculty and reject any overlapping class/resource interval.
    if "faculty_ids" in updates:
        eff_faculty_ids = list(dict.fromkeys([int(x) for x in (updates.get("faculty_ids") or []) if x]))
        if "faculty_id" in updates and updates["faculty_id"] and int(updates["faculty_id"]) not in eff_faculty_ids:
            eff_faculty_ids.insert(0, int(updates["faculty_id"]))
        updates["faculty_ids"] = eff_faculty_ids
        updates["faculty_id"] = eff_faculty_ids[0] if eff_faculty_ids else None
    elif "faculty_id" in updates:
        eff_faculty_ids = [int(updates["faculty_id"])] if updates["faculty_id"] else []
        updates["faculty_ids"] = eff_faculty_ids
    else:
        eff_faculty_ids = current.get("faculty_ids") or ([current["faculty_id"]] if current.get("faculty_id") else [])
    eff_faculty = updates.get("faculty_id", current.get("faculty_id"))
    eff_room = updates.get("room", current.get("room"))
    eff_programme = updates.get("programme", current.get("programme"))
    eff_course = updates.get("course", current.get("course"))
    eff_batch = updates.get("batch", current.get("batch"))
    eff_semester = updates.get("semester", current.get("semester"))
    eff_section = updates.get("section", current.get("section"))

    if eff_faculty_ids:
        fac = sb.table("users").select("id").eq("role", "faculty").in_("id", eff_faculty_ids).execute()
        found_ids = {int(x["id"]) for x in (fac.data or [])}
        if found_ids != set(eff_faculty_ids):
            raise HTTPException(404, "One or more assigned professors could not be found.")

    day_slots = (
        sb.table("timetable_slots").select("*")
        .eq("is_active", True).eq("day_of_week", eff_day).execute().data or []
    )
    conflict = _timetable_conflict(
        day_slots, eff_start, eff_end, faculty_id=eff_faculty, faculty_ids=eff_faculty_ids, room=eff_room,
        programme=eff_programme, course=eff_course, batch=eff_batch,
        semester=eff_semester, section=eff_section, exclude_id=slot_id
    )
    if conflict:
        raise HTTPException(409, conflict)

    res = sb.table("timetable_slots").update(updates).eq("id", slot_id).execute()
    _audit(admin["id"], "UPDATE_TIMETABLE", f"Updated slot id={slot_id}")
    return res.data[0]


@router.delete("/timetable/{slot_id}", summary="Delete a timetable slot")
def delete_slot(slot_id: int, admin: dict = Depends(require_admin)):
    existing = sb.table("timetable_slots").select("id, is_active").eq("id", slot_id).limit(1).execute()
    if not existing.data or existing.data[0].get("is_active") is False:
        raise HTTPException(404, "Active timetable slot not found")
    # Soft-clear the slot so attendance sessions and historical reports remain intact.
    # Inactive slots are hidden from current timetables and excluded from clash checks.
    sb.table("timetable_slots").update({"is_active": False, "published": False}).eq("id", slot_id).execute()
    _audit(admin["id"], "CLEAR_TIMETABLE_SLOT", f"Cleared slot id={slot_id}")
    return {"message": "Timetable slot cleared. Historical attendance records were preserved."}


# 
#  ATTENDANCE OVERSIGHT
# 

class RecordOverride(BaseModel):
    status: str  # 'present' or 'absent'


@router.get("/attendance/sessions", summary="View all attendance sessions")
def list_sessions(
    slot_id: Optional[int]      = Query(None),
    date:    Optional[DateType] = Query(None),
    admin: dict = Depends(require_admin),
):
    q = sb.table("attendance_sessions").select(
        "*, timetable_slots(subject, programme, batch), users(full_name)"
    )
    if slot_id: q = q.eq("slot_id", slot_id)
    if date:    q = q.eq("date", str(date))
    res = q.order("date", desc=True).execute()
    return res.data or []


@router.patch("/attendance/sessions/{sid}/close", summary="Force-close an attendance session")
def close_session(sid: int, admin: dict = Depends(require_admin)):
    from datetime import datetime, timezone
    existing = sb.table("attendance_sessions") \
                 .select("id, is_open, slot_id, timetable_slots(programme, batch, section)") \
                 .eq("id", sid).limit(1).execute()
    if not existing.data:
        raise HTTPException(404, "Session not found")
    s = existing.data[0]

    # Insert absent records for students who never marked (same logic as faculty close)
    slot = s.get("timetable_slots") or {}
    prog, batch, section = slot.get("programme"), slot.get("batch"), slot.get("section")
    if prog and batch:
        q_stud = sb.table("users").select("id").eq("role", "student") \
                   .eq("is_active", True).eq("programme", prog).eq("batch", batch)
        if section:
            q_stud = q_stud.eq("section", section)
        class_student_ids = {u["id"] for u in (q_stud.execute().data or [])}
        marked_ids = {r["student_id"] for r in (
            sb.table("attendance_records").select("student_id").eq("session_id", sid).execute().data or []
        )}
        missing_ids = class_student_ids - marked_ids
        if missing_ids:
            now_iso = datetime.now(timezone.utc).isoformat()
            try:
                sb.table("attendance_records").insert([
                    {"session_id": sid, "student_id": st_id, "status": "absent", "marked_at": now_iso}
                    for st_id in missing_ids
                ]).execute()
            except Exception as exc:
                print(f"ADMIN CLOSE: failed to insert absent records for session {sid}: {exc}")

    res = sb.table("attendance_sessions").update({
        "is_open":   False,
        "closed_at": datetime.now(timezone.utc).isoformat()
    }).eq("id", sid).execute()
    _audit(admin["id"], "CLOSE_SESSION", f"Admin force-closed session id={sid}")
    return res.data[0]


@router.delete("/attendance/sessions/{sid}", summary="Delete an attendance session")
def delete_session(sid: int, admin: dict = Depends(require_admin)):
    existing = sb.table("attendance_sessions").select("id").eq("id", sid).limit(1).execute()
    if not existing.data:
        raise HTTPException(404, "Session not found")
    sb.table("attendance_sessions").delete().eq("id", sid).execute()
    _audit(admin["id"], "DELETE_SESSION", f"Deleted session id={sid}")
    return {"message": "Session deleted"}


@router.patch("/attendance/records/{rid}", summary="Override a student's attendance status")
def override_record(rid: int, body: RecordOverride, admin: dict = Depends(require_admin)):
    if body.status not in ("present", "absent"):
        raise HTTPException(400, "Status must be 'present' or 'absent'")
    existing = sb.table("attendance_records").select("id,student_id").eq("id", rid).limit(1).execute()
    if not existing.data:
        raise HTTPException(404, "Record not found")
    res = sb.table("attendance_records").update({"status": body.status}).eq("id", rid).execute()
    _audit(admin["id"], "OVERRIDE_ATTENDANCE",
           f"Overrode record id={rid}  {body.status}")
    return res.data[0]


@router.get("/attendance/report", summary="Full attendance report")
def attendance_report(
    programme: Optional[str]      = Query(None),
    batch:     Optional[str]      = Query(None),
    subject:   Optional[str]      = Query(None),
    date_from: Optional[DateType] = Query(None),
    date_to:   Optional[DateType] = Query(None),
    admin: dict = Depends(require_admin),
):
    q = sb.table("attendance_records").select(
        "*, "
        "attendance_sessions(date, slot_id, timetable_slots(subject, programme, batch)), "
        "users!attendance_records_student_id_fkey(full_name, enrollment_no, programme, batch)"
    )
    res = q.order("marked_at", desc=True).execute()
    records = res.data or []

    # Filter in Python for nested fields
    if programme:
        records = [r for r in records if
                   (r.get("attendance_sessions") or {}).get("timetable_slots", {}).get("programme", "") == programme]
    if batch:
        records = [r for r in records if
                   (r.get("attendance_sessions") or {}).get("timetable_slots", {}).get("batch", "") == batch]
    if subject:
        records = [r for r in records if
                   subject.lower() in ((r.get("attendance_sessions") or {}).get("timetable_slots", {}).get("subject", "") or "").lower()]
    if date_from:
        records = [r for r in records if
                   ((r.get("attendance_sessions") or {}).get("date") or "") >= str(date_from)]
    if date_to:
        records = [r for r in records if
                   ((r.get("attendance_sessions") or {}).get("date") or "") <= str(date_to)]

    return {"total": len(records), "records": records}


# 
#  EVENTS
# 

class EventBody(BaseModel):
    name:        str
    description: Optional[str]  = None
    event_date:  DateType
    event_time:  Optional[str]  = None
    venue:       Optional[str]  = None
    category:    Optional[str]  = None
    is_featured: bool           = False


@router.get("/events", summary="List all events")
def list_events(admin: dict = Depends(require_admin)):
    return sb.table("events").select("*").order("event_date").execute().data or []


@router.post("/events", status_code=201, summary="Create event")
def create_event(body: EventBody, admin: dict = Depends(require_admin)):
    d = body.model_dump()
    d["event_date"] = str(d["event_date"])
    res = sb.table("events").insert(d).execute()
    _audit(admin["id"], "CREATE_EVENT", f"Created event '{body.name}'")
    return res.data[0]


@router.put("/events/{eid}", summary="Update event")
def update_event(eid: int, body: EventBody, admin: dict = Depends(require_admin)):
    existing = sb.table("events").select("id").eq("id", eid).limit(1).execute()
    if not existing.data:
        raise HTTPException(404, "Event not found")
    d = body.model_dump(exclude_none=True)
    if "event_date" in d: d["event_date"] = str(d["event_date"])
    res = sb.table("events").update(d).eq("id", eid).execute()
    _audit(admin["id"], "UPDATE_EVENT", f"Updated event id={eid}")
    return res.data[0]


@router.delete("/events/{eid}", summary="Delete event")
def delete_event(eid: int, admin: dict = Depends(require_admin)):
    existing = sb.table("events").select("id").eq("id", eid).limit(1).execute()
    if not existing.data:
        raise HTTPException(404, "Event not found")
    sb.table("events").delete().eq("id", eid).execute()
    _audit(admin["id"], "DELETE_EVENT", f"Deleted event id={eid}")
    return {"message": "Event deleted"}


# 
#  NEWS
# 

class NewsBody(BaseModel):
    title:          str
    excerpt:        Optional[str]      = None
    body:           Optional[str]      = None
    tag:            Optional[str]      = None
    image_url:      Optional[str]      = None
    published:      bool               = True
    is_featured:    bool               = False
    published_date: Optional[DateType] = None
    venue:          Optional[str]      = None


@router.get("/news", summary="List all news items")
def list_news(admin: dict = Depends(require_admin)):
    return sb.table("news_items").select("*").order("published_date", desc=True).execute().data or []


@router.post("/news", status_code=201, summary="Create news item")
def create_news(body: NewsBody, admin: dict = Depends(require_admin)):
    d = body.model_dump()
    if d.get("published_date"): d["published_date"] = str(d["published_date"])
    res = sb.table("news_items").insert(d).execute()
    _audit(admin["id"], "CREATE_NEWS", f"Created news '{body.title}'")
    return res.data[0]


@router.put("/news/{nid}", summary="Update news item")
def update_news(nid: int, body: NewsBody, admin: dict = Depends(require_admin)):
    existing = sb.table("news_items").select("id").eq("id", nid).limit(1).execute()
    if not existing.data:
        raise HTTPException(404, "News item not found")
    d = body.model_dump(exclude_none=True)
    if "published_date" in d: d["published_date"] = str(d["published_date"])
    res = sb.table("news_items").update(d).eq("id", nid).execute()
    _audit(admin["id"], "UPDATE_NEWS", f"Updated news id={nid}")
    return res.data[0]


@router.delete("/news/{nid}", summary="Delete news item")
def delete_news(nid: int, admin: dict = Depends(require_admin)):
    existing = sb.table("news_items").select("id").eq("id", nid).limit(1).execute()
    if not existing.data:
        raise HTTPException(404, "News item not found")
    sb.table("news_items").delete().eq("id", nid).execute()
    _audit(admin["id"], "DELETE_NEWS", f"Deleted news id={nid}")
    return {"message": "News item deleted"}


# 
#  ANNOUNCEMENTS
# 

class AnnouncementBody(BaseModel):
    title: str
    body: Optional[str] = None
    target: Optional[str] = None
    priority: Optional[str] = None
    pinUntil: Optional[datetime] = None

@router.get("/announcements", summary="List announcements")
def list_announcements(admin: dict = Depends(require_admin)):
    return sb.table("announcements").select("*").order("ts", desc=True).execute().data or []

@router.post("/announcements", status_code=201, summary="Create announcement")
def create_announcement(body: AnnouncementBody, admin: dict = Depends(require_admin)):
    d=body.model_dump()
    d["ts"]=datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
    if d.get("pinUntil"): d["pinUntil"]=d["pinUntil"].replace(tzinfo=None).isoformat()
    res=sb.table("announcements").insert(d).execute()
    _audit(admin["id"], "ANNOUNCEMENT_POST", f"Created announcement '{body.title}'")
    return res.data[0]

@router.put("/announcements/{aid}", summary="Update announcement")
def update_announcement(aid:int, body:AnnouncementBody, admin:dict=Depends(require_admin)):
    existing=sb.table("announcements").select("id").eq("id",aid).limit(1).execute()
    if not existing.data: raise HTTPException(404,"Announcement not found")
    d=body.model_dump(exclude_unset=True)
    if d.get("pinUntil"): d["pinUntil"]=d["pinUntil"].replace(tzinfo=None).isoformat()
    res=sb.table("announcements").update(d).eq("id",aid).execute()
    _audit(admin["id"], "ANNOUNCEMENT_UPDATE", f"Updated announcement id={aid}")
    return res.data[0]

@router.delete("/announcements/{aid}", summary="Delete announcement")
def delete_announcement(aid:int, admin:dict=Depends(require_admin)):
    existing=sb.table("announcements").select("id").eq("id",aid).limit(1).execute()
    if not existing.data: raise HTTPException(404,"Announcement not found")
    sb.table("announcements").delete().eq("id",aid).execute()
    _audit(admin["id"], "ANNOUNCEMENT_DELETE", f"Deleted announcement id={aid}")
    return {"message":"Announcement deleted"}


# 
#  FACULTY DIRECTORY
# 

class FacultyDirBody(BaseModel):
    name:           str
    designation:    str
    department:     Optional[str] = None
    specialisation: Optional[str] = None
    email:          Optional[str] = None
    phone:          Optional[str] = None
    photo_url:      Optional[str] = None
    initials:       Optional[str] = None
    bio:            Optional[str] = None
    sort_order:     int           = 100
    is_active:      bool          = True


@router.get("/faculty-directory", summary="List faculty directory")
def list_faculty_dir(admin: dict = Depends(require_admin)):
    return sb.table("faculty_directory").select("*").order("sort_order").execute().data or []


@router.post("/faculty-directory", status_code=201, summary="Add faculty to directory")
def create_faculty_dir(body: FacultyDirBody, admin: dict = Depends(require_admin)):
    res = sb.table("faculty_directory").insert(body.model_dump()).execute()
    _audit(admin["id"], "CREATE_FACULTY_DIR", f"Added '{body.name}' to directory")
    return res.data[0]


@router.put("/faculty-directory/{fid}", summary="Update faculty directory entry")
def update_faculty_dir(fid: int, body: FacultyDirBody, admin: dict = Depends(require_admin)):
    existing = sb.table("faculty_directory").select("id").eq("id", fid).limit(1).execute()
    if not existing.data:
        raise HTTPException(404, "Faculty entry not found")
    res = sb.table("faculty_directory").update(body.model_dump()).eq("id", fid).execute()
    _audit(admin["id"], "UPDATE_FACULTY_DIR", f"Updated faculty dir id={fid}")
    return res.data[0]


@router.delete("/faculty-directory/{fid}", summary="Remove from faculty directory")
def delete_faculty_dir(fid: int, admin: dict = Depends(require_admin)):
    existing = sb.table("faculty_directory").select("id").eq("id", fid).limit(1).execute()
    if not existing.data:
        raise HTTPException(404, "Faculty entry not found")
    sb.table("faculty_directory").delete().eq("id", fid).execute()
    _audit(admin["id"], "DELETE_FACULTY_DIR", f"Deleted faculty dir id={fid}")
    return {"message": "Faculty entry deleted"}


# 
#  STATS / AUDIT / MESSAGES
# 

@router.get("/stats", summary="Dashboard statistics")
def stats(admin: dict = Depends(require_admin)):
    """Load core metrics with a retry and isolate optional dashboard panels.

    Optional queries run concurrently so a slow panel does not delay every other
    metric. A transient failure in the required users query is retried once.
    """
    from concurrent.futures import ThreadPoolExecutor
    from time import sleep
    from fastapi import status

    users = None
    last_error = None
    for attempt in range(2):
        try:
            users = sb.table("users").select("role, is_active").execute().data or []
            break
        except Exception as e:
            last_error = e
            print(
                f"ADMIN STATS USERS QUERY FAILED (attempt {attempt + 1}/2): "
                f"{type(e).__name__}: {str(e)[:180]}"
            )
            if attempt == 0:
                sleep(0.25)

    if users is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Dashboard data is temporarily unavailable. Please retry in a few seconds.",
            headers={"Retry-After": "3"},
        ) from last_error

    counts = {}
    for user in users:
        role = user.get("role") or "unknown"
        counts[role] = counts.get(role, 0) + 1
    active = sum(1 for user in users if user.get("is_active"))

    def optional_query(label, query, default):
        try:
            return query().data or default
        except Exception as e:
            print(
                f"ADMIN STATS OPTIONAL QUERY FAILED ({label}): "
                f"{type(e).__name__}: {str(e)[:180]}"
            )
            return default

    queries = {
        "recent_logins": (
            lambda: sb.table("users").select("id,username,full_name,role,last_login")
                .not_.is_("last_login", "null").order("last_login", desc=True).limit(10).execute(),
            [],
        ),
        "audit_log": (
            lambda: sb.table("audit_log").select("*").order("ts", desc=True).limit(20).execute(),
            [],
        ),
        "timetable_slots": (
            lambda: sb.table("timetable_slots").select("id").execute(),
            [],
        ),
        "attendance_sessions": (
            lambda: sb.table("attendance_sessions").select("id,is_open").execute(),
            [],
        ),
        "attendance_records": (
            lambda: sb.table("attendance_records").select("id").execute(),
            [],
        ),
        "announcements": (
            lambda: sb.table("announcements").select("id,title,body,ts,target,priority")
                .order("ts", desc=True).limit(10).execute(),
            [],
        ),
    }

    # Each panel has its own fallback; one failing query cannot fail the endpoint.
    with ThreadPoolExecutor(max_workers=len(queries)) as executor:
        futures = {
            name: executor.submit(optional_query, name, query, default)
            for name, (query, default) in queries.items()
        }
        results = {name: future.result() for name, future in futures.items()}

    return {
        "counts": counts,
        "total": len(users),
        "active": active,
        "recent_logins": results["recent_logins"],
        "log": results["audit_log"],
        "timetable_count": len(results["timetable_slots"]),
        "attendance_sessions": results["attendance_sessions"],
        "attendance_records_count": len(results["attendance_records"]),
        "announcements": results["announcements"],
    }


@router.get("/audit", summary="Full audit log")
def audit_log(admin: dict = Depends(require_admin)):
    """Return audit entries without relying on an optional PostgREST FK relationship.

    Some production schemas do not declare audit_log.user_id as a foreign key
    to users.id. A nested select (users(username, full_name)) then fails with
    PostgREST PGRST200 and breaks the audit panel. Fetch the two tables
    independently and enrich entries in Python instead.
    """
    entries = (
        sb.table("audit_log")
        .select("*")
        .order("ts", desc=True)
        .limit(200)
        .execute()
        .data or []
    )
    users = sb.table("users").select("id, username, full_name").execute().data or []
    users_by_id = {str(user["id"]): user for user in users if user.get("id") is not None}

    for entry in entries:
        user_id = entry.get("user_id")
        entry["users"] = users_by_id.get(str(user_id)) if user_id is not None else None

    return entries

@router.get("/messages", summary="Contact messages")
def list_messages(admin: dict = Depends(require_admin)):
    return sb.table("contact_messages").select("*").order("submitted_at", desc=True).execute().data or []


@router.patch("/messages/{mid}/read", summary="Mark message as read")
def mark_read(mid: int, admin: dict = Depends(require_admin)):
    existing = sb.table("contact_messages").select("id").eq("id", mid).limit(1).execute()
    if not existing.data:
        raise HTTPException(404, "Message not found")
    sb.table("contact_messages").update({"is_read": True}).eq("id", mid).execute()
    return {"message": "Marked as read"}

# ADMIN CHAT REVIEW — protected by the standard admin dependency
@router.get("/chat/conversations", summary="List chat conversations for administrators")
def admin_chat_conversations(admin: dict = Depends(require_admin)):
    conversations = sb.table("chat_conversations").select("*").order("created_at", desc=True).execute().data or []
    members = sb.table("chat_members").select("conversation_id,user_id").execute().data or []
    users = sb.table("users").select("id,full_name,role,username").execute().data or []
    user_map = {str(u.get("id")): u for u in users}
    grouped = {}
    for member in members:
        uid = str(member.get("user_id"))
        person = user_map.get(uid, {})
        grouped.setdefault(str(member.get("conversation_id")), []).append({
            "id": uid, "full_name": person.get("full_name") or person.get("username") or uid,
            "role": person.get("role") or "unknown"
        })
    latest = sb.table("interconnected_chat_messages").select("id,conversation_id,sender_id,body,created_at").order("created_at", desc=True).limit(1000).execute().data or []
    latest_by_conv = {}
    for message in latest:
        latest_by_conv.setdefault(str(message.get("conversation_id")), message)
    rows = [{**c, "members": grouped.get(str(c.get("id")), []), "last_message": latest_by_conv.get(str(c.get("id")))} for c in conversations]
    return {"conversations": rows, "total": len(rows)}

@router.get("/chat/conversations/{conversation_id}/messages", summary="Review a conversation's messages")
def admin_chat_messages(conversation_id: str, admin: dict = Depends(require_admin)):
    exists = sb.table("chat_conversations").select("id").eq("id", conversation_id).limit(1).execute().data
    if not exists:
        raise HTTPException(404, "Conversation not found")
    messages = sb.table("interconnected_chat_messages").select("id,conversation_id,sender_id,body,created_at").eq("conversation_id", conversation_id).order("created_at").limit(500).execute().data or []
    users = sb.table("users").select("id,full_name,role,username").execute().data or []
    user_map = {str(u.get("id")): u for u in users}
    for message in messages:
        person = user_map.get(str(message.get("sender_id")), {})
        message["sender_name"] = person.get("full_name") or person.get("username") or str(message.get("sender_id") or "Unknown")
        message["sender_role"] = person.get("role") or "unknown"
    return {"messages": messages, "total": len(messages)}
