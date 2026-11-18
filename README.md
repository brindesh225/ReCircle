
# ReCircle – Smart Waste Collection & Recycling Platform

A functional local-first full-stack prototype built with Flask, SQLite, HTML, CSS and vanilla JavaScript. It runs without paid API keys or external services.

## Features
- Public landing page and household registration/login
- Three roles: household, collector and NGO administrator
- Password hashing, server-side sessions and role/ownership checks
- Persistent pickup requests, assignment history and status timeline
- Collector accept → start → complete workflow
- Actual collection quantity, category, remarks and proof photo upload
- NGO overview, category/month analytics, search/filter and CSV export
- Collector creation, activation/deactivation and availability
- Demo data for all three roles

## Requirements
- Python 3.10+
- Arch Linux or another OS with Python

## Run on Arch Linux
```bash
sudo pacman -S python
cd recircle
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # optional; edit secrets before production
python app.py
```
Open http://127.0.0.1:5000

The app creates `instance/recircle.db` and `uploads/` automatically. Set `SEED_DEMO=false` to disable demo seeding on a fresh database. The first admin is created from `ADMIN_NAME`, `ADMIN_EMAIL`, and `ADMIN_PASSWORD` environment variables. The default local demo admin is listed below.

## Demo accounts
| Role | Email | Password |
|---|---|---|
| NGO Admin | admin@recircle.local | Admin@123 |
| Household | ananya@example.com | House@123 |
| Household | rahul@example.com | House@123 |
| Collector | kiran@example.com | Collect@123 |
| Collector | meera@example.com | Collect@123 |

Change all demo passwords before exposing the app to a network. Collector accounts are created by the NGO admin; public registration is household-only.

## Workflow test
1. Log in as `ananya@example.com` and submit a pickup request.
2. Log in as `admin@recircle.local`, open the request and assign Kiran or Meera.
3. Log in as the assigned collector, accept the pickup, then start it.
4. Open the request again, enter actual quantity/category/date, attach a JPG/PNG/WEBP proof image and confirm completion.
5. Log back in as the household or admin to see the completed status, proof and actual weight.
6. Use the admin dashboard filters and Export CSV.

## Configuration
Environment variables are read by `app.py`:
- `SECRET_KEY`: session signing key
- `DATABASE`: SQLite database path
- `UPLOAD_FOLDER`: proof image directory
- `ADMIN_NAME`, `ADMIN_EMAIL`, `ADMIN_PASSWORD`: first admin account
- `SEED_DEMO`: `true` (default) or `false`

You can export variables in your shell before running:
```bash
export SECRET_KEY="$(python -c 'import secrets; print(secrets.token_hex(32))')"
export ADMIN_EMAIL="admin@yourngo.org"
export ADMIN_PASSWORD="use-a-unique-long-password"
python app.py
```

## Security and prototype notes
- Passwords are stored as Werkzeug password hashes, not plaintext.
- Role gates and record ownership are enforced server-side; changing a URL does not grant access.
- Uploads are limited to 5 MB and image extensions PNG/JPG/JPEG/WEBP. Proof access is authenticated and scoped to the household, assigned collector or admin.
- SQLite foreign keys are enabled. Status changes record actor and timestamp.
- For production: run behind HTTPS with a production WSGI server, set a strong secret, add CSRF protection and rate limiting, use object storage and malware/content inspection for uploads, add database migrations/backups, audit account changes, and configure secure cookie flags. The development server is not for production.
- This prototype uses server-rendered Flask templates rather than a separate React SPA to keep local setup lightweight. The backend and database are modular and can later be exposed as a REST API for a React frontend.
- The landing page uses a Google Fonts import when internet is available; system fonts remain usable offline.


## Deploy to Render (demo deployment)
1. Push this project folder to a GitHub repository.
2. In Render, choose **New → Blueprint** and connect that repository (Render reads `render.yaml`).
3. When prompted, set `ADMIN_PASSWORD` to a strong unique password. Keep the generated `SECRET_KEY`.
4. Deploy. Render installs dependencies and starts `gunicorn app:app`.
5. Open the `https://...onrender.com` URL shown in the Render dashboard.

**Important:** Render's free web service has an ephemeral filesystem. The SQLite database and uploaded proof images in this demo can be lost when the service restarts or redeploys. Do not use this deployment for real household data. For durable production use, migrate SQLite to a managed PostgreSQL database and store proof images in persistent object storage (or a supported persistent disk), then configure backups, HTTPS, CSRF protection, rate limiting and secure cookie settings. Demo credentials are public in this repository's README, so change all demo passwords and disable demo seeding before public production use.
=======
0c33f0475bd105d1443e4def7572d04399dbc424
