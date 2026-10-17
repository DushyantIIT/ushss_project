"""Scoped messaging API for the USHSS portal.

Apply supabase/migrations/20261017_interconnected_chat.sql before enabling this router.
Conversation membership and class scope are checked on every read/write.
"""
from datetime import datetime, timezone
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.db import sb
from app.deps import get_current_user

router = APIRouter(prefix="/chat", tags=["Chat"])

def _class_key(user: dict) -> tuple:
    return tuple(str(user.get(k) or "").strip().lower() for k in ("programme", "batch", "semester", "section"))

def _same_class(a: dict, b: dict) -> bool:
    ka, kb = _class_key(a), _class_key(b)
    # Programme and batch are mandatory; semester/section are enforced when present
    if not ka[0] or not ka[1] or ka[:2] != kb[:2]:
        return False
    for i in (2, 3):
        if ka[i] and kb[i] and ka[i] != kb[i]:
            return False
    return True

def _user(user_id: str) -> dict:
    result = sb.table("users").select("id,full_name,role,is_active,status,programme,batch,semester,section").eq("id", user_id).limit(1).execute()
    if not result.data or not result.data[0].get("is_active", True) or result.data[0].get("status", "approved") != "approved":
        raise HTTPException(404, "User not found or inactive")
    return result.data[0]

def _is_authorized(user: dict, other: dict) -> bool:
    if user.get("role") == "admin":
        return True
    if user["id"] == other["id"]:
        return False
    if user.get("role") in ("student", "cr") and other.get("role") in ("student", "cr"):
        return _same_class(user, other)
    if user.get("role") == "student" and other.get("role") == "faculty":
        return _faculty_teaches_class(other, user)
    if user.get("role") == "faculty" and other.get("role") in ("student", "cr"):
        return _faculty_teaches_class(user, other)
    if user.get("role") == "cr" and other.get("role") == "faculty":
        return _faculty_teaches_class(other, user)
    if user.get("role") == "faculty" and other.get("role") == "cr":
        return _faculty_teaches_class(user, other)
    return False

def _faculty_teaches_class(faculty: dict, student: dict) -> bool:
    if not student.get("programme") or not student.get("batch"):
        return False
    # Timetable rows are the source of truth for faculty/class assignments.
    try:
        rows = sb.table("timetable_slots").select("*").eq("programme", student["programme"]).eq("batch", student["batch"]).execute().data or []
    except Exception:
        return False
    for row in rows:
        assigned = [str(row.get(k) or "") for k in ("faculty_id", "user_id", "teacher_id", "created_by")]
        if str(faculty["id"]) not in assigned:
            continue
        for key in ("semester", "section"):
            wanted = str(student.get(key) or "").strip().lower()
            actual = str(row.get(key) or "").strip().lower()
            if wanted and actual and wanted != actual:
                break
        else:
            return True
    return False

def _conversation(conversation_id: str, user: dict) -> dict:
    result = sb.table("chat_conversations").select("*").eq("id", conversation_id).limit(1).execute()
    if not result.data:
        raise HTTPException(404, "Conversation not found")
    conversation = result.data[0]
    member = sb.table("chat_members").select("id").eq("conversation_id", conversation_id).eq("user_id", str(user["id"])).limit(1).execute()
    if user.get("role") != "admin" and not member.data:
        raise HTTPException(403, "You are not a member of this conversation")
    return conversation

class DirectChatBody(BaseModel):
    recipient_id: str = Field(min_length=1, max_length=128)

class MessageBody(BaseModel):
    body: str = Field(min_length=1, max_length=4000)

class GroupChatBody(BaseModel):
    title: Optional[str] = Field(default=None, max_length=100)

@router.get("/people")
def available_people(user: dict = Depends(get_current_user)):
    rows = sb.table("users").select("id,full_name,role,programme,batch,semester,section").eq("is_active", True).execute().data or []
    people = []
    for other in rows:
        if str(other["id"]) != str(user["id"]) and other.get("status", "approved") == "approved" and _is_authorized(user, other):
            people.append({k: other.get(k) for k in ("id","full_name","role","programme","batch","semester","section")})
    return {"people": people}

@router.get("/conversations")
def list_conversations(user: dict = Depends(get_current_user)):
    memberships = sb.table("chat_members").select("conversation_id,last_read_at").eq("user_id", str(user["id"])).execute().data or []
    result = []
    for membership in memberships:
        conv = sb.table("chat_conversations").select("*").eq("id", membership["conversation_id"]).limit(1).execute().data
        if not conv:
            continue
        c = conv[0]
        last = sb.table("chat_messages").select("id,body,sender_id,created_at").eq("conversation_id", c["id"]).order("created_at", desc=True).limit(1).execute().data or []
        c["last_message"] = last[0] if last else None
        c["last_read_at"] = membership.get("last_read_at")
        result.append(c)
    return sorted(result, key=lambda x: (x.get("last_message") or {}).get("created_at") or x.get("created_at") or "", reverse=True)

