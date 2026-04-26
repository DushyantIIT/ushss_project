"""
app/database.py
───────────────
SQLAlchemy session setup — Supabase PostgreSQL (psycopg2).

Required env vars (set in .env):
  DATABASE_URL  — full postgresql+psycopg2://... connection string  (preferred)
  OR
  SUPABASE_URL + SUPABASE_DB_PASSWORD  — auto-builds the connection string

Connection string format:
  postgresql+psycopg2://postgres:<password>@db.<project-ref>.supabase.co:5432/postgres
"""

import os
from urllib.parse import quote_plus, urlparse

from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

load_dotenv()

# ── Resolve DATABASE_URL ───────────────────────────────────────────────────────

def _build_database_url() -> str:
    """Build connection URL from Supabase env vars if DATABASE_URL is not set."""
    explicit = os.getenv("DATABASE_URL")
    if explicit:
        return explicit

    supabase_url = os.getenv("SUPABASE_URL") or os.getenv("NEXT_PUBLIC_SUPABASE_URL", "")
    db_password  = os.getenv("SUPABASE_DB_PASSWORD", "")

    if not supabase_url or not db_password:
        raise RuntimeError(
            "Database not configured. Set DATABASE_URL or "
            "SUPABASE_URL + SUPABASE_DB_PASSWORD in your .env file."
        )

    host = urlparse(supabase_url).netloc  # e.g. jskzssdwgzxvpzfurxos.supabase.co
    project_ref = host.split(".")[0]      # e.g. jskzssdwgzxvpzfurxos
    encoded_pw  = quote_plus(db_password)

    return (
        f"postgresql+psycopg2://postgres:{encoded_pw}"
        f"@db.{project_ref}.supabase.co:5432/postgres"
    )


DATABASE_URL = _build_database_url()

# ── Engine ─────────────────────────────────────────────────────────────────────
# Supabase free-tier idles connections — pool_pre_ping keeps them alive.

engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,       # detects stale connections before use
    pool_size=5,              # keep 5 persistent connections
    max_overflow=10,          # allow up to 10 extra connections under load
    pool_recycle=300,         # recycle connections every 5 min (Supabase timeout safe)
    echo=os.getenv("DEBUG", "false").lower() == "true",  # log SQL in debug mode
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


# ── FastAPI dependency ─────────────────────────────────────────────────────────

def get_db():
    """Yield a DB session and close it when the request is done."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ── Health check helper ────────────────────────────────────────────────────────

def ping_db() -> bool:
    """Returns True if the database connection is healthy."""
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False
