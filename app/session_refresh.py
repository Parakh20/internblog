"""Silent session refresh using the persistent browser profile.

The blog session cookie dies after a few idle minutes, but the IITB SSO
session at sso.iitb.ac.in lives much longer. A headless visit to the blog
completes the OIDC redirect silently and yields a fresh blog cookie, exactly
as the user's own browser would. This never touches credentials: if SSO
presents a login form the refresh reports failure and manual re-auth via
scripts/reauth.py is required.
"""

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

REFRESH_TIMEOUT_MS = 60000


def silent_refresh(profile_dir: Path, blog_url: str, storage_state_path: Path) -> bool:
    """Attempt a credential-free session refresh. Returns True on success."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        logger.error("playwright not installed, cannot refresh session")
        return False

    try:
        with sync_playwright() as pw:
            context = pw.chromium.launch_persistent_context(str(profile_dir), headless=True)
            try:
                page = context.pages[0] if context.pages else context.new_page()
                page.goto(blog_url, wait_until="networkidle", timeout=REFRESH_TIMEOUT_MS)
                content = page.content().lower()
                on_login_page = 'type="password"' in content or "sso.iitb.ac.in" in page.url
                if on_login_page:
                    logger.warning("silent refresh landed on a login page, manual re-auth needed")
                    return False
                context.storage_state(path=str(storage_state_path))
                logger.info("silent session refresh succeeded, storage state updated")
                return True
            finally:
                context.close()
    except Exception:
        logger.exception("silent session refresh failed")
        return False
