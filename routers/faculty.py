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

class FacultyProfileUpdate(BaseModel):
    full_name: Optional[str] = None
    phone: Optional[str] = None


@router.get("/profile", summary="View your profile (read-only)")
def get_profile(faculty: dict = Depends(require_faculty)):
    """Returns your own profile. Contact admin to make changes."""
    # Remove password hash before returning
    faculty.pop("password_hash", None)
    return faculty


@router.patch("/profile", summary="Update your profile")
def update_profile(body: FacultyProfileUpdate, faculty: dict = Depends(require_faculty)):
    updates = {}
    if body.full_name is not None:
        name = body.full_name.strip()
        if not name:
            raise HTTPException(400, "Full name cannot be empty")
        if "<" in name or ">" in name:
            raise HTTPException(400, "Full name cannot contain HTML characters (< or >)")
        updates["full_name"] = name
    if body.phone is not None:
        updates["phone"] = body.phone.strip() or None

    if not updates:
        raise HTTPException(400, "No fields to update")

    try:
        updated_res = sb.table("users").update(updates).eq("id", faculty["id"]).execute()
        if not updated_res.data:
            raise HTTPException(404, "Faculty user not found")
        updated_faculty = updated_res.data[0]
        updated_faculty.pop("password_hash", None)
        return {"message": "Profile updated successfully", "faculty": updated_faculty}
    except HTTPException:
        raise
    except Exception as e:
        print(f"FACULTY PROFILE UPDATE ERROR: faculty={faculty['id']} err={e!r}")
        raise HTTPException(500, "Could not update profile. Please try again.")


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
    slot = sb.table("timetable_slots").select("id, subject, faculty_id, day_of_week") \
             .eq("id", body.slot_id).limit(1).execute()
    if not slot.data:
        raise HTTPException(404, "Timetable slot not found")

    slot_data = slot.data[0]
    if faculty["role"] != "admin" and slot_data["faculty_id"] != faculty["id"]:
        raise HTTPException(403, "You can only open sessions for your own timetable slots")

    if body.date != DateType.today() and faculty["role"] != "admin":
        raise HTTPException(400, f"Attendance sessions can only be opened for today's date ({DateType.today()}).")

    # Verify session date weekday matches the scheduled slot weekday
    day_name = body.date.strftime("%A")
    scheduled_day = (slot_data.get("day_of_week") or "").strip()
    if scheduled_day and scheduled_day.lower() != day_name.lower():
        raise HTTPException(400, f"Session date {body.date} is a {day_name}, but this timetable slot is scheduled for {scheduled_day}")

    # Check if session already exists for this slot+date
    existing = sb.table("attendance_sessions") \
                 .select("id, is_open") \
                 .eq("slot_id", body.slot_id) \
                 .eq("date", str(body.date)).limit(1).execute()
    if existing.data:
        sess = existing.data[0]
        if sess["is_open"]:
            raise HTTPException(409, "An open session already exists for this slot and date")
        else:
            # Re-open a closed session: delete auto-absent rows so students can mark again
            try:
                sb.table("attendance_records") \
                  .delete() \
                  .eq("session_id", sess["id"]) \
                  .eq("status", "absent") \
                  .execute()
            except Exception as exc:
                print(f"REOPEN: could not remove absent records for session {sess['id']}: {exc}")
            res = sb.table("attendance_sessions").update({
                "is_open":    True,
                "opened_at":  datetime.now(timezone.utc).isoformat(),
                "closed_at":  None,
            }).eq("id", sess["id"]).execute()
            return {"message": "Session re-opened (absent records cleared)", "session": res.data[0]}

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
                .select("id, faculty_id, is_open, slot_id, timetable_slots(programme, batch, section)") \
                .eq("id", sid).limit(1).execute()
    if not session.data:
        raise HTTPException(404, "Session not found")

    s = session.data[0]
    if faculty["role"] != "admin" and s["faculty_id"] != faculty["id"]:
        raise HTTPException(403, "You can only close your own sessions")
    if not s["is_open"]:
        raise HTTPException(400, "Session is already closed")

    # Mark all students in the class who did not mark attendance as "absent"
    slot = s.get("timetable_slots") or {}
    prog = slot.get("programme")
    batch = slot.get("batch")
    section = slot.get("section")
    if prog and batch:
        q_stud = sb.table("users").select("id").eq("role", "student").eq("is_active", True).eq("programme", prog).eq("batch", batch)
        if section:
            q_stud = q_stud.eq("section", section)
        class_students = q_stud.execute().data or []
        class_student_ids = {u["id"] for u in class_students}

        # Check who already marked attendance
        marked_rows = sb.table("attendance_records").select("student_id").eq("session_id", sid).execute().data or []
        marked_ids = {r["student_id"] for r in marked_rows}

        missing_ids = class_student_ids - marked_ids
        if missing_ids:
            now_iso = datetime.now(timezone.utc).isoformat()
            absent_payload = [
                {"session_id": sid, "student_id": st_id, "status": "absent", "marked_at": now_iso}
                for st_id in missing_ids
            ]
            try:
                sb.table("attendance_records").insert(absent_payload).execute()
            except Exception as exc:
                print(f"FAILED TO INSERT ABSENT RECORDS FOR SESSION {sid}: {exc}")

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




