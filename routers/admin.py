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

#  tiny helper 
def _audit(admin_id: int, action: str, detail: str):
    sb.table("audit_log").insert(
        {"user_id": admin_id, "action": action, "detail": detail}
    ).execute()


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
    q = sb.table("users").select("*").eq("id", uid)
    res = q.single().execute()
    if not res.data:
        raise HTTPException(404, "User not found")
    return res.data


@router.post("/users", status_code=201, summary="Create a user")
def create_user(body: UserCreate, admin: dict = Depends(require_admin)):
    # Class Representatives are existing students promoted by Admin; they cannot be created as new accounts.
    if body.role == "cr":
        raise HTTPException(400, "A Class Representative must be assigned from an existing Student account.")
    _validate_programme_batch(body.programme, body.batch, body.role)
    if body.role not in VALID_ROLES:
        raise HTTPException(400, f"Invalid role. Must be one of: {VALID_ROLES}")

    # check duplicate username+role
    dup = sb.table("users").select("id").eq("username", body.username).eq("role", body.role).execute()
    if dup.data:
        raise HTTPException(409, "Username already exists for this role")

    # check duplicate email
    dup_email = sb.table("users").select("id").eq("email", body.email).execute()
    if dup_email.data:
        raise HTTPException(409, "Email already in use")

    # Every account's password lives in Supabase Auth, admin-created ones
    # included — the profile row never stores a hash. Auto-confirm the
    # email since an admin is vouching for this account directly.
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
        "domain":         body.domain,
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
            sb.auth.delete_user(supabase_uid)
        except Exception:
            pass
        raise HTTPException(502, "Could not complete user creation. Please try again.")

    _audit(admin["id"], "CREATE_USER", f"Created {body.role} '{body.username}'")
    return res.data[0]


