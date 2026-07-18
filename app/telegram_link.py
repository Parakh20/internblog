"""Correlates an inbound Telegram /start deep link back to a signed-in
user, so they never have to look up or paste a raw chat_id.

Flow: the settings page shows a link of the form
https://t.me/<bot>?start=<code>, where <code> is a single-use token stored
on the user's row (User.telegram_link_code). When the user taps the link
and hits Start in Telegram, Telegram sends our webhook an Update whose
message text is "/start <code>" - parse_start_command extracts the
(chat_id, code) pair so the webhook handler can look up the matching user
and record their chat_id.
"""

import secrets

START_PREFIX = "/start "


def generate_link_code() -> str:
    return secrets.token_urlsafe(16)


def build_connect_url(bot_username: str, code: str) -> str:
    return f"https://t.me/{bot_username}?start={code}"


def parse_start_command(update: dict) -> tuple[int, str] | None:
    """Returns (chat_id, code) if this Update is a "/start <code>" message,
    else None. Ignores updates with no message, non-/start text, or a bare
    /start with no code."""
    message = update.get("message")
    if not message:
        return None
    text = message.get("text", "")
    if not text.startswith(START_PREFIX):
        return None
    code = text[len(START_PREFIX):].strip()
    if not code:
        return None
    chat_id = message.get("chat", {}).get("id")
    if chat_id is None:
        return None
    return chat_id, code
