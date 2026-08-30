"""
app/seed.py
───────────
Populates Supabase with demo data and default user accounts.
Idempotent — safe to call on startup.
"""

from datetime import date, datetime, timezone
from app.database import sb, ping_db

FACULTY_DATA = [
    {"name": "Dr. Anup Singh Beniwal",   "designation": "Professor",           "initials": "ASB", "email": "anupbeniwal@ipu.ac.in",        "sort_order": 1},
    {"name": "Dr. Ashutosh Mohan",       "designation": "Professor",           "initials": "AM",  "email": "ashutosh.ushss@ipu.ac.in",      "sort_order": 2},
    {"name": "Dr. Manpreet Kaur Kang",   "designation": "Professor",           "initials": "MK",  "email": "manpreetkaurkang@ipu.ac.in",    "sort_order": 3},
    {"name": "Dr. Vivek Sachdeva",       "designation": "Professor",           "initials": "VS",  "email": "viveksachdeva@ipu.ac.in",       "sort_order": 4},
    {"name": "Dr. Shuchi Sharma",        "designation": "Dean of Department",  "initials": "SS",  "email": "shuchi.sharma@ipu.ac.in",       "sort_order": 5},
    {"name": "Dr. Chetna Tiwari",        "designation": "Associate Professor", "initials": "CT",  "email": "chetna.ushss@ipu.ac.in",        "sort_order": 6},
    {"name": "Dr. Naresh Kumar Vats",    "designation": "Associate Professor", "initials": "NV",  "email": "naresh.ushss@ipu.ac.in",        "sort_order": 7},
    {"name": "Dr. Shubhanku Kochar",     "designation": "Associate Professor", "initials": "SK",  "email": "shubhankukochar@ipu.ac.in",     "sort_order": 8},
    {"name": "Dr. Prarthna Agarwal Goel","designation": "Assistant Professor", "initials": "PG",  "email": "prarthna@ipu.ac.in",            "sort_order": 9},
    {"name": "Dr. Pooja Rathore",        "designation": "Assistant Professor", "initials": "PR",  "email": "poojarathore@ipu.ac.in",        "sort_order": 10},
    {"name": "Dr. Sami Ahmad Khan",      "designation": "Assistant Professor", "initials": "SAK", "email": "samikhan@ipu.ac.in",            "sort_order": 11},
    {"name": "Saurabh Maji",             "designation": "Assistant Professor", "initials": "SM",  "email": "saurabh.m@ipu.ac.in",           "sort_order": 12},
    {"name": "Dr. Sonika Redhu",         "designation": "Assistant Professor", "initials": "SR",  "email": "sonika.ushss@ipu.ac.in",        "sort_order": 13},
]

EVENTS_DATA = [
    {
        "name": "Guest Lecture: Indian Constitutionalism & Federalism",
        "description": "A special lecture by a former IAS officer on the evolving nature of centre-state relations in India.",
        "event_date": "2025-04-22", "event_time": "3:00 PM",
        "venue": "Seminar Hall A", "category": "lecture", "is_featured": False,
    },
    {
        "name": "National Seminar on Gender & Labour in Contemporary India",
        "description": "Two-day academic seminar with paper presentations from scholars across India's leading universities.",
        "event_date": "2025-05-05", "event_time": "10:00 AM",
        "venue": "Conference Hall, GGSIPU Campus", "category": "seminar", "is_featured": True,
    },
    {
        "name": "M.A. Admissions 2025–26 — Last Date",
        "description": "Deadline for submission of IPU CET applications for all USHSS postgraduate programmes.",
        "event_date": "2025-05-15", "event_time": None,
        "venue": "Online · ipu.admissions.nic.in", "category": "admissions", "is_featured": False,
    },
]

NEWS_DATA = [
    {
        "title": "USHSS Hosts Annual Humanities Conclave: Reimagining India's Social Contract",
        "excerpt": "Leading scholars, policymakers, and student researchers gathered for a two-day conclave exploring the future of democratic participation.",
        "tag": "Seminar", "is_featured": True,
        "published_date": "2025-03-18", "venue": "Seminar Hall, GGSIPU",
    },
    {
        "title": "M.A. Admissions 2025–26 now open via IPU CET. Last date: May 15, 2025.",
        "excerpt": "Applications for all M.A. programmes at USHSS are now being accepted through the IPU CET portal.",
        "tag": "Admissions", "is_featured": False,
        "published_date": "2025-04-14", "venue": None,
    },
]

