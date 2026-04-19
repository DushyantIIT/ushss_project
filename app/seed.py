"""
app/seed.py — Populate the USHSS database with demo data.
(Adapted from original app/seed.py — updated imports for FastAPI structure)

Run once standalone:  python -m app.seed
Or called from main.py startup lifespan automatically.
"""

from datetime import date, datetime, timezone
from sqlalchemy.orm import Session

from app.models import Base, User, Faculty, Event, NewsItem, ContactMessage, RoleEnum
from app.security import hash_password   # replaces bcrypt.hashpw inline call


# ── Seed data (unchanged from original seed.py) ────────────────────────────────

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
        "event_date": date(2025, 4, 22), "event_time": "3:00 PM",
        "venue": "Seminar Hall A", "category": "lecture", "is_featured": False,
    },
    {
        "name": "National Seminar on Gender & Labour in Contemporary India",
        "description": "Two-day academic seminar with paper presentations from scholars across India's leading universities.",
        "event_date": date(2025, 5, 5), "event_time": "10:00 AM",
        "venue": "Conference Hall, GGSIPU Campus", "category": "seminar", "is_featured": True,
    },
    {
        "name": "M.A. Admissions 2025–26 — Last Date",
        "description": "Deadline for submission of IPU CET applications for all USHSS postgraduate programmes.",
        "event_date": date(2025, 5, 15), "event_time": None,
        "venue": "Online · ipu.admissions.nic.in", "category": "admissions", "is_featured": False,
    },
    {
        "name": "Annual Psychology Awareness Day",
        "description": "Interactive sessions, mental health screenings, and panel discussions open to all GGSIPU students.",
        "event_date": date(2025, 6, 10), "event_time": "10:00 AM",
        "venue": "Main Auditorium", "category": "awareness", "is_featured": False,
    },
    {
        "name": "Alumni Meet 2025 — Humanities & Social Sciences",
        "description": "Annual homecoming event for USHSS graduates to reconnect, network, and engage with current students.",
        "event_date": date(2025, 6, 20), "event_time": "5:00 PM",
        "venue": "USHSS Lawn", "category": "alumni", "is_featured": True,
    },
    {
        "name": "Orientation 2025 — Welcome New Scholars",
        "description": "Formal orientation ceremony for the incoming 2025–26 batch of M.A. and Ph.D. students.",
        "event_date": date(2025, 7, 1), "event_time": "9:30 AM",
        "venue": "Seminar Hall", "category": "orientation", "is_featured": False,
    },
]

NEWS_DATA = [
    {
        "title": "USHSS Hosts Annual Humanities Conclave: Reimagining India's Social Contract",
        "excerpt": "Leading scholars, policymakers, and student researchers gathered for a two-day conclave exploring the future of democratic participation and social equity in post-pandemic India.",
        "tag": "Seminar", "is_featured": True,
        "published_date": date(2025, 3, 18), "venue": "Seminar Hall, GGSIPU",
    },
    {
        "title": "M.A. Admissions 2025–26 now open via IPU CET. Last date: May 15, 2025.",
        "excerpt": "Applications for all M.A. programmes at USHSS are now being accepted through the IPU CET portal.",
        "tag": "Admissions", "is_featured": False,
        "published_date": date(2025, 4, 14), "venue": None,
    },
    {
        "title": "Dr. Sunita Mehta receives UGC Major Research Project grant of ₹12 lakhs for postcolonial fiction study.",
        "excerpt": "The University Grants Commission has awarded a major research project grant to Dr. Sunita Mehta, Associate Professor.",
        "tag": "Achievement", "is_featured": False,
        "published_date": date(2025, 4, 8), "venue": None,
    },
    {
        "title": "Three USHSS alumni selected for Indian Administrative Service in UPSC CSE 2024.",
        "excerpt": "Three graduates of USHSS have cleared the UPSC Civil Services Examination 2024 and have been allocated the IAS cadre.",
        "tag": "Placement", "is_featured": False,
        "published_date": date(2025, 4, 2), "venue": None,
    },
    {
        "title": "Research Methodology Workshop for PhD Scholars: 'Qualitative Methods in Social Science'",
        "excerpt": "A three-day intensive workshop on qualitative research methods was conducted for all registered PhD scholars.",
        "tag": "Workshop", "is_featured": False,
        "published_date": date(2025, 3, 25), "venue": "Research Lab, USHSS",
    },
]

