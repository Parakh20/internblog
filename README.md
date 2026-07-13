# internblog-monitor

Event-driven monitoring for the IIT Bombay internship/placement blog.

Detects new or changed internship postings and pushes alerts via WhatsApp and
Google Calendar, with AI summaries and resume-match suggestions. Single-user
personal project, deployed on an Oracle Cloud Always Free VM via Docker Compose.

## Status

Step 0: investigating whether the blog exposes a pollable endpoint
(JSON/XHR/GraphQL/RSS) before choosing between direct HTTP polling and
Playwright. See docs/investigation.md.

## Docs

- docs/investigation.md: Step 0 endpoint investigation findings
- docs/decisions.md: architecture and library decision log
