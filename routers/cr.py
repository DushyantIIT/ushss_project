"""
routers/cr.py
─────────────
CLASS REPRESENTATIVE rights (extends student rights):
  ✅ Everything a student can do
  ✅ View classmates list                    GET /api/cr/classmates
  ⛔ Any data changes — admin only
  ⛔ Open/close sessions — faculty only
"""

from datetime import datetime, timezone
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from app.db import sb
from app.deps import require_student   # CR role is included in require_student

router = APIRouter(prefix="/cr", tags=["Class Representative"])


@router.get("/profile", summary="View CR profile")
def get_profile(cr: dict = Depends(require_student)):
    if cr["role"] not in ("cr", "admin"):
        raise HTTPException(403, "Class Representatives only")
    cr.pop("password_hash", None)
    return cr

@router.get("/classmates", summary="View all students in your programme and batch")
def view_classmates(cr: dict = Depends(require_student)):
    """
    Returns all active students in the CR's own programme and batch.
    CR cannot edit any records — read-only.
    """
    # Only CRs (and admins) should access this; regular students are blocked
    if cr["role"] not in ("cr", "admin"):
        raise HTTPException(403, "Class Representatives only")

    if not cr.get("programme") or not cr.get("batch"):
        raise HTTPException(400, "Your account has no programme/batch assigned. Contact admin.")

    res = sb.table("users").select(
        "id, full_name, username, email, phone, enrollment_no, programme, batch, is_active"
    ).eq("role", "student") \
     .eq("programme", cr["programme"]) \
     .eq("batch", cr["batch"]) \
     .eq("is_active", True) \
     .order("full_name").execute()

    return {
        "programme": cr["programme"],
        "batch":     cr["batch"],
        "count":     len(res.data or []),
        "students":  res.data or [],
    }


class AnnouncementBody(BaseModel):
    title: str
    body: Optional[str] = None
    target: Optional[str] = None
    priority: Optional[str] = None

class AssignmentBody(BaseModel):
    title: str
    description: Optional[str] = None
    subject: Optional[str] = None
    due_date: Optional[str] = None
    file_name: Optional[str] = None

class MaterialBody(BaseModel):
    title: str
    description: Optional[str] = None
    subject: Optional[str] = None
    file_name: Optional[str] = None
    file_url: Optional[str] = None
    size: Optional[str] = None

def _require_cr(cr: dict):
    if cr["role"] not in ("cr", "admin"):
        raise HTTPException(403, "Class Representatives only")

@router.get("/announcements", summary="View class announcements")
def get_announcements(cr: dict = Depends(require_student)):
    _require_cr(cr)
    return sb.table("announcements").select("*").order("ts", desc=True).execute().data or []

@router.post("/announcements", status_code=201, summary="Create a persistent class announcement")
def create_announcement(body: AnnouncementBody, cr: dict = Depends(require_student)):
    _require_cr(cr)
    row = {
        "title": body.title.strip(),
        "body": (body.body or "").strip(),
        "target": body.target or cr.get("programme") or "all",
        "priority": body.priority or "normal",
        "ts": datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
    }
    res = sb.table("announcements").insert(row).execute()
    return res.data[0]

@router.delete("/announcements/{aid}", summary="Delete a class announcement")
def delete_announcement(aid: int, cr: dict = Depends(require_student)):
    _require_cr(cr)
    existing = sb.table("announcements").select("id,target").eq("id", aid).single().execute()
    if not existing.data:
        raise HTTPException(404, "Announcement not found")
    if cr["role"] != "admin":
        target = str(existing.data.get("target") or "").lower()
        prog = str(cr.get("programme") or "").lower()
        if target not in ("all", "student", "students", "", prog):
            raise HTTPException(403, "You can only delete announcements for your class")
    sb.table("announcements").delete().eq("id", aid).execute()
    return {"message": "Announcement deleted"}

@router.get("/assignments", summary="View class assignments")
def get_assignments(cr: dict = Depends(require_student)):
    _require_cr(cr)
    return sb.table("assignments").select("*").eq("is_active", True).order("due_date").execute().data or []

@router.post("/assignments", status_code=201, summary="Create a persistent class assignment")
def create_assignment(body: AssignmentBody, cr: dict = Depends(require_student)):
    _require_cr(cr)
    row = {
        "title": body.title.strip(),
        "description": (body.description or "").strip(),
        "subject": body.subject,
        "due_date": body.due_date,
        "file_name": body.file_name,
        "programme": cr.get("programme"),
        "batch": cr.get("batch"),
        "created_by": cr["id"],
        "is_active": True,
    }
    res = sb.table("assignments").insert(row).execute()
    return res.data[0]

@router.delete("/assignments/{aid}", summary="Delete a class assignment")
def delete_assignment(aid: int, cr: dict = Depends(require_student)):
    _require_cr(cr)
    existing = sb.table("assignments").select("id,created_by").eq("id", aid).single().execute()
    if not existing.data:
        raise HTTPException(404, "Assignment not found")
    if cr["role"] != "admin" and existing.data.get("created_by") != cr["id"]:
        raise HTTPException(403, "You can only delete assignments you uploaded")
    sb.table("assignments").delete().eq("id", aid).execute()
    return {"message": "Assignment deleted"}

@router.get("/materials", summary="View class study materials")
def get_materials(cr: dict = Depends(require_student)):
    _require_cr(cr)
    return sb.table("study_materials").select("*").eq("is_active", True).order("uploaded_at", desc=True).execute().data or []

@router.post("/materials", status_code=201, summary="Create persistent class study material")
def create_material(body: MaterialBody, cr: dict = Depends(require_student)):
    _require_cr(cr)
    row = {
        "title": body.title.strip(),
        "description": (body.description or "").strip(),
        "subject": body.subject,
        "file_name": body.file_name,
        "file_url": body.file_url,
        "size": body.size,
        "programme": cr.get("programme"),
        "batch": cr.get("batch"),
        "uploaded_by": cr["id"],
        "is_active": True,
    }
    res = sb.table("study_materials").insert(row).execute()
    return res.data[0]

@router.delete("/materials/{mid}", summary="Delete a class study material")
def delete_material(mid: int, cr: dict = Depends(require_student)):
    _require_cr(cr)
    existing = sb.table("study_materials").select("id,uploaded_by").eq("id", mid).single().execute()
    if not existing.data:
        raise HTTPException(404, "Study material not found")
    if cr["role"] != "admin" and existing.data.get("uploaded_by") != cr["id"]:
        raise HTTPException(403, "You can only delete materials you uploaded")
    sb.table("study_materials").delete().eq("id", mid).execute()
    return {"message": "Study material deleted"}
