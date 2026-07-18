from app.telegram_link import build_connect_url, generate_link_code, parse_start_command


def test_generate_link_code_is_url_safe_and_reasonably_long():
    code = generate_link_code()
    assert len(code) >= 16
    assert all(c.isalnum() or c in "-_" for c in code)


def test_generate_link_code_is_unique_across_calls():
    assert generate_link_code() != generate_link_code()


def test_build_connect_url_embeds_bot_username_and_code():
    url = build_connect_url("Internblog_bot", "abc123")
    assert url == "https://t.me/Internblog_bot?start=abc123"


def test_parse_start_command_extracts_chat_id_and_code():
    update = {
        "update_id": 1,
        "message": {
            "message_id": 1,
            "chat": {"id": 999888777, "type": "private"},
            "text": "/start abc123",
        },
    }
    result = parse_start_command(update)
    assert result == (999888777, "abc123")


def test_parse_start_command_ignores_non_start_messages():
    update = {"message": {"chat": {"id": 1}, "text": "hello"}}
    assert parse_start_command(update) is None


def test_parse_start_command_ignores_start_without_code():
    update = {"message": {"chat": {"id": 1}, "text": "/start"}}
    assert parse_start_command(update) is None


def test_parse_start_command_ignores_updates_without_a_message():
    assert parse_start_command({"update_id": 2}) is None
