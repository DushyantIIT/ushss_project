# Vercel Deployment Guide for USHSS Project

This FastAPI project is configured to run smoothly on **Vercel** serverless functions with standard `@vercel/python`.

---

## 1. Project Files Configured for Vercel

- **`vercel.json`**: Configures the serverless runtime and routes:
  - Route `/static/(.*)` directly serves static files.
  - Route `/(.*)` routes to `api/index.py`.
- **`api/index.py`**: Serverless entry point exposing `app` from `main.py`.
- **`main.py`**: Resolves `STATIC_DIR` and `TEMPLATES_DIR` via absolute `Path(__file__)` so Jinja2 templates and assets load in serverless environments.
- **`app/database.py` & `app/security.py` & `app/rate_limit.py`**: Detect `VERCEL=1` in production mode and extract client IP from Vercel proxy headers (`x-real-ip` / `x-forwarded-for`).

---

## 2. Deploy via Vercel Web Dashboard (Easiest)

1. Push your latest code to your GitHub repository:
   ```bash
   git push origin main
   ```
2. Go to [https://vercel.com](https://vercel.com) and log in.
3. Click **"Add New..."** → **"Project"**.
4. Import your GitHub repository (`ushss_project`).
5. **Framework Preset**: Leave as **Other** (Vercel automatically detects `vercel.json`).
6. **Root Directory**: `./` (leave default).
7. Under **Environment Variables**, add the production keys from your `.env`:
   - `SECRET_KEY`: Your strong 32+ character JWT secret.
   - `SUPABASE_URL`: `https://<your-project-id>.supabase.co`
   - `SUPABASE_SERVICE_ROLE_KEY`: Your Supabase service role secret key.
   - `ENVIRONMENT`: `production`
   - *(Optional)* SMTP settings (`SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`, `SMTP_FROM`) if you want live notification emails.
8. Click **Deploy**.

---

## 3. Deploy via Vercel CLI (Alternative)

If you prefer deploying directly from your terminal:

```bash
# 1. Install Vercel CLI globally
npm i -g vercel

# 2. Login to your Vercel account
vercel login

# 3. Deploy to preview
vercel

# 4. Deploy to production
vercel --prod
```

During CLI setup, accept the default directory and linking options.
