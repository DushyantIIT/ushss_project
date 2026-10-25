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

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from typing import Optional
from datetime import date as DateType, datetime, timezone, timedelta
import math
import os
import re

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
    """Students may edit contact details only; identity and enrolment are admin-controlled."""
    if body.email is not None or body.programme is not None or body.batch is not None:
        raise HTTPException(
            403,
            "Email, programme, and batch changes must be handled by the department administrator."
        )

    updates = {}
    if body.full_name is not None:
        full_name = body.full_name.strip()
        if not full_name or "<" in full_name or ">" in full_name:
            raise HTTPException(400, "Enter a valid full name without HTML characters.")
        updates["full_name"] = full_name
    if body.phone is not None:
        phone = body.phone.strip()
        if len(phone) > 32 or any(ch in phone for ch in "<>"):
            raise HTTPException(400, "Enter a valid phone number.")
        updates["phone"] = phone or None

    if not updates:
        safe_student = dict(student)
        safe_student.pop("password_hash", None)
        return {"message": "No changes provided", "student": safe_student}

    uid = student["id"]
    try:
        result = sb.table("users").update(updates).eq("id", uid).execute()
        # Some PostgREST configurations do not return updated rows unless selected;
        # verify that the account still exists before reporting success.
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
    # Never expose a whole programme/batch when a student's grouping is
    # incomplete. Timetable visibility requires an exact programme, batch,
    # course, semester, and section match.
    if not all(str(student.get(k) or "").strip() for k in ("programme", "batch", "semester", "section")):
        return []

    def normalise(value):
        return " ".join(str(value or "").strip().lower().split())

    def semester_key(value):
        raw = normalise(value).replace("semester", "").replace("sem", "").strip().upper()
        roman = {"I":"1", "II":"2", "III":"3", "IV":"4", "V":"5", "VI":"6", "VII":"7", "VIII":"8"}
        return roman.get(raw, raw)

    def programme_key(value):
        raw = normalise(value)
        if "b.a" in raw or raw.startswith("ba"):
            return "ba"
        if "m.a" in raw or raw.startswith("ma"):
            return "ma"
        return raw

    def course_key(*values):
        raw = " ".join(normalise(v) for v in values if v)
        if "economics" in raw:
            return "economics"
        if "english" in raw:
            return "english"
        return ""

    def legacy_group_part(row, pattern):
        # Older records stored course/batch/semester/section in one label.
        label = str(row.get("section") or "")
        match = re.search(pattern, label, flags=re.IGNORECASE)
        return match.group(1).strip() if match else ""

    def row_semester(row):
        return row.get("semester") or legacy_group_part(row, r"\\bSem(?:ester)?\\s+([^·]+)")

    def row_section(row):
        return legacy_group_part(row, r"\\bSection\\s+([^·]+)") or row.get("section")

    def row_course(row):
        return course_key(row.get("course"), row.get("department"), row.get("section"))

    student_programme = programme_key(student.get("programme"))
    student_batch = normalise(student.get("batch"))
    student_semester = semester_key(student.get("semester"))
    student_section = normalise(student.get("section")) or "none"
    student_course = course_key(student.get("department"), student.get("programme"), student.get("course"))
    if not student_programme or not student_batch or not student_semester or not student_course:
        return []

    q = sb.table("timetable_slots").select(
        "id, subject, day_of_week, start_time, end_time, room, programme, batch, "
        "department, course, semester, section, published, users(full_name, email)"
    ).eq("published", True).eq("is_active", True)

    if day:
        q = q.eq("day_of_week", day)

    rows = q.order("day_of_week").order("start_time").execute().data or []

    # Enforce every group dimension together. A Section A student in semester V
    # must not see semester IV, Section B, another course, or another batch.
    matched = []
    for row in rows:
        slot_programme = programme_key(row.get("programme"))
        slot_batch = normalise(row.get("batch"))
        slot_semester = semester_key(row_semester(row))
        slot_section = normalise(row_section(row)) or "none"
        slot_course = row_course(row)
        if (
            slot_programme == student_programme
            and slot_batch == student_batch
            and slot_semester == student_semester
            and slot_section == student_section
            and slot_course == student_course
        ):
            matched.append(row)

    return matched


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
        "id, slot_id, faculty_id, date, opened_at, anchor_lat, anchor_lon, "
        "timetable_slots(subject, day_of_week, start_time, end_time, room, section, programme, batch), "
        "users(full_name)"  # faculty who opened it
    ).eq("is_open", True).in_("slot_id", slot_ids).execute()

    today_str = str(DateType.today())
    raw_sessions = sessions.data or []
    result = []
    slot_map = {s["id"]: s for s in slot_rows}

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

        # Fallback slot enrichment for SQLite / missing nested joins
        if not s.get("timetable_slots") and s.get("slot_id") in slot_map:
            s["timetable_slots"] = slot_map[s["slot_id"]]
        elif not s.get("timetable_slots") and s.get("slot_id"):
            s_res = sb.table("timetable_slots").select("subject, day_of_week, start_time, end_time, room, section, programme, batch").eq("id", s["slot_id"]).limit(1).execute()
            if s_res.data:
                s["timetable_slots"] = s_res.data[0]

        # Fallback faculty enrichment
        if not s.get("users") and s.get("faculty_id"):
            u_res = sb.table("users").select("full_name").eq("id", s["faculty_id"]).limit(1).execute()
            if u_res.data:
                s["users"] = u_res.data[0]

        result.append(s)

    # Batch query whether student marked attendance (avoid N+1)
    if result:
        sess_ids = [s["id"] for s in result]
        recs = sb.table("attendance_records").select("session_id, status, review_status, marked_at, flags") \
                 .in_("session_id", sess_ids) \
                 .eq("student_id", student["id"]).execute().data or []
        marked_map = {r["session_id"]: r for r in recs}
        for sess in result:
            rec = marked_map.get(sess["id"])
            sess["already_marked"] = bool(rec)
            sess["my_status"] = rec.get("status") if rec else None
            sess["review_status"] = rec.get("review_status") if rec else None
            sess["marked_at"] = rec.get("marked_at") if rec else None

    return result


