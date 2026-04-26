"""
routers/admin.py
────────────────
ADMIN ONLY — full rights over every table.

Users
  GET    /api/admin/users
  POST   /api/admin/users
  GET    /api/admin/users/{uid}
  PUT    /api/admin/users/{uid}
  DELETE /api/admin/users/{uid}
  PATCH  /api/admin/users/{uid}/toggle"""
routers/admin.py
────────────────
ADMIN ONLY — full rights over every table.

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
  PATCH  /api/admin/attendance/records/{rid}      — override a student's status
  GET    /api/admin/attendance/report             — full report

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
  POST /api/admin/reset-password              — reset any user's password
"""

from datetime import date as DateType
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, EmailStr, Field

from app.database import sb
from app.deps import require_admin
from app.security import hash_password

router = APIRouter(prefix="/admin", tags=["Admin"])

# ── tiny helper ──────────────────────────────────────────────────────────────
def _audit(admin_id: int, action: str, detail: str):
    sb.table("audit_log").insert(
        {"user_id": admin_id, "action": action, "detail": detail}
    ).execute()


# ═══════════════════════════════════════════════════════════════
#  USERS
# ═══════════════════════════════════════════════════════════════

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
    programme:     Optional[str] = None
    batch:         Optional[str] = None
    is_active:     bool = True


class UserUpdate(BaseModel):
    full_name:     Optional[str]      = None
    email:         Optional[EmailStr] = None
    phone:         Optional[str]      = None
    designation:   Optional[str]      = None
    enrollment_no: Optional[str]      = None
    department:    Optional[str]      = None
    programme:     Optional[str]      = None
    batch:         Optional[str]      = None
    is_active:     Optional[bool]     = None
    password:      Optional[str]      = Field(None, min_length=6)


@router.get("/users", summary="List all users")
def list_users(
    role:      Optional[str]  = Query(None),
    search:    Optional[str]  = Query(None),
    is_active: Optional[bool] = Query(None),
    admin: dict = Depends(require_admin),
):
    q = sb.table("users").select("*")
    if role:      q = q.eq("role", role)
    if is_active is not None: q = q.eq("is_active", is_active)
    res = q.order("created_at", desc=True).execute()
    users = res.data or []

    # text search (supabase-py doesn't chain ilike easily across cols — filter in Python)
    if search:
        s = search.lower()
        users = [u for u in users if
                 s in (u.get("full_name") or "").lower() or
                 s in (u.get("username") or "").lower() or
                 s in (u.get("email") or "").lower()]
    return {"users": users, "total": len(users)}


@router.get("/users/{uid}", summary="Get single user")
def get_user(uid: int, admin: dict = Depends(require_admin)):
    res = sb.table("users").select("*").eq("id", uid).single().execute()
    if not res.data:
        raise HTTPException(404, "User not found")
    return res.data


@router.post("/users", status_code=201, summary="Create a user")
def create_user(body: UserCreate, admin: dict = Depends(require_admin)):
    VALID_ROLES = ("student", "faculty", "cr", "admin")
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

    row = {
        "username":      body.username,
        "role":          body.role,
        "full_name":     body.full_name,
        "email":         body.email,
        "password_hash": hash_password(body.password),
        "phone":         body.phone,
        "designation":   body.designation,
        "enrollment_no": body.enrollment_no,
        "department":    body.department,
        "programme":     body.programme,
        "batch":         body.batch,
        "is_active":     body.is_active,
    }
    res = sb.table("users").insert(row).execute()
    _audit(admin["id"], "CREATE_USER", f"Created {body.role} '{body.username}'")
    return res.data[0]


@router.put("/users/{uid}", summary="Update a user")
def update_user(uid: int, body: UserUpdate, admin: dict = Depends(require_admin)):
    existing = sb.table("users").select("id").eq("id", uid).single().execute()
    if not existing.data:
        raise HTTPException(404, "User not found")

    updates = body.model_dump(exclude_none=True)

    if "password" in updates:
        updates["password_hash"] = hash_password(updates.pop("password"))

    if "email" in updates:
        dup = sb.table("users").select("id").eq("email", updates["email"]).neq("id", uid).execute()
        if dup.data:
            raise HTTPException(409, "Email already in use")

    if "is_active" in updates and not updates["is_active"] and uid == admin["id"]:
        raise HTTPException(400, "Cannot deactivate your own account")

    if not updates:
        raise HTTPException(400, "No fields to update")

    res = sb.table("users").update(updates).eq("id", uid).execute()
    _audit(admin["id"], "UPDATE_USER", f"Updated user id={uid}")
    return res.data[0]


@router.delete("/users/{uid}", summary="Delete a user")
def delete_user(uid: int, admin: dict = Depends(require_admin)):
    if uid == admin["id"]:
        raise HTTPException(400, "Cannot delete your own account")
    existing = sb.table("users").select("id,username,role").eq("id", uid).single().execute()
    if not existing.data:
        raise HTTPException(404, "User not found")
    u = existing.data
    sb.table("users").delete().eq("id", uid).execute()
    _audit(admin["id"], "DELETE_USER", f"Deleted {u['role']} '{u['username']}'")
    return {"message": "User deleted successfully"}


@router.patch("/users/{uid}/toggle", summary="Toggle active/inactive")
def toggle_user(uid: int, admin: dict = Depends(require_admin)):
    if uid == admin["id"]:
        raise HTTPException(400, "Cannot deactivate your own account")
    res = sb.table("users").select("id,username,is_active").eq("id", uid).single().execute()
    if not res.data:
        raise HTTPException(404, "User not found")
    new_status = not res.data["is_active"]
    sb.table("users").update({"is_active": new_status}).eq("id", uid).execute()
    _audit(admin["id"], "TOGGLE_USER",
           f"{res.data['username']} → {'active' if new_status else 'inactive'}")
    return {"is_active": new_status}


# ═══════════════════════════════════════════════════════════════
#  PASSWORD RESET  (admin resets any user's password)
# ═══════════════════════════════════════════════════════════════

class PasswordResetBody(BaseModel):
    username:     str
    new_password: str = Field(..., min_length=6)


@router.post("/reset-password", summary="Reset any user's password")
def reset_password(body: PasswordResetBody, admin: dict = Depends(require_admin)):
    res = sb.table("users").select("id,full_name,username").eq("username", body.username).eq("is_active", True).single().execute()
    if not res.data:
        raise HTTPException(404, "No active user with that username")
    u = res.data
    sb.table("users").update({"password_hash": hash_password(body.new_password)}).eq("id", u["id"]).execute()
    _audit(admin["id"], "PASSWORD_RESET", f"Admin reset password for '{u['username']}'")
    return {"message": f"Password for '{u['username']}' has been reset."}


# ═══════════════════════════════════════════════════════════════
#  TIMETABLE  (admin creates / manages the schedule)
# ═══════════════════════════════════════════════════════════════

class TimetableCreate(BaseModel):
    subject:     str
    day_of_week: str    # Monday … Saturday
    start_time:  str    # HH:MM
    end_time:    str
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
    room:        Optional[str] = None
    department:  Optional[str] = None
    faculty_id:  Optional[int] = None


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


# ═══════════════════════════════════════════════════════════════
#  ATTENDANCE OVERSIGHT
# ═══════════════════════════════════════════════════════════════

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
           f"Overrode record id={rid} → {body.status}")
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


# ═══════════════════════════════════════════════════════════════
#  EVENTS
# ═══════════════════════════════════════════════════════════════

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
def delete_event(eid: int, admin: dict = Depends(require_admin)):
    existing = sb.table("events").select("id").eq("id", eid).single().execute()
    if not existing.data:
        raise HTTPException(404, "Event not found")
    sb.table("events").delete().eq("id", eid).execute()
    _audit(admin["id"], "DELETE_EVENT", f"Deleted event id={eid}")
    return {"message": "Event deleted"}


# ═══════════════════════════════════════════════════════════════
#  NEWS
# ═══════════════════════════════════════════════════════════════

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
    existing = sb.table("news_items").select("id").eq("id", nid).single().execute()
    if not existing.data:
        raise HTTPException(404, "News item not found")
    d = body.model_dump(exclude_none=True)
    if "published_date" in d: d["published_date"] = str(d["published_date"])
    res = sb.table("news_items").update(d).eq("id", nid).execute()
    _audit(admin["id"], "UPDATE_NEWS", f"Updated news id={nid}")
    return res.data[0]


@router.delete("/news/{nid}", summary="Delete news item")
def delete_news(nid: int, admin: dict = Depends(require_admin)):
    existing = sb.table("news_items").select("id").eq("id", nid).single().execute()
    if not existing.data:
        raise HTTPException(404, "News item not found")
    sb.table("news_items").delete().eq("id", nid).execute()
    _audit(admin["id"], "DELETE_NEWS", f"Deleted news id={nid}")
    return {"message": "News item deleted"}


# ═══════════════════════════════════════════════════════════════
#  FACULTY DIRECTORY
# ═══════════════════════════════════════════════════════════════

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
    existing = sb.table("faculty_directory").select("id").eq("id", fid).single().execute()
    if not existing.data:
        raise HTTPException(404, "Faculty entry not found")
    res = sb.table("faculty_directory").update(body.model_dump()).eq("id", fid).execute()
    _audit(admin["id"], "UPDATE_FACULTY_DIR", f"Updated faculty dir id={fid}")
    return res.data[0]


@router.delete("/faculty-directory/{fid}", summary="Remove from faculty directory")
def delete_faculty_dir(fid: int, admin: dict = Depends(require_admin)):
    existing = sb.table("faculty_directory").select("id").eq("id", fid).single().execute()
    if not existing.data:
        raise HTTPException(404, "Faculty entry not found")
    sb.table("faculty_directory").delete().eq("id", fid).execute()
    _audit(admin["id"], "DELETE_FACULTY_DIR", f"Deleted faculty dir id={fid}")
    return {"message": "Faculty entry deleted"}


# ═══════════════════════════════════════════════════════════════
#  STATS / AUDIT / MESSAGES
# ═══════════════════════════════════════════════════════════════

@router.get("/stats", summary="Dashboard statistics")
def stats(admin: dict = Depends(require_admin)):
    users = sb.table("users").select("role, is_active").execute().data or []
    counts = {}
    for u in users:
        counts[u["role"]] = counts.get(u["role"], 0) + 1
    active = sum(1 for u in users if u["is_active"])

    recent = sb.table("users").select("*").not_.is_("last_login", "null") \
               .order("last_login", desc=True).limit(10).execute().data or []
    log    = sb.table("audit_log").select("*").order("ts", desc=True).limit(20).execute().data or []

    return {"counts": counts, "total": len(users), "active": active,
            "recent_logins": recent, "log": log}


@router.get("/audit", summary="Full audit log")
def audit_log(admin: dict = Depends(require_admin)):
    return sb.table("audit_log").select("*, users(username, full_name)") \
             .order("ts", desc=True).limit(200).execute().data or []


@router.get("/messages", summary="Contact messages")
def list_messages(admin: dict = Depends(require_admin)):
    return sb.table("contact_messages").select("*").order("submitted_at", desc=True).execute().data or []


@router.patch("/messages/{mid}/read", summary="Mark message as read")
def mark_read(mid: int, admin: dict = Depends(require_admin)):
    existing = sb.table("contact_messages").select("id").eq("id", mid).single().execute()
    if not existing.data:
        raise HTTPException(404, "Message not found")
    sb.table("contact_messages").update({"is_read": True}).eq("id", mid).execute()
    return {"message": "Marked as read"}


Timetable  (admin owns the schedule)
  GET    /api/admin/timetable
  POST   /api/admin/timetable
  PUT    /api/admin/timetable/{slot_id}
  DELETE /api/admin/timetable/{slot_id}

Attendance oversight
  GET    /api/admin/attendance/sessions
  PATCH  /api/admin/attendance/sessions/{sid}/close
  DELETE /api/admin/attendance/sessions/{sid}
  PATCH  /api/admin/attendance/records/{rid}      — override a student's status
  GET    /api/admin/attendance/report             — full report

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
  POST /api/admin/reset-password              — reset any user's password
"""

