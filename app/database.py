"""
app/database.py
───────────────
SQLAlchemy session setup with Supabase-compatible PostgreSQL configuration.
"""

import os
from typing import Optional
from urllib.parse import quote_plus, urlparse

from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

load_dotenv()

DEFAULT_SUPABASE_URL = "https://jskzssdwgzxvpzfurxos.supabase.co"
DEFAULT_SUPABASE_PUBLISHABLE_KEY = (
    "sb_publishable_aeasaeZVMlBjyGapl1WApA_iC8OQIPk"
)

SUPABASE_URL = os.getenv("SUPABASE_URL") or os.getenv(
    "NEXT_PUBLIC_SUPABASE_URL", DEFAULT_SUPABASE_URL
)
SUPABASE_KEY = (
    os.getenv("SUPABASE_SERVICE_ROLE_KEY")
    or os.getenv("SUPABASE_KEY")
    or os.getenv("NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY", DEFAULT_SUPABASE_PUBLISHABLE_KEY)
)


def _project_ref_from_supabase_url(url: str) -> Optional[str]:
    try:
        host = urlparse(url).netloc
    except Exception:
        return None
    if not host:
        return None
    return host.split(".")[0]


def _build_supabase_database_url() -> Optional[str]:
    project_ref = _project_ref_from_supabase_url(SUPABASE_URL or "")
    db_password = os.getenv("SUPABASE_DB_PASSWORD")
    if not project_ref or not db_password:
        return None
    encoded_password = quote_plus(db_password)
    return (
        f"postgresql+psycopg2://postgres:{encoded_password}"
        f"@db.{project_ref}.supabase.co:5432/postgres"
    )


DATABASE_URL = (
    os.getenv("DATABASE_URL")
    or _build_supabase_database_url()
    or "sqlite:///./ushss.db"
)

engine_kwargs = {}
if DATABASE_URL.startswith("sqlite"):
    engine_kwargs["connect_args"] = {"check_same_thread": False}

engine = create_engine(DATABASE_URL, **engine_kwargs)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
