"""
routers/faculty.py
──────────────────
FACULTY rights:
  ✅ View own profile                        GET  /api/faculty/profile
  ✅ View students in own department         GET  /api/faculty/students
  ✅ View timetable slots assigned to them   GET  /api/faculty/timetable
  ✅ Open an attendance session              POST /api/faculty/attendance/open
  ✅ Close their own attendance session      PATCH /api/faculty/attendance/{sid}/close
  ✅ View attendance records for a session   GET  /api/faculty/attendance/{sid}/records
  ⛔ Profile edits / password change — admin only
"""

from datetime import date as DateType, datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from app.db import sb
from app.deps import require_faculty

router = APIRouter(prefix="/faculty", tags=["Faculty"])


# ═══════════════════════════════════════════════════════════════
#  PROFILE  (read-only)
# ═══════════════════════════════════════════════════════════════

@router.get("/profile", summary="View your profile (read-only)")
def get_profile(faculty: dict = Depends(require_faculty)):
    """Returns your own profile. Contact admin to make changes."""
    # Remove password hash before returning
    faculty.pop("password_hash", None)
    return faculty


# ═══════════════════════════════════════════════════════════════
#  VIEW STUDENTS IN DEPARTMENT  (read-only)
# ═══════════════════════════════════════════════════════════════

@router.get("/students", summary="View students in your department (read-only)")
def view_department_students(
    programme: Optional[str] = Query(None),
    batch:     Optional[str] = Query(None),
    faculty: dict = Depends(require_faculty),
):
    q = sb.table("users").select(
        "id, username, full_name, email, phone, enrollment_no, department, programme, batch, is_active"
    ).eq("role", "student").eq("is_active", True)

    if faculty.get("department"):
        q = q.eq("department", faculty["department"])
    if programme:
        q = q.ilike("programme", f"%{programme}%")
    if batch:
        q = q.eq("batch", batch)

    res = q.order("full_name").execute()
    return res.data or []


# ═══════════════════════════════════════════════════════════════
#  TIMETABLE  (view slots assigned to this faculty)
# ═══════════════════════════════════════════════════════════════

@router.get("/timetable", summary="View your timetable slots")
def get_my_timetable(faculty: dict = Depends(require_faculty)):
    res = sb.table("timetable_slots").select("*") \
            .eq("faculty_id", faculty["id"]) \
            .order("day_of_week").order("start_time").execute()
    return res.data or []


# ═══════════════════════════════════════════════════════════════
#  ATTENDANCE SESSIONS  (faculty opens / closes sessions)
# ═══════════════════════════════════════════════════════════════

class OpenSessionBody(BaseModel):
    slot_id: int
    date:    DateType


@router.post("/attendance/open", status_code=201, summary="Open an attendance session")
def open_session(body: OpenSessionBody, faculty: dict = Depends(require_faculty)):
    """
    Faculty opens a session for one of their timetable slots on a given date.
    Students can mark attendance only while session is open.
    """
    # Verify the slot belongs to this faculty (or admin can open any)
    slot = sb.table("timetable_slots").select("id, subject, faculty_id") \
             .eq("id", body.slot_id).single().execute()
    if not slot.data:
        raise HTTPException(404, "Timetable slot not found")

    if faculty["role"] != "admin" and slot.data["faculty_id"] != faculty["id"]:
        raise HTTPException(403, "You can only open sessions for your own timetable slots")

    # Check if session already exists for this slot+date
    existing = sb.table("attendance_sessions") \
                 .select("id, is_open") \
                 .eq("slot_id", body.slot_id) \
                 .eq("date", str(body.date)).execute()
    if existing.data:
        sess = existing.data[0]
        if sess["is_open"]:
            raise HTTPException(409, "An open session already exists for this slot and date")
        else:
            # Re-open a closed session
            res = sb.table("attendance_sessions").update({
                "is_open": True,
                "opened_at": datetime.now(timezone.utc).isoformat(),
                "closed_at": None,
            }).eq("id", sess["id"]).execute()
            return {"message": "Session re-opened", "session": res.data[0]}

    # Create new session
    res = sb.table("attendance_sessions").insert({
        "slot_id":    body.slot_id,
        "faculty_id": faculty["id"],
        "date":       str(body.date),
        "is_open":    True,
    }).execute()

    sb.table("audit_log").insert({
        "user_id": faculty["id"],
        "action":  "OPEN_ATTENDANCE_SESSION",
        "detail":  f"Faculty '{faculty['username']}' opened session for slot {body.slot_id} on {body.date}",
    }).execute()

    return {"message": "Attendance session opened", "session": res.data[0]}


@router.patch("/attendance/{sid}/close", summary="Close your attendance session")
def close_session(sid: int, faculty: dict = Depends(require_faculty)):
    """Faculty closes the session — students can no longer mark attendance."""
    session = sb.table("attendance_sessions") \
                .select("id, faculty_id, is_open") \
                .eq("id", sid).single().execute()
    if not session.data:
        raise HTTPException(404, "Session not found")

    s = session.data
    if faculty["role"] != "admin" and s["faculty_id"] != faculty["id"]:
        raise HTTPException(403, "You can only close your own sessions")
    if not s["is_open"]:
        raise HTTPException(400, "Session is already closed")

    res = sb.table("attendance_sessions").update({
        "is_open":   False,
        "closed_at": datetime.now(timezone.utc).isoformat(),
    }).eq("id", sid).execute()

    sb.table("audit_log").insert({
        "user_id": faculty["id"],
        "action":  "CLOSE_ATTENDANCE_SESSION",
        "detail":  f"Faculty '{faculty['username']}' closed session id={sid}",
    }).execute()

    return {"message": "Session closed", "session": res.data[0]}


@router.get("/attendance/{sid}/records", summary="View attendance records for a session")
def session_records(sid: int, faculty: dict = Depends(require_faculty)):
    """View who marked attendance for a session."""
    session = sb.table("attendance_sessions") \
                .select("id, faculty_id, date, timetable_slots(subject, programme, batch)") \
                .eq("id", sid).single().execute()
    if not session.data:
        raise HTTPException(404, "Session not found")

    if faculty["role"] != "admin" and session.data["faculty_id"] != faculty["id"]:
        raise HTTPException(403, "You can only view records for your own sessions")

    records = sb.table("attendance_records") \
                .select("*, users!attendance_records_student_id_fkey(full_name, enrollment_no, programme, batch)") \
                .eq("session_id", sid) \
                .order("marked_at").execute()

    return {
        "session":  session.data,
        "records":  records.data or [],
        "total":    len(records.data or []),
        "present":  sum(1 for r in (records.data or []) if r["status"] == "present"),
        "absent":   sum(1 for r in (records.data or []) if r["status"] == "absent"),
    }
