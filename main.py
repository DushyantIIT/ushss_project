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
from app.seed import seed
from routers import auth, admin, student, faculty, cr, password_reset, public


@asynccontextmanager
async def lifespan(app: FastAPI):
    print("\n🏛  USHSS Backend starting up…")
    from app.database import USE_SQLITE_FALLBACK
    if USE_SQLITE_FALLBACK:
        # Local dev mode — seed demo accounts into SQLite (safe to call multiple times)
        try:
            from app.database import get_sqlite_conn
            conn = get_sqlite_conn()
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM users")
            cnt = cur.fetchone()[0]
            conn.close()
            if cnt == 0:
                print("🌱 Empty local DB — seeding demo data…")
                seed()
            else:
                print(f"✓  Local SQLite DB has {cnt} user(s) — skipping seed")
        except Exception as e:
            print("Startup seed notice:", e)
        print("✓  Running in LOCAL SQLite fallback mode")
    else:
        if ping_db():
            print("✓  Supabase connection OK")
        else:
            print("✗  WARNING: Cannot reach Supabase — check env vars on Render")
    print("✓  API docs → /docs\n")
    yield
    print("\n🏛  USHSS Backend shutting down…")


_IS_PROD = bool(os.environ.get("RENDER") or os.environ.get("RENDER_SERVICE_ID") or os.environ.get("ENVIRONMENT") == "production")
# Disable interactive API docs in production — they leak the full schema
_docs_url   = None if _IS_PROD else "/docs"
_redoc_url  = None if _IS_PROD else "/redoc"
_openapi_url= None if _IS_PROD else "/openapi.json"

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
    docs_url=_docs_url,
    redoc_url=_redoc_url,
    openapi_url=_openapi_url,
    lifespan=lifespan,
)

app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

CORS_ORIGINS = [o.strip() for o in os.environ.get("CORS_ORIGINS", "").split(",") if o.strip()]
# Same-origin deployment needs no CORS headers. If origins are configured,
# credentials are allowed only for those explicit origins (never "*").
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS or [],
    allow_credentials=bool(CORS_ORIGINS),
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(GZipMiddleware, minimum_size=1000)

@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "geolocation=(self)"
    if _IS_PROD:
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "font-src 'self' https://fonts.gstatic.com data:; "
        "img-src 'self' data: https: blob:; "
        "connect-src 'self' https:; "
        "frame-ancestors 'self'; "
        "base-uri 'self'; "
        "form-action 'self';"
    )
    return response

app.include_router(public.router,         prefix="/api")
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

@app.api_route("/dashboard/admin/pending-requests", methods=["GET", "HEAD"], include_in_schema=False)
def admin_pending_requests_page(request: Request):
    return templates.TemplateResponse(request, "admin_pending_requests.html")

@app.api_route("/dashboard/student", methods=["GET", "HEAD"], include_in_schema=False)
def student_dashboard(request: Request):
    return templates.TemplateResponse(request, "ushss-student-portal.html")

@app.api_route("/dashboard/faculty", methods=["GET", "HEAD"], include_in_schema=False)
def faculty_dashboard(request: Request):
    return templates.TemplateResponse(request, "ushss-faculty-portal.html")

@app.api_route("/dashboard/cr", methods=["GET", "HEAD"], include_in_schema=False)
def cr_dashboard(request: Request):
    return templates.TemplateResponse(request, "ushss-cr-portal.html")

@app.api_route("/waiting", methods=["GET", "HEAD"], include_in_schema=False)
def waiting_page(request: Request):
    return templates.TemplateResponse(request, "waiting.html")

@app.api_route("/rejected", methods=["GET", "HEAD"], include_in_schema=False)
def rejected_page(request: Request):
    return templates.TemplateResponse(request, "rejected.html")

from fastapi.responses import FileResponse, PlainTextResponse

@app.get("/robots.txt", include_in_schema=False)
def robots_txt():
    content = "User-agent: *\nAllow: /\nDisallow: /dashboard/\nDisallow: /api/\n"
    return PlainTextResponse(content, media_type="text/plain")

@app.get("/googleda3d4b79bd268fbe.html", include_in_schema=False)
def google_verification():
    vpath = os.path.join(os.path.dirname(__file__), "static", "googleda3d4b79bd268fbe.html")
    return FileResponse(vpath)

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