DEMO_USERS = [
    # Admin
    {
        "username": "admin001",   "password": "Admin@1234",
        "role": RoleEnum.admin,   "full_name": "USHSS Administrator",
        "email": "admin.ushss@ipu.ac.in",
        "designation": "Administrator", "department": "Administration",
    },
    # Faculty
    {
        "username": "fac001",     "password": "Faculty@123",
        "role": RoleEnum.faculty, "full_name": "Dr. Anup Singh Beniwal",
        "email": "anupbeniwal@ipu.ac.in",
        "designation": "Professor", "department": "USHSS",
    },
    {
        "username": "fac002",     "password": "Faculty@123",
        "role": RoleEnum.faculty, "full_name": "Dr. Shuchi Sharma",
        "email": "shuchi.sharma@ipu.ac.in",
        "designation": "Dean / Professor", "department": "USHSS",
    },
    # Students
    {
        "username": "2301001",    "password": "Student@123",
        "role": RoleEnum.student, "full_name": "John Sharma",
        "email": "john.sharma@ipu.ac.in",
        "enrollment_no": "2301001", "programme": "M.A. English",
        "batch": "2023", "department": "Humanities",
    },
    {
        "username": "2401001",    "password": "Student@123",
        "role": RoleEnum.student, "full_name": "Rahul Gupta",
        "email": "rahul.gupta@ipu.ac.in",
        "enrollment_no": "2401001", "programme": "M.A. Economics",
        "batch": "2024", "department": "Social Sciences",
    },
    # CR
    {
        "username": "cr2301001",  "password": "Cr@12345",
        "role": RoleEnum.cr,      "full_name": "Amit CR Leader",
        "email": "cr.english@ipu.ac.in",
        "enrollment_no": "cr2301001", "programme": "M.A. English",
        "batch": "2023", "department": "Humanities",
        "designation": "Class Representative",
    },
]


# ── seed() function (same logic as original seed.py) ──────────────────────────

def seed(db: Session) -> None:
    """Idempotent — safe to call on every startup."""

    # Faculty
    if db.query(Faculty).count() == 0:
        for f in FACULTY_DATA:
            db.add(Faculty(**f))
        db.commit()
        print(f"  ✓ Seeded {len(FACULTY_DATA)} faculty members")
    else:
        print("  · Faculty already seeded — skipping")

    # Events
    if db.query(Event).count() == 0:
        for e in EVENTS_DATA:
            db.add(Event(**e))
        db.commit()
        print(f"  ✓ Seeded {len(EVENTS_DATA)} events")
    else:
        print("  · Events already seeded — skipping")

    # News
    if db.query(NewsItem).count() == 0:
        for n in NEWS_DATA:
            db.add(NewsItem(**n))
        db.commit()
        print(f"  ✓ Seeded {len(NEWS_DATA)} news items")
    else:
        print("  · News already seeded — skipping")

    # Users
    created = 0
    for u in DEMO_USERS:
        exists = db.query(User).filter_by(username=u["username"], role=u["role"]).first()
        if not exists:
            user = User(
                username      = u["username"],
                role          = u["role"],
                full_name     = u["full_name"],
                email         = u["email"],
                is_active     = True,
                designation   = u.get("designation"),
                department    = u.get("department"),
                programme     = u.get("programme"),
                batch         = u.get("batch"),
                enrollment_no = u.get("enrollment_no"),
                password_hash = hash_password(u["password"]),
            )
            db.add(user)
            created += 1
    db.commit()
    if created:
        print(f"  ✓ Created {created} demo user(s)")
    else:
        print("  · Demo users already exist — skipping")

    print("\n  Demo login credentials:")
    print("  ┌──────────────────┬──────────────────────┬────────────────┐")
    print("  │ Role             │ Username             │ Password       │")
    print("  ├──────────────────┼──────────────────────┼────────────────┤")
    for u in DEMO_USERS:
        r = u["role"].value
        print(f"  │ {r:<16} │ {u['username']:<20} │ {u['password']:<14} │")
    print("  └──────────────────┴──────────────────────┴────────────────┘\n")


# ── Standalone ─────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    from app.database import SessionLocal, engine

    print("\n  Seeding USHSS database …\n")
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        seed(db)
    finally:
        db.close()
    print("  Done.\n")
