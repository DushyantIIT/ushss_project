"""
app/routers/student.py
──────────────────────
Self-service endpoints for students.

  GET  /api/student/profile          — view own profile
  PUT  /api/student/profile          — update own contact details
  POST /api/student/change-password  — change own password
"""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from pydantic import BaseModel, Field, EmailStr
from typing import Optional

from app.database import get_db
from app.deps import get_current_user
from app.security import verify_password, hash_password
from app.models import User, AuditLog, RoleEnum
from app.schemas import UserOut, MessageOut

router = APIRouter(prefix="/student", tags=["Student"])


def _require_student(current_user: User = Depends(get_current_user)) -> User:
    if current_user.role not in (RoleEnum.student, RoleEnum.cr):
        raise HTTPException(status_code=403, detail="Students only")
    return current_user


class StudentProfileUpdate(BaseModel):
    phone: Optional[str] = None
    email: Optional[EmailStr] = None
    model_config = {"str_strip_whitespace": True}


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(..., min_length=1)
    new_password: str     = Field(..., min_length=6)
    confirm_password: str = Field(..., min_length=6)


@router.get("/profile", response_model=UserOut, summary="View your profile")
def get_profile(student: User = Depends(_require_student)):
    return student


@router.put("/profile", response_model=MessageOut, summary="Update contact details")
def update_profile(
    body: StudentProfileUpdate,
    db: Session = Depends(get_db),
    student: User = Depends(_require_student),
):
    if body.email and body.email != student.email:
        if db.query(User).filter(User.email == body.email, User.id != student.id).first():
            raise HTTPException(status_code=409, detail="Email already in use")
        student.email = body.email
    if body.phone is not None:
        student.phone = body.phone

    db.add(AuditLog(user_id=student.id, action="STUDENT_PROFILE_UPDATE",
                    detail=f"{student.username} updated their profile"))
    db.commit()
    return MessageOut(message="Profile updated successfully")


@router.post("/change-password", response_model=MessageOut, summary="Change your password")
def change_password(
    body: ChangePasswordRequest,
    db: Session = Depends(get_db),
    student: User = Depends(_require_student),
):
    if not verify_password(body.current_password, student.password_hash):
        raise HTTPException(status_code=400, detail="Current password is incorrect")
    if body.new_password != body.confirm_password:
        raise HTTPException(status_code=400, detail="New passwords do not match")
    if body.new_password == body.current_password:
        raise HTTPException(status_code=400, detail="New password must differ from current")

    student.password_hash = hash_password(body.new_password)
    db.add(AuditLog(user_id=student.id, action="PASSWORD_CHANGE",
                    detail=f"{student.username} changed their password"))
    db.commit()
    return MessageOut(message="Password changed successfully")
