# Fixing the Render Deployment — Step by Step

## Root Cause of the Error

Two problems in your old code:

| Problem | Cause | Fix |
|---------|-------|-----|
| `sqlalchemy.exc.OperationalError` | Old `main.py` still calls `Base.metadata.create_all(bind=engine)` on startup | Replace `main.py`, `app/database.py`, `app/deps.py` with new files |
| `Network is unreachable` (IPv6) | Direct Supabase DB connection uses port 5432 which is IPv6 on Render free tier | Removed psycopg2 entirely — supabase-py uses HTTPS (IPv4 safe) |

---

## Step 1 — Push these files to GitHub

Replace these files in your repo with the ones in this zip:

```
main.py                          ← replaces old main.py
app/database.py                  ← replaces old app/database.py
app/deps.py                      ← replaces old app/deps.py
requirements.txt                 ← replaces old requirements.txt
routers/admin.py                 ← new (full admin CRUD)
routers/faculty.py               ← new (attendance sessions)
routers/student.py               ← new (timetable + mark attendance)
routers/cr.py                    ← new (classmates view)
routers/auth.py                  ← updated (uses supabase-py)
routers/password_reset.py        ← updated (uses supabase-py)
sql/schema.sql                   ← run once in Supabase SQL Editor
```

---

## Step 2 — Set Environment Variables on Render

Go to **Render Dashboard → Your Service → Environment** and add:

| Key | Value |
|-----|-------|
| `SUPABASE_URL` | `https://jskzssdwgzxvpzfurxos.supabase.co` |
| `NEXT_PUBLIC_SUPABASE_URL` | `https://jskzssdwgzxvpzfurxos.supabase.co` |
| `NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY` | `sb_publishable_aeasaeZVMlBjyGapl1WApA_iC8OQIPk` |
| `SECRET_KEY` | any long random string |
| `DEBUG` | `false` |
| `CORS_ORIGINS` | `*` |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | `480` |

> ⚠️ Do NOT set `DATABASE_URL` or `SUPABASE_DB_PASSWORD` — those were for psycopg2 which is now removed.

---

## Step 3 — Run schema.sql in Supabase (one time only)

Go to **Supabase Dashboard → SQL Editor** → paste and run `sql/schema.sql`.
This creates all tables. Safe to run again — uses `CREATE TABLE IF NOT EXISTS`.

---

## Step 4 — Verify on Render

After deploy completes, visit:
```
https://your-render-url.onrender.com/health
```

Expected response:
```json
{
  "status": "ok",
  "database": "supabase",
  "connected": true
}
```

---

## Why supabase-py works on Render free tier (and psycopg2 didn't)

- `psycopg2` opens a **TCP connection on port 5432** → Supabase resolves this to an **IPv6 address** → Render free tier has no IPv6 → crash
- `supabase-py` uses **HTTPS REST API (PostgREST)** on port 443 → always IPv4 → works everywhere