class AttendanceRecordInput(BaseModel):
    student_id: int
    status: str


class BulkAttendanceBody(BaseModel):
    records: list[AttendanceRecordInput]


@router.post("/attendance/{sid}/records", status_code=201, summary="Save faculty-marked attendance records")
def save_session_records(sid: int, body: BulkAttendanceBody, faculty: dict = Depends(require_faculty)):
    """Persist attendance marked by the faculty member for their own open session."""
    session_res = sb.table("attendance_sessions").select(
        "id, faculty_id, is_open, slot_id, timetable_slots(programme, batch, subject)"
    ).eq("id", sid).limit(1).execute()
    if not session_res.data:
        raise HTTPException(404, "Attendance session not found")
    session = session_res.data[0]
    if faculty["role"] != "admin" and session["faculty_id"] != faculty["id"]:
        raise HTTPException(403, "You can only edit your own attendance sessions")
    if not session["is_open"]:
        raise HTTPException(400, "This attendance session is closed")
    slot = session.get("timetable_slots") or {}
    programme, batch = slot.get("programme"), slot.get("batch")
    if not body.records:
        raise HTTPException(400, "At least one attendance record is required")

    # Validate statuses
    for item in body.records:
        status = item.status.lower().strip()
        if status not in ("present", "absent"):
            raise HTTPException(422, f"Attendance status must be present or absent, got '{item.status}'")

    student_ids = [item.student_id for item in body.records]
    # Batch validate students in a single query
    users_res = sb.table("users").select("id, programme, batch").eq("role", "student").eq("is_active", True).in_("id", student_ids).execute()
    valid_users = {u["id"]: u for u in (users_res.data or [])}

    for item in body.records:
        u = valid_users.get(item.student_id)
        if not u:
            raise HTTPException(404, f"Active student {item.student_id} was not found")
        if programme and batch and (u.get("programme") != programme or u.get("batch") != batch):
            raise HTTPException(403, f"Student {item.student_id} does not belong to this class")

    # Batch check existing records
    existing_res = sb.table("attendance_records").select("id, student_id").eq("session_id", sid).in_("student_id", student_ids).execute()
    existing_by_student = {r["student_id"]: r["id"] for r in (existing_res.data or [])}

    saved = []
    now_iso = datetime.now(timezone.utc).isoformat()
    to_insert = []
    for item in body.records:
        st_status = item.status.lower().strip()
        if item.student_id in existing_by_student:
            rec_id = existing_by_student[item.student_id]
            upd = sb.table("attendance_records").update({
                "status": st_status,
                "marked_at": now_iso
            }).eq("id", rec_id).execute().data
            if upd:
                saved.append(upd[0])
        else:
            to_insert.append({
                "session_id": sid,
                "student_id": item.student_id,
                "status": st_status,
                "marked_at": now_iso
            })

    if to_insert:
        ins = sb.table("attendance_records").insert(to_insert).execute().data
        if ins:
            saved.extend(ins)

    try:
        sb.table("audit_log").insert({
            "user_id": faculty["id"],
            "action": "SAVE_ATTENDANCE_RECORDS",
            "detail": f"Faculty '{faculty['username']}' saved {len(saved)} records for session id={sid}"
        }).execute()
    except Exception as exc:
        print(f"ATTENDANCE AUDIT WARNING: {exc!r}")
    return {"message": "Attendance records saved", "session_id": sid, "saved": len(saved), "records": saved}


@router.get("/attendance/{sid}/records", summary="View attendance records for a session")
def session_records(sid: int, faculty: dict = Depends(require_faculty)):
    """View who marked attendance for a session."""
    session = sb.table("attendance_sessions") \
                .select("id, faculty_id, date, timetable_slots(subject, programme, batch)") \
                .eq("id", sid).limit(1).execute()
    if not session.data:
        raise HTTPException(404, "Session not found")

    if faculty["role"] != "admin" and session.data[0]["faculty_id"] != faculty["id"]:
        raise HTTPException(403, "You can only view records for your own sessions")

    records = sb.table("attendance_records") \
                .select("*, users!attendance_records_student_id_fkey(full_name, enrollment_no, programme, batch)") \
                .eq("session_id", sid) \
                .order("marked_at").execute()

    return {
        "session":  session.data[0],
        "records":  records.data or [],
        "total":    len(records.data or []),
        "present":  sum(1 for r in (records.data or []) if r["status"] == "present"),
        "absent":   sum(1 for r in (records.data or []) if r["status"] == "absent"),
    }


