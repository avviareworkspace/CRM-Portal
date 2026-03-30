# CRM Portal — Technical Documentation

This document describes the **College / Institute CRM Portal** (Django application): architecture, setup, configuration, deployment, and operations. It is intended for developers and technical stakeholders handing off or maintaining the system.

**End-user guide:** For administrators and counsellors using the web UI (menus, leads, imports, permissions), see [USER_MANUAL.md](./USER_MANUAL.md).

---

## 1. Overview

The application is a **web-based CRM** for managing **leads**, **counsellors**, **activities**, **business opportunities**, **daily targets**, and **reporting**. It supports:

- **Admin users** — full CRM configuration, all leads, assignments, imports, analytics, optional granular permissions.
- **Counsellor users** — assigned leads only, activities, follow-ups, businesses, calendar, daily targets.

Authentication is **email + password** (custom backend). The default Django `/admin/` site is separate from this app’s UI (the app uses its own templates under `main_app`).

---

## 2. Technology Stack

| Layer | Technology |
|--------|------------|
| Runtime | Python 3.11+ recommended |
| Framework | Django **4.2.9** (LTS) |
| Database (production) | **PostgreSQL** (via `DATABASE_URL`) |
| Database (local optional) | **SQLite** when `USE_SQLITE_LOCAL=true` and `DEBUG` |
| HTTP server (production) | **Gunicorn** |
| Static files (production) | **WhiteNoise** |
| Cache (optional) | **Redis** + **django-redis**; falls back to in-memory cache |
| Async tasks (optional) | **Celery** + Redis broker |
| File / cloud storage (optional) | **django-storages** + **boto3** (S3-compatible) |
| Lead import | **CSV** (stdlib) + **Excel .xlsx** (**openpyxl**) — pandas not required |
| Frontend | AdminLTE-style UI, Bootstrap 4, jQuery, Chart.js, DataTables |

---

## 3. Project Structure

```
college_management_system/   # Django project package
  settings.py                # Main configuration (env-driven)
  urls.py                    # Root URLconf — includes main_app at "" and Django admin at admin/
  wsgi.py                    # WSGI entry for Gunicorn
  celery.py                  # Celery application (optional worker)

main_app/                    # Primary application
  models.py                  # ORM models (User, Lead, Activity, Business, …)
  admin_views.py             # Admin-facing views
  counsellor_views.py        # Counsellor-facing views
  views.py                   # Auth, login, password reset hooks
  urls.py                    # All app routes (see section 6)
  forms.py                   # Form classes
  middleware.py              # Login / role routing middleware
  utils.py                   # Pagination, permissions, counsellor snapshots
  lead_import_io.py          # CSV/XLSX row iteration for bulk import
  templates/                 # HTML templates
  static/                    # Static assets
  management/commands/       # e.g. seed_crm_reference

manage.py
requirements.txt
.env.example                 # Environment variable template (copy to .env)
```

---

## 4. User Roles & Permissions

### 4.1 User types (`CustomUser.user_type`)

| Value | Role |
|-------|------|
| `'1'` | **Admin** |
| `'2'` | **Counsellor** |

Middleware redirects users to the wrong area if they open the other role’s URLs.

### 4.2 Admin model permissions (`Admin` profile)

Admins linked via `Admin` model can have restricted capabilities (unless **superadmin**):

- **Delete** — delete leads, bulk delete, delete all leads (dangerous action).
- **Performance** — performance-related views.
- **Counsellor work** — counsellor work / pipeline views.
- **Settings** — settings-oriented admin features.

Sidebar and routes respect these flags via `admin_perm_required` and context processor `admin_permissions`.

### 4.3 Superadmin

`Admin.is_superadmin` grants all permissions.

---

## 5. Core Domain Models (Summary)

- **CustomUser** — email login; `user_type` for admin vs counsellor.
- **Admin** — profile for admin users + permission flags.
- **Counsellor** — profile linked to `CustomUser`; employee metadata.
- **LeadSource** — configurable lead sources.
- **LeadStatus** — configurable statuses (DB-driven; falls back to defaults if empty).
- **Lead** — core lead record (status, priority, assignment, follow-up, etc.).
- **LeadActivity** — calls, emails, visits, notes, transfers, etc.
- **ActivityType** / **NextAction** — configurable reference data.
- **Business** — opportunities linked to leads and counsellors.
- **DailyTarget** / **DailyTargetAssignment** — daily targets per counsellor.
- **NotificationAdmin** / **NotificationCounsellor** — in-app notifications.
- **DataAccessLog** — audit-style logging for some counsellor actions.

