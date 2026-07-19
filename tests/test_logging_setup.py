import json

from app.logging_setup import read_recent_log_lines


def test_read_recent_log_lines_returns_empty_list_when_file_missing(tmp_path):
    assert read_recent_log_lines(tmp_path / "missing.jsonl") == []


def test_read_recent_log_lines_returns_newest_first(tmp_path):
    log_path = tmp_path / "internblog.jsonl"
    log_path.write_text(
        "\n".join(
            json.dumps({"ts": f"2026-07-19T10:0{i}:00+0000", "level": "INFO", "logger": "x", "message": f"line {i}"})
            for i in range(3)
        )
        + "\n"
    )

    entries = read_recent_log_lines(log_path)

    assert [e["message"] for e in entries] == ["line 2", "line 1", "line 0"]


def test_read_recent_log_lines_respects_limit(tmp_path):
    log_path = tmp_path / "internblog.jsonl"
    log_path.write_text(
        "\n".join(
            json.dumps({"ts": "2026-07-19T10:00:00+0000", "level": "INFO", "logger": "x", "message": f"line {i}"})
            for i in range(10)
        )
        + "\n"
    )

    entries = read_recent_log_lines(log_path, limit=3)

    assert len(entries) == 3
    assert entries[0]["message"] == "line 9"


def test_read_recent_log_lines_skips_unparseable_lines(tmp_path):
    log_path = tmp_path / "internblog.jsonl"
    log_path.write_text(
        json.dumps({"ts": "2026-07-19T10:00:00+0000", "level": "INFO", "logger": "x", "message": "good"})
        + "\nnot valid json\n"
    )

    entries = read_recent_log_lines(log_path)

    assert len(entries) == 1
    assert entries[0]["message"] == "good"
