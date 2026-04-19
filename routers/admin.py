"""
routers/admin.py
────────────────
Admin-only CRUD endpoints (mirrors the original app/app.py Flask admin routes
and the ad.js Node.js server routes).

  GET    /api/admin/users               — list all users (filter by role/search)
  POST   /api/admin/users               — create a user
  GET    /api/admin/users/{uid}         — get single user
  PUT    /api/admin/users/{uid}         — update user
  DELETE /api/admin/users/{uid}         — delete user
  PATCH  /api/admin/users/{uid}/toggle  — toggle active/inactive
  GET    /api/admin/stats               — dashboard stats
  GET    /api/admin/audit               — audit log
  GET    /api/admin/messages            — contact messages
  PATCH  /api/admin/messages/{mid}/read — mark message read
"""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import get_current_user, require_admin
from app.models import AuditLog, ContactMessage, RoleEnum, User
from app.schemas import AdminUserCreate, AdminUserUpdate, MessageOut, UserOut
from app.security import hash_password

router = APIRouter(prefix="/admin", tags=["Admin"])


# ── Users ─────────────────────────────────────────────────────────────────────

@router.get("/users", response_model=dict, summary="List all users")
def list_users(
    role:      Optional[str]  = Query(None),
    search:    Optional[str]  = Query(None),
    is_active: Optional[bool] = Query(None),
    page:      int            = Query(1, ge=1),
    per_page:  int            = Query(20, ge=1, le=100),
    db:        Session        = Depends(get_db),
    _:         User           = Depends(require_admin),
):
    q = db.query(User)
    if role:
        try:
            q = q.filter(User.role == RoleEnum(role))
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid role: {role}")
    if search:
        like = f"%{search}%"
        q = q.filter(
            User.full_name.ilike(like) |
            User.username.ilike(like)  |
            User.email.ilike(like)
        )
    if is_active is not None:
        q = q.filter(User.is_active == is_active)

    total = q.count()
    users = (
        q.order_by(User.created_at.desc())
         .offset((page - 1) * per_page)
         .limit(per_page)
         .all()
    )
    return {
        "users": [UserOut.model_validate(u) for u in users],
        "total": total,
        "page":  page,
        "pages": (total + per_page - 1) // per_page,
    }


@router.get("/users/{uid}", response_model=UserOut, summary="Get a single user")
def get_user(
    uid: int,
    db:  Session = Depends(get_db),
    _:   User    = Depends(require_admin),
):
    user = db.query(User).filter(User.id == uid).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return UserOut.model_validate(user)


@router.post("/users", response_model=UserOut, status_code=201, summary="Create a user")
def create_user(
    body:  AdminUserCreate,
    db:    Session = Depends(get_db),
    admin: User    = Depends(require_admin),
):
    try:
        role = RoleEnum(body.role.lower())
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid role: {body.role}")

    if db.query(User).filter(User.username == body.username, User.role == role).first():
        raise HTTPException(status_code=409, detail="Username already exists for this role")
    if db.query(User).filter(User.email == body.email).first():
        raise HTTPException(status_code=409, detail="Email already in use")

    user = User(
        username      = body.username,
        role          = role,
        full_name     = body.full_name,
        email         = body.email,
        phone         = body.phone,
        designation   = body.designation,
        enrollment_no = body.enrollment_no,
        department    = body.department,
        programme     = body.programme,
        batch         = body.batch,
        is_active     = body.is_active,
        password_hash = hash_password(body.password),
    )
    db.add(user)
    db.add(AuditLog(
        user_id = admin.id,
        action  = "CREATE_USER",
        detail  = f"Created {role.value} {body.username}",
    ))
    db.commit()
    db.refresh(user)
    return UserOut.model_validate(user)