Exact fields: see `main_app/models.py`.

---

## 6. URL Map (Functional)

Routes are defined in **`main_app/urls.py`**, included at the site root from **`college_management_system/urls.py`**. Django’s built-in **`/admin/`** is separate from this CRM UI. Highlights:

**Auth:** `/`, `/doLogin/`, `/logout_user/`, password reset flow, optional Firebase messaging JS.

**Admin:** dashboard, counsellor activity progress, profile, notifications, counsellor CRUD, admin user CRUD, lead CRUD/import/assign/transfer, lead sources & statuses, activity types, next actions, daily targets, businesses list, send notifications, analytics JSON, calendar JSON.

**Counsellor:** home, profile, notifications, my leads, lead detail & edit, activities, businesses, pending tasks, daily target, calendar, analytics JSON, FCM token, scheduled notification check.

**Destructive / sensitive (admin, delete permission where noted):**

- `POST /leads/delete/<id>/` — single lead delete.
- `POST /leads/delete/bulk/` — delete selected IDs (current page selection).
- `POST /leads/delete/all/` — **delete every lead**; requires typing exactly `DELETE ALL LEADS`.

---

## 7. Installation (Local Development)

### 7.1 Prerequisites

- Python **3.11+** (3.10+ usually works)
- `pip` / virtualenv

### 7.2 Steps

```bash
git clone <repository-url>
cd CRM-Portal-main
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
# Edit .env — at minimum SECRET_KEY, DJANGO_DEBUG=True, DJANGO_ALLOWED_HOSTS
```

**Database choice:**

- **SQLite (simplest local):** set `USE_SQLITE_LOCAL=true` and `DJANGO_DEBUG=True`; ensure `DATABASE_URL` is not forcing Postgres, or rely on settings logic (see `settings.py`).
- **PostgreSQL local:** set `DATABASE_URL` to a valid Postgres URI.

Run migrations:

```bash
python manage.py migrate
```

Create admin user (app login, not necessarily Django superuser for `/admin/`):

```bash
python manage.py createsuperuser
# Ensure the user is created with user_type suitable for your flow;
# project may use CustomUser — follow your onboarding process or Admin/Counsellor creation via UI after first login.
```

Collect static (production or when testing WhiteNoise):

```bash
python manage.py collectstatic --noinput
```

Run server:

```bash
python manage.py runserver
```

### 7.3 Reference data seeding

Populate default-like **lead statuses**, **activity types**, **next actions** (if command exists in your tree):

```bash
python manage.py seed_crm_reference
```

---

## 8. Environment Variables

Copy **`.env.example`** to **`.env`**. Important variables (non-exhaustive — see `college_management_system/settings.py` for the full picture):

| Variable | Purpose |
|----------|---------|
| `SECRET_KEY` or `DJANGO_SECRET_KEY` | Django secret; **required** in production |
| `DJANGO_DEBUG` | `True` / `False` |
| `DJANGO_ALLOWED_HOSTS` | Comma-separated hostnames (no `https://`, no trailing `/`) |
| `CSRF_TRUSTED_ORIGINS` | Comma-separated origins, e.g. `https://app.example.com` |
| `DATABASE_URL` | PostgreSQL connection URI (production) |
| `USE_SQLITE_LOCAL` | When `true` + `DEBUG`, use SQLite for local dev |
| `REDIS_URL` | Enables Redis cache + Celery broker defaults |
| `EMAIL_ADDRESS`, `EMAIL_PASSWORD` | SMTP (e.g. Gmail app password) |
| `OPENAI_API_KEY` | Optional AI features |
| `FIREBASE_*` | Optional Firebase config for messaging |
| `AWS_*` / storage settings | Optional S3 media/static via django-storages |
| `ADMIN_DASHBOARD_CACHE_SECONDS` | Admin dashboard aggregate cache TTL (default 45); `0` disables |
| `COUNSELLOR_SNAPSHOT_CACHE_SECONDS` | Counsellor snapshot cache TTL; `0` disables |
| `SESSION_SAVE_EVERY_REQUEST` | Default `false` for performance |
| `DATABASE_CONN_MAX_AGE` | Postgres connection persistence (pooler-dependent) |
| `SUPABASE_TRANSACTION_POOLER` | Hint for transaction pooler behaviour |
| `DATA_UPLOAD_MAX_NUMBER_FIELDS` | Large bulk forms (e.g. many lead checkboxes) |
| `META_VERIFY_TOKEN` | Optional override for Meta webhook verify token (else use Admin → WhatsApp & Meta) |
| `META_APP_SECRET` | Optional override for webhook `X-Hub-Signature-256` verification |
| `META_ACCESS_TOKEN` | Optional override for sending WhatsApp messages via Graph API |
| `META_WHATSAPP_PHONE_NUMBER_ID` | Optional override for Cloud API phone number ID |
| `META_FACEBOOK_PAGE_ID` | Optional override for sending Messenger/Instagram replies from Social inbox |

