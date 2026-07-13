# Step 0: Endpoint Investigation

Goal: determine whether the IITB internship blog exposes an underlying
JSON/XHR/GraphQL endpoint, RSS/Atom feed, WebSocket, or SSE stream that can be
polled directly with an authenticated session, instead of driving a full
browser with Playwright.

Investigated: 2026-07-13, using a session obtained via manual SSO login in a
visible Playwright browser (scripts/reauth.py).

## Status: complete

## What the site is

`https://campus.placements.iitb.ac.in/blog/internship/` is a stock
**WordPress 5.8** install (twentysixteen theme), served behind Apache
`mod_auth_openidc` which enforces IITB SSO (OpenID Connect against
`sso.iitb.ac.in`). Authentication is a single cookie:
`mod_auth_openidc_session`. Without it, every request 302-redirects to the SSO
authorize endpoint.

## What was checked

| Check | Result |
|-------|--------|
| XHR/JSON calls during page load | None. Page is fully server-rendered HTML; only static assets load (CSS/JS/fonts). |
| GraphQL | None present. |
| WebSocket / SSE | None present. |
| WP REST API | **Works.** `index.php?rest_route=/wp/v2/posts` returns full post JSON with plain cookie-authenticated HTTP (no browser). Supports `per_page`, `orderby=modified`, pagination via `X-WP-Total` headers. Each post includes `id`, `slug`, `title`, full `content.rendered` HTML, `date_gmt`, and `modified_gmt`. |
| RSS feed | **Works.** `?feed=rss2` returns valid RSS with the same cookie. Less useful than REST (no modified timestamps, truncated by default), kept as fallback only. |
| Unauthenticated access | 302 to `sso.iitb.ac.in/authorize` (OIDC code flow with PKCE). This redirect is a clean, reliable "session dead" signal. |

Note: pretty permalinks for the REST route (`/wp-json/...`) were not relied on;
the `index.php?rest_route=` form is used since it is advertised by the page's
`rel="https://api.w.org/"` link and confirmed working.

## Decision

**Poll the WP REST API directly with plain HTTP (httpx) and the persisted
`mod_auth_openidc_session` cookie. No Playwright in the monitoring loop.**

Rationale:

- The REST API returns structured JSON with both `date_gmt` and
  `modified_gmt`, which makes new-post and edited-post detection trivial and
  exact, better than diffing rendered HTML.
- A cookie-authenticated `httpx` GET every few minutes is far lighter and more
  reliable on a small always-free VM than a persistent headless browser.
- Regular polling doubles as the session keepalive the user observed
  (mod_auth_openidc sessions stay alive with activity).
- Session-expiry detection is unambiguous: any 302 toward `sso.iitb.ac.in`
  or non-JSON response means the session died; the system pauses and alerts
  for manual re-login (scripts/reauth.py), never attempting SSO automation.

Playwright remains in the project solely for the manual re-auth flow
(visible browser, human logs in, session saved).

## Key endpoints

- Posts: `https://campus.placements.iitb.ac.in/blog/internship/index.php?rest_route=/wp/v2/posts&per_page=100&orderby=modified&order=desc`
- Single post: `...?rest_route=/wp/v2/posts/<id>`
- RSS fallback: `https://campus.placements.iitb.ac.in/blog/internship/?feed=rss2`

Currently 18 posts total (fits in one page; pagination handled anyway).
