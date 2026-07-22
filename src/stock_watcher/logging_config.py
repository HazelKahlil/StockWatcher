"""Safe, local logging defaults for the Mac Replay foundation."""

from __future__ import annotations

import logging
import re
from logging.handlers import RotatingFileHandler
from pathlib import Path

DEFAULT_MAX_BYTES = 1_000_000
DEFAULT_BACKUP_COUNT = 30
_SENSITIVE_VALUE = re.compile(
    r"(?i)\b(token|api[_-]?key|secret|password|account(?:_id)?)\b\s*([=:])\s*([^\s,;]+)"
)


class RedactingFilter(logging.Filter):
    """Redact credentials and account identifiers before a record reaches a handler."""

    def filter(self, record: logging.LogRecord) -> bool:
        rendered = record.getMessage()
        record.msg = _SENSITIVE_VALUE.sub(r"\1\2[REDACTED]", rendered)
        record.args = ()
        return True


def configure_logging(
    log_dir: Path,
    *,
    logger_name: str = "stock_watcher",
    max_bytes: int = DEFAULT_MAX_BYTES,
    backup_count: int = DEFAULT_BACKUP_COUNT,
) -> logging.Logger:
    """Configure a redacted rolling file logger without recording user or market payloads."""
    if max_bytes < 1 or backup_count < 1:
        raise ValueError("max_bytes and backup_count must be positive")

    log_dir.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger(logger_name)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    logger.handlers.clear()

    handler = RotatingFileHandler(
        log_dir / "stock-watcher.log",
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding="utf-8",
    )
    handler.addFilter(RedactingFilter())
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    logger.addHandler(handler)
    return logger