from datetime import date as DateType
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, EmailStr, Field

from app.db import sb
from app.deps import require_admin
from app.security import hash_password

router = APIRouter(prefix="/admin", tags=["Admin"])

# ── tiny helper ──────────────────────────────────────────────────────────────
def _audit(admin_id: int, action: str, detail: str):
    sb.table("audit_log").insert(
        {"user_id": admin_id, "action": action, "detail": detail}
    ).execute()


# ═══════════════════════════════════════════════════════════════
#  USERS
# ═══════════════════════════════════════════════════════════════

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
    programme:     Optional[str] = None
    batch:         Optional[str] = None
    is_active:     bool = True


class UserUpdate(BaseModel):
    full_name:     Optional[str]      = None
    email:         Optional[EmailStr] = None
    phone:         Optional[str]      = None
    designation:   Optional[str]      = None
    enrollment_no: Optional[str]      = None
    department:    Optional[str]      = None
    programme:     Optional[str]      = None
    batch:         Optional[str]      = None
    is_active:     Optional[bool]     = None
    password:      Optional[str]      = Field(None, min_length=6)


@router.get("/users", summary="List all users")
def list_users(
    role:      Optional[str]  = Query(None),
    search:    Optional[str]  = Query(None),
    is_active: Optional[bool] = Query(None),
    admin: dict = Depends(require_admin),
):
    q = sb.table("users").select("*")
    if role:      q = q.eq("role", role)
    if is_active is not None: q = q.eq("is_active", is_active)
    res = q.order("created_at", desc=True).execute()
    users = res.data or []

    # text search (supabase-py doesn't chain ilike easily across cols — filter in Python)
    if search:
        s = search.lower()
        users = [u for u in users if
                 s in (u.get("full_name") or "").lower() or
                 s in (u.get("username") or "").lower() or
                 s in (u.get("email") or "").lower()]
    return {"users": users, "total": len(users)}


