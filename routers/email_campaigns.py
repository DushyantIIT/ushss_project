"""Admin email campaigns and SMTP delivery worker."""
import hmac
import os
from datetime import datetime, timezone
from html import escape
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, EmailStr, Field

from app.database import sb
from app.deps import require_admin
from app.email_utils import send_email_strict

router = APIRouter(prefix="/admin/emails", tags=["Admin Email"])
cron_router = APIRouter(tags=["Email Scheduler"])


def _now():
    return datetime.now(timezone.utc)


def _iso(value):
    return value.isoformat() if hasattr(value, "isoformat") else value


def _html_document(body_html: str, body_text: str) -> str:
    content = body_html or "<p>" + escape(body_text or "").replace("\n", "<br>") + "</p>"
    return (
        '<!doctype html><html><body style="margin:0;background:#f4f1eb;font-family:Arial,sans-serif;color:#252525">'
        '<div style="max-width:680px;margin:24px auto;background:#fff;border:1px solid #e6e0d6">'
        '<div style="background:#6e1212;padding:22px 28px;color:#f4c856;font-size:20px;font-weight:bold">USHSS</div>'
        '<div style="padding:28px;line-height:1.65">' + content + '</div>'
        '<div style="padding:16px 28px;background:#f8f6f1;color:#6b6258;font-size:12px">University School of Humanities &amp; Social Sciences</div>'
        '</div></body></html>'
    )


def _course_match(user: dict, course: Optional[str]) -> bool:
    if not course:
        return True
    wanted = course.strip().lower()
    values = [str(user.get(k) or "").strip().lower() for k in ("domain", "department", "programme")]
    aliases = {"english": ("english",), "economics": ("economics", "econ")}
    terms = aliases.get(wanted, (wanted,))
    return any(any(term in value for term in terms) for value in values)


