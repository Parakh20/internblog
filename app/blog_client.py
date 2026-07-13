"""HTTP client for the WordPress REST API behind IITB SSO.

The blog sits behind Apache mod_auth_openidc. Auth is a single session cookie
read from the Playwright storage state file. Session death is detected, never
worked around: any redirect toward SSO or non-JSON response marks the session
expired and the caller pauses.
"""

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

SESSION_COOKIE_NAME = "mod_auth_openidc_session"
SSO_HOST = "sso.iitb.ac.in"
POSTS_PER_PAGE = 100
REQUEST_TIMEOUT = 30.0


class SessionExpiredError(Exception):
    """The SSO session is no longer valid. Manual re-login is required."""


class CookieLoadError(Exception):
    """The storage state file is missing or has no session cookie."""


@dataclass
class FetchResult:
    posts: list[dict] = field(default_factory=list)
    http_status: int = 0


def classify_response(status_code: int, location: str, content_type: str) -> str:
    """Classify a REST API response as 'ok', 'session_expired', or 'error'.

    Pure function so it is trivially testable. A redirect toward SSO or an
    HTML body where JSON was expected both mean the session died.
    """
    if 300 <= status_code < 400:
        if SSO_HOST in location:
            return "session_expired"
        return "error"
    if status_code == 200:
        if "application/json" in content_type:
            return "ok"
        return "session_expired"
    if status_code in (401, 403):
        return "session_expired"
    return "error"


def load_session_cookie(storage_state_path: Path) -> str:
    if not storage_state_path.exists():
        raise CookieLoadError(f"storage state not found at {storage_state_path}")
    state = json.loads(storage_state_path.read_text())
    for cookie in state.get("cookies", []):
        if cookie.get("name") == SESSION_COOKIE_NAME:
            return cookie["value"]
    raise CookieLoadError(f"no {SESSION_COOKIE_NAME} cookie in {storage_state_path}")


class BlogClient:
    """Fetches posts from the WP REST API using the persisted session cookie.

    The cookie is reloaded from disk whenever the storage state file changes,
    so a manual re-auth (scripts/reauth.py) is picked up without a restart.
    """

    def __init__(self, base_url: str, storage_state_path: Path):
        self.base_url = base_url.rstrip("/")
        self.storage_state_path = storage_state_path
        self._cookie: str | None = None
        self._cookie_mtime: float = 0.0

    def _current_cookie(self) -> str:
        mtime = self.storage_state_path.stat().st_mtime if self.storage_state_path.exists() else 0.0
        if self._cookie is None or mtime != self._cookie_mtime:
            self._cookie = load_session_cookie(self.storage_state_path)
            self._cookie_mtime = mtime
            logger.info("session cookie loaded from storage state")
        return self._cookie

    def fetch_posts(self) -> FetchResult:
        """Fetch all posts, following REST pagination.

        Raises SessionExpiredError when the session is dead and httpx errors
        or RuntimeError on other failures. Returns the full post list on
        success so removal detection can compare against the database.
        """
        cookie = self._current_cookie()
        posts: list[dict] = []
        page = 1
        last_status = 0
        with httpx.Client(timeout=REQUEST_TIMEOUT, follow_redirects=False) as client:
            while True:
                resp = client.get(
                    f"{self.base_url}/index.php",
                    params={
                        "rest_route": "/wp/v2/posts",
                        "per_page": POSTS_PER_PAGE,
                        "page": page,
                        "orderby": "modified",
                        "order": "desc",
                    },
                    headers={"Cookie": f"{SESSION_COOKIE_NAME}={cookie}"},
                )
                last_status = resp.status_code
                verdict = classify_response(
                    resp.status_code,
                    resp.headers.get("location", ""),
                    resp.headers.get("content-type", ""),
                )
                if verdict == "session_expired":
                    raise SessionExpiredError(
                        f"HTTP {resp.status_code} on page {page}, redirect or non-JSON response"
                    )
                if verdict == "error":
                    raise RuntimeError(f"unexpected HTTP {resp.status_code} on page {page}")

                batch = resp.json()
                if not isinstance(batch, list):
                    raise RuntimeError(f"expected JSON list, got {type(batch).__name__}")
                posts.extend(batch)

                total_pages = int(resp.headers.get("x-wp-totalpages", "1"))
                if page >= total_pages or not batch:
                    break
                page += 1
        return FetchResult(posts=posts, http_status=last_status)
