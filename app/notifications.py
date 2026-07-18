"""Telegram push notification for newly extracted posts."""

import logging

import httpx

from app.extraction import PostCategory

logger = logging.getLogger(__name__)

TELEGRAM_API_BASE = "https://api.telegram.org"
REQUEST_TIMEOUT = 15.0

# Short label per category so a message reads at a glance without repeating
# any placement-office boilerplate from the source post.
_CATEGORY_LABELS = {
    PostCategory.NEW_LISTING: "New posting",
    PostCategory.TEST_UPDATE: "Test/OA update",
    PostCategory.TEST_RESCHEDULE: "Test/OA rescheduled",
    PostCategory.PPT: "PPT",
    PostCategory.SHORTLIST_RESULT: "Shortlist result",
    PostCategory.DEADLINE_EXTENSION: "Deadline extended",
    PostCategory.ADMINISTRATIVE: "Notice",
    PostCategory.OTHER: "Update",
}


def format_notification_message(
    category: PostCategory,
    company: str | None,
    role: str | None,
    deadline: str | None,
    stipend: str | None,
    location: str | None = None,
    application_link: str | None = None,
) -> str:
    label = _CATEGORY_LABELS.get(category, "Update")
    lines = [f"{label}: {company or 'Unknown company'}"]
    if role:
        lines.append(f"Role: {role}")
    if deadline:
        lines.append(f"Date: {deadline}")
    if stipend:
        lines.append(f"Stipend: {stipend}")
    if location:
        lines.append(f"Location: {location}")
    if application_link:
        lines.append(f"Link: {application_link}")
    return "\n".join(lines)


def send_telegram_message(bot_token: str, chat_id: str, text: str) -> int | None:
    """Send a message via the Telegram Bot API. Returns the new message_id
    on success (needed so a later update can edit this exact message), or
    None on failure."""
    url = f"{TELEGRAM_API_BASE}/bot{bot_token}/sendMessage"
    try:
        response = httpx.post(
            url, json={"chat_id": chat_id, "text": text}, timeout=REQUEST_TIMEOUT
        )
        response.raise_for_status()
        return response.json()["result"]["message_id"]
    except httpx.HTTPError:
        logger.exception("failed to send telegram notification")
        return None


def edit_telegram_message(bot_token: str, chat_id: str, message_id: int, text: str) -> bool:
    """Edit a previously-sent message in place, e.g. when a deadline_extension
    updates a company's existing new_listing notification rather than
    sending a duplicate. Returns False if the edit fails (message too old,
    deleted, or identical to current text) - the caller should fall back to
    sending a new message."""
    url = f"{TELEGRAM_API_BASE}/bot{bot_token}/editMessageText"
    try:
        response = httpx.post(
            url,
            json={"chat_id": chat_id, "message_id": message_id, "text": text},
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        return True
    except httpx.HTTPError:
        logger.warning("failed to edit telegram message %s, will fall back to sending new", message_id)
        return False