@router.put("/users/{uid}", summary="Update a user")
def update_user(uid: int, body: UserUpdate, admin: dict = Depends(require_admin)):
    existing = (sb.table("users").select("id,username,role,is_super_admin,supabase_uid,programme,batch,domain").eq("id", uid).single().execute())
    if not existing.data:
        raise HTTPException(
            404,
            "User not found"
        )

    target = existing.data

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

    if "role" in updates:
        # Admins may assign/remove the Class Representative role for students.
        # Other role changes remain restricted to the Super Admin.
        new_role = updates["role"]
        current_role = target.get("role")
        if new_role not in VALID_ROLES:
            raise HTTPException(400, f"Invalid role. Must be one of: {VALID_ROLES}")
        if not admin.get("is_super_admin", False):
            if not ({current_role, new_role} <= {"student", "cr"}):
                raise HTTPException(403, "Only the Super Admin can make this role change.")

    new_password = updates.pop("password", None)
    if new_password:
        if not target.get("supabase_uid"):
            raise HTTPException(500, "This account has no linked Auth identity; cannot set a password.")
        try:
            sb.auth.admin.update_user_by_id(target["supabase_uid"], {"password": new_password})
        except Exception:
            raise HTTPException(502, "Could not update the password. Please try again.")

    if "email" in updates:
        dup = sb.table("users").select("id").eq("email", updates["email"]).neq("id", uid).execute()
        if dup.data:
            raise HTTPException(409, "Email already in use")

    if "is_active" in updates and not updates["is_active"] and uid == admin["id"]:
        raise HTTPException(400, "Cannot deactivate your own account")

    if not updates and not new_password:
        raise HTTPException(400, "No fields to update")

    if updates:
        res = sb.table("users").update(updates).eq("id", uid).execute()
        row = res.data[0]
    else:
        row = sb.table("users").select("*").eq("id", uid).single().execute().data

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
        .single()
        .execute()
    )

    if not existing.data:
        raise HTTPException(
            status_code=404,
            detail="User not found."
        )

    target = existing.data

    # Nobody can delete the permanent Super Admin.
    if target.get("is_super_admin", False):
        raise HTTPException(
            status_code=403,
            detail="The Super Admin cannot be deleted."
        )

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
        .single()
        .execute()
    )

    if not result.data:
        raise HTTPException(
            status_code=404,
            detail="User not found."
        )

    target = result.data

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
        .single()
        .execute()
    )
    if not existing.data:
        raise HTTPException(404, "Registration request not found")

    target = existing.data
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
        .select("id, username, role, status, email, full_name")
        .eq("id", uid)
        .single()
        .execute()
    )
    if not existing.data:
        raise HTTPException(404, "Registration request not found")

    target = existing.data
    if target["status"] != "pending":
        raise HTTPException(400, f"This request has already been {target['status']}")

    # Same backend-enforced rule as approval.
    if target["role"] == "admin" and not admin.get("is_super_admin", False):
        raise HTTPException(403, "Only the Super Admin can reject Admin registrations.")

    sb.table("users").update({
        "status":           "rejected",
        "approved_by":      admin["id"],
        "approved_at":      datetime.now(timezone.utc).isoformat(),
        "rejection_reason": body.rejection_reason,
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
        .single()
        .execute()
    )

    if not res.data:
        raise HTTPException(
            status_code=404,
            detail="No active user with that username"
        )

    u = res.data

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
#  TIMETABLE  (admin creates / manages the schedule)
# 

class TimetableCreate(BaseModel):
    subject:     str
    day_of_week: str
    start_time:  str    # HH:MM
    end_time:    str    # HH:MM
    programme:   str
    batch:       str
    room:        Optional[str] = None
    department:  Optional[str] = None
    faculty_id:  Optional[int] = None


class TimetableUpdate(BaseModel):
    subject:     Optional[str] = None
    day_of_week: Optional[str] = None
    start_time:  Optional[str] = None
    end_time:    Optional[str] = None
    programme:   Optional[str] = None
    batch:       Optional[str] = None
    section:     Optional[str] = None
    room:        Optional[str] = None
    department:  Optional[str] = None
    faculty_id:  Optional[int] = None


# Academic timetable rule: classes are one hour, with a fixed 30-minute
# break from 13:00 to 13:30. The API rejects any slot outside these windows.
VALID_TIMETABLE_SLOTS = {
    ("09:00", "10:00"),
    ("10:00", "11:00"),
    ("11:00", "12:00"),
    ("12:00", "13:00"),
    ("13:30", "14:30"),
    ("14:30", "15:30"),
    ("15:30", "16:30"),
    ("16:30", "17:30"),
}


def _validate_timetable_slot(start_time: str, end_time: str, day_of_week: str) -> None:
    day = (day_of_week or "").strip().lower()
    if day not in {"monday", "tuesday", "wednesday", "thursday", "friday"}:
        raise HTTPException(400, "Timetable day must be Monday through Friday.")

    if (start_time, end_time) not in VALID_TIMETABLE_SLOTS:
        raise HTTPException(
            400,
            "Invalid timetable slot. Classes run hourly from 9:00 AM to 1:00 PM "
            "and 1:30 PM to 5:30 PM, with a fixed 30-minute break from 1:00 PM to 1:30 PM."
        )


@router.get("/timetable", summary="List all timetable slots")
def list_timetable(
    programme: Optional[str] = Query(None),
    batch:     Optional[str] = Query(None),
    admin: dict = Depends(require_admin),
):
    q = sb.table("timetable_slots").select("*, users(full_name, email)")
    if programme: q = q.ilike("programme", f"%{programme}%")
    if batch:     q = q.eq("batch", batch)
    res = q.order("day_of_week").order("start_time").execute()
    return res.data or []


@router.post("/timetable", status_code=201, summary="Create a timetable slot")
def create_slot(body: TimetableCreate, admin: dict = Depends(require_admin)):
    _validate_timetable_slot(body.start_time, body.end_time, body.day_of_week)
    if body.faculty_id:
        fac = sb.table("users").select("id").eq("id", body.faculty_id).eq("role", "faculty").execute()
        if not fac.data:
            raise HTTPException(404, "Faculty user not found")
    res = sb.table("timetable_slots").insert(body.model_dump()).execute()
    _audit(admin["id"], "CREATE_TIMETABLE",
           f"{body.subject} {body.day_of_week} {body.start_time} [{body.programme} {body.batch}]")
    return res.data[0]


@router.put("/timetable/{slot_id}", summary="Update a timetable slot")
def update_slot(slot_id: int, body: TimetableUpdate, admin: dict = Depends(require_admin)):
    existing = sb.table("timetable_slots").select("id").eq("id", slot_id).single().execute()
    if not existing.data:
        raise HTTPException(404, "Timetable slot not found")
    updates = body.model_dump(exclude_none=True)
    if not updates:
        raise HTTPException(400, "No fields to update")

    current = sb.table("timetable_slots").select(
        "day_of_week,start_time,end_time"
    ).eq("id", slot_id).single().execute().data or {}
    _validate_timetable_slot(
        updates.get("start_time", current.get("start_time")),
        updates.get("end_time", current.get("end_time")),
        updates.get("day_of_week", current.get("day_of_week")),
    )
    res = sb.table("timetable_slots").update(updates).eq("id", slot_id).execute()
    _audit(admin["id"], "UPDATE_TIMETABLE", f"Updated slot id={slot_id}")
    return res.data[0]


@router.delete("/timetable/{slot_id}", summary="Delete a timetable slot")
def delete_slot(slot_id: int, admin: dict = Depends(require_admin)):
    existing = sb.table("timetable_slots").select("id").eq("id", slot_id).single().execute()
    if not existing.data:
        raise HTTPException(404, "Timetable slot not found")
    sb.table("timetable_slots").delete().eq("id", slot_id).execute()
    _audit(admin["id"], "DELETE_TIMETABLE", f"Deleted slot id={slot_id}")
    return {"message": "Timetable slot deleted"}


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
    existing = sb.table("attendance_sessions").select("id,is_open").eq("id", sid).single().execute()
    if not existing.data:
        raise HTTPException(404, "Session not found")
    res = sb.table("attendance_sessions").update({
        "is_open": False,
        "closed_at": datetime.now(timezone.utc).isoformat()
    }).eq("id", sid).execute()
    _audit(admin["id"], "CLOSE_SESSION", f"Admin force-closed session id={sid}")
    return res.data[0]


@router.delete("/attendance/sessions/{sid}", summary="Delete an attendance session")
def delete_session(sid: int, admin: dict = Depends(require_admin)):
    existing = sb.table("attendance_sessions").select("id").eq("id", sid).single().execute()
    if not existing.data:
        raise HTTPException(404, "Session not found")
    sb.table("attendance_sessions").delete().eq("id", sid).execute()
    _audit(admin["id"], "DELETE_SESSION", f"Deleted session id={sid}")
    return {"message": "Session deleted"}


@router.patch("/attendance/records/{rid}", summary="Override a student's attendance status")
def override_record(rid: int, body: RecordOverride, admin: dict = Depends(require_admin)):
    if body.status not in ("present", "absent"):
        raise HTTPException(400, "Status must be 'present' or 'absent'")
    existing = sb.table("attendance_records").select("id,student_id").eq("id", rid).single().execute()
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
    existing = sb.table("events").select("id").eq("id", eid).single().execute()
    if not existing.data:
        raise HTTPException(404, "Event not found")
    d = body.model_dump(exclude_none=True)
    if "event_date" in d: d["event_date"] = str(d["event_date"])
    res = sb.table("events").update(d).eq("id", eid).execute()
    _audit(admin["id"], "UPDATE_EVENT", f"Updated event id={eid}")
    return res.data[0]


@router.delete("/events/{eid}", summary="Delete event")