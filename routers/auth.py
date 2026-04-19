"""
routers/auth.py
───────────────
Authentication endpoints.

  POST /api/login  — username + password + role → JWT token
  GET  /api/me     — current user profile (requires Bearer token)
  POST /api/logout — informational (JWT is stateless; client discards token)
"""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import get_current_user
from app.models import AuditLog, RoleEnum, User
from app.schemas import LoginRequest, LoginResponse, MessageOut, UserOut
from app.security import create_access_token, verify_password

router = APIRouter(tags=["Authentication"])

REDIRECT_MAP = {
    RoleEnum.student: "/dashboard/student",
    RoleEnum.faculty: "/dashboard/faculty",
    RoleEnum.cr:      "/dashboard/cr",
    RoleEnum.admin:   "/dashboard/admin",
}


@router.post("/login", response_model=LoginResponse, summary="Authenticate and receive JWT")
def login(body: LoginRequest, request: Request, db: Session = Depends(get_db)):
    # Validate role enum
    try:
        role = RoleEnum(body.role.lower())
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid role: {body.role}")

    # Look up user
    user = db.query(User).filter(
        User.username == body.username,
        User.role     == role,
    ).first()

    if not user or not verify_password(body.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid credentials. Please try again.")

    if not user.is_active:
        raise HTTPException(status_code=403, detail="Account is inactive. Contact admin.")

    # Update last_login
    user.last_login = datetime.now(timezone.utc)

    # Audit log
    db.add(AuditLog(
        user_id = user.id,
        action  = "LOGIN",
        detail  = f"{user.role.value} {user.username} logged in",
        ip      = request.client.host if request.client else None,
    ))
    db.commit()
    db.refresh(user)

    token = create_access_token({"id": user.id, "role": user.role.value, "sub": user.username})

    return LoginResponse(
        success      = True,
        token        = token,
        redirect_url = REDIRECT_MAP[role],
        user         = UserOut.model_validate(user),
    )


@router.get("/me", response_model=UserOut, summary="Get current user profile")
def me(current_user: User = Depends(get_current_user)):
    return UserOut.model_validate(current_user)


@router.post("/logout", response_model=MessageOut, summary="Logout (client must discard token)")
def logout(current_user: User = Depends(get_current_user)):
    return MessageOut(message="Logged out. Discard your token on the client side.")
