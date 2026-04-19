"""
app/schemas.py
──────────────
Pydantic v2 request / response models for USHSS.
"""

from datetime import datetime, date
from typing import Optional
from pydantic import BaseModel, EmailStr, Field


# ── Generic ───────────────────────────────────────────────────────────────────

class MessageOut(BaseModel):
    message: str


# ── User ──────────────────────────────────────────────────────────────────────

class UserOut(BaseModel):
    id:            int
    username:      str
    role:          str
    full_name:     str
    email:         str
    phone:         Optional[str]   = None
    designation:   Optional[str]   = None
    enrollment_no: Optional[str]   = None
    department:    Optional[str]   = None
    programme:     Optional[str]   = None
    batch:         Optional[str]   = None
    is_active:     bool
    last_login:    Optional[datetime] = None
    created_at:    Optional[datetime] = None

    model_config = {"from_attributes": True}


class LoginRequest(BaseModel):
    username: str = Field(..., min_length=1)
    password: str = Field(..., min_length=1)
    role:     str = Field(default="student")

    model_config = {"str_strip_whitespace": True}


class LoginResponse(BaseModel):
    success:      bool
    token:        str
    token_type:   str = "bearer"
    redirect_url: str
    user:         UserOut


# ── Admin user create / update ────────────────────────────────────────────────

class AdminUserCreate(BaseModel):
    username:      str          = Field(..., min_length=1)
    password:      str          = Field(..., min_length=6)
    role:          str
    full_name:     str          = Field(..., min_length=1)
    email:         EmailStr
    phone:         Optional[str] = None
    designation:   Optional[str] = None
    enrollment_no: Optional[str] = None
    department:    Optional[str] = None
    programme:     Optional[str] = None
    batch:         Optional[str] = None
    is_active:     bool          = True

    model_config = {"str_strip_whitespace": True}


class AdminUserUpdate(BaseModel):
    full_name:     Optional[str]   = None
    email:         Optional[EmailStr] = None
    phone:         Optional[str]   = None
    designation:   Optional[str]   = None
    enrollment_no: Optional[str]   = None
    department:    Optional[str]   = None
    programme:     Optional[str]   = None
    batch:         Optional[str]   = None
    is_active:     Optional[bool]  = None
    password:      Optional[str]   = Field(None, min_length=6)

    model_config = {"str_strip_whitespace": True}


# ── Password change (self-service) ────────────────────────────────────────────

class ChangePasswordRequest(BaseModel):
    current_password: str = Field(..., min_length=1)
    new_password:     str = Field(..., min_length=6)
    confirm_password: str = Field(..., min_length=6)


# ── Password reset (token-based) ──────────────────────────────────────────────

class ResetRequestBody(BaseModel):
    username: str = Field(..., min_length=1)
    model_config = {"str_strip_whitespace": True}


class ResetConfirmBody(BaseModel):
    token:            str = Field(..., min_length=1)
    new_password:     str = Field(..., min_length=6)
    confirm_password: str = Field(..., min_length=6)
