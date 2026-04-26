"""
routers/student.py
──────────────────
STUDENT / CR rights:
  ✅ View own profile                        GET  /api/student/profile
  ✅ View their timetable                    GET  /api/student/timetable
  ✅ View open attendance sessions           GET  /api/student/attendance/open
  ✅ Mark attendance in an open session      POST /api/student/attendance/mark
  ✅ View own attendance history             GET  /api/student/attendance/history
  ⛔ Any profile edits / password change — admin only
  ⛔ Open or close sessions — faculty only
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from typing import Optional

from app.db import sb
from app.deps import require_student

router = APIRouter(prefix="/student", tags=["Student"])


# ═══════════════════════════════════════════════════════════════
#  PROFILE  (read-only)
# ═══════════════════════════════════════════════════════════════

@router.get("/profile", summary="View your profile (read-only)")
def get_profile(student: dict = Depends(require_student)):
    """Returns your profile. Contact admin to make any changes."""
    student.pop("password_hash", None)
    return student


# ═══════════════════════════════════════════════════════════════
#  TIMETABLE  (view own schedule)
# ═══════════════════════════════════════════════════════════════

@router.get("/timetable", summary="View your class timetable")
def get_timetable(
    day: Optional[str] = Query(None, description="Filter by day e.g. Monday"),
    student: dict = Depends(require_student),
):
    """
    Returns all timetable slots for the student's programme and batch.
    Includes the assigned faculty name and room.
    """
    if not student.get("programme") or not student.get("batch"):
        raise HTTPException(400, "Your account has no programme/batch assigned. Contact admin.")

    q = sb.table("timetable_slots").select(
        "id, subject, day_of_week, start_time, end_time, room, programme, batch, department, "
        "users(full_name, email)"   # joined faculty info
    ).eq("programme", student["programme"]).eq("batch", student["batch"])

    if day:
        q = q.eq("day_of_week", day)

    res = q.order("day_of_week").order("start_time").execute()
    return res.data or []


# ═══════════════════════════════════════════════════════════════
#  ATTENDANCE — VIEW OPEN SESSIONS
# ═══════════════════════════════════════════════════════════════

@router.get("/attendance/open", summary="View open attendance sessions for your classes")
def open_sessions(student: dict = Depends(require_student)):
    """
    Returns all currently open attendance sessions for the student's
    programme and batch. Student can mark attendance in any of these.
    """
    if not student.get("programme") or not student.get("batch"):
        raise HTTPException(400, "Your account has no programme/batch assigned. Contact admin.")

    # Get all timetable slot IDs for this student's programme+batch
    slots = sb.table("timetable_slots").select("id").eq(
        "programme", student["programme"]
    ).eq("batch", student["batch"]).execute()

    slot_ids = [s["id"] for s in (slots.data or [])]
    if not slot_ids:
        return []

    # Get open sessions for those slots
    sessions = sb.table("attendance_sessions").select(
        "id, slot_id, date, opened_at, "
        "timetable_slots(subject, day_of_week, start_time, end_time, room), "
        "users(full_name)"  # faculty who opened it
    ).eq("is_open", True).in_("slot_id", slot_ids).execute()

    result = sessions.data or []

    # Flag whether this student already marked attendance for each session
    if result:
        for sess in result:
            rec = sb.table("attendance_records").select("id, status") \
                    .eq("session_id", sess["id"]) \
                    .eq("student_id", student["id"]).execute()
            sess["already_marked"] = bool(rec.data)
            sess["my_status"] = rec.data[0]["status"] if rec.data else None

    return result


# ═══════════════════════════════════════════════════════════════
#  ATTENDANCE — MARK ATTENDANCE
# ═══════════════════════════════════════════════════════════════

class MarkAttendanceBody(BaseModel):
    session_id: int


@router.post("/attendance/mark", status_code=201, summary="Mark yourself present in an open session")
def mark_attendance(body: MarkAttendanceBody, student: dict = Depends(require_student)):
    """
    Marks the student as PRESENT in the given open session.
    - Session must be open
    - The session's slot must belong to the student's programme+batch
    - Student cannot mark twice for the same session
    """
    # 1. Verify session exists and is open
    session = sb.table("attendance_sessions").select(
        "id, is_open, slot_id, timetable_slots(programme, batch, subject)"
    ).eq("id", body.session_id).single().execute()

    if not session.data:
        raise HTTPException(404, "Attendance session not found")

    sess = session.data
    if not sess["is_open"]:
        raise HTTPException(400, "This attendance session is closed. You can no longer mark attendance.")

    # 2. Verify the session belongs to the student's class
    slot_prog  = (sess.get("timetable_slots") or {}).get("programme")
    slot_batch = (sess.get("timetable_slots") or {}).get("batch")
    if slot_prog != student.get("programme") or slot_batch != student.get("batch"):
        raise HTTPException(403, "This session is not for your class.")

    # 3. Check if already marked
    existing = sb.table("attendance_records") \
                 .select("id, status") \
                 .eq("session_id", body.session_id) \
                 .eq("student_id", student["id"]).execute()
    if existing.data:
        raise HTTPException(409, f"You have already marked attendance as '{existing.data[0]['status']}'.")

    # 4. Insert record
    res = sb.table("attendance_records").insert({
        "session_id": body.session_id,
        "student_id": student["id"],
        "status":     "present",
    }).execute()

    sb.table("audit_log").insert({
        "user_id": student["id"],
        "action":  "MARK_ATTENDANCE",
        "detail":  f"Student '{student['username']}' marked present in session id={body.session_id}",
    }).execute()

    subject = (sess.get("timetable_slots") or {}).get("subject", "")
    return {
        "message": f"Attendance marked as PRESENT for '{subject}'.",
        "record":  res.data[0],
    }


# ═══════════════════════════════════════════════════════════════
#  ATTENDANCE — VIEW OWN HISTORY
# ═══════════════════════════════════════════════════════════════

@router.get("/attendance/history", summary="View your attendance history")
def attendance_history(
    subject: Optional[str] = Query(None),
    student: dict = Depends(require_student),
):
    """
    Returns the full attendance history for the logged-in student,
    with subject name, date, and present/absent status.
    """
    res = sb.table("attendance_records").select(
        "id, status, marked_at, "
        "attendance_sessions(date, timetable_slots(subject, start_time, end_time, room))"
    ).eq("student_id", student["id"]).order("marked_at", desc=True).execute()

    records = res.data or []

    if subject:
        records = [r for r in records if
                   subject.lower() in (
                       (r.get("attendance_sessions") or {})
                       .get("timetable_slots", {})
                       .get("subject", "") or ""
                   ).lower()]

    total   = len(records)
    present = sum(1 for r in records if r["status"] == "present")
    absent  = total - present

    return {
        "student":    student["full_name"],
        "total":      total,
        "present":    present,
        "absent":     absent,
        "percentage": round((present / total * 100), 1) if total else 0,
        "records":    records,
    }
