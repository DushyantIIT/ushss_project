"""
routers/cr.py
─────────────
CLASS REPRESENTATIVE rights (extends student rights):
  ✅ Everything a student can do
  ✅ View classmates list                    GET /api/cr/classmates
  ⛔ Any data changes — admin only
  ⛔ Open/close sessions — faculty only
"""

from fastapi import APIRouter, Depends, HTTPException
from app.db import sb
from app.deps import require_student   # CR role is included in require_student

router = APIRouter(prefix="/cr", tags=["Class Representative"])


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
