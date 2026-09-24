from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.db import sb
from app.deps import get_current_user, require_super_admin

router = APIRouter(prefix="/chat", tags=["Chat"])


class SendMessageBody(BaseModel):
    recipient_id: int
    message: str = Field(..., min_length=1, max_length=4000)


@router.get("/me")
def chat_me(user: dict = Depends(get_current_user)):
    return {"id": user["id"], "full_name": user.get("full_name"), "username": user.get("username"), "role": user.get("role"), "is_super_admin": user.get("is_super_admin", False)}


@router.get("/users")
def chat_users(user: dict = Depends(get_current_user)):
    rows = (
        sb.table("users")
        .select("id,full_name,username,role,department,programme,batch,is_super_admin")
        .eq("is_active", True)
        .eq("status", "approved")
        .neq("id", user["id"])
        .order("full_name")
        .execute()
        .data or []
    )
    return rows


@router.get("/conversations")
def conversations(user: dict = Depends(get_current_user)):
    sent = (
        sb.table("chat_messages")
        .select("id,sender_id,recipient_id,message,is_read,created_at")
        .eq("sender_id", user["id"])
        .order("created_at", desc=True)
        .execute()
        .data or []
    )
    received = (
        sb.table("chat_messages")
        .select("id,sender_id,recipient_id,message,is_read,created_at")
        .eq("recipient_id", user["id"])
        .order("created_at", desc=True)
        .execute()
        .data or []
    )
    rows = sorted(sent + received, key=lambda m: m.get("created_at") or "", reverse=True)
    ids = set()
    for row in rows:
        other = row["recipient_id"] if row["sender_id"] == user["id"] else row["sender_id"]
        ids.add(other)
    profiles = {}
    if ids:
        users = sb.table("users").select("id,full_name,username,role,is_super_admin").in_("id", list(ids)).execute().data or []
        profiles = {u["id"]: u for u in users}

    result = []
    seen = set()
    for row in rows:
        other_id = row["recipient_id"] if row["sender_id"] == user["id"] else row["sender_id"]
        if other_id in seen:
            continue
        seen.add(other_id)
        p = profiles.get(other_id, {})
        unread = sum(
            1 for m in rows
            if m["sender_id"] == other_id and m["recipient_id"] == user["id"] and not m.get("is_read")
        )
        result.append({
            "user": p,
            "last_message": row,
            "unread": unread,
        })
    return result


@router.get("/thread/{other_id}")
def thread(
    other_id: int,
    limit: int = Query(100, ge=1, le=200),
    user: dict = Depends(get_current_user),
):
    if other_id == user["id"]:
        raise HTTPException(400, "You cannot chat with yourself.")
    other = sb.table("users").select("id,full_name,username,role,is_super_admin").eq("id", other_id).eq("is_active", True).eq("status", "approved").single().execute()
    if not other.data:
        raise HTTPException(404, "User not found.")
    sent = (
        sb.table("chat_messages")
        .select("id,sender_id,recipient_id,message,is_read,created_at")
        .eq("sender_id", user["id"]).eq("recipient_id", other_id)
        .order("created_at")
        .limit(limit)
        .execute()
        .data or []
    )
    received = (
        sb.table("chat_messages")
        .select("id,sender_id,recipient_id,message,is_read,created_at")
        .eq("sender_id", other_id).eq("recipient_id", user["id"])
        .order("created_at")
        .limit(limit)
        .execute()
        .data or []
    )
    rows = sorted(sent + received, key=lambda m: m.get("created_at") or "")[-limit:]
    sb.table("chat_messages").update({"is_read": True}).eq("sender_id", other_id).eq("recipient_id", user["id"]).eq("is_read", False).execute()
    return {"user": other.data, "messages": rows}


@router.post("/send", status_code=201)
def send_message(body: SendMessageBody, user: dict = Depends(get_current_user)):
    if body.recipient_id == user["id"]:
        raise HTTPException(400, "You cannot send a message to yourself.")
    recipient = (
        sb.table("users")
        .select("id,full_name,username,role,is_super_admin")
        .eq("id", body.recipient_id)
        .eq("is_active", True)
        .eq("status", "approved")
        .single()
        .execute()
    )
    if not recipient.data:
        raise HTTPException(404, "Recipient not found or inactive.")
    text = body.message.strip()
    if not text:
        raise HTTPException(400, "Message cannot be empty.")
    row = sb.table("chat_messages").insert({
        "sender_id": user["id"],
        "recipient_id": body.recipient_id,
        "message": text,
        "is_read": False,
    }).execute().data
    return row[0]


@router.get("/admin/all")
def admin_all_chat(
    limit: int = Query(500, ge=1, le=2000),
    admin: dict = Depends(require_super_admin),
):
    rows = (
        sb.table("chat_messages")
        .select("id,sender_id,recipient_id,message,is_read,created_at")
        .order("created_at", desc=True)
        .limit(limit)
        .execute()
        .data or []
    )
    ids = {x["sender_id"] for x in rows} | {x["recipient_id"] for x in rows}
    profiles = {}
    if ids:
        users = sb.table("users").select("id,full_name,username,role,is_super_admin,email,phone,department,programme,batch").in_("id", list(ids)).execute().data or []
        profiles = {u["id"]: u for u in users}
    return {
        "messages": rows,
        "users": profiles,
        "total": len(rows),
    }
