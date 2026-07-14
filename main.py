"""
main.py
───────
USHSS Portal — FastAPI entry point.

All SQLAlchemy references removed.
Database → Supabase (via supabase-py HTTP client, IPv4 safe on Render free tier).

Run locally:
    uvicorn main:app --reload --host 0.0.0.0 --port 8000

Render starts it via Procfile:
    web: uvicorn main:app --host 0.0.0.0 --port $PORT
"""

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.database import ping_db
from routers import auth, admin, student, faculty, cr, password_reset


@asynccontextmanager
async def lifespan(app: FastAPI):
    print("\n🏛  USHSS Backend starting up…")
    if ping_db():
        print("✓  Supabase connection OK")
    else:
        print("✗  WARNING: Cannot reach Supabase — check env vars on Render")
    print("✓  No SQLAlchemy — tables managed via Supabase SQL Editor")
    print("✓  API docs → /docs\n")
    yield
    print("\n🏛  USHSS Backend shutting down…")


app = FastAPI(
    title="USHSS Portal API",
    description=(
        "Backend for USHSS (GGSIPU) portal.\n\n"
        "**Database**: Supabase (PostgreSQL via supabase-py)\n\n"
        "**Permissions**:\n"
        "- `admin` — full rights over all data\n"
        "- `faculty` — host attendance sessions, view students\n"
        "- `student` / `cr` — view timetable, mark attendance\n\n"
        "All protected routes require `Authorization: Bearer <token>` from `POST /api/login`."
    ),
    version="3.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

CORS_ORIGINS = os.environ.get("CORS_ORIGINS", "*").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(GZipMiddleware, minimum_size=1000)

app.include_router(auth.router,           prefix="/api")
app.include_router(admin.router,          prefix="/api")
app.include_router(student.router,        prefix="/api")
app.include_router(faculty.router,        prefix="/api")
app.include_router(cr.router,             prefix="/api")
app.include_router(password_reset.router, prefix="/api")

@app.api_route("/", methods=["GET", "HEAD"], include_in_schema=False)
def home(request: Request):
    return templates.TemplateResponse(request, "ushss_website.html")

@app.api_route("/dashboard/admin", methods=["GET", "HEAD"], include_in_schema=False)
def admin_dashboard(request: Request):
    return templates.TemplateResponse(request, "admin.html")

@app.api_route("/dashboard/student", methods=["GET", "HEAD"], include_in_schema=False)
def student_dashboard(request: Request):
    return templates.TemplateResponse(request, "ushss-student-portal.html")

@app.api_route("/dashboard/faculty", methods=["GET", "HEAD"], include_in_schema=False)
def faculty_dashboard(request: Request):
    return templates.TemplateResponse(request, "ushss-faculty-portal.html")

@app.api_route("/dashboard/cr", methods=["GET", "HEAD"], include_in_schema=False)
def cr_dashboard(request: Request):
    return templates.TemplateResponse(request, "ushss-cr-portal.html")

from fastapi.responses import FileResponse

@app.get("/googleasasasomething.html", include_in_schema=False)
def google_verification():
    return FileResponse("googleasasasomething.html")

@app.get("/health", tags=["System"])
def health():
    ok = ping_db()
    return {"status": "ok" if ok else "degraded", "database": "supabase", "connected": ok}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host=os.environ.get("HOST", "0.0.0.0"),
        port=int(os.environ.get("PORT", 8000)),
        reload=os.environ.get("DEBUG", "false").lower() == "true",
        log_level="info",
    )
