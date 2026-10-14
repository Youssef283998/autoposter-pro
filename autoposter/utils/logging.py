"""Logging setup: rotating files, optional JSON lines, rich console.

Every handler gets a :class:`~autoposter.utils.redact.RedactingFilter` so
tokens never reach disk or the terminal.
"""

from __future__ import annotations

import json
import logging
import logging.handlers
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from autoposter.utils.redact import RedactingFilter, redact_text

LOGGER_NAME = "autoposter"
_LOG_FORMAT = "%(asctime)s %(levelname)-8s %(name)s: %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


class JsonLinesFormatter(logging.Formatter):
    """One JSON object per line; exception text is redacted before output."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            payload["exc"] = redact_text(self.formatException(record.exc_info))
        return json.dumps(payload, ensure_ascii=False)


def get_logger(name: str | None = None) -> logging.Logger:
    """Return a child of the root AutoPoster logger."""
    if not name or name == LOGGER_NAME:
        return logging.getLogger(LOGGER_NAME)
    if name.startswith(f"{LOGGER_NAME}."):
        return logging.getLogger(name)
    return logging.getLogger(f"{LOGGER_NAME}.{name}")


def setup_logging(
    *,
    log_dir: Path | None = None,
    level: str = "INFO",
    json_lines: bool = False,
    console: bool = True,
    max_bytes: int = 5 * 1024 * 1024,
    backup_count: int = 3,
) -> logging.Logger:
    """Configure the AutoPoster root logger.

    Args:
        log_dir: Directory for ``autoposter.log``; ``None`` disables file logs.
        level: Log level name (``DEBUG``, ``INFO``, ...).
        json_lines: Emit JSON lines to the file handler instead of plain text.
        console: Attach a stderr console handler.
        max_bytes: Rotating file size cap.
        backup_count: How many rotated files to keep.
    """
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    logger.propagate = False

    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()

    redactor = RedactingFilter()
    formatter = logging.Formatter(_LOG_FORMAT, datefmt=_DATE_FORMAT)

    if console:
        try:
            from rich.logging import RichHandler

            console_handler: logging.Handler = RichHandler(
                rich_tracebacks=False, show_time=False, show_path=False, markup=False
            )
            console_handler.setFormatter(logging.Formatter("%(message)s"))
        except Exception:  # pragma: no cover - fallback if rich is unavailable
            console_handler = logging.StreamHandler()
            console_handler.setFormatter(formatter)
        console_handler.setLevel(logger.level)
        console_handler.addFilter(redactor)
        logger.addHandler(console_handler)

    if log_dir is not None:
        log_dir.mkdir(parents=True, exist_ok=True)
        file_handler = logging.handlers.RotatingFileHandler(
            log_dir / "autoposter.log",
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding="utf-8",
        )
        file_handler.setFormatter(JsonLinesFormatter() if json_lines else formatter)
        file_handler.setLevel(logging.DEBUG)
        file_handler.addFilter(redactor)
        logger.addHandler(file_handler)

    return logger
