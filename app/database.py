"""
app/database.py
───────────────
SQLAlchemy session setup with Supabase-compatible PostgreSQL configuration.
"""

import os
import warnings
from typing import Optional
from urllib.parse import quote_plus, urlparse

from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

load_dotenv()

# `NEXT_PUBLIC_SUPABASE_URL` fallback keeps compatibility with existing deployments.
SUPABASE_URL = os.getenv("SUPABASE_URL") or os.getenv("NEXT_PUBLIC_SUPABASE_URL")


def _project_ref_from_supabase_url(url: str) -> Optional[str]:
    if not url:
        return None
    host = urlparse(url).netloc
    if not host or not host.endswith(".supabase.co"):
        warnings.warn("SUPABASE_URL is set but is not a valid Supabase project URL.")
        return None
    return host.split(".")[0]


def _build_supabase_database_url() -> Optional[str]:
    project_ref = _project_ref_from_supabase_url(SUPABASE_URL or "")
    db_password = os.getenv("SUPABASE_DB_PASSWORD")
    if not project_ref or not db_password:
        if os.getenv("DATABASE_URL") is None and SUPABASE_URL and not db_password:
            warnings.warn(
                "SUPABASE_DB_PASSWORD not set; falling back to sqlite unless DATABASE_URL is provided."
            )
        return None
    encoded_password = quote_plus(db_password)
    return (
        f"postgresql+psycopg2://postgres:@Admin49841234"
        f"@db.jskzssdwgzxvpzfurxos.supabase.co:5432/postgres"
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