**Platform-provided (auto-used in settings when present):**

- **Vercel:** `VERCEL`, `VERCEL_URL` — extend `ALLOWED_HOSTS` / `CSRF_TRUSTED_ORIGINS`.
- **Railway:** `RAILWAY_PUBLIC_DOMAIN`, `RAILWAY_ENVIRONMENT`.
- **Render:** `RENDER_EXTERNAL_URL`.

---

## 9. Database & Hosting Notes

### 9.1 Render vs Supabase

- **Render** (or Railway, Vercel, VPS) hosts the **Django app**.
- **Supabase** is **one option** for **PostgreSQL**. You can also use **Render Postgres**, Neon, RDS, etc.
- Only **PostgreSQL** (or SQLite for dev) is required — not Supabase specifically.

### 9.2 Supabase connection pooling

- Prefer **transaction pooler** (**port `6543`**) for web apps and many short-lived connections.
- **Session pooler** (`5432` on pooler host) has **low max clients**; the project defaults `CONN_MAX_AGE` appropriately for pooler hosts to avoid `MaxClientsInSessionMode`.

### 9.3 IPv6 issues on some hosts

On **Vercel** / **Railway**, settings may resolve **IPv4** `hostaddr` for Postgres while keeping TLS hostname verification — mitigates “Cannot assign requested address” to some `db.*.supabase.co` endpoints.

---

## 10. Lead Import

**Formats:** `.csv`, `.xlsx`

**Expected columns (typical):**

- **Required (as per UI guidance):** `first_name`, `last_name`, `email`, `phone`, `course_interested`
- **Optional:** `alternate_phone`, `School Name`, `graduation_status` (YES/NO), `graduation_course`, `graduation_year`, `graduation_college`, `industry`

Import uses **batched `bulk_create`** for performance. **Auto-assignment** strategies exist (round-robin, workload-based, etc.) when selected on the import form.

**Limits:** `MAX_LEAD_IMPORT_MB` (default 10 MB) in settings / env.

---

## 11. Security

- **CSRF** enabled; set `CSRF_TRUSTED_ORIGINS` for HTTPS deployments.
- **Session cookies:** `HttpOnly`, `SameSite`; **secure** cookies when `DEBUG=False`.
- **HSTS**, **SSL redirect**, **X-Frame-Options** in production (`DEBUG=False`).
- **Role middleware** blocks cross-role URL access.
- **Admin delete permission** gates destructive lead actions.
- **Delete all leads** requires explicit typed phrase **`DELETE ALL LEADS`**.
- **Media / static:** in production, configure the web server or object storage to serve `/media/`; do not rely on Django for user uploads in production unless intentionally configured.

---

## 12. Performance

- **Admin dashboard** aggregates cached (`ADMIN_DASHBOARD_CACHE_SECONDS`); recent activity strip loaded fresh.
- **Counsellor activity snapshot** cached per counsellor (`COUNSELLOR_SNAPSHOT_CACHE_SECONDS`).
- **`SESSION_SAVE_EVERY_REQUEST=false`** by default reduces session writes.
- **Redis** recommended in production for **shared cache** across multiple workers/instances.
- **Gunicorn** worker count and DB pool limits should match your Postgres plan.

