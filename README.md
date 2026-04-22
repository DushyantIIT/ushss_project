# USHSS Portal — FastAPI Backend

**University School of Humanities & Social Sciences, GGSIPU**  
Stack: FastAPI · SQLAlchemy · Supabase PostgreSQL · bcrypt · JWT · Jinja2

---

## Project Structure

ushss_project/
├── main.py                  ← App entry point (serves frontend + registers routers)
├── requirements.txt
├── .env.example             ← Copy to .env and edit
├── render.yaml              ← Render deployment blueprint
│
├── templates/               ← HTML pages served by FastAPI
│   ├── ushss_website.html       (home page  →  GET /)
│   ├── admin.html               (admin dashboard  →  GET /dashboard/admin)
│   ├── ushss-student-portal.html
│   ├── ushss-faculty-portal.html
│   └── ushss-cr-portal.html
│
├── static/                  ← JS / CSS
│   └── ad.js
│
├── app/                     ← Backend logic
│   ├── models.py            ← SQLAlchemy ORM tables
│   ├── seed.py              ← Demo data loader (runs on startup)
│   ├── database.py          ← DB engine + get_db()
│   ├── security.py          ← bcrypt + JWT helpers
│   ├── deps.py              ← get_current_user dependency
│   └── schemas.py           ← Pydantic request/response models
│
└── routers/
    ├── auth.py              ← POST /api/login  |  GET /api/me
    ├── admin.py             ← /api/admin/* (CRUD users, stats, audit, messages)
    ├── student.py           ← /api/student/*
    ├── faculty.py           ← /api/faculty/*
    ├── cr.py                ← /api/cr/*
    └── password_reset.py    ← /api/reset/*
```

---

## 1 · Setup

```bash
# Navigate into the project folder
cd ushss_project

# Create a virtual environment
python3 -m venv .venv

# Activate it
source .venv/bin/activate          # macOS / Linux
# .venv\Scripts\activate           # Windows

# Install all dependencies
pip install -r requirements.txt

# Copy and configure environment variables
cp .env.example .env
# Open .env and change SECRET_KEY before deploying!
```

---

## 2 · Run (Development)

```bash
# Safest way — uses venv Python directly (avoids system uvicorn conflict)
.venv/bin/python -m uvicorn main:app --reload --host 0.0.0.0 --port 8000

# Alternative (only if venv is definitely active)
python -m uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

The database (`ushss.db`) and demo data are created **automatically** on first start.

| URL | What you see |
|-----|-------------|
| http://localhost:8000 | USHSS main website |
| http://localhost:8000/docs | Swagger UI (interactive API) |
| http://localhost:8000/redoc | ReDoc API reference |
| http://localhost:8000/dashboard/admin | Admin dashboard |
| http://localhost:8000/dashboard/student | Student portal |
| http://localhost:8000/dashboard/faculty | Faculty portal |
| http://localhost:8000/dashboard/cr | CR portal |
| http://localhost:8000/health | Health check |

---

## 3 · Deploy on Render (free)

1. Push to GitHub, then go to **[render.com](https://render.com) → New → Web Service**
2. Connect repo: `DushyantIIT/ushss_project`
3. Set these fields:

| Field | Value |
|---|---|
| **Build Command** | `pip install -r requirements.txt` |
| **Start Command** | `uvicorn main:app --host 0.0.0.0 --port $PORT` |

4. Add environment variables:

| Key | Value |
|---|---|
| `SECRET_KEY` | Click **Generate** |
| `DATABASE_URL` | `postgresql+psycopg2://postgres:<SUPABASE_DB_PASSWORD>@db.jskzssdwgzxvpzfurxos.supabase.co:5432/postgres` |
| `NEXT_PUBLIC_SUPABASE_URL` | `https://jskzssdwgzxvpzfurxos.supabase.co` |
| `NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY` | `sb_publishable_aeasaeZVMlBjyGapl1WApA_iC8OQIPk` |
| `CORS_ORIGINS` | `*` |
| `DEBUG` | `false` |

5. Click **Create Web Service** — live in ~2 min. Every `git push` auto-redeploys.

> ⚠️ Free tier sleeps after 15 min idle. Use [UptimeRobot](https://uptimerobot.com) to keep it awake.

---

## 4 · Run (Production / Self-hosted)

```bash
uvicorn main:app --host 0.0.0.0 --port 8000 --workers 4
# or with gunicorn:
gunicorn main:app -w 4 -k uvicorn.workers.UvicornWorker --bind 0.0.0.0:8000
```

---

## 4 · Default Login Credentials

> ⚠️ **Change all passwords before going to production!**

| Role    | Username    | Password      |
|---------|-------------|---------------|
| Admin   | `admin001`  | `Admin@1234`  |
| Faculty | `fac001`    | `Faculty@123` |
| Faculty | `fac002`    | `Faculty@123` |
| Student | `2301001`   | `Student@123` |
| Student | `2401001`   | `Student@123` |
| CR      | `cr2301001` | `Cr@12345`    |

---

## 5 · Test the API (curl)

### Login and get a token
```bash
curl -s -X POST http://localhost:8000/api/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin001","password":"Admin@1234","role":"admin"}' | python3 -m json.tool
```

### Use the token for protected routes
```bash
# Replace <TOKEN> with the token from the login response
TOKEN="<TOKEN>"

# Get current user
curl -s http://localhost:8000/api/me \
  -H "Authorization: Bearer $TOKEN" | python3 -m json.tool

# Student profile (login as student first)
curl -s http://localhost:8000/api/student/profile \
  -H "Authorization: Bearer $TOKEN" | python3 -m json.tool
```

### Health check (no auth needed)
```bash
curl http://localhost:8000/health
```

---

## 6 · Test with Swagger UI (easiest)

1. Open **http://localhost:8000/docs**
2. Click `POST /api/login` → **Try it out** → fill credentials → **Execute**
3. Copy the `token` from the response
4. Click **Authorize** (top right 🔒) → paste the token → **Authorize**
5. Now all protected endpoints work directly in the browser

---

## 7 · Re-seed the database

If you delete `ushss.db` and restart, the seed runs automatically.  
To force a manual re-seed:

```bash
python3 -m app.seed
```

---

## 8 · Supabase PostgreSQL setup

```bash
pip install psycopg2-binary
```

In `.env`:
```
NEXT_PUBLIC_SUPABASE_URL=https://jskzssdwgzxvpzfurxos.supabase.co
NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY=sb_publishable_aeasaeZVMlBjyGapl1WApA_iC8OQIPk
DATABASE_URL=postgresql+psycopg2://postgres:<SUPABASE_DB_PASSWORD>@db.jskzssdwgzxvpzfurxos.supabase.co:5432/postgres
```

SQLAlchemy will then use Supabase Postgres as the SQL database.

---

## 9 · API Reference

### Auth
| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `POST` | `/api/login` | ✗ | Login → returns JWT token |
| `GET`  | `/api/me` | ✓ | Current user profile |
| `POST` | `/api/logout` | ✓ | Logout (discard token client-side) |

### Student (`/api/student/*`)
| Method | Path | Description |
|--------|------|-------------|
| `GET`  | `/api/student/profile` | View own profile |
| `PUT`  | `/api/student/profile` | Update phone / email |
| `POST` | `/api/student/change-password` | Change password |

### Faculty (`/api/faculty/*`)
| Method | Path | Description |
|--------|------|-------------|
| `GET`  | `/api/faculty/profile` | View own profile |
| `PUT`  | `/api/faculty/profile` | Update details |
| `POST` | `/api/faculty/change-password` | Change password |
| `GET`  | `/api/faculty/students` | View students in own department |

### Class Representative (`/api/cr/*`)
| Method | Path | Description |
|--------|------|-------------|
| `GET`  | `/api/cr/profile` | View own profile |
| `PUT`  | `/api/cr/profile` | Update contact details |
| `POST` | `/api/cr/change-password` | Change password |
| `GET`  | `/api/cr/classmates` | View students in same programme & batch |

### Admin (`/api/admin/*`) — requires admin JWT
| Method | Path | Description |
|--------|------|-------------|
| `GET`  | `/api/admin/users` | List all users (filter by role/search) |
| `POST` | `/api/admin/users` | Create a user |
| `GET`  | `/api/admin/users/{id}` | Get single user |
| `PUT`  | `/api/admin/users/{id}` | Update user |
| `DELETE` | `/api/admin/users/{id}` | Delete user |
| `PATCH` | `/api/admin/users/{id}/toggle` | Toggle active status |
| `GET`  | `/api/admin/stats` | Dashboard stats |
| `GET`  | `/api/admin/audit` | Audit log |
| `GET`  | `/api/admin/messages` | Contact messages |
| `PATCH` | `/api/admin/messages/{id}/read` | Mark message read |

### Password Reset (`/api/reset/*`)
| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/reset/request` | Generate reset token (printed to console in dev) |
| `POST` | `/api/reset/confirm` | Submit token + new password |

---

## 10 · Production Checklist

- [ ] Generate a strong `SECRET_KEY`:  
  `python3 -c "import secrets; print(secrets.token_hex(32))"`
- [ ] Set `DEBUG=false` in `.env`
- [ ] Switch `DATABASE_URL` to Supabase PostgreSQL
- [ ] Restrict `CORS_ORIGINS` to your frontend domain
- [ ] Change **all** default passwords
- [ ] Run behind Nginx / Caddy with HTTPS
- [ ] Set up automated DB backups
