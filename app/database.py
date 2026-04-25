"""
app/database.py
───────────────
SQLAlchemy engine + session factory + Base for USHSS.
"""

import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# Use DATABASE_URL env var for production (PostgreSQL), fall back to local SQLite
DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://postgres.jskzssdwgzxvpzfurxos:@Admin49841234@aws-1-ap-northeast-1.pooler.supabase.com:6543/postgres")

# SQLite-specific connect_args; ignored for other dialects
connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}

engine = create_engine(DATABASE_URL, connect_args=connect_args, echo=False)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db():
    """FastAPI dependency — yields a DB session, closed on teardown."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