# ═══════════════════════════════════════════════════════════════
#  SHARED CLASS CONTENT
# ═══════════════════════════════════════════════════════════════

@router.get("/dashboard", summary="Live faculty dashboard data")
def faculty_dashboard(faculty: dict = Depends(require_faculty)):
    students = sb.table("users").select(
        "id,username,full_name,email,enrollment_no,programme,batch,is_active,department"
    ).eq("role","student").eq("is_active",True)
    if faculty.get("department"):
        students = students.eq("department", faculty["department"])
    student_rows = students.order("full_name").execute().data or []

    sessions = sb.table("attendance_sessions").select(
        "id,slot_id,date,opened_at,closed_at,is_open,timetable_slots(subject,programme,batch,start_time,end_time,day_of_week,room)"
    ).eq("faculty_id", faculty["id"]).order("date", desc=True).limit(100).execute().data or []
    session_ids = [s["id"] for s in sessions]
    records = []
    if session_ids:
        records = sb.table("attendance_records").select("session_id,student_id,status").in_("session_id", session_ids).execute().data or []

    by_student = {}
    for rec in records:
        st = by_student.setdefault(rec["student_id"], {"present":0,"total":0})
        st["total"] += 1
        if rec["status"] == "present":
            st["present"] += 1
    for st in student_rows:
        x = by_student.get(st["id"], {"present":0,"total":0})
        st["attendance_percentage"] = round((x["present"] / x["total"]) * 100, 1) if x["total"] else None

    for sess in sessions:
        recs = [r for r in records if r["session_id"] == sess["id"]]
        sess["present"] = sum(1 for r in recs if r["status"] == "present")
        sess["total"] = len(recs)

    timetable = sb.table("timetable_slots").select("*").eq("faculty_id", faculty["id"]).order("day_of_week").order("start_time").execute().data or []
    return {
        "students": student_rows,
        "sessions": sessions,
        "timetable": timetable,
        "announcements": sb.table("announcements").select("*").order("ts", desc=True).limit(10).execute().data or []
    }


class AnnouncementBody(BaseModel):
    title: str
    body: Optional[str] = None
    target: Optional[str] = None
    priority: Optional[str] = None

@router.post("/announcements", status_code=201, summary="Create a persistent announcement")
def create_announcement(body: AnnouncementBody, faculty: dict = Depends(require_faculty)):
    row = {
        "title": body.title.strip(),
        "body": (body.body or "").strip(),
        "target": body.target or "All",
        "priority": body.priority or "normal",
        "created_by": faculty["id"],
        "ts": datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
    }
    res = sb.table("announcements").insert(row).execute()
    return res.data[0]

@router.delete("/announcements/{aid}", summary="Delete an announcement")
def delete_announcement(aid: int, faculty: dict = Depends(require_faculty)):
    existing = sb.table("announcements").select("id, created_by").eq("id", aid).limit(1).execute()
    if not existing.data:
        raise HTTPException(404, "Announcement not found")
    ann = existing.data[0]
    if faculty["role"] != "admin" and ann.get("created_by") and ann["created_by"] != faculty["id"]:
        raise HTTPException(403, "You can only delete your own announcements")
    sb.table("announcements").delete().eq("id", aid).execute()
    return {"message": "Announcement deleted"}

@router.get("/announcements", summary="View announcements")
def get_announcements(faculty: dict = Depends(require_faculty)):
    return sb.table("announcements").select("*").order("ts", desc=True).execute().data or []

@router.get("/assignments", summary="View assignments")
def get_assignments(faculty: dict = Depends(require_faculty)):
    return sb.table("assignments").select("*").eq("is_active", True).order("due_date").execute().data or []

@router.get("/materials", summary="View study materials")
def get_materials(faculty: dict = Depends(require_faculty)):
    return sb.table("study_materials").select("*").eq("is_active", True).order("uploaded_at", desc=True).execute().data or []

@router.get("/attendance/sessions", summary="View your attendance sessions")
def attendance_sessions(faculty: dict = Depends(require_faculty)):
    q = sb.table("attendance_sessions").select(
        "id, slot_id, faculty_id, date, opened_at, closed_at, is_open, timetable_slots(subject, programme, batch)"
    ).eq("faculty_id", faculty["id"]).order("date", desc=True)
    return q.execute().data or []