@router.get("/users/{uid}", summary="Get single user")
def get_user(uid: int, admin: dict = Depends(require_admin)):
    res = sb.table("users").select("*").eq("id", uid).single().execute()
    if not res.data:
        raise HTTPException(404, "User not found")
    return res.data


@router.post("/users", status_code=201, summary="Create a user")
def create_user(body: UserCreate, admin: dict = Depends(require_admin)):
    VALID_ROLES = ("student", "faculty", "cr", "admin")
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

    row = {
        "username":      body.username,
        "role":          body.role,
        "full_name":     body.full_name,
        "email":         body.email,
        "password_hash": hash_password(body.password),
        "phone":         body.phone,
        "designation":   body.designation,
        "enrollment_no": body.enrollment_no,
        "department":    body.department,
        "programme":     body.programme,
        "batch":         body.batch,
        "is_active":     body.is_active,
    }
    res = sb.table("users").insert(row).execute()
    _audit(admin["id"], "CREATE_USER", f"Created {body.role} '{body.username}'")
    return res.data[0]


@router.put("/users/{uid}", summary="Update a user")
def update_user(uid: int, body: UserUpdate, admin: dict = Depends(require_admin)):
    existing = sb.table("users").select("id").eq("id", uid).single().execute()
    if not existing.data:
        raise HTTPException(404, "User not found")

    updates = body.model_dump(exclude_none=True)

    if "password" in updates:
        updates["password_hash"] = hash_password(updates.pop("password"))

    if "email" in updates:
        dup = sb.table("users").select("id").eq("email", updates["email"]).neq("id", uid).execute()
        if dup.data:
            raise HTTPException(409, "Email already in use")

    if "is_active" in updates and not updates["is_active"] and uid == admin["id"]:
        raise HTTPException(400, "Cannot deactivate your own account")

    if not updates:
        raise HTTPException(400, "No fields to update")

    res = sb.table("users").update(updates).eq("id", uid).execute()
    _audit(admin["id"], "UPDATE_USER", f"Updated user id={uid}")
    return res.data[0]


