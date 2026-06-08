import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


LOGGER_NAME = "trading"
_CONSOLE_FORMAT = "%(asctime)s %(levelname)s %(message)s"


class JsonFormatter(logging.Formatter):
    """Render each log record as one JSON line, merging structured ``fields``."""

    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        fields = getattr(record, "fields", None)
        if fields:
            entry.update(fields)
        if record.exc_info:
            entry["traceback"] = self.formatException(record.exc_info)
        return json.dumps(entry, ensure_ascii=False)


def configure_logging(name: str, directory: Path) -> logging.Logger:
    """Configure the package logger to write JSON lines to a monthly file and the console.

    Writes to ``<directory>/<name>_<YYYY-MM>.jsonl`` and a readable console line.

    Args:
        name: Configuration name, used in the log file stem.
        directory: Directory the log file is written to; created if absent.
    """
    directory.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(logging.INFO)
    if logger.handlers:
        return logger

    month = datetime.now(timezone.utc).strftime("%Y-%m")
    file_handler = logging.FileHandler(directory / f"{name}_{month}.jsonl", encoding="utf-8")
    file_handler.setFormatter(JsonFormatter())
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(logging.Formatter(_CONSOLE_FORMAT))
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)
    return logger


def log_event(logger: logging.Logger, message: str, **fields: Any) -> None:
    """Emit an INFO record carrying structured ``fields`` for the JSON log."""
    logger.info(message, extra={"fields": fields})
