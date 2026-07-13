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
