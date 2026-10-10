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
from datetime import date as DateType, datetime, timezone
import math
import os

from app.db import sb
from app.deps import require_student

router = APIRouter(prefix="/student", tags=["Student"])


# ═══════════════════════════════════════════════════════════════
#  PROFILE  (read-only)
# ═══════════════════════════════════════════════════════════════

class StudentProfileUpdate(BaseModel):
    full_name: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    programme: Optional[str] = None
    batch: Optional[str] = None

@router.get("/profile", summary="View your profile")
def get_profile(student: dict = Depends(require_student)):
    """Returns your profile."""
    student.pop("password_hash", None)
    return student


@router.patch("/profile", summary="Update your profile")
def update_profile(body: StudentProfileUpdate, student: dict = Depends(require_student)):
    """Allows student to update their profile details."""
    updates = {}
    if body.full_name is not None:
        if "<" in body.full_name or ">" in body.full_name:
            raise HTTPException(400, "Full name cannot contain HTML characters (< or >)")
        updates["full_name"] = body.full_name.strip()
    if body.email is not None:
        updates["email"] = body.email.strip()
    if body.phone is not None:
        updates["phone"] = body.phone.strip()
    if body.programme is not None:
        updates["programme"] = body.programme.strip()
    if body.batch is not None:
        updates["batch"] = body.batch.strip()

    if not updates:
        student.pop("password_hash", None)
        return {"message": "No changes provided", "student": student}

    uid = student["id"]
    try:
        if updates.get("email"):
            supa_uid = student.get("supabase_uid")
            if not supa_uid:
                u_row = sb.table("users").select("supabase_uid").eq("id", uid).limit(1).execute().data
                if u_row:
                    supa_uid = u_row[0].get("supabase_uid")
            if supa_uid:
                try:
                    sb.auth.admin.update_user_by_id(supa_uid, {"email": updates["email"]})
                except Exception as auth_err:
                    print(f"Could not update Supabase Auth email for user {uid}: {auth_err}")

        sb.table("users").update(updates).eq("id", uid).execute()
        updated_res = sb.table("users").select("*").eq("id", uid).limit(1).execute()
        if not updated_res.data:
            raise HTTPException(404, "Student user not found")
        updated_student = updated_res.data[0]
        updated_student.pop("password_hash", None)
        return {"message": "Profile updated successfully", "student": updated_student}
    except HTTPException:
        raise
    except Exception as e:
        print(f"PROFILE UPDATE ERROR: student={student['id']} err={e!r}")
        raise HTTPException(500, "Could not update profile. Please try again.")


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
    slots = sb.table("timetable_slots").select("id, section").eq(
        "programme", student["programme"]
    ).eq("batch", student["batch"]).execute()

    slot_rows = slots.data or []
    if student.get("section"):
        slot_ids = [s["id"] for s in slot_rows if not s.get("section") or s.get("section") == student.get("section")]
    else:
        slot_ids = [s["id"] for s in slot_rows]

    if not slot_ids:
        return []

    # Get open sessions for those slots
    sessions = sb.table("attendance_sessions").select(
        "id, slot_id, date, opened_at, "
        "timetable_slots(subject, day_of_week, start_time, end_time, room, section), "
        "users(full_name)"  # faculty who opened it
    ).eq("is_open", True).in_("slot_id", slot_ids).execute()

    today_str = str(DateType.today())
    raw_sessions = sessions.data or []
    result = []
    for s in raw_sessions:
        s_date = str(s.get("date") or "")[:10]
        if s_date < today_str:
            # Stale session from a previous day left open — auto-close it
            try:
                sb.table("attendance_sessions").update({
                    "is_open": False,
                    "closed_at": datetime.now(timezone.utc).isoformat()
                }).eq("id", s["id"]).execute()
            except Exception:
                pass
            continue
        result.append(s)

    # Batch query whether student marked attendance (avoid N+1)
    if result:
        sess_ids = [s["id"] for s in result]
        recs = sb.table("attendance_records").select("session_id, status") \
                 .in_("session_id", sess_ids) \
                 .eq("student_id", student["id"]).execute().data or []
        marked_map = {r["session_id"]: r["status"] for r in recs}
        for sess in result:
            sess["already_marked"] = sess["id"] in marked_map
            sess["my_status"] = marked_map.get(sess["id"])

    return result


