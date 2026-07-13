# Decision Log

One entry per nontrivial architecture or library choice, with rationale.

| Date | Decision | Rationale |
|------|----------|-----------|
| 2026-07-13 | Repo initialized before any code; incremental commits per logical change | Requested workflow: small checkpointed increments, easy rollback and review |
| 2026-07-13 | Monitor via WordPress REST API over plain HTTP, not Playwright | Blog is stock WordPress behind mod_auth_openidc; REST returns structured JSON with modified timestamps using only the session cookie. Lighter and more reliable than a browser. See investigation.md |
| 2026-07-13 | Playwright kept only for manual re-auth (scripts/reauth.py) | SSO login must stay manual per constraints; visible browser plus saved storage state is the simplest safe flow |
