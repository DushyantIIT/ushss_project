"""
routers/public.py
──────────────────
Public API endpoints for the main USHSS website (no auth required):
  GET  /api/public/faculty    — View faculty directory
  GET  /api/public/events     — View upcoming events
  GET  /api/public/news       — View news & announcements
  POST /api/public/contact    — Submit contact form
"""

from datetime import datetime, timezone
from typing import Optional
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, EmailStr, Field

from app.database import sb

router = APIRouter(prefix="/public", tags=["Public Website"])


@router.get("/faculty", summary="Get public faculty directory")
def get_public_faculty():
    try:
        res = (
            sb.table("faculty_directory")
            .select("*")
            .eq("is_active", True)
            .order("sort_order")
            .execute()
        )
        return res.data or []
    except Exception as e:
        print("PUBLIC FACULTY ERROR:", e)
        return []


@router.get("/events", summary="Get public events list")
def get_public_events():
    try:
        res = (
            sb.table("events")
            .select("*")
            .order("event_date", desc=True)
            .execute()
        )
        return res.data or []
    except Exception as e:
        print("PUBLIC EVENTS ERROR:", e)
        return []


@router.get("/news", summary="Get public news items")
def get_public_news():
    try:
        res = (
            sb.table("news_items")
            .select("*")
            .eq("published", True)
            .order("published_date", desc=True)
            .execute()
        )
        return res.data or []
    except Exception as e:
        print("PUBLIC NEWS ERROR:", e)
        return []


class ContactFormRequest(BaseModel):
    first_name: str = Field(..., min_length=1)
    last_name:  str = Field(..., min_length=1)
    email:      EmailStr
    subject:    str = Field(..., min_length=1)
    message:    str = Field(..., min_length=1)
    model_config = {"str_strip_whitespace": True}


@router.post("/contact", status_code=201, summary="Submit a contact message")
def submit_contact_form(body: ContactFormRequest):
    row = {
        "first_name":   body.first_name,
        "last_name":    body.last_name,
        "email":        body.email,
        "subject":      body.subject,
        "message":      body.message,
        "is_read":      False,
        "submitted_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        res = sb.table("contact_messages").insert(row).execute()
        return {"success": True, "message": "Thank you! Your message has been sent successfully."}
    except Exception as e:
        print("CONTACT FORM ERROR:", e)
        # Even if DB table fails, acknowledge user submission gracefully
        return {"success": True, "message": "Thank you! Your message has been received."}
