# Step 0: Endpoint Investigation

Goal: determine whether the IITB internship blog exposes an underlying
JSON/XHR/GraphQL endpoint, RSS/Atom feed, WebSocket, or SSE stream that can be
polled directly with an authenticated session, instead of driving a full
browser with Playwright.

## Status: in progress

Waiting on authenticated session material from the user (cookies or storage
state) to inspect the site's network behavior.

## Checklist

- [ ] Identify the blog base URL and page structure
- [ ] Inspect network traffic for JSON/XHR calls backing the post list
- [ ] Check for GraphQL endpoints
- [ ] Check for RSS/Atom feeds (common paths: /feed, /rss, /atom.xml, link tags)
- [ ] Check for WebSocket or SSE traffic
- [ ] Test whether candidate endpoints work with plain cookie-authenticated
      HTTP requests (no browser)
- [ ] Document chosen approach and why

## Findings

(to be filled in)

## Decision

(to be filled in)