DEMO_USERS = [
    {
        "username": "admin001",   "password": "Admin@1234",
        "role": "admin",          "full_name": "USHSS Administrator",
        "email": "admin.ushss@ipu.ac.in",
        "designation": "Administrator", "department": "Administration",
        "is_super_admin": True,
    },
    {
        "username": "fac001",     "password": "Faculty@123",
        "role": "faculty",        "full_name": "Dr. Anup Singh Beniwal",
        "email": "anupbeniwal@ipu.ac.in",
        "designation": "Professor", "department": "USHSS",
        "is_super_admin": False,
    },
    {
        "username": "fac002",     "password": "Faculty@123",
        "role": "faculty",        "full_name": "Dr. Shuchi Sharma",
        "email": "shuchi.sharma@ipu.ac.in",
        "designation": "Dean / Professor", "department": "USHSS",
        "is_super_admin": False,
    },
    {
        "username": "2301001",    "password": "Student@123",
        "role": "student",        "full_name": "John Sharma",
        "email": "john.sharma@ipu.ac.in",
        "enrollment_no": "2301001", "programme": "M.A. English",
        "batch": "2023", "department": "Humanities",
        "is_super_admin": False,
    },
    {
        "username": "2401001",    "password": "Student@123",
        "role": "student",        "full_name": "Rahul Gupta",
        "email": "rahul.gupta@ipu.ac.in",
        "enrollment_no": "2401001", "programme": "M.A. Economics",
        "batch": "2024", "department": "Social Sciences",
        "is_super_admin": False,
    },
    {
        "username": "cr2301001",  "password": "Cr@12345",
        "role": "cr",             "full_name": "Amit CR Leader",
        "email": "cr.english@ipu.ac.in",
        "enrollment_no": "cr2301001", "programme": "M.A. English",
        "batch": "2023", "department": "Humanities",
        "designation": "Class Representative",
        "is_super_admin": False,
    },
]


def seed():
    """Seed demo data into Supabase if not already present."""
    if not ping_db():
        print("SEED WARNING: Cannot connect to Supabase — skipping seed")
        return

    print("🌱 Checking / Seeding Supabase data...")

    # 1. Faculty directory
    try:
        f_count = sb.table("faculty_directory").select("id").limit(1).execute()
        if not f_count.data:
            for f in FACULTY_DATA:
                sb.table("faculty_directory").insert(f).execute()
            print(f"  ✓ Seeded {len(FACULTY_DATA)} faculty directory entries")
    except Exception as e:
        print("  · Faculty directory seed note:", e)

    # 2. Events
    try:
        e_count = sb.table("events").select("id").limit(1).execute()
        if not e_count.data:
            for ev in EVENTS_DATA:
                sb.table("events").insert(ev).execute()
            print(f"  ✓ Seeded {len(EVENTS_DATA)} events")
    except Exception as e:
        print("  · Events seed note:", e)

    # 3. News
    try:
        n_count = sb.table("news_items").select("id").limit(1).execute()
        if not n_count.data:
            for nw in NEWS_DATA:
                sb.table("news_items").insert(nw).execute()
            print(f"  ✓ Seeded {len(NEWS_DATA)} news items")
    except Exception as e:
        print("  · News seed note:", e)

    # 4. Users
    import bcrypt
    created_users = 0
    for u in DEMO_USERS:
        try:
            existing = sb.table("users").select("id, supabase_uid").eq("username", u["username"]).eq("role", u["role"]).execute()
            if not existing.data:
                pwd_hash = bcrypt.hashpw(u["password"].encode(), bcrypt.gensalt()).decode()
                supa_uid = None
                try:
                    auth_res = sb.auth.sign_up({
                        "email": u["email"],
                        "password": u["password"],
                        "options": {"data": {"full_name": u["full_name"], "role": u["role"]}},
                    })
                    supa_user = getattr(auth_res, "user", None)
                    supa_uid = getattr(supa_user, "id", None) if supa_user else None
                except Exception as ae:
                    print(f"  · Auth sign_up note for {u['username']}: {ae}")

                if not supa_uid:
                    supa_uid = "local-" + u["username"]

                user_row = {
                    "username":       u["username"],
                    "role":           u["role"],
                    "full_name":      u["full_name"],
                    "email":          u["email"],
                    "password_hash":  pwd_hash,
                    "phone":          u.get("phone"),
                    "enrollment_no":  u.get("enrollment_no"),
                    "department":     u.get("department"),
                    "programme":      u.get("programme"),
                    "batch":          u.get("batch"),
                    "designation":    u.get("designation"),
                    "is_active":      True,
                    "is_super_admin": u.get("is_super_admin", False),
                    "status":         "approved",
                    "supabase_uid":   supa_uid,
                }
                res = sb.table("users").insert(user_row).execute()
                if res.data:
                    created_users += 1
        except Exception as ue:
            print(f"  · Seed user note for {u['username']}: {ue}")

    if created_users:
        print(f"  ✓ Created {created_users} demo user profile(s)")
    else:
        print("  · Demo users already present")


if __name__ == "__main__":
    seed()
