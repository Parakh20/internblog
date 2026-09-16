# internblog

Watches the IIT Bombay internship blog and turns each new post into a
calendar event and a Telegram message, so students stop refreshing the blog
by hand.

**Live:** https://internblog.duckdns.org (sign-in limited to allowlisted
IIT Bombay students). Anyone can run the [demo](#try-it-without-an-iitb-login).

## The problem

During internship season the placement office announces everything on one
SSO-protected WordPress blog: new listings, deadline extensions, test and OA
schedules, PPTs, shortlists. There are no notifications. Missing a post can
mean missing a deadline, so students refresh the blog many times a day and
copy dates into their calendars by hand.

internblog does that checking for you. It polls the blog every 2 minutes,
works out what changed, pulls out the company, role, deadline, CGPA cutoff,
branches and stipend, and pushes the result to each signed-in user's Google
Calendar and Telegram. When a deadline is extended, it edits the existing
calendar event and Telegram message instead of sending a duplicate.

## How it works

```
IITB blog (WordPress, behind SSO)
   │  plain HTTP + session cookie, every 2 min        app/blog_client.py
   ▼
snapshot (gzipped JSON) ─► change detection           app/change_detection.py
   │  new / edited / removed posts only
   ▼
LLM extraction (Groq, OpenRouter fallback)             app/extraction.py
   │  JSON validated with Pydantic, then date fixes
   ▼
SQLite / Postgres ─► per-user push                     app/pipeline.py
                     ├─ Google Calendar (own secondary calendars)
                     ├─ Telegram (edits in place on updates)
                     └─ ICS feed + web dashboard (Google sign-in)
```

**Where the LLM is used, and where it isn't.** Fetching, change detection,
session handling, deduplication and notification grouping are ordinary
code, because they need to be exact and testable. The LLM does one job:
reading placement-office prose that has no fixed format and returning
structured fields. Its output is never trusted blindly:

- Every response is validated against a Pydantic schema (`InternshipExtraction`).
- Known model mistakes are fixed in code: years before the season get clamped,
  a bare time with no date gets combined with the post date, and nulls get
  turned into empty lists.
- Deadlines that have already passed never trigger a notification, so
  re-extracting old posts can't spam anyone.
- If extraction fails, the post is still stored, and
  `scripts/backfill_extractions.py` can retry later.

Design decisions and the reasons behind them are in
[docs/decisions.md](docs/decisions.md). The endpoint investigation (why the
monitor calls the REST API instead of driving a browser) is in
[docs/investigation.md](docs/investigation.md).

## Try it without an IITB login

The real blog needs IIT Bombay SSO, so there is a demo that feeds sample
posts ([demo/sample_posts.json](demo/sample_posts.json), all made up)
through the same pipeline code. Only the HTTP fetch is swapped out.

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env        # set GROQ_API_KEY (free at https://console.groq.com)
.venv/bin/python scripts/demo.py --reset
```

Round 1 finds three new posts. Round 2 finds one edited post (deadline
extended) and one new shortlist post:

```
== Round 2: one post edited, one new post
   fetch status=ok posts=4 new=1 modified=1 removed=0
   [new_listing       ] Acme Robotics      role=Software Engineering Intern (Perception) | deadline=2026-10-30T23:59:00+05:30 | ...
   [test_update       ] Globex Analytics   role=Quant Research Intern | deadline=2026-11-04T18:00:00+05:30 | ...
   [administrative    ] -                  ...
   [shortlist_result  ] Initech            role=Product Management Intern | ...
   [deadline_extension] Acme Robotics      role=Software Engineering Intern, Perception | deadline=2026-11-06T23:59:00+05:30 | ...
```

The demo writes to `data/demo.db`, which has no users, so nothing is sent to
Telegram or Google Calendar. Without an API key, posts are still stored and
diffed, and extraction is skipped.

## Run the real monitor locally

Needs an IIT Bombay SSO account.

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/playwright install chromium
cp .env.example .env
```

Fill in `.env`. The minimum is `GROQ_API_KEY`. Signing in to the dashboard
also needs `GOOGLE_OAUTH_CLIENT_ID`, `GOOGLE_OAUTH_CLIENT_SECRET`,
`SECRET_ENCRYPTION_KEY` and `OWNER_EMAIL`. For a local run, set
`OAUTH_REDIRECT_BASE_URL=http://localhost:8000` and add
`http://localhost:8000/auth/callback` as a redirect URI on the OAuth client.

Log in to the blog once. This opens a visible browser, and you complete SSO yourself:

```bash
.venv/bin/python scripts/reauth.py
```

Start the app:

```bash
.venv/bin/uvicorn app.main:app --port 8000
curl localhost:8000/health
```

Open http://localhost:8000 and sign in with Google. `/admin` shows fetch logs
and session state to `OWNER_EMAIL`.

SSO login is never automated. When the blog session dies, the monitor first
tries a headless refresh that reuses the still-valid SSO session without
entering any credentials. If SSO asks for a password, monitoring pauses until
you run `scripts/reauth.py` again. The app picks up the new session without
a restart.

## Tests

```bash
.venv/bin/python -m pytest tests/
```

Unit tests cover response classification, change detection, extraction
validation and date fixes, notification grouping, calendar pushes, OAuth
session handling, the Telegram webhook, HTML sanitizing and the demo fixture.
External APIs (Groq, Google, Telegram) are always faked.

## Deploy

Docker Compose runs the app, Postgres and Caddy (automatic HTTPS). See
[docs/deployment.md](docs/deployment.md).

```bash
docker compose up -d --build
```

## Configuration

Every setting is in [.env.example](.env.example) with a comment. Secrets live only
in `.env`, which is gitignored along with the blog session
(`storage_state.json`, `browser_profile/`), the database, snapshots, logs and
any roll-list files.

## Known limitations

- It only works for people with IIT Bombay SSO access. Blog content is not
  redistributed beyond signed-in, allowlisted users.
- Extraction accuracy depends on the free-tier model. Always check the
  original post before acting on a deadline. Each dashboard entry links back
  to it.
- There is no migration tool. `app/db.py` adds known new columns at startup.
