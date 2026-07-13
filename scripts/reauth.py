"""Manual re-authentication helper.

Opens a visible Chromium window on the internship blog. The user logs in
through IITB SSO manually. The script detects when the blog is reachable
while logged in, saves the session (persistent profile plus a storage state
export), and exits.

This script never touches credentials. It only waits for a human login.

Usage:
    python scripts/reauth.py
"""

import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

BLOG_URL = "https://campus.placements.iitb.ac.in/blog/internship/"
PROJECT_ROOT = Path(__file__).resolve().parent.parent
PROFILE_DIR = PROJECT_ROOT / "browser_profile"
STORAGE_STATE_PATH = PROJECT_ROOT / "storage_state.json"

LOGIN_TIMEOUT_SECONDS = 600
POLL_INTERVAL_SECONDS = 2
STABLE_CHECKS_REQUIRED = 3


def is_logged_in(page) -> bool:
    """Consider the session live when the browser sits on the blog URL
    and the page is not an SSO or login form."""
    url = page.url
    if not url.startswith("https://campus.placements.iitb.ac.in/blog"):
        return False
    try:
        content = page.content().lower()
    except Exception:
        return False
    login_markers = ["sso login", "type=\"password\"", "gymkhana sso", "log in to continue"]
    return not any(marker in content for marker in login_markers)


def main() -> int:
    PROFILE_DIR.mkdir(exist_ok=True)
    with sync_playwright() as pw:
        context = pw.chromium.launch_persistent_context(
            str(PROFILE_DIR),
            headless=False,
            viewport={"width": 1280, "height": 900},
        )
        page = context.pages[0] if context.pages else context.new_page()
        page.goto(BLOG_URL, wait_until="domcontentloaded")

        print(f"Browser opened at {BLOG_URL}")
        print("Log in through SSO in the window. This script will detect it.")

        deadline = time.monotonic() + LOGIN_TIMEOUT_SECONDS
        consecutive_ok = 0
        while time.monotonic() < deadline:
            if is_logged_in(page):
                consecutive_ok += 1
                if consecutive_ok >= STABLE_CHECKS_REQUIRED:
                    context.storage_state(path=str(STORAGE_STATE_PATH))
                    print(f"Login detected. Session saved to {STORAGE_STATE_PATH}")
                    print(f"Persistent profile kept at {PROFILE_DIR}")
                    context.close()
                    return 0
            else:
                consecutive_ok = 0
            time.sleep(POLL_INTERVAL_SECONDS)

        print("Timed out waiting for login. Nothing was saved.", file=sys.stderr)
        context.close()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
