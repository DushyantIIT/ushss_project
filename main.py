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
from pathlib import Path
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.database import ping_db
from app.seed import seed
from routers import auth, admin, student, faculty, cr, password_reset, public, chat

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
TEMPLATES_DIR = BASE_DIR / "templates"


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


_IS_PROD = bool(
    os.environ.get("VERCEL")
    or os.environ.get("RENDER")
    or os.environ.get("RENDER_SERVICE_ID")
    or os.environ.get("ENVIRONMENT") == "production"
)
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

if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

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

# ── Vercel Path Rewriter Middleware ────────────────────────────
# When Vercel rewrites requests to /api/index.py, scope['path'] can receive
# the physical script name ('/api/index.py' or '/api/index') or pass the original
# path in headers such as 'x-matched-path' or 'x-forwarded-uri'.
# This middleware restores the user's actual requested path so FastAPI routes match.
@app.middleware("http")
async def vercel_path_correction(request: Request, call_next):
    # Check headers provided by Vercel's proxy
    matched_path = (
        request.headers.get("x-matched-path")
        or request.headers.get("x-invoke-path")
        or request.headers.get("x-forwarded-uri")
    )
    if matched_path:
        # Strip query string if present
        clean_path = matched_path.split("?")[0]
        request.scope["path"] = clean_path
    else:
        # Fallback: if scope['path'] was rewritten to /api/index.py or /api/index
        current_path = request.scope.get("path", "")
        if current_path.startswith("/api/index.py"):
            rest = current_path[len("/api/index.py"):]
            request.scope["path"] = rest if rest.startswith("/") else f"/{rest}" if rest else "/"
        elif current_path.startswith("/api/index"):
            rest = current_path[len("/api/index"):]
            request.scope["path"] = rest if rest.startswith("/") else f"/{rest}" if rest else "/"
        elif current_path == "/api" or current_path == "/api/":
            request.scope["path"] = "/"

    return await call_next(request)


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
app.include_router(chat.router,           prefix="/api")

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

@app.api_route("/dashboard/chat", methods=["GET", "HEAD"], include_in_schema=False)
def chat_dashboard(request: Request):
    return templates.TemplateResponse(request, "chat.html")

@app.api_route("/dashboard/admin/chats", methods=["GET", "HEAD"], include_in_schema=False)
def admin_chat_management_page(request: Request):
    return templates.TemplateResponse(request, "admin_chats.html")

@app.api_route("/waiting", methods=["GET", "HEAD"], include_in_schema=False)
def waiting_page(request: Request):
    return templates.TemplateResponse(request, "waiting.html")

@app.api_route("/rejected", methods=["GET", "HEAD"], include_in_schema=False)
def rejected_page(request: Request):
    return templates.TemplateResponse(request, "rejected.html")

from fastapi.responses import FileResponse, PlainTextResponse

@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    # Serve a small inline SVG so browsers never request a missing static file.
    from fastapi.responses import Response
    svg = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><rect width="64" height="64" rx="12" fill="#17365D"/><path d="M16 15h32v5H21v10h23v5H21v14h-5z" fill="#fff"/><path d="M28 35h20v5H28z" fill="#D7B56D"/></svg>"""
    return Response(content=svg, media_type="image/svg+xml", headers={"Cache-Control": "public, max-age=86400"})

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