@router.post("/direct", status_code=201)
def create_direct(body: DirectChatBody, user: dict = Depends(get_current_user)):
    other = _user(body.recipient_id)
    if not _is_authorized(user, other):
        raise HTTPException(403, "Private chat is limited to classmates and assigned faculty")
    # Reuse an existing direct conversation containing both users.
    mine = sb.table("chat_members").select("conversation_id").eq("user_id", str(user["id"])).execute().data or []
    theirs = sb.table("chat_members").select("conversation_id").eq("user_id", str(other["id"])).execute().data or []
    their_ids = {x["conversation_id"] for x in theirs}
    for item in mine:
        if item["conversation_id"] not in their_ids:
            continue
        conv = sb.table("chat_conversations").select("*").eq("id", item["conversation_id"]).eq("kind", "direct").limit(1).execute().data
        if conv:
            return conv[0]
    conv = sb.table("chat_conversations").insert({"kind":"direct","title":None,"created_by":str(user["id"]),"created_at":datetime.now(timezone.utc).isoformat()}).execute().data[0]
    sb.table("chat_members").insert([{"conversation_id":conv["id"],"user_id":str(user["id"])},{"conversation_id":conv["id"],"user_id":str(other["id"])}]).execute()
    return conv

@router.post("/groups", status_code=201)
def create_class_group(body: GroupChatBody, user: dict = Depends(get_current_user)):
    if user.get("role") not in ("cr", "faculty", "admin"):
        raise HTTPException(403, "Only CRs, faculty, or admins can create class groups")
    if not user.get("programme") or not user.get("batch"):
        raise HTTPException(400, "Your account must have a programme and batch")
    if user["role"] != "admin" and user["role"] == "faculty":
        # A faculty member must have at least one timetable assignment for this class.
        if not _faculty_teaches_class(user, user):
            raise HTTPException(403, "No assigned class found for this faculty account")
    conv = sb.table("chat_conversations").insert({
        "kind":"class","title":(body.title or "").strip() or "Class chat",
        "programme":user.get("programme"),"batch":user.get("batch"),
        "semester":user.get("semester"),"section":user.get("section"),
        "created_by":str(user["id"]),"created_at":datetime.now(timezone.utc).isoformat()
    }).execute().data[0]
    users = sb.table("users").select("id,role,programme,batch,semester,section,is_active,status").eq("programme", user["programme"]).eq("batch", user["batch"]).eq("is_active", True).execute().data or []
    members = []
    for person in users:
        if person.get("status", "approved") != "approved":
            continue
        if person["id"] == user["id"] or (person.get("role") in ("student","cr") and _same_class(user, person)):
            members.append({"conversation_id":conv["id"],"user_id":str(person["id"])})
        elif person.get("role") == "faculty" and _faculty_teaches_class(person, user):
            members.append({"conversation_id":conv["id"],"user_id":str(person["id"])})
    if not any(m["user_id"] == str(user["id"]) for m in members):
        members.append({"conversation_id":conv["id"],"user_id":str(user["id"])})
    sb.table("chat_members").insert(members).execute()
    return conv

@router.get("/{conversation_id}/messages")
def get_messages(conversation_id: str, before: Optional[str] = Query(default=None), user: dict = Depends(get_current_user)):
    _conversation(conversation_id, user)
    q = sb.table("chat_messages").select("id,conversation_id,sender_id,body,created_at").eq("conversation_id", conversation_id)
    if before:
        q = q.lt("created_at", before)
    rows = q.order("created_at", desc=True).limit(100).execute().data or []
    sb.table("chat_members").update({"last_read_at":datetime.now(timezone.utc).isoformat()}).eq("conversation_id", conversation_id).eq("user_id", str(user["id"])).execute()
    return {"messages": list(reversed(rows))}

@router.post("/{conversation_id}/messages", status_code=201)
def send_message(conversation_id: str, body: MessageBody, user: dict = Depends(get_current_user)):
    _conversation(conversation_id, user)
    text = body.body.strip()
    if not text:
        raise HTTPException(400, "Message cannot be empty")
    if user.get("role") == "admin":
        # Admins can participate only if they explicitly join the conversation.
        member = sb.table("chat_members").select("id").eq("conversation_id", conversation_id).eq("user_id", str(user["id"])).limit(1).execute()
        if not member.data:
            raise HTTPException(403, "Join the conversation before sending messages")
    row = sb.table("chat_messages").insert({"conversation_id":conversation_id,"sender_id":str(user["id"]),"body":text,"created_at":datetime.now(timezone.utc).isoformat()}).execute().data
    return row[0]