# ═══════════════════════════════════════════════════════════════
#  ATTENDANCE — MARK ATTENDANCE
# ═══════════════════════════════════════════════════════════════

class MarkAttendanceBody(BaseModel):
    session_id:   int
    latitude:     Optional[float] = None
    longitude:    Optional[float] = None
    gps_accuracy: Optional[float] = None   # browser-reported accuracy in metres
    device_uuid:  Optional[str]  = None    # browser fingerprint UUID


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Return great-circle distance in metres between two lat/lon points."""
    R = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a  = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(min(1.0, math.sqrt(a)))


def _extract_subnet(ip: str) -> Optional[str]:
    try:
        parts = ip.split(".")
        if len(parts) == 4:
            return f"{parts[0]}.{parts[1]}.{parts[2]}.0/24"
    except Exception:
        pass
    return None


@router.post("/attendance/mark", status_code=201, summary="Mark yourself present in an open session")
def mark_attendance(body: MarkAttendanceBody, request: Request, student: dict = Depends(require_student)):
    """
    Multi-layer anti-cheating attendance mark:
    1. Session must be open and today
    2. 5-minute time window (unless teacher disabled it)
    3. GPS 3-tier zone check against dynamic classroom anchor
    4. Device UUID conflict check (one device per session)
    5. Subnet match (soft signal)
    6. Weighted suspicion score → instant present OR review queue
    """
    # ── Fetch session with all anti-cheating fields ──────────────────────────
    session = sb.table("attendance_sessions").select(
        "id, is_open, date, opened_at, slot_id, faculty_id, "
        "anchor_lat, anchor_lon, anchor_accuracy, "
        "time_limit_disabled, teacher_subnet, session_cap, "
        "timetable_slots(programme, batch, section, subject, room)"
    ).eq("id", body.session_id).limit(1).execute()

    if not session.data:
        raise HTTPException(404, f"Attendance session #{body.session_id} not found. Please confirm if your instructor has opened the session.")

    sess = session.data[0]
    if not sess["is_open"]:
        raise HTTPException(400, "This attendance session has already been closed by the faculty. Attendance could not be recorded.")

    # Verify today's date
    sess_date  = str(sess.get("date") or "")[:10]
    today_str  = str(DateType.today())
    if sess_date != today_str:
        raise HTTPException(400, f"This session is for {sess_date}. Attendance can only be recorded on the day of the class.")

    # Verify student belongs to this class
    slot_info = sess.get("timetable_slots")
    if not slot_info and sess.get("slot_id"):
        slot_res = sb.table("timetable_slots").select("programme, batch, section, subject, room").eq("id", sess["slot_id"]).limit(1).execute()
        if slot_res.data:
            slot_info = slot_res.data[0]
            sess["timetable_slots"] = slot_info
    slot_info = slot_info or {}
    slot_prog  = slot_info.get("programme")
    slot_batch = slot_info.get("batch")
    slot_sec   = slot_info.get("section")
    if slot_prog != student.get("programme") or slot_batch != student.get("batch"):
        raise HTTPException(403, f"Attendance not recorded: This session is for '{slot_prog} ({slot_batch})', but your enrolled programme is '{student.get('programme')} ({student.get('batch')})'.")
    if slot_sec and student.get("section") and slot_sec != student.get("section"):
        raise HTTPException(403, f"Attendance not recorded: This session is for Section {slot_sec}, but you are registered in Section {student.get('section')}.")

    # Check if already marked
    existing = sb.table("attendance_records") \
                 .select("id, status, review_status, marked_at") \
                 .eq("session_id", body.session_id) \
                 .eq("student_id", student["id"]).limit(1).execute()
    if existing.data:
        rec = existing.data[0]
        status_disp = rec.get("status", "present").upper()
        if rec.get("review_status") == "pending":
            raise HTTPException(409, f"Attendance already submitted and is currently PENDING teacher approval.")
        raise HTTPException(409, f"Attendance has already been recorded as '{status_disp}' for this session.")

    # Check session cap
    if sess.get("session_cap"):
        present_count_res = sb.table("attendance_records") \
            .select("id", count="exact") \
            .eq("session_id", body.session_id) \
            .eq("status", "present").execute()
        present_count = present_count_res.count or len(present_count_res.data or [])
        if present_count >= sess["session_cap"]:
            raise HTTPException(403, f"Classroom headcount capacity ({sess['session_cap']} students) has been reached. Please ask your instructor for manual check-in.")

    flags:    list[str] = []
    score:    int       = 0
    gps_zone: int       = 0
    gps_distance: Optional[float] = None
    subnet_match: Optional[bool]  = None
    time_delta_seconds: int       = 0
    review_status: str            = "approved"

    # ── GATE 1: 5-minute time window ────────────────────────────────────────
    opened_at_str = sess.get("opened_at") or ""
    if opened_at_str and not sess.get("time_limit_disabled", False):
        try:
            opened_dt = datetime.fromisoformat(opened_at_str.replace("Z", "+00:00"))
            now_utc   = datetime.now(timezone.utc)
            elapsed   = (now_utc - opened_dt).total_seconds()
            time_delta_seconds = int(max(0, elapsed))
            if elapsed > 300:  # 5 minutes
                flags.append("LATE")
                score += 30
                review_status = "pending"
        except Exception:
            pass

    # ── GATE 2: GPS 3-tier zone (server-side) ───────────────────────────────
    anchor_lat = sess.get("anchor_lat")
    anchor_lon = sess.get("anchor_lon")
    # Fall back to env-var campus centre if no classroom anchor set yet
    if anchor_lat is None:
        try:
            anchor_lat = float(os.getenv("CAMPUS_LATITUDE", ""))
            anchor_lon = float(os.getenv("CAMPUS_LONGITUDE", ""))
        except (ValueError, TypeError):
            anchor_lat = anchor_lon = None

    if anchor_lat is not None and anchor_lon is not None:
        if body.latitude is None or body.longitude is None:
            raise HTTPException(400, "Location permission is required to mark attendance.")
        if not (-90 <= body.latitude <= 90 and -180 <= body.longitude <= 180):
            raise HTTPException(400, "Invalid location coordinates.")
        gps_distance = _haversine_m(anchor_lat, anchor_lon, body.latitude, body.longitude)
        if gps_distance > 150:
            raise HTTPException(
                403,
                f"Attendance not recorded: You are outside the classroom boundary ({int(gps_distance)} m away; limit is 150 m). "
                "Classroom presence is required to record attendance. Please move closer and try again."
            )
        elif gps_distance > 50:
            gps_zone = 1
            flags.append("GPS_DRIFT")
            score += 20

    # ── GATE 3: Device UUID conflict (one device per session) ────────────────
    device_uuid = (body.device_uuid or "").strip() or None
    if device_uuid:
        conflict = sb.table("attendance_records") \
                     .select("id, student_id") \
                     .eq("session_id", body.session_id) \
                     .eq("device_uuid", device_uuid) \
                     .neq("student_id", student["id"]) \
                     .limit(1).execute()
        if conflict.data:
            raise HTTPException(
                403,
                "Another student has already used this device for today's session. "
                "Each student must use their own device."
            )
        # FRESH_PROFILE check: student has history but all 3 stores were blank
        history = sb.table("attendance_records") \
                    .select("device_uuid") \
                    .eq("student_id", student["id"]) \
                    .not_.is_("device_uuid", "null") \
                    .limit(5).execute().data or []
        # Only flag if student has prior records with a UUID (not a brand-new user)
        if history and device_uuid not in [h["device_uuid"] for h in history]:
            flags.append("NEW_DEVICE")
            score += 10
    else:
        # UUID is blank — only flag if student has prior UUID history (not day-one)
        history = sb.table("attendance_records") \
                    .select("device_uuid") \
                    .eq("student_id", student["id"]) \
                    .not_.is_("device_uuid", "null") \
                    .limit(3).execute().data or []
        if history:
            flags.append("FRESH_PROFILE")
            score += 15

    # ── PASSIVE: Subnet match (soft bonus/signal only) ───────────────────────
    client_ip = (
        request.headers.get("X-Forwarded-For", "").split(",")[0].strip()
        or request.headers.get("X-Real-IP", "")
        or (request.client.host if request.client else "")
    )
    student_subnet  = _extract_subnet(client_ip) if client_ip else None
    teacher_subnet  = sess.get("teacher_subnet")
    if teacher_subnet and student_subnet:
        subnet_match = (student_subnet == teacher_subnet)
        if not subnet_match:
            score += 15
    # if teacher was on cellular, teacher_subnet is None → no penalty

    # ── Time delta score (late submission even within window) ─────────────────
    if time_delta_seconds > 240:   # > 4 min inside window
        score += 10

    score = min(100, score)

    # ── Route to approved or pending ─────────────────────────────────────────
    if score >= 50 and review_status != "pending":
        review_status = "pending"

    # ── Build and insert record ───────────────────────────────────────────────
    rec_payload: dict = {
        "session_id":         body.session_id,
        "student_id":         student["id"],
        "status":             "present",
        "device_uuid":        device_uuid,
        "gps_zone":           gps_zone,
        "gps_distance":       gps_distance,
        "subnet_match":       subnet_match,
        "suspicion_score":    float(score),
        "flags":              ",".join(flags) if flags else None,
        "review_status":      review_status,
        "time_delta_seconds": time_delta_seconds,
    }
    if body.latitude is not None:
        rec_payload["latitude"]  = float(body.latitude)
    if body.longitude is not None:
        rec_payload["longitude"] = float(body.longitude)

    try:
        res = sb.table("attendance_records").insert(rec_payload).execute()
    except Exception as exc:
        if "unique" in str(exc).lower() or "duplicate" in str(exc).lower():
            raise HTTPException(409, "You have already marked attendance for this session.")
        print(f"ATTENDANCE INSERT ERROR: session={body.session_id} student={student['id']} err={exc!r}")
        raise HTTPException(500, "Could not record attendance. Please try again.")

    sb.table("audit_log").insert({
        "user_id": student["id"],
        "action":  "MARK_ATTENDANCE",
        "detail":  (
            f"Student '{student['username']}' marked present in session id={body.session_id} "
            f"[score={score}, flags={','.join(flags) or 'none'}, review={review_status}]"
        ),
    }).execute()

    subject = (sess.get("timetable_slots") or {}).get("subject", "")
    if review_status == "pending":
        if "LATE" in flags:
            msg = "Attendance window has closed. Ask your teacher to accept your late check-in."
        else:
            msg = "Attendance submitted. Pending teacher approval."
        return {
            "message": msg,
            "pending": True,
            "record":  res.data[0],
        }

    return {
        "message":           f"Attendance marked as PRESENT for '{subject}'.",
        "pending":           False,
        "suspicion_score":   score,
        "gps_zone":          gps_zone,
        "record":            res.data[0],
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
        "id, session_id, status, review_status, marked_at, "
        "attendance_sessions(id, slot_id, faculty_id, date, timetable_slots(subject, start_time, end_time, room), users(full_name))"
    ).eq("student_id", student["id"]).order("marked_at", desc=True).execute()

    records = res.data or []

    # Fallback enrichment for nested joins (SQLite / missing foreign key resolution)
    for r in records:
        sess = r.get("attendance_sessions")
        if not sess and r.get("session_id"):
            s_res = sb.table("attendance_sessions").select("id, slot_id, faculty_id, date").eq("id", r["session_id"]).limit(1).execute()
            if s_res.data:
                sess = s_res.data[0]
                r["attendance_sessions"] = sess
        if sess:
            if not sess.get("timetable_slots") and sess.get("slot_id"):
                sl_res = sb.table("timetable_slots").select("subject, start_time, end_time, room").eq("id", sess["slot_id"]).limit(1).execute()
                if sl_res.data:
                    sess["timetable_slots"] = sl_res.data[0]
            if not sess.get("users") and sess.get("faculty_id"):
                u_res = sb.table("users").select("full_name").eq("id", sess["faculty_id"]).limit(1).execute()
                if u_res.data:
                    sess["users"] = u_res.data[0]

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

def _normalise_announcement_text(value: Optional[str]) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip()


def _announcement_visible_to_user(target: Optional[str], user: dict) -> bool:
    raw = str(target or "").strip()
    if not raw:
        return True
    programme = _normalise_announcement_text(user.get("programme"))
    batch = _normalise_announcement_text(user.get("batch"))
    semester = _normalise_announcement_text(user.get("semester"))
    universal = {"all", "all students", "all classmates", "all students and crs", "student", "students"}
    for item in [part.strip() for part in raw.split(",") if part.strip()]:
        item_norm = _normalise_announcement_text(item)
        if item_norm in universal:
            return True
        if item_norm in {"faculty", "cr", "specific students"}:
            continue
        sem_match = re.search(r"(?:semester|sem)\s*(i{1,3}|iv|v|vi|vii|viii|ix|x|\d+)\s*$", item.lower())
        target_base = item[:sem_match.start()].strip(" -–—|,") if sem_match else item
        target_norm = _normalise_announcement_text(target_base)
        if sem_match:
            roman_to_int = {"i": "1", "ii": "2", "iii": "3", "iv": "4", "v": "5", "vi": "6", "vii": "7", "viii": "8", "ix": "9", "x": "10"}
            wanted = roman_to_int.get(sem_match.group(1).lower(), sem_match.group(1).lower())
            actual_match = re.search(r"(?:semester|sem)?\s*(i{1,3}|iv|v|vi|vii|viii|ix|x|\d+)\s*$", str(user.get("semester") or "").lower())
            actual = actual_match.group(1).lower() if actual_match else semester
            actual = roman_to_int.get(actual, actual)
            if not actual or actual != wanted:
                continue
        if programme and target_norm == programme:
            return True
        if programme and batch and target_norm == f"{programme} {batch}":
            return True
    return False


@router.get("/announcements", summary="View announcements for your class")
def get_announcements(student: dict = Depends(require_student)):
    rows = sb.table("announcements").select("*").order("ts", desc=True).execute().data or []
    return [a for a in rows if _announcement_visible_to_user(a.get("target"), student)]

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
