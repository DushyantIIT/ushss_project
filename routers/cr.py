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
import base64
import os
import re
from uuid import uuid4
import mimetypes
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
    rows = sb.table("announcements").select("*").order("ts", desc=True).execute().data or []
    if cr["role"] == "admin":
        return rows
    prog = (cr.get("programme") or "").strip().lower()
    return [a for a in rows if str(a.get("target") or "").lower() in ("all", "student", "students", "", prog)]

@router.post("/announcements", status_code=201, summary="Create a persistent class announcement")
def create_announcement(body: AnnouncementBody, cr: dict = Depends(require_student)):
    _require_cr(cr)
    row = {
        "title": body.title.strip(),
        "body": (body.body or "").strip(),
        "target": body.target or cr.get("programme") or "all",
        "priority": body.priority or "normal",
        "created_by": cr["id"],
        "ts": datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
    }
    res = sb.table("announcements").insert(row).execute()
    return res.data[0]

@router.delete("/announcements/{aid}", summary="Delete a class announcement")
def delete_announcement(aid: int, cr: dict = Depends(require_student)):
    _require_cr(cr)
    res = sb.table("announcements").select("id,target,created_by").eq("id", aid).limit(1).execute()
    if not res.data:
        raise HTTPException(404, "Announcement not found")
    existing = res.data[0]
    if cr["role"] != "admin":
        if existing.get("created_by") is not None and existing.get("created_by") != cr["id"]:
            raise HTTPException(403, "You can only delete announcements that you created")
        if existing.get("created_by") is None:
            raise HTTPException(403, "Only administrators can delete general system announcements")
    sb.table("announcements").delete().eq("id", aid).execute()
    return {"message": "Announcement deleted"}

@router.get("/assignments", summary="View class assignments")
def get_assignments(cr: dict = Depends(require_student)):
    _require_cr(cr)
    q = sb.table("assignments").select("*").eq("is_active", True)
    if cr["role"] != "admin":
        if cr.get("programme"):
            q = q.eq("programme", cr["programme"])
        if cr.get("batch"):
            q = q.eq("batch", cr["batch"])
    return q.order("due_date").execute().data or []

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
    res = sb.table("assignments").select("id,created_by").eq("id", aid).limit(1).execute()
    if not res.data:
        raise HTTPException(404, "Assignment not found")
    existing = res.data[0]
    if cr["role"] != "admin" and existing.get("created_by") != cr["id"]:
        raise HTTPException(403, "You can only delete assignments you uploaded")
    sb.table("assignments").delete().eq("id", aid).execute()
    return {"message": "Assignment deleted"}

@router.get("/materials", summary="View class study materials")
def get_materials(cr: dict = Depends(require_student)):
    _require_cr(cr)
    q = sb.table("study_materials").select("*").eq("is_active", True)
    if cr["role"] != "admin":
        if cr.get("programme"):
            q = q.eq("programme", cr["programme"])
        if cr.get("batch"):
            q = q.eq("batch", cr["batch"])
    rows = q.order("uploaded_at", desc=True).execute().data or []
    for row in rows:
        path = row.get("file_url")
        if path and not str(path).startswith("http"):
            try:
                signed = sb.storage.from_("ushss-study-materials").create_signed_url(str(path), 3600)
                row["file_url"] = signed.get("signedURL") or signed.get("signedUrl") or ""
            except Exception as exc:
                print(f"CR MATERIAL SIGNED URL WARNING: {type(exc).__name__}: {str(exc)[:160]}")
                row["file_url"] = ""
    return rows

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



class UploadMaterialBody(BaseModel):
    title: str
    description: Optional[str] = None
    subject: Optional[str] = None
    file_name: str
    file_data: str
    content_type: Optional[str] = None


@router.post("/materials/upload", status_code=201, summary="Upload a material file to Supabase Storage")
def upload_material_file(body: UploadMaterialBody, cr: dict = Depends(require_student)):
    _require_cr(cr)
    try:
        encoded = body.file_data.split(",", 1)[1] if body.file_data.startswith("data:") and "," in body.file_data else body.file_data
        content = base64.b64decode(encoded, validate=True)
    except Exception:
        raise HTTPException(400, "The uploaded file data is invalid")
    if not content:
        raise HTTPException(400, "The uploaded file is empty")
    if len(content) > 10 * 1024 * 1024:
        raise HTTPException(413, "Files must be 10 MB or smaller")
    ALLOWED_EXTENSIONS = {".pdf", ".docx", ".doc", ".pptx", ".ppt", ".txt", ".xlsx", ".xls", ".png", ".jpg", ".jpeg", ".zip"}
    ext = os.path.splitext(body.file_name)[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(400, f"Unsupported file type '{ext}'. Allowed: PDF, Word, PowerPoint, Excel, text, images, ZIP.")

    safe_name = re.sub(r"[^A-Za-z0-9._-]+", "_", os.path.basename(body.file_name))[:160] or "material.bin"
    content_type = mimetypes.guess_type(safe_name)[0] or "application/octet-stream"
    bucket = "ushss-study-materials"
    try:
        try:
            sb.storage.create_bucket(bucket, options={"public": False, "file_size_limit": 10 * 1024 * 1024})
        except Exception:
            pass
        storage_path = f"{cr['id']}/{uuid4().hex}_{safe_name}"
        storage = sb.storage.from_(bucket)
        storage.upload(storage_path, content, file_options={"content-type": content_type, "upsert": "false"})
        file_url = storage_path
    except Exception as exc:
        print(f"CR MATERIAL STORAGE ERROR: {type(exc).__name__}: {str(exc)[:200]}")
        raise HTTPException(503, "File storage is unavailable. Please check the Supabase Storage bucket configuration.")
    row = {
        "title": body.title.strip(),
        "description": (body.description or "").strip(),
        "subject": body.subject,
        "file_name": safe_name,
        "file_url": file_url,
        "size": f"{len(content) / (1024 * 1024):.2f} MB",
        "programme": cr.get("programme"),
        "batch": cr.get("batch"),
        "uploaded_by": cr["id"],
        "is_active": True,
    }
    try:
        result = sb.table("study_materials").insert(row).execute()
        if not result.data:
            raise RuntimeError("Material metadata insert returned no row")
        return result.data[0]
    except Exception as exc:
        try:
            sb.storage.from_(bucket).remove([storage_path])
        except Exception:
            pass
        print(f"CR MATERIAL METADATA ERROR: {type(exc).__name__}: {str(exc)[:200]}")
        raise HTTPException(503, "The file was uploaded but its record could not be saved.")


@router.delete("/materials/{mid}", summary="Delete a class study material")
def delete_material(mid: int, cr: dict = Depends(require_student)):
    _require_cr(cr)
    res = sb.table("study_materials").select("id,uploaded_by,file_url").eq("id", mid).limit(1).execute()
    if not res.data:
        raise HTTPException(404, "Study material not found")
    existing = res.data[0]
    if cr["role"] != "admin" and existing.get("uploaded_by") != cr["id"]:
        raise HTTPException(403, "You can only delete materials you uploaded")
    file_url = str(existing.get("file_url") or "")
    marker = "/storage/v1/object/public/ushss-study-materials/"
    storage_path = file_url.split(marker, 1)[1].split("?", 1)[0] if marker in file_url else file_url
    if storage_path:
        try:
            sb.storage.from_("ushss-study-materials").remove([storage_path])
        except Exception as exc:
            print(f"CR MATERIAL FILE DELETE WARNING: {type(exc).__name__}: {str(exc)[:160]}")
    sb.table("study_materials").delete().eq("id", mid).execute()
    return {"message": "Study material deleted"}
