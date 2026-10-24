"""The engine's log: engine.log in the data folder, rotated at 1 MB, 5 files at most.

What goes in: job names, counts, message ids, error type names. What never does:
mail subjects, senders or bodies (no code logs them), and API keys (every line
passes through vault.redact on its way out, whatever logged it).
"""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from ..vault import redact

LOGGER_NAME = "assistant.engine"
MAX_BYTES = 1_000_000
BACKUPS = 4


class RedactKeys(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.msg, record.args = redact(record.getMessage()), None
        return True


def setup_logging(log_dir: Path) -> logging.Logger:
    """Safe to call twice: the old handlers are closed and replaced."""
    log_dir.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger(LOGGER_NAME)
    for handler in list(logger.handlers):
        handler.close()
        logger.removeHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    file = RotatingFileHandler(log_dir / "engine.log", maxBytes=MAX_BYTES, backupCount=BACKUPS, encoding="utf-8")
    file.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    console = logging.StreamHandler(sys.stderr)
    console.setFormatter(logging.Formatter("%(asctime)s %(message)s", "%H:%M:%S"))
    for handler in (file, console):
        handler.addFilter(RedactKeys())
        logger.addHandler(handler)
    return logger