@router.put("/users/{uid}", response_model=UserOut, summary="Update a user")
def update_user(
    uid:   int,
    body:  AdminUserUpdate,
    db:    Session = Depends(get_db),
    admin: User    = Depends(require_admin),
):
    user = db.query(User).filter(User.id == uid).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    if body.full_name:
        user.full_name = body.full_name
    if body.email:
        conflict = db.query(User).filter(User.email == body.email, User.id != uid).first()
        if conflict:
            raise HTTPException(status_code=409, detail="Email already in use")
        user.email = body.email
    if body.phone         is not None: user.phone         = body.phone
    if body.designation   is not None: user.designation   = body.designation
    if body.enrollment_no is not None: user.enrollment_no = body.enrollment_no
    if body.department    is not None: user.department    = body.department
    if body.programme     is not None: user.programme     = body.programme
    if body.batch         is not None: user.batch         = body.batch
    if body.is_active     is not None:
        if user.id == admin.id and not body.is_active:
            raise HTTPException(status_code=400, detail="Cannot deactivate your own account")
        user.is_active = body.is_active
    if body.password:
        user.password_hash = hash_password(body.password)

    db.add(AuditLog(user_id=admin.id, action="UPDATE_USER",
                    detail=f"Updated user id={uid}"))
    db.commit()
    db.refresh(user)
    return UserOut.model_validate(user)


@router.delete("/users/{uid}", response_model=MessageOut, summary="Delete a user")
def delete_user(
    uid:   int,
    db:    Session = Depends(get_db),
    admin: User    = Depends(require_admin),
):
    user = db.query(User).filter(User.id == uid).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if user.id == admin.id:
        raise HTTPException(status_code=400, detail="Cannot delete your own account")

    db.add(AuditLog(user_id=admin.id, action="DELETE_USER",
                    detail=f"Deleted {user.role.value} {user.username}"))
    db.delete(user)
    db.commit()
    return MessageOut(message="User deleted successfully")


@router.patch("/users/{uid}/toggle", response_model=dict, summary="Toggle active status")
def toggle_user(
    uid:   int,
    db:    Session = Depends(get_db),
    admin: User    = Depends(require_admin),
):
    user = db.query(User).filter(User.id == uid).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if user.id == admin.id:
        raise HTTPException(status_code=400, detail="Cannot deactivate your own account")

    user.is_active = not user.is_active
    db.add(AuditLog(user_id=admin.id, action="TOGGLE_USER",
                    detail=f"{user.username} → {'active' if user.is_active else 'inactive'}"))
    db.commit()
    return {"is_active": user.is_active}


# ── Stats ─────────────────────────────────────────────────────────────────────

@router.get("/stats", response_model=dict, summary="Dashboard statistics")
def stats(
    db: Session = Depends(get_db),
    _:  User    = Depends(require_admin),
):
    counts = {role.value: db.query(User).filter(User.role == role).count()
              for role in RoleEnum}
    total  = db.query(User).count()
    active = db.query(User).filter(User.is_active == True).count()

    recent_logins = (
        db.query(User)
        .filter(User.last_login.isnot(None))
        .order_by(User.last_login.desc())
        .limit(10).all()
    )
    audit = (
        db.query(AuditLog)
        .order_by(AuditLog.ts.desc())
        .limit(20).all()
    )

    return {
        "counts":       counts,
        "total":        total,
        "active":       active,
        "recentLogins": [UserOut.model_validate(u) for u in recent_logins],
        "log": [
            {"id": l.id, "action": l.action, "detail": l.detail,
             "ts": l.ts.isoformat() if l.ts else None, "user_id": l.user_id}
            for l in audit
        ],
    }


# ── Audit log ─────────────────────────────────────────────────────────────────

@router.get("/audit", response_model=list, summary="Full audit log")
def audit_log(
    db: Session = Depends(get_db),
    _:  User    = Depends(require_admin),
):
    rows = db.query(AuditLog).order_by(AuditLog.ts.desc()).limit(200).all()
    return [
        {"id": r.id, "action": r.action, "detail": r.detail,
         "ts": r.ts.isoformat() if r.ts else None, "user_id": r.user_id}
        for r in rows
    ]


# ── Contact messages ──────────────────────────────────────────────────────────

@router.get("/messages", response_model=list, summary="List contact messages")
def list_messages(
    db: Session = Depends(get_db),
    _:  User    = Depends(require_admin),
):
    msgs = db.query(ContactMessage).order_by(ContactMessage.submitted_at.desc()).all()
    return [m.to_dict() for m in msgs]


@router.patch("/messages/{mid}/read", response_model=MessageOut, summary="Mark message read")
def mark_read(
    mid: int,
    db:  Session = Depends(get_db),
    _:   User    = Depends(require_admin),
):
    msg = db.query(ContactMessage).filter(ContactMessage.id == mid).first()
    if not msg:
        raise HTTPException(status_code=404, detail="Message not found")
    msg.is_read = True
    db.commit()
    return MessageOut(message="Marked as read")
