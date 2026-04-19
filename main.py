"""
main.py
───────
FastAPI application — entry point for the USHSS portal.
(Based on files/main.py — removed app.core.* references, added Jinja2 template serving)

Run (development):
    uvicorn main:app --reload --host 0.0.0.0 --port 8000

Run (production):
    uvicorn main:app --host 0.0.0.0 --port 8000 --workers 4
"""

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.database import engine, SessionLocal
from app.models import Base
from app.seed import seed
from routers import auth, admin, student, faculty, cr, password_reset


# ── Lifespan (startup / shutdown) ─────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    print("\n🏛  USHSS Backend starting up…")

    # Create all tables (idempotent)
    Base.metadata.create_all(bind=engine)
    print("✓  Database tables ready")

    # Auto-seed on first run
    db = SessionLocal()
    try:
        seed(db)
    finally:
        db.close()

    print("✓  Listening at http://0.0.0.0:8000")
    print("✓  API docs at  http://localhost:8000/docs\n")

    yield

    print("\n🏛  USHSS Backend shutting down…")


# ── App ───────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="USHSS Portal API",
    description=(
        "Backend API for the University School of Humanities & Social Sciences "
        "(GGSIPU) student/faculty/admin portal.\n\n"
        "**Authentication**: All protected routes require a Bearer JWT token "
        "obtained from `POST /api/login`."
    ),
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)


# ── Static files & Templates ──────────────────────────────────────────────────

app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")


# ── Middleware ────────────────────────────────────────────────────────────────

CORS_ORIGINS = os.environ.get("CORS_ORIGINS", "*").split(",")

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(GZipMiddleware, minimum_size=1000)


# ── API Routers ───────────────────────────────────────────────────────────────

app.include_router(auth.router,           prefix="/api")
app.include_router(admin.router,          prefix="/api")
app.include_router(student.router,        prefix="/api")
app.include_router(faculty.router,        prefix="/api")
app.include_router(cr.router,             prefix="/api")
app.include_router(password_reset.router, prefix="/api")


# ── Frontend routes (serve HTML templates) ────────────────────────────────────

@app.get("/", include_in_schema=False)
def home(request: Request):
    return templates.TemplateResponse(request, "ushss_website.html")


@app.get("/dashboard/admin", include_in_schema=False)
def admin_dashboard(request: Request):
    return templates.TemplateResponse(request, "admin.html")


@app.get("/dashboard/student", include_in_schema=False)
def student_dashboard(request: Request):
    return templates.TemplateResponse(request, "ushss-student-portal.html")


@app.get("/dashboard/faculty", include_in_schema=False)
def faculty_dashboard(request: Request):
    return templates.TemplateResponse(request, "ushss-faculty-portal.html")


@app.get("/dashboard/cr", include_in_schema=False)
def cr_dashboard(request: Request):
    return templates.TemplateResponse(request, "ushss-cr-portal.html")


# ── Health check ──────────────────────────────────────────────────────────────

@app.get("/health", tags=["System"])
def health():
    """Quick health-check endpoint for load balancers / uptime monitors."""
    return {"status": "ok"}


# ── Dev entrypoint ────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host=os.environ.get("HOST", "0.0.0.0"),
        port=int(os.environ.get("PORT", 8000)),
        reload=os.environ.get("DEBUG", "true").lower() == "true",
        log_level="info",
    )
