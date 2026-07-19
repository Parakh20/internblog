"""Structured JSON lines logging with rotation, plus readable console output."""

import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

MAX_LOG_BYTES = 5 * 1024 * 1024
BACKUP_COUNT = 5


class JsonLinesFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            entry["exception"] = self.formatException(record.exc_info)
        extra = getattr(record, "extra_fields", None)
        if extra:
            entry.update(extra)
        return json.dumps(entry, ensure_ascii=False)


def setup_logging(log_dir: Path, level: int = logging.INFO) -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    root.setLevel(level)

    file_handler = RotatingFileHandler(
        log_dir / "internblog.jsonl", maxBytes=MAX_LOG_BYTES, backupCount=BACKUP_COUNT
    )
    file_handler.setFormatter(JsonLinesFormatter())
    root.addHandler(file_handler)

    console = logging.StreamHandler()
    console.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    root.addHandler(console)


def log_event(logger: logging.Logger, message: str, **fields) -> None:
    """Log with structured extra fields that land in the JSON lines file."""
    logger.info(message, extra={"extra_fields": fields})


def read_recent_log_lines(log_path: Path, limit: int = 200) -> list[dict]:
    """Reads the last `limit` entries from the JSON-lines log file, newest
    first, for display on /admin. Only reads the active file, not rotated
    backups (internblog.jsonl.1, .2, ...) - "recent" activity doesn't need
    them, and reading just the live file keeps this cheap on every /admin
    load. Lines that fail to parse (e.g. a write caught mid-flush) are
    skipped rather than raising, so one bad line doesn't blank the page."""
    if not log_path.exists():
        return []
    with log_path.open(encoding="utf-8") as f:
        lines = f.readlines()
    entries = []
    for line in lines[-limit:]:
        line = line.strip()
        if not line:
            continue
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    entries.reverse()
    return entries
