# internblog-monitor

Event-driven monitoring for the IIT Bombay internship blog
(`campus.placements.iitb.ac.in/blog/internship`). Detects new or changed
postings via the WordPress REST API and extracts structured data (company,
role, deadline, CGPA cutoff, branches, stipend) with the Claude API.
WhatsApp alerts, Google Calendar sync, dashboard, and resume matching come
in later phases.

## How it works

- Every 5 minutes (configurable) an APScheduler job fetches all posts from
  the blog's WP REST API over plain HTTP, authenticated with the
  `mod_auth_openidc_session` cookie persisted from a real browser login.
- Polling itself keeps the session alive. If it still expires, the system
  first tries a silent refresh: a headless browser visit that completes the
  SSO redirect using the still-valid SSO session, with no credential entry.
  If SSO shows a login form, monitoring pauses and an alert fires (WhatsApp
  in Phase 2); manual re-login is then required.
- Every successful fetch is snapshotted to `data/snapshots/` (gzipped JSON).
- Changes are detected via WP modified timestamps plus a content hash, then
  new or edited posts go through Claude structured extraction into the DB.
- Everything is logged as JSON lines to `logs/internblog.jsonl` (rotated).

## Setup (local dev)

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/playwright install chromium
cp .env.example .env   # fill in ANTHROPIC_API_KEY at minimum
```

Log in once (opens a visible browser window, waits for you to complete SSO):

```bash
.venv/bin/python scripts/reauth.py
```

Run the monitor:

```bash
.venv/bin/uvicorn app.main:app --port 8000
```

Check health: `curl localhost:8000/health`
Trigger a cycle manually: `curl -X POST localhost:8000/cycle`
Run tests: `.venv/bin/python -m pytest tests/`

Backfill extraction for posts stored without one (for example after topping
up Anthropic credits):

```bash
.venv/bin/python scripts/backfill_extractions.py
```

## Environment variables

See `.env.example`. Required: `ANTHROPIC_API_KEY`. Everything else has
sensible defaults. `DATABASE_URL` defaults to local SQLite; deployment uses
Postgres via Docker Compose.

## Deploy (Oracle Cloud VM, Docker Compose)

1. Copy the repo to the VM, plus `storage_state.json` and `browser_profile/`
   from a machine where you have logged in (or run `scripts/reauth.py` on the
   VM over X forwarding / VNC once).
2. Create `.env` with `ANTHROPIC_API_KEY` and `POSTGRES_PASSWORD`.
3. `docker compose up -d --build`
4. Health check from the VM or over Tailscale: `curl localhost:8000/health`

The dashboard (Phase 3) will bind to localhost and be reached over Tailscale
only. Nothing is exposed publicly.

## Manual re-authentication

When you get the session-expired alert:

```bash
.venv/bin/python scripts/reauth.py
```

Log in through the browser window that opens. The script saves the session
and the monitor picks it up automatically on the next cycle, no restart
needed.

## Docs

- docs/investigation.md: why the REST API is polled instead of Playwright
- docs/decisions.md: decision log