class TemplateInput(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    subject: str = Field(min_length=1, max_length=250)
    body_text: str = ""
    body_html: str = ""
    is_active: bool = True


class TestEmailInput(BaseModel):
    to: EmailStr
    subject: str = Field(min_length=1, max_length=250)
    body_text: str = ""
    body_html: str = ""


class CampaignInput(BaseModel):
    name: str = Field(min_length=2, max_length=150)
    subject: str = Field(min_length=1, max_length=250)
    body_text: str = ""
    body_html: str = ""
    programme: Optional[str] = None
    course: Optional[str] = None
    semester: Optional[str] = None
    section: Optional[str] = None
    scheduled_at: Optional[datetime] = None
    template_id: Optional[int] = None


@router.get("/templates")
def list_templates(admin: dict = Depends(require_admin)):
    res = sb.table("email_templates").select("*").order("updated_at", desc=True).execute()
    return {"templates": res.data or []}


@router.post("/templates", status_code=201)
def create_template(body: TemplateInput, admin: dict = Depends(require_admin)):
    row = body.model_dump()
    row["created_by"] = admin["id"]
    res = sb.table("email_templates").insert(row).execute()
    if not res.data:
        raise HTTPException(502, "Could not save the email template.")
    return res.data[0]


@router.put("/templates/{template_id}")
def update_template(template_id: int, body: TemplateInput, admin: dict = Depends(require_admin)):
    row = body.model_dump()
    row["updated_at"] = _now().isoformat()
    res = sb.table("email_templates").update(row).eq("id", template_id).execute()
    if not res.data:
        raise HTTPException(404, "Email template not found.")
    return res.data[0]


@router.delete("/templates/{template_id}")
def delete_template(template_id: int, admin: dict = Depends(require_admin)):
    res = sb.table("email_templates").delete().eq("id", template_id).execute()
    if not res.data:
        raise HTTPException(404, "Email template not found.")
    return {"deleted": True}


@router.post("/test")
def send_test_email(body: TestEmailInput, admin: dict = Depends(require_admin)):
    try:
        send_email_strict(
            str(body.to), body.subject, body.body_text,
            _html_document(body.body_html, body.body_text),
        )
    except Exception as exc:
        print(f"USHSS TEST EMAIL FAILED: {type(exc).__name__}: {str(exc)[:240]}")
        raise HTTPException(502, "Test email failed. Check SMTP settings and server logs.")
    return {"sent": True, "to": str(body.to)}


@router.post("/campaigns", status_code=201)
def create_campaign(body: CampaignInput, admin: dict = Depends(require_admin)):
    subject, body_text, body_html = body.subject, body.body_text, body.body_html
    if body.template_id:
        template = sb.table("email_templates").select("*").eq("id", body.template_id).limit(1).execute()
        if not template.data:
            raise HTTPException(404, "Selected email template was not found.")
        selected = template.data[0]
        subject = body.subject or selected.get("subject") or ""
        body_text = body_text or selected.get("body_text") or ""
        body_html = body_html or selected.get("body_html") or ""

    query = sb.table("users").select("id,email,full_name,programme,domain,department,semester,section,role,status,is_active")
    query = query.eq("role", "student").eq("is_active", True).eq("status", "approved")
    users = query.execute().data or []

    recipients = []
    for user in users:
        if not user.get("email"):
            continue
        if body.programme and body.programme.strip().lower() not in str(user.get("programme") or "").lower():
            continue
        if not _course_match(user, body.course):
            continue
        if body.semester and str(user.get("semester") or "").strip() != str(body.semester).strip():
            continue
        if body.section and body.section.strip().lower() not in ("all", "none") and str(user.get("section") or "").strip().lower() != body.section.strip().lower():
            continue
        recipients.append(user)

    if not recipients:
        raise HTTPException(400, "No approved active students matched the selected audience.")

    scheduled_at = body.scheduled_at
    if scheduled_at and scheduled_at.tzinfo is None:
        scheduled_at = scheduled_at.replace(tzinfo=timezone.utc)
    scheduled_at = scheduled_at or _now()

    campaign_row = {
        "name": body.name, "subject": subject, "body_text": body_text,
        "body_html": body_html, "target_filters": {
            "programme": body.programme, "course": body.course,
            "semester": body.semester, "section": body.section,
        },
        "scheduled_at": scheduled_at.astimezone(timezone.utc).isoformat(),
        "status": "scheduled", "created_by": admin["id"],
        "recipient_count": len(recipients),
    }
    created = sb.table("email_campaigns").insert(campaign_row).execute()
    if not created.data:
        raise HTTPException(502, "Could not create the email campaign.")
    campaign = created.data[0]

    rows = [{
        "campaign_id": campaign["id"], "user_id": u["id"],
        "recipient_email": u["email"], "recipient_name": u.get("full_name") or "",
        "status": "pending", "attempts": 0,
    } for u in recipients]
    try:
        for offset in range(0, len(rows), 500):
            sb.table("email_deliveries").insert(rows[offset:offset + 500]).execute()
    except Exception as exc:
        try:
            sb.table("email_campaigns").delete().eq("id", campaign["id"]).execute()
        except Exception:
            pass
        print(f"EMAIL CAMPAIGN RECIPIENT INSERT FAILED: {type(exc).__name__}: {str(exc)[:240]}")
        raise HTTPException(502, "Could not prepare campaign recipients. No email was sent.")
    return {"campaign": campaign, "recipient_count": len(recipients)}


@router.get("/campaigns")
def list_campaigns(admin: dict = Depends(require_admin)):
    campaigns = sb.table("email_campaigns").select("*").order("created_at", desc=True).limit(50).execute().data or []
    for campaign in campaigns:
        rows = sb.table("email_deliveries").select("status").eq("campaign_id", campaign["id"]).execute().data or []
        campaign["delivery_stats"] = {
            status: sum(1 for row in rows if row.get("status") == status)
            for status in ("pending", "sending", "sent", "failed")
        }
    return {"campaigns": campaigns}


@router.get("/deliveries")
def list_deliveries(campaign_id: Optional[int] = None, status: Optional[str] = None, admin: dict = Depends(require_admin)):
    query = sb.table("email_deliveries").select("*").order("created_at", desc=True).limit(300)
    if campaign_id:
        query = query.eq("campaign_id", campaign_id)
    if status:
        query = query.eq("status", status)
    return {"deliveries": query.execute().data or []}


@router.post("/campaigns/{campaign_id}/retry")
def retry_failed(campaign_id: int, admin: dict = Depends(require_admin)):
    campaign = sb.table("email_campaigns").select("id,status").eq("id", campaign_id).limit(1).execute()
    if not campaign.data:
        raise HTTPException(404, "Email campaign not found.")
    failed = sb.table("email_deliveries").select("id").eq("campaign_id", campaign_id).eq("status", "failed").execute().data or []
    if not failed:
        raise HTTPException(400, "This campaign has no failed deliveries to retry.")
    sb.table("email_deliveries").update({
        "status": "pending", "last_error": None, "next_attempt_at": _now().isoformat(),
    }).eq("campaign_id", campaign_id).eq("status", "failed").execute()
    sb.table("email_campaigns").update({"status": "scheduled", "scheduled_at": _now().isoformat()}).eq("id", campaign_id).execute()
    return {"queued_for_retry": len(failed)}


def _dispatch_due_campaigns(limit: int = 30):
    now = _now()
    candidates = sb.table("email_campaigns").select("*").in_("status", ["scheduled", "sending"]).order("scheduled_at").limit(20).execute().data or []
    sent = failed = claimed = 0
    for campaign in candidates:
        due = campaign.get("scheduled_at")
        if campaign.get("status") == "scheduled" and due and datetime.fromisoformat(str(due).replace("Z", "+00:00")) > now:
            continue
        if campaign.get("status") == "scheduled":
            started = sb.table("email_campaigns").update({"status": "sending"}).eq("id", campaign["id"]).eq("status", "scheduled").execute()
            if not started.data:
                continue

        pending = sb.table("email_deliveries").select("*").eq("campaign_id", campaign["id"]).eq("status", "pending").limit(max(1, limit - claimed)).execute().data or []
        for delivery in pending:
            # Conditional update is the claim: concurrent workers cannot both send this row.
            claim = sb.table("email_deliveries").update({
                "status": "sending", "attempts": int(delivery.get("attempts") or 0) + 1,
                "last_attempt_at": now.isoformat(),
            }).eq("id", delivery["id"]).eq("status", "pending").execute()
            if not claim.data:
                continue
            claimed += 1
            html_body = _html_document(campaign.get("body_html") or "", campaign.get("body_text") or "")
            try:
                send_email_strict(
                    delivery["recipient_email"], campaign["subject"],
                    campaign.get("body_text") or "", html_body,
                )
                sb.table("email_deliveries").update({
                    "status": "sent", "sent_at": _now().isoformat(), "last_error": None,
                }).eq("id", delivery["id"]).eq("status", "sending").execute()
                sent += 1
            except Exception as exc:
                message = f"{type(exc).__name__}: {str(exc)[:700]}"
                sb.table("email_deliveries").update({
                    "status": "failed", "last_error": message,
                }).eq("id", delivery["id"]).eq("status", "sending").execute()
                failed += 1
                print(f"USHSS EMAIL DELIVERY {delivery['id']} FAILED: {message}")

            if claimed >= limit:
                break

        remaining = sb.table("email_deliveries").select("id,status").eq("campaign_id", campaign["id"]).execute().data or []
        states = [row.get("status") for row in remaining]
        if states and all(state == "sent" for state in states):
            final_status = "sent"
            sb.table("email_campaigns").update({"status": final_status, "sent_at": _now().isoformat()}).eq("id", campaign["id"]).execute()
        elif any(state == "pending" or state == "sending" for state in states):
            sb.table("email_campaigns").update({"status": "sending"}).eq("id", campaign["id"]).execute()
        elif any(state == "failed" for state in states):
            sb.table("email_campaigns").update({"status": "failed"}).eq("id", campaign["id"]).execute()

        if claimed >= limit:
            break
    return {"claimed": claimed, "sent": sent, "failed": failed}


@router.post("/dispatch")
def dispatch_now(admin: dict = Depends(require_admin)):
    return _dispatch_due_campaigns(30)


@cron_router.post("/api/email/dispatch")
def cron_dispatch(x_cron_secret: Optional[str] = Header(None, alias="x-cron-secret")):
    expected = os.environ.get("EMAIL_CRON_SECRET", "")
    if not expected or not x_cron_secret or not hmac.compare_digest(expected, x_cron_secret):
        raise HTTPException(401, "Invalid scheduler credential.")
    return _dispatch_due_campaigns(30)