@router.delete("/users/{uid}", summary="Delete a user")
def delete_user(uid: int, admin: dict = Depends(require_admin)):
    if uid == admin["id"]:
        raise HTTPException(400, "Cannot delete your own account")
    existing = sb.table("users").select("id,username,role").eq("id", uid).single().execute()
    if not existing.data:
        raise HTTPException(404, "User not found")
    u = existing.data
    sb.table("users").delete().eq("id", uid).execute()
    _audit(admin["id"], "DELETE_USER", f"Deleted {u['role']} '{u['username']}'")
    return {"message": "User deleted successfully"}


@router.patch("/users/{uid}/toggle", summary="Toggle active/inactive")
def toggle_user(uid: int, admin: dict = Depends(require_admin)):
    if uid == admin["id"]:
        raise HTTPException(400, "Cannot deactivate your own account")
    res = sb.table("users").select("id,username,is_active").eq("id", uid).single().execute()
    if not res.data:
        raise HTTPException(404, "User not found")
    new_status = not res.data["is_active"]
    sb.table("users").update({"is_active": new_status}).eq("id", uid).execute()
    _audit(admin["id"], "TOGGLE_USER",
           f"{res.data['username']} → {'active' if new_status else 'inactive'}")
    return {"is_active": new_status}


# ═══════════════════════════════════════════════════════════════
#  PASSWORD RESET  (admin resets any user's password)
# ═══════════════════════════════════════════════════════════════

