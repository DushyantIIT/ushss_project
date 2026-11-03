# USHSS automated email campaigns

## What is included

- Admin-only campaign creation at `/dashboard/admin/emails`.
- Audience filters for BA/MA programme, English/Economics course, semester and section.
- Future scheduling, a branded HTML wrapper, reusable templates and test emails.
- Delivery records with pending/sending/sent/failed states, attempt counts and errors.
- Manual retry of failed recipients.
- A protected dispatch endpoint for an external scheduler: `GET /api/email/dispatch` or `POST /api/email/dispatch`.
- Existing registration approval/rejection email notifications continue to use SMTP.

Campaign recipients are snapshotted when the campaign is created. Later changes to a student's profile do not silently add or remove that student from an already-created campaign.

## 1. Apply the Supabase schema

Open Supabase Dashboard → SQL Editor and run the complete file:

`sql/email_campaigns.sql`

The three tables are `email_templates`, `email_campaigns` and `email_deliveries`. RLS is enabled and browser roles are denied direct access; the application accesses them using its server-side service-role client.

## 2. Configure SMTP in Vercel

In Vercel → Project → Settings → Environment Variables, add these variables for Production (and Preview if needed). Do not commit SMTP passwords or app passwords to GitHub.

### Gmail / Google Workspace

| Variable | Example |
|---|---|
| `SMTP_HOST` | `smtp.gmail.com` |
| `SMTP_PORT` | `587` |
| `SMTP_USERNAME` | Full Gmail / Workspace address |
| `SMTP_PASSWORD` | Google App Password (not the normal account password) |
| `SMTP_FROM` | `USHSS Portal <sender@example.edu>` |
| `SMTP_USE_TLS` | `true` |
| `SMTP_USE_SSL` | `false` |

Google accounts generally require 2-Step Verification and an App Password where the Workspace administrator permits it. If App Passwords are disabled by the university, request an approved SMTP relay or use the university's mail server.

### University SMTP server

Ask the university IT/mail administrator for the SMTP hostname, port, authentication username, password or relay authorization, sender address, and whether STARTTLS or implicit SSL is required. For STARTTLS use `SMTP_USE_TLS=true` and `SMTP_USE_SSL=false`. For implicit SSL (commonly port 465) use `SMTP_USE_SSL=true` and `SMTP_USE_TLS=false`.

Optional: set `SMTP_REPLY_TO` to the university contact address that should receive replies.

## 3. Configure the scheduler secret

Generate a long random secret and add it as `EMAIL_CRON_SECRET` in Vercel. The endpoint accepts either an `x-cron-secret: <secret>` header or an `Authorization: Bearer <secret>` header. Vercel Cron can use the `CRON_SECRET` environment variable for its bearer header.

Configure a scheduler (Vercel Cron on a plan that supports the required frequency, or an external cron service) to make an authenticated GET request to:

`https://YOUR-VERCEL-DOMAIN/api/email/dispatch`

Run it every minute for near-real-time scheduling, or at a frequency supported by your hosting plan. Each run claims and processes up to 30 pending deliveries. Repeated calls continue processing larger campaigns. The endpoint returns 401 unless the secret is configured and valid.

The admin page also has a **Process due emails** action for manual processing and testing.

## 4. Test before sending to students

1. Add the SMTP variables and redeploy the Vercel project.
2. Open `/dashboard/admin/emails` while signed in as an administrator.
3. Send a test email to an address you control.
4. Create a small test campaign using a narrow audience filter.
5. Process due emails and inspect the delivery records.
6. Only then create larger campaigns.

## Delivery and duplicate protection

Each campaign stores one delivery row per selected student, protected by a unique campaign/student constraint. Workers claim a pending row with a conditional database update before sending, preventing two concurrent workers from claiming the same row. Failed deliveries are not silently marked as sent; administrators can retry them.

SMTP is not an exactly-once transport. If an SMTP server accepts a message but the connection fails before the application receives confirmation, the outcome can be ambiguous. A retry after such an ambiguous failure may result in a duplicate. For this reason, rows left in the `sending` state are not automatically resent; inspect the provider/mailbox logs before manually changing their state.

## Important limits

- Campaigns target active, approved student accounts only.
- Course matching uses the existing user profile `domain` / `department` fields and programme text; keep these profile values consistent (English or Economics).
- The current worker processes up to 30 recipients per invocation. Increase the batch only after checking SMTP provider sending limits and Vercel execution limits.
- Gmail and university SMTP relays impose sending limits. For large student cohorts, confirm the university-approved bulk-mail policy before sending.
