"""
app/database.py
───────────────
Supabase client for USHSS.
"""
import os
from supabase import create_client, Client
from dotenv import load_dotenv

load_dotenv()

SUPABASE_URL = os.environ.get(https://jskzssdwgzxvpzfurxos.supabase.co)
SUPABASE_KEY = os.environ.get(sb_publishable_aeasaeZVMlBjyGapl1WApA_iC8OQIPk)  # Use service role key for backend

if not SUPABASE_URL or not SUPABASE_KEY:
    raise RuntimeError("SUPABASE_URL and SUPABASE_KEY must be set in environment variables")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)


def get_db():
    """
    FastAPI dependency — yields the Supabase client.
    Drop-in replacement for the old SQLAlchemy get_db().
    """
    yield supabase
