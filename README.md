# CRM Portal (College / Institute CRM)

Django-based **Customer Relationship Management** for lead intake, counsellor assignment, activities, follow-ups, business tracking, daily targets, and admin analytics.

**Full technical documentation:** see **[DOCUMENTATION.md](./DOCUMENTATION.md)** (setup, env vars, deployment, security, troubleshooting).

**User manual (admins & counsellors):** **[USER_MANUAL.md](./USER_MANUAL.md)** — how to use every feature in the web app.

---

## Quick facts

- **Django 4.2** (LTS), Python **3.11+** recommended  
- **PostgreSQL** in production (`DATABASE_URL`); optional **SQLite** for local dev  
- **Admin** vs **Counsellor** roles; optional **granular admin permissions**  
- Lead **import**: `.csv` / `.xlsx` (openpyxl), bulk insert  
- Optional **Redis** (cache + Celery), **WhiteNoise** for static files  

---

## Quick start

```bash
python3 -m venv .venv && source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env   # then edit SECRET_KEY, DEBUG, hosts, DATABASE_URL as needed
python manage.py migrate
python manage.py runserver
```

Open **http://127.0.0.1:8000/** — login page.

For **production**, **environment variables**, **Supabase/Render/Vercel**, **lead import columns**, and **URL map**, use **[DOCUMENTATION.md](./DOCUMENTATION.md)**.

---

## Repository

Default remote may be `Hackersh18/CRM-Portal` or your fork — adjust clone URL accordingly.

---

## License

See **LICENSE** in the repository (if present). MIT mentioned historically; confirm the file shipped with your copy.
