# USHSS Portal

FastAPI-based portal for the University School of Humanities & Social Sciences (GGSIPU).

**Stack:** FastAPI · Supabase PostgreSQL/Auth · JWT · Jinja2

## Architecture

- **Supabase is the production source of truth** for profiles, faculty, events, news, timetable and attendance data.
- **Supabase Auth** owns passwords and authentication identities.
- The application does **not** silently fall back to SQLite in production.
- SQLite can be enabled explicitly for local development with `USE_SQLITE_FALLBACK=1`.
- `SUPABASE_SERVICE_ROLE_KEY` is used only by the server; never expose it in frontend code.

## Project structure

```
main.py                 FastAPI entry point
app/database.py         Supabase client + optional explicit SQLite dev fallback
app/deps.py             JWT/role dependencies
app/security.py         JWT helpers
app/seed.py             Demo/public data and Auth-backed demo accounts
routers/auth.py         Login and registration
routers/admin.py        Admin operations
routers/student.py      Student portal
routers/faculty.py      Faculty portal
routers/cr.py           CR portal
routers/password_reset.py Admin password reset
templates/              Portal pages
static/                 Frontend assets
sql/                    Database schema/migrations
tests/                  Integration tests
render.yaml             Render deployment blueprint
```

## Local setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Set these values in `.env`:

```env
SUPABASE_URL=https://<your-project-ref>.supabase.co
SUPABASE_SERVICE_ROLE_KEY=<server-only-service-role-key>
SECRET_KEY=<strong-random-secret>
USE_SQLITE_FALLBACK=0
CORS_ORIGINS=*
DEBUG=false
```

Run:

```bash
python -m uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

Useful endpoints:

- `/` — public USHSS website
- `/docs` — Swagger API
- `/dashboard/admin` — admin dashboard
- `/dashboard/student` — student portal
- `/dashboard/faculty` — faculty portal
- `/dashboard/cr` — CR portal
- `/health` — database health check

## Render deployment

The included `render.yaml` declares the required server-side variables.

In Render → Service → Environment, set:

- `SUPABASE_URL`
- `SUPABASE_SERVICE_ROLE_KEY`
- `SECRET_KEY` (the blueprint can generate this)
- `CORS_ORIGINS`
- `DEBUG=false`
- `USE_SQLITE_FALLBACK=0`

Do **not** put the Supabase service-role key in GitHub, templates, JavaScript, or client-side configuration.

## Authentication

All user passwords are handled by Supabase Auth.

Login:

```http
POST /api/login
Content-Type: application/json

{"username":"<username>","password":"<password>","role":"student"}
```

Registration creates a Supabase Auth identity first and then a matching portal profile. If Supabase Auth is unavailable, registration fails instead of creating a fake/local identity.

Admin password reset uses the Supabase Auth admin API and requires the target profile to have a real `supabase_uid`.

## Database

The repaired production schema includes:

- `faculty_directory`
- `timetable_slots`
- `attendance_sessions`
- `attendance_records`

Relevant indexes and Row Level Security are enabled in Supabase.

Run database changes through Supabase migrations/SQL, not through a hidden local SQLite database.

## Testing

With valid Supabase environment variables:

```bash
python -m unittest discover -s tests -v
```

For a local-only SQLite development environment:

```env
USE_SQLITE_FALLBACK=1
```

Do not enable that setting in production.

## Production checklist

- [ ] Set a strong `SECRET_KEY`
- [ ] Set `SUPABASE_URL`
- [ ] Set `SUPABASE_SERVICE_ROLE_KEY` only in the server environment
- [ ] Keep `USE_SQLITE_FALLBACK=0`
- [ ] Restrict `CORS_ORIGINS` to the deployed frontend/domain
- [ ] Enable Supabase Auth leaked-password protection
- [ ] Change/remove demo credentials before public launch
- [ ] Use HTTPS
- [ ] Keep database backups enabled