class PasswordResetBody(BaseModel):
    username:     str
    new_password: str = Field(..., min_length=6)


@router.post("/reset-password", summary="Reset any user's password")
def reset_password(body: PasswordResetBody, admin: dict = Depends(require_admin)):
    res = sb.table("users").select("id,full_name,username").eq("username", body.username).eq("is_active", True).single().execute()
    if not res.data:
        raise HTTPException(404, "No active user with that username")
    u = res.data
    sb.table("users").update({"password_hash": hash_password(body.new_password)}).eq("id", u["id"]).execute()
    _audit(admin["id"], "PASSWORD_RESET", f"Admin reset password for '{u['username']}'")
    return {"message": f"Password for '{u['username']}' has been reset."}


# ═══════════════════════════════════════════════════════════════
#  TIMETABLE  (admin creates / manages the schedule)
# ═══════════════════════════════════════════════════════════════

class TimetableCreate(BaseModel):
    subject:     str
    day_of_week: str    # Monday … Saturday
    start_time:  str    # HH:MM
    end_time:    str
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
    room:        Optional[str] = None
    department:  Optional[str] = None
    faculty_id:  Optional[int] = None


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


# ═══════════════════════════════════════════════════════════════
#  ATTENDANCE OVERSIGHT
# ═══════════════════════════════════════════════════════════════

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
           f"Overrode record id={rid} → {body.status}")
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


# ═══════════════════════════════════════════════════════════════
#  EVENTS
# ═══════════════════════════════════════════════════════════════

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
def delete_event(eid: int, admin: dict = Depends(require_admin)):
    existing = sb.table("events").select("id").eq("id", eid).single().execute()
    if not existing.data:
        raise HTTPException(404, "Event not found")
    sb.table("events").delete().eq("id", eid).execute()
    _audit(admin["id"], "DELETE_EVENT", f"Deleted event id={eid}")
    return {"message": "Event deleted"}