---

## 13. Background Tasks (Celery)

If `REDIS_URL` is set, Celery is configured to use Redis as broker/result backend. Ensure a **worker process** runs:

```bash
celery -A college_management_system worker -l info
```

The Celery app is defined in `college_management_system/celery.py`.

---

## 14. WhatsApp, Instagram, Facebook & Meta webhooks

Inbound messages from **Meta** can create or update **leads** and optionally notify admins:

| Channel | Webhook `object` | CRM toggle |
|--------|-------------------|------------|
| **WhatsApp** (Cloud API) | `whatsapp_business_account` | Accept WhatsApp inbound |
| **Instagram** DMs | `instagram` | Accept Instagram DMs |
| **Facebook Page** (Messenger) | `page` | Accept Facebook Page / Messenger |

1. Run migrations so `MetaIntegrationSettings` exists.
2. As an admin with **Settings** permission, open **WhatsApp & Meta** in the sidebar.
3. Set **Public site URL** (HTTPS, no trailing slash), **Verify token**, **App secret**, and (for outbound API) **Access token** + **WhatsApp Phone number ID**.
4. Copy the **Webhook callback URL** into [Meta for Developers](https://developers.facebook.com/) → your app → **Webhooks**, and subscribe to **`messages`** for WhatsApp. For Instagram and/or Page, add those products and subscribe to their **messaging** fields (same callback URL is fine).
5. Enable the toggles that match the channels you connected in Meta.

**Callback path:** `POST/GET /integrations/meta/webhook/` (no login; CSRF exempt; signature checked when app secret is set).

**Env overrides:** `META_VERIFY_TOKEN`, `META_APP_SECRET`, `META_ACCESS_TOKEN`, `META_WHATSAPP_PHONE_NUMBER_ID`, `META_FACEBOOK_PAGE_ID` override the database values when set (useful on PaaS).

**Social inbox:** Admins with **Settings** permission can open **Social inbox** (`/integrations/chats/`) to see all threads (WhatsApp / Instagram / Facebook), read history, and **send replies**. New inbound webhooks append to the thread and still update the linked **lead**. Replies use the Cloud API for WhatsApp and `POST /{page-id}/messages` for Messenger/Instagram (Page access token; Page ID from the webhook entry or the **Facebook Page ID** field in integration settings).

**Code:** `main_app/meta_services.py` (webhook ingest, `send_thread_reply`, `send_whatsapp_text`), `main_app/views_meta.py` (webhook, settings, inbox), models `SocialChatThread` / `SocialChatMessage`.

---

## 15. Production Deployment Checklist

1. `DJANGO_DEBUG=False`
2. Strong `SECRET_KEY` set
3. `DJANGO_ALLOWED_HOSTS` and `CSRF_TRUSTED_ORIGINS` match public URLs
4. `DATABASE_URL` points to production Postgres (pooler URI if on Supabase serverless)
5. `python manage.py migrate`
6. `python manage.py collectstatic --noinput`
7. Gunicorn bound to `0.0.0.0:$PORT` (Render/Railway) or your platform’s convention
8. Redis for cache (recommended) if multiple instances
9. Email SMTP credentials for password reset / notifications
10. Regular **database backups** configured at the provider

---

## 16. Troubleshooting

| Symptom | Things to check |
|---------|------------------|
| `DisallowedHost` | `DJANGO_ALLOWED_HOSTS`; on Vercel/Railway/Render use platform env or set hosts explicitly |
| CSRF failure on login | `CSRF_TRUSTED_ORIGINS` must include `https://your-domain` |
| `SECRET_KEY` / `ImproperlyConfigured` | Set `SECRET_KEY` in environment |
| `MaxClientsInSessionMode` | Use transaction pool **6543**; avoid holding too many DB connections |
| Slow pages | Redis cache, same region as DB, avoid serverless cold starts for heavy Django |
| Import errors | Column names, file size, openpyxl for xlsx |

---

## 17. License & Support

See **README.md** for repository license and support notes. This documentation reflects the codebase at the time of writing; always verify behaviour against `settings.py` and `urls.py` after upgrades.

---

## Document history

- Maintained alongside the CRM Portal codebase for handover and client deployments.
