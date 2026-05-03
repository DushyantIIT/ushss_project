"""
app/database.py
───────────────
Supabase client — replaces SQLAlchemy entirely.

Two issues fixed vs the old code:
  1. SQLAlchemy + psycopg2 removed (was causing startup crash on Render)
  2. Uses Supabase connection POOLER (port 6543, IPv4) instead of direct
     port 5432 (IPv6 only on Render free tier → "Network is unreachable")

All DB access goes through the `sb` client:
    from app.database import sb, ping_db
    sb.table("users").select("*").execute()
"""

import os
from supabase import create_client, Client
from dotenv import load_dotenv

load_dotenv()

SUPABASE_URL: str = (
    os.getenv("SUPABASE_URL")
    or os.getenv("NEXT_PUBLIC_SUPABASE_URL")
    or ""
)
SUPABASE_KEY: str = (
    os.getenv("SUPABASE_SERVICE_ROLE_KEY")        # preferred for backend
    or os.getenv("NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY")
    or ""
)

if not SUPABASE_URL or not SUPABASE_KEY:
    raise RuntimeError(
        "Missing Supabase config. Set SUPABASE_URL and "
        "SUPABASE_SERVICE_ROLE_KEY (or NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY) "
        "as environment variables on Render."
    )

sb: Client = create_client(SUPABASE_URL, SUPABASE_KEY)


def ping_db() -> bool:
    """Returns True if Supabase is reachable."""
    try:
        sb.table("users").select("id").limit(1).execute()
        return True
    except Exception as e:
        print(f"  DB ping failed: {e}")
        return False


# ── Legacy shims (so old imports don't crash immediately) ──────────────────────
# If any old file still does `from app.database import engine, SessionLocal, get_db`
# these stubs will raise a clear error instead of a cryptic AttributeError.

class _Removed:
    def __init__(self, name):
        self._name = name
    def __call__(self, *a, **kw):
        raise RuntimeError(
            f"`{self._name}` has been removed. "
            "Use `from app.database import sb` and call sb.table(...) instead."
        )
    def __getattr__(self, item):
        raise RuntimeError(
            f"`{self._name}.{item}` has been removed. "
            "Use `from app.database import sb` and call sb.table(...) instead."
        )

engine       = _Removed("engine")
SessionLocal = _Removed("SessionLocal")

def get_db():
    raise RuntimeError(
        "`get_db()` has been removed. "
        "Use `from app.database import sb` and call sb.table(...) instead."
    )