# ═══════════════════════════════════════════════════════════════
#  NEWS
# ═══════════════════════════════════════════════════════════════

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
    existing = sb.table("news_items").select("id").eq("id", nid).single().execute()
    if not existing.data:
        raise HTTPException(404, "News item not found")
    d = body.model_dump(exclude_none=True)
    if "published_date" in d: d["published_date"] = str(d["published_date"])
    res = sb.table("news_items").update(d).eq("id", nid).execute()
    _audit(admin["id"], "UPDATE_NEWS", f"Updated news id={nid}")
    return res.data[0]


@router.delete("/news/{nid}", summary="Delete news item")
def delete_news(nid: int, admin: dict = Depends(require_admin)):
    existing = sb.table("news_items").select("id").eq("id", nid).single().execute()
    if not existing.data:
        raise HTTPException(404, "News item not found")
    sb.table("news_items").delete().eq("id", nid).execute()
    _audit(admin["id"], "DELETE_NEWS", f"Deleted news id={nid}")
    return {"message": "News item deleted"}


# ═══════════════════════════════════════════════════════════════
#  FACULTY DIRECTORY
# ═══════════════════════════════════════════════════════════════

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
    existing = sb.table("faculty_directory").select("id").eq("id", fid).single().execute()
    if not existing.data:
        raise HTTPException(404, "Faculty entry not found")
    res = sb.table("faculty_directory").update(body.model_dump()).eq("id", fid).execute()
    _audit(admin["id"], "UPDATE_FACULTY_DIR", f"Updated faculty dir id={fid}")
    return res.data[0]


@router.delete("/faculty-directory/{fid}", summary="Remove from faculty directory")
def delete_faculty_dir(fid: int, admin: dict = Depends(require_admin)):
    existing = sb.table("faculty_directory").select("id").eq("id", fid).single().execute()
    if not existing.data:
        raise HTTPException(404, "Faculty entry not found")
    sb.table("faculty_directory").delete().eq("id", fid).execute()
    _audit(admin["id"], "DELETE_FACULTY_DIR", f"Deleted faculty dir id={fid}")
    return {"message": "Faculty entry deleted"}


# ═══════════════════════════════════════════════════════════════
#  STATS / AUDIT / MESSAGES
# ═══════════════════════════════════════════════════════════════

@router.get("/stats", summary="Dashboard statistics")
def stats(admin: dict = Depends(require_admin)):
    users = sb.table("users").select("role, is_active").execute().data or []
    counts = {}
    for u in users:
        counts[u["role"]] = counts.get(u["role"], 0) + 1
    active = sum(1 for u in users if u["is_active"])

    recent = sb.table("users").select("*").not_.is_("last_login", "null") \
               .order("last_login", desc=True).limit(10).execute().data or []
    log    = sb.table("audit_log").select("*").order("ts", desc=True).limit(20).execute().data or []

    return {"counts": counts, "total": len(users), "active": active,
            "recent_logins": recent, "log": log}


@router.get("/audit", summary="Full audit log")
def audit_log(admin: dict = Depends(require_admin)):
    return sb.table("audit_log").select("*, users(username, full_name)") \
             .order("ts", desc=True).limit(200).execute().data or []


@router.get("/messages", summary="Contact messages")
def list_messages(admin: dict = Depends(require_admin)):
    return sb.table("contact_messages").select("*").order("submitted_at", desc=True).execute().data or []


@router.patch("/messages/{mid}/read", summary="Mark message as read")
def mark_read(mid: int, admin: dict = Depends(require_admin)):
    existing = sb.table("contact_messages").select("id").eq("id", mid).single().execute()
    if not existing.data:
        raise HTTPException(404, "Message not found")
    sb.table("contact_messages").update({"is_read": True}).eq("id", mid).execute()
    return {"message": "Marked as read"}
