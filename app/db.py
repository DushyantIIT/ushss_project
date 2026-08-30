"""
app/db.py
─────────
Re-exports Supabase client from app.database to ensure consistent single-instance database access.
"""

from app.database import sb, ping_db

__all__ = ["sb", "ping_db"]