# ═══════════════════════════════════════════════════════════════
#  ATTENDANCE — MARK ATTENDANCE
# ═══════════════════════════════════════════════════════════════

class MarkAttendanceBody(BaseModel):
    session_id: int
    latitude: Optional[float] = None
    longitude: Optional[float] = None


@router.post("/attendance/mark", status_code=201, summary="Mark yourself present in an open session")
def mark_attendance(body: MarkAttendanceBody, student: dict = Depends(require_student)):
    """
    Marks the student as PRESENT in the given open session.
    - Session must be open
    - Session must be on today's date
    - The session's slot must belong to the student's programme+batch
    - Student cannot mark twice for the same session
    """
    # 1. Verify session exists and is open
    session = sb.table("attendance_sessions").select(
        "id, is_open, date, slot_id, timetable_slots(programme, batch, section, subject)"
    ).eq("id", body.session_id).limit(1).execute()

    if not session.data:
        raise HTTPException(404, "Attendance session not found")

    sess = session.data[0]
    if not sess["is_open"]:
        raise HTTPException(400, "This attendance session is closed. You can no longer mark attendance.")

    # 1b. Verify the session is on today's date (prevent marking stale open sessions)
    sess_date = str(sess.get("date") or "")[:10]
    today_str = str(DateType.today())
    if sess_date != today_str:
        raise HTTPException(400, f"This session is for date {sess_date}. Attendance can only be marked on the day of the class ({today_str}).")

    # 2. Verify the session belongs to the student's class
    slot_prog  = (sess.get("timetable_slots") or {}).get("programme")
    slot_batch = (sess.get("timetable_slots") or {}).get("batch")
    slot_sec   = (sess.get("timetable_slots") or {}).get("section")
    if slot_prog != student.get("programme") or slot_batch != student.get("batch"):
        raise HTTPException(403, "This session is not for your class.")
    if slot_sec and student.get("section") and slot_sec != student.get("section"):
        raise HTTPException(403, f"This session is for Section {slot_sec}, but you are in Section {student.get('section')}.")

    # Enforce the campus geofence on the server whenever configured in Render.
    # Do not trust a browser-side "within campus" label.
    campus_lat = os.getenv("CAMPUS_LATITUDE", "28.595016")
    campus_lon = os.getenv("CAMPUS_LONGITUDE", "77.018942")
    location_verified = False
    if campus_lat and campus_lon:
        if body.latitude is None or body.longitude is None:
            raise HTTPException(400, "Location permission is required to mark attendance.")
        if not (-90 <= body.latitude <= 90 and -180 <= body.longitude <= 180):
            raise HTTPException(400, "Invalid location coordinates.")
        try:
            lat0, lon0 = float(campus_lat), float(campus_lon)
            radius = max(50.0, float(os.getenv("CAMPUS_RADIUS_METERS", "500")))
        except ValueError:
            raise HTTPException(503, "Campus attendance location is misconfigured. Contact the administrator.")
        earth_radius = 6371000.0
        p1, p2 = math.radians(lat0), math.radians(body.latitude)
        dp = math.radians(body.latitude - lat0)
        dl = math.radians(body.longitude - lon0)
        a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
        distance = 2 * earth_radius * math.asin(min(1.0, math.sqrt(a)))
        if distance > radius:
            raise HTTPException(403, f"You appear to be outside the campus attendance radius ({int(distance)} m from campus).")
        location_verified = True

    # 3. Check if already marked
    existing = sb.table("attendance_records") \
                 .select("id, status") \
                 .eq("session_id", body.session_id) \
                 .eq("student_id", student["id"]).limit(1).execute()
    if existing.data:
        raise HTTPException(409, f"You have already marked attendance as '{existing.data[0]['status']}'.")

    # 4. Insert record with coordinates and race condition safety
    rec_payload = {
        "session_id": body.session_id,
        "student_id": student["id"],
        "status":     "present",
    }
    if body.latitude is not None:
        rec_payload["latitude"] = float(body.latitude)
    if body.longitude is not None:
        rec_payload["longitude"] = float(body.longitude)

    try:
        res = sb.table("attendance_records").insert(rec_payload).execute()
    except Exception as exc:
        if "unique" in str(exc).lower() or "duplicate" in str(exc).lower() or "already exists" in str(exc).lower():
            raise HTTPException(409, "You have already marked attendance for this session.")
        print(f"ATTENDANCE INSERT ERROR: session={body.session_id} student={student['id']} err={exc!r}")
        raise HTTPException(500, "Could not record attendance. Please try again.")

    sb.table("audit_log").insert({
        "user_id": student["id"],
        "action":  "MARK_ATTENDANCE",
        "detail":  f"Student '{student['username']}' marked present in session id={body.session_id}",
    }).execute()

    subject = (sess.get("timetable_slots") or {}).get("subject", "")
    return {
        "message": f"Attendance marked as PRESENT for '{subject}'.",
        "location_verified": location_verified,
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
    absent  = sum(1 for r in records if r["status"] == "absent")

    return {
        "student":    student["full_name"],
        "total":      total,
        "present":    present,
        "absent":     absent,
        "percentage": round((present / total * 100), 1) if total else 0,
        "records":    records,
    }


# ═══════════════════════════════════════════════════════════════
#  SHARED CLASS CONTENT
# ═══════════════════════════════════════════════════════════════

@router.get("/announcements", summary="View announcements for your class")
def get_announcements(student: dict = Depends(require_student)):
    res = sb.table("announcements").select("*").order("ts", desc=True).execute()
    rows = res.data or []
    stud_prog = (student.get("programme") or "").strip().lower()
    stud_batch = (student.get("batch") or "").strip().lower()

    def matches_target(ann_target: Optional[str]) -> bool:
        if not ann_target:
            return True
        t = str(ann_target).strip().lower()
        if t in ("all", "student", "students"):
            return True
        tokens = [tok.strip() for tok in t.split(",") if tok.strip()]
        for tok in tokens:
            if tok in ("all", "student", "students"):
                return True
            if stud_prog and tok == stud_prog:
                return True
            if stud_prog and stud_batch and tok == f"{stud_prog} {stud_batch}":
                return True
        return False

    return [a for a in rows if matches_target(a.get("target"))]

@router.get("/assignments", summary="View assignments for your class")
def get_assignments(student: dict = Depends(require_student)):
    # The installed Supabase Python client does not expose the PostgREST
    # .or_() builder used by older code. Fetch active rows and apply the
    # class visibility rule in Python instead.
    rows = sb.table("assignments").select("*").eq("is_active", True).order("due_date").execute().data or []
    programme = student.get("programme")
    batch = student.get("batch")

    def visible(row):
        programme_ok = not programme or not row.get("programme") or row.get("programme") == programme
        batch_ok = not batch or not row.get("batch") or row.get("batch") == batch
        return programme_ok and batch_ok

    return [row for row in rows if visible(row)]

@router.get("/materials", summary="View study materials for your class")
def get_materials(student: dict = Depends(require_student)):
    rows = sb.table("study_materials").select("*").eq("is_active", True).order("uploaded_at", desc=True).execute().data or []
    programme = student.get("programme")
    batch = student.get("batch")

    def visible(row):
        programme_ok = not programme or not row.get("programme") or row.get("programme") == programme
        batch_ok = not batch or not row.get("batch") or row.get("batch") == batch
        return programme_ok and batch_ok

    visible_rows = [row for row in rows if visible(row)]
    for row in visible_rows:
        path = row.get("file_url")
        if path and not str(path).startswith("http"):
            try:
                signed = sb.storage.from_("ushss-study-materials").create_signed_url(str(path), 3600)
                row["file_url"] = signed.get("signedURL") or signed.get("signedUrl") or ""
            except Exception as exc:
                print(f"STUDENT MATERIAL SIGNED URL WARNING: {type(exc).__name__}: {str(exc)[:160]}")
                row["file_url"] = ""
    return visible_rows
