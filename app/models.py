"""
USHSS Database Models
(adapted from app/modles.py — fixed typo in filename, replaced flask_sqlalchemy
 with plain SQLAlchemy declarative_base so it works with FastAPI)
"""

import enum
from datetime import datetime, timezone

import bcrypt
from sqlalchemy import (
    Boolean, Column, DateTime, Date, Enum as SAEnum,
    ForeignKey, Integer, String, Text, UniqueConstraint
)
from sqlalchemy.orm import relationship, declarative_base

Base = declarative_base()


# ── Enums ──────────────────────────────────────────────────────────────────────

class RoleEnum(str, enum.Enum):
    student = "student"
    faculty = "faculty"
    cr      = "cr"
    admin   = "admin"


# ── User ───────────────────────────────────────────────────────────────────────

class User(Base):
    __tablename__ = "users"

    id            = Column(Integer, primary_key=True, index=True)
    username      = Column(String(80),  nullable=False)
    role          = Column(SAEnum(RoleEnum), nullable=False)
    full_name     = Column(String(150), nullable=False)
    email         = Column(String(150), nullable=False, unique=True, index=True)
    password_hash = Column(String(256), nullable=False)
    is_active     = Column(Boolean, default=True, nullable=False)
    created_at    = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    last_login    = Column(DateTime, nullable=True)

    # Role-specific optional fields (mirrors old modles.py)
    phone         = Column(String(30),  nullable=True)
    enrollment_no = Column(String(30),  nullable=True)   # students / CR
    department    = Column(String(80),  nullable=True)
    programme     = Column(String(80),  nullable=True)   # e.g. M.A. English
    batch         = Column(String(20),  nullable=True)   # e.g. 2024-26
    designation   = Column(String(100), nullable=True)   # faculty / admin

    audit_logs = relationship("AuditLog", back_populates="user", cascade="all, delete-orphan")

    __table_args__ = (
        UniqueConstraint("username", "role", name="uq_username_role"),
    )

    # kept from old modles.py
    def set_password(self, password: str):
        salt = bcrypt.gensalt()
        self.password_hash = bcrypt.hashpw(password.encode(), salt).decode()

    def check_password(self, password: str) -> bool:
        return bcrypt.checkpw(password.encode(), self.password_hash.encode())

    def to_dict(self):
        return {
            "id":            self.id,
            "username":      self.username,
            "role":          self.role.value if hasattr(self.role, "value") else self.role,
            "full_name":     self.full_name,
            "email":         self.email,
            "is_active":     self.is_active,
            "phone":         self.phone,
            "designation":   self.designation,
            "department":    self.department,
            "programme":     self.programme,
            "batch":         self.batch,
            "enrollment_no": self.enrollment_no,
            "last_login":    self.last_login.isoformat() if self.last_login else None,
            "created_at":    self.created_at.isoformat() if self.created_at else None,
        }

    def __repr__(self):
        return f"<User {self.username!r} [{self.role}]>"


# ── AuditLog ───────────────────────────────────────────────────────────────────

class AuditLog(Base):
    __tablename__ = "audit_log"

    id      = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    action  = Column(String(80), nullable=False)
    detail  = Column(Text,       nullable=True)
    ip      = Column(String(45), nullable=True)
    ts      = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    user = relationship("User", back_populates="audit_logs")

    def __repr__(self):
        return f"<AuditLog {self.action!r} @ {self.ts}>"


# ── ContactMessage (from old modles.py) ────────────────────────────────────────

