<<<<<<< HEAD
import os
from supabase import create_client, Client
from dotenv import load_dotenv

load_dotenv()

SUPABASE_URL = (
=======
>>>>>>> 4a8f0d8a863f8f212dbd131f7f9cd73e162ce3fb
    os.getenv("SUPABASE_URL")
    or os.getenv("NEXT_PUBLIC_SUPABASE_URL")
    or ""
)
SUPABASE_KEY = (
    os.getenv("SUPABASE_SERVICE_ROLE_KEY")
    or os.getenv("NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY")
    or ""
)

if not SUPABASE_URL or not SUPABASE_KEY:
    raise RuntimeError(
        "Missing Supabase config. Set SUPABASE_URL and "
        "NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY on Render."
    )

sb: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

def ping_db() -> bool:
    try:
        sb.table("users").select("id").limit(1).execute()
        return True
    except Exception as e:
        print(f"DB ping failed: {e}")
        return False
