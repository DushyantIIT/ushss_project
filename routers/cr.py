"""
app/routers/cr.py
─────────────────
Self-service endpoints for Class Representatives (CRs).

  GET  /api/cr/profile          — view own profile
  PUT  /api/cr/profile          — update contact details
  POST /api/cr/change-password  — change own password
  GET  /api/cr/classmates       — view students in same programme & batch
"""
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import get_current_user
from app.security import hash_password, verify_password
from app.models import AuditLog, RoleEnum, User
from app.schemas import MessageOut, UserOut

router = APIRouter(prefix="/cr", tags=["Class Representative"])


def _require_cr(current_user: User = Depends(get_current_user)) -> User:
    if current_user.role not in (RoleEnum.cr, RoleEnum.admin):
        raise HTTPException(status_code=403, detail="Class Representatives only")
    return current_user


class CRProfileUpdate(BaseModel):
    phone: Optional[str]      = None
    email: Optional[EmailStr] = None
    model_config = {"str_strip_whitespace": True}


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(..., min_length=1)
    new_password: str     = Field(..., min_length=6)
    confirm_password: str = Field(..., min_length=6)


@router.get("/profile", response_model=UserOut, summary="View your CR profile")
def get_profile(cr: User = Depends(_require_cr)):
    return cr


@router.put("/profile", response_model=MessageOut, summary="Update contact details")
def update_profile(
    body: CRProfileUpdate,
    db: Session = Depends(get_db),
    cr: User    = Depends(_require_cr),
):
    if body.email and body.email != cr.email:
        if db.query(User).filter(User.email == body.email, User.id != cr.id).first():
            raise HTTPException(status_code=409, detail="Email already in use")
        cr.email = body.email
    if body.phone is not None:
        cr.phone = body.phone

    db.add(AuditLog(user_id=cr.id, action="CR_PROFILE_UPDATE",
                    detail=f"CR {cr.username} updated profile"))
    db.commit()
    return MessageOut(message="Profile updated successfully")


@router.post("/change-password", response_model=MessageOut, summary="Change your password")
def change_password(
    body: ChangePasswordRequest,
    db: Session = Depends(get_db),
    cr: User    = Depends(_require_cr),
):
    if not verify_password(body.current_password, cr.password_hash):
        raise HTTPException(status_code=400, detail="Current password is incorrect")
    if body.new_password != body.confirm_password:
        raise HTTPException(status_code=400, detail="Passwords do not match")
    if body.new_password == body.current_password:
        raise HTTPException(status_code=400, detail="New password must differ from current")

    cr.password_hash = hash_password(body.new_password)
    db.add(AuditLog(user_id=cr.id, action="PASSWORD_CHANGE",
                    detail=f"CR {cr.username} changed password"))
    db.commit()
    return MessageOut(message="Password changed successfully")


@router.get(
    "/classmates",
    response_model=List[UserOut],
    summary="View students in your programme and batch",
)
def view_classmates(
    db: Session = Depends(get_db),
    cr: User    = Depends(_require_cr),
):
    """
    Returns the active student list for the CR's own programme and batch.
    CRs can use this to manage attendance lists, group communications, etc.
    """
    if not cr.programme or not cr.batch:
        raise HTTPException(
            status_code=400,
            detail="Your account has no programme or batch assigned. Contact admin."
        )

    students = (
        db.query(User)
        .filter(
            User.role == RoleEnum.student,
            User.programme == cr.programme,
            User.batch == cr.batch,
            User.is_active == True,
        )
        .order_by(User.full_name)
        .all()
    )
    return [UserOut.model_validate(s) for s in students]
