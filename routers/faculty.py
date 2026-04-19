"""
app/routers/faculty.py
──────────────────────
Self-service endpoints for faculty members.

  GET  /api/faculty/profile          — view own profile
  PUT  /api/faculty/profile          — update own details
  POST /api/faculty/change-password  — change own password
  GET  /api/faculty/students         — view students in own department
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

router = APIRouter(prefix="/faculty", tags=["Faculty"])


def _require_faculty(current_user: User = Depends(get_current_user)) -> User:
    if current_user.role not in (RoleEnum.faculty, RoleEnum.admin):
        raise HTTPException(status_code=403, detail="Faculty only")
    return current_user


class FacultyProfileUpdate(BaseModel):
    phone: Optional[str]       = None
    email: Optional[EmailStr]  = None
    designation: Optional[str] = None
    model_config = {"str_strip_whitespace": True}


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(..., min_length=1)
    new_password: str     = Field(..., min_length=6)
    confirm_password: str = Field(..., min_length=6)


@router.get("/profile", response_model=UserOut, summary="View your faculty profile")
def get_profile(faculty: User = Depends(_require_faculty)):
    return faculty


@router.put("/profile", response_model=MessageOut, summary="Update your profile")
def update_profile(
    body: FacultyProfileUpdate,
    db: Session = Depends(get_db),
    faculty: User = Depends(_require_faculty),
):
    if body.email and body.email != faculty.email:
        if db.query(User).filter(User.email == body.email, User.id != faculty.id).first():
            raise HTTPException(status_code=409, detail="Email already in use")
        faculty.email = body.email
    if body.phone is not None:
        faculty.phone = body.phone
    if body.designation is not None:
        faculty.designation = body.designation

    db.add(AuditLog(user_id=faculty.id, action="FACULTY_PROFILE_UPDATE",
                    detail=f"{faculty.username} updated profile"))
    db.commit()
    return MessageOut(message="Profile updated successfully")


@router.post("/change-password", response_model=MessageOut, summary="Change your password")
def change_password(
    body: ChangePasswordRequest,
    db: Session = Depends(get_db),
    faculty: User = Depends(_require_faculty),
):
    if not verify_password(body.current_password, faculty.password_hash):
        raise HTTPException(status_code=400, detail="Current password is incorrect")
    if body.new_password != body.confirm_password:
        raise HTTPException(status_code=400, detail="Passwords do not match")
    if body.new_password == body.current_password:
        raise HTTPException(status_code=400, detail="New password must differ from current")

    faculty.password_hash = hash_password(body.new_password)
    db.add(AuditLog(user_id=faculty.id, action="PASSWORD_CHANGE",
                    detail=f"Faculty {faculty.username} changed password"))
    db.commit()
    return MessageOut(message="Password changed successfully")


@router.get(
    "/students",
    response_model=List[UserOut],
    summary="View students in your department",
)
def view_department_students(
    programme: Optional[str] = None,
    batch: Optional[str]     = None,
    db: Session              = Depends(get_db),
    faculty: User            = Depends(_require_faculty),
):
    """
    Returns students belonging to the same department as the logged-in faculty.
    Optionally filter by programme or batch year.
    """
    q = db.query(User).filter(
        User.role == RoleEnum.student,
        User.is_active == True,
    )
    if faculty.department:
        q = q.filter(User.department == faculty.department)
    if programme:
        q = q.filter(User.programme.ilike(f"%{programme}%"))
    if batch:
        q = q.filter(User.batch == batch)

    return [UserOut.model_validate(u) for u in q.order_by(User.full_name).all()]
