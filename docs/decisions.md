# Decision Log

One entry per nontrivial architecture or library choice, with rationale.

| Date | Decision | Rationale |
|------|----------|-----------|
| 2026-07-13 | Repo initialized before any code; incremental commits per logical change | Requested workflow: small checkpointed increments, easy rollback and review |
| 2026-07-13 | Monitor via WordPress REST API over plain HTTP, not Playwright | Blog is stock WordPress behind mod_auth_openidc; REST returns structured JSON with modified timestamps using only the session cookie. Lighter and more reliable than a browser. See investigation.md |
| 2026-07-13 | Playwright kept only for manual re-auth (scripts/reauth.py) | SSO login must stay manual per constraints; visible browser plus saved storage state is the simplest safe flow |
| 2026-07-13 | Silent session refresh added before pause-and-alert | Observed the blog session dies after roughly 8 idle minutes while the SSO session lives much longer. A headless browser visit completes the OIDC redirect with zero credential entry, same as the user's own browser. If SSO shows a login form the refresh backs off and alerts. Deviation from the original pause-immediately spec, flagged for review |
| 2026-07-13 | Session cookie rotation persisted from Set-Cookie headers | mod_auth_openidc rotates the session cookie; ignoring rotation strands the monitor on a stale value |
| 2026-07-13 | Claude model claude-opus-4-8 via messages.parse structured outputs | Guaranteed schema-valid extraction, no JSON repair code. Model configurable via ANTHROPIC_MODEL if cost becomes a concern |
| 2026-07-13 | Change detection requires both timestamp and content hash to differ | WordPress can bump modified_gmt without a real content change; hash guard avoids noise alerts |
| 2026-07-13 | SQLite for dev, Postgres via compose for deploy, same SQLAlchemy models | Single code path, switched by DATABASE_URL |
| 2026-07-13 | Extraction failures never block post storage | Posts are stored first; scripts/backfill_extractions.py re-runs extraction later (needed today: API credit balance was empty) |