class ContactMessage(Base):
    __tablename__ = "contact_messages"

    id           = Column(Integer,     primary_key=True, index=True)
    first_name   = Column(String(80),  nullable=False)
    last_name    = Column(String(80),  nullable=False)
    email        = Column(String(150), nullable=False)
    subject      = Column(String(200), nullable=False)
    message      = Column(Text,        nullable=False)
    ip_address   = Column(String(45),  nullable=True)
    is_read      = Column(Boolean,     default=False)
    submitted_at = Column(DateTime,    default=lambda: datetime.now(timezone.utc))

    def to_dict(self):
        return {
            "id":           self.id,
            "first_name":   self.first_name,
            "last_name":    self.last_name,
            "email":        self.email,
            "subject":      self.subject,
            "message":      self.message,
            "is_read":      self.is_read,
            "submitted_at": self.submitted_at.isoformat() if self.submitted_at else None,
        }

    def __repr__(self):
        return f"<ContactMessage from {self.email!r}>"


# ── Faculty (from old modles.py) ───────────────────────────────────────────────

class Faculty(Base):
    __tablename__ = "faculty"

    id             = Column(Integer,     primary_key=True, index=True)
    name           = Column(String(150), nullable=False)
    designation    = Column(String(100), nullable=False)
    department     = Column(String(100), nullable=True)
    specialisation = Column(String(250), nullable=True)
    email          = Column(String(150), nullable=True)
    phone          = Column(String(30),  nullable=True)
    photo_url      = Column(String(300), nullable=True)
    initials       = Column(String(5),   nullable=True)
    bio            = Column(Text,        nullable=True)
    sort_order     = Column(Integer,     default=100)
    is_active      = Column(Boolean,     default=True)

    def to_dict(self):
        return {
            "id":             self.id,
            "name":           self.name,
            "designation":    self.designation,
            "department":     self.department,
            "specialisation": self.specialisation,
            "email":          self.email,
            "phone":          self.phone,
            "photo_url":      self.photo_url,
            "initials":       self.initials,
            "bio":            self.bio,
            "sort_order":     self.sort_order,
        }

    def __repr__(self):
        return f"<Faculty {self.name!r}>"


# ── Event (from old modles.py) ─────────────────────────────────────────────────

class Event(Base):
    __tablename__ = "events"

    id          = Column(Integer,     primary_key=True, index=True)
    name        = Column(String(250), nullable=False)
    description = Column(Text,        nullable=True)
    event_date  = Column(Date,        nullable=False)
    event_time  = Column(String(30),  nullable=True)
    venue       = Column(String(200), nullable=True)
    category    = Column(String(80),  nullable=True)
    is_featured = Column(Boolean,     default=False)
    created_at  = Column(DateTime,    default=lambda: datetime.now(timezone.utc))

    def to_dict(self):
        return {
            "id":          self.id,
            "name":        self.name,
            "description": self.description,
            "event_date":  self.event_date.isoformat() if self.event_date else None,
            "event_time":  self.event_time,
            "venue":       self.venue,
            "category":    self.category,
            "is_featured": self.is_featured,
        }

    def __repr__(self):
        return f"<Event {self.name!r} on {self.event_date}>"


# ── NewsItem (from old modles.py) ──────────────────────────────────────────────

class NewsItem(Base):
    __tablename__ = "news_items"

    id             = Column(Integer,     primary_key=True, index=True)
    title          = Column(String(300), nullable=False)
    excerpt        = Column(Text,        nullable=True)
    body           = Column(Text,        nullable=True)
    tag            = Column(String(80),  nullable=True)
    image_url      = Column(String(300), nullable=True)
    published      = Column(Boolean,     default=True)
    is_featured    = Column(Boolean,     default=False)
    published_date = Column(Date,        nullable=False,
                            default=lambda: datetime.now(timezone.utc).date())
    venue          = Column(String(200), nullable=True)
    created_at     = Column(DateTime,    default=lambda: datetime.now(timezone.utc))

    def to_dict(self):
        return {
            "id":             self.id,
            "title":          self.title,
            "excerpt":        self.excerpt,
            "tag":            self.tag,
            "image_url":      self.image_url,
            "is_featured":    self.is_featured,
            "published_date": self.published_date.isoformat() if self.published_date else None,
            "venue":          self.venue,
        }

    def __repr__(self):
        return f"<NewsItem {self.title!r}>"
