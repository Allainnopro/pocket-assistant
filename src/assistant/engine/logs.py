"""The engine's log: engine.log in the data folder, rotated at 1 MB, 5 files at most.

What goes in: job names, counts, message ids, error type names. What never does:
mail subjects, senders or bodies (no code logs them), and API keys (every line
passes through vault.redact on its way out, whatever logged it).

The console gets its lines through a queue and a thread of its own: in a classic
console window, a stray click (QuickEdit) freezes every write to it until a key
is pressed, and that must never freeze the engine's jobs.
"""

from __future__ import annotations

import logging
import queue
import sys
from logging.handlers import QueueHandler, QueueListener, RotatingFileHandler
from pathlib import Path
from typing import TextIO

from ..vault import redact

LOGGER_NAME = "assistant.engine"
MAX_BYTES = 1_000_000
BACKUPS = 4
_console: QueueListener | None = None


class RedactKeys(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.msg, record.args = redact(record.getMessage()), None
        return True


def setup_logging(log_dir: Path, console: TextIO | None = None) -> logging.Logger:
    """Safe to call twice: the old handlers are closed and replaced. console defaults to stderr."""
    global _console
    stop_logging()
    log_dir.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    file = RotatingFileHandler(log_dir / "engine.log", maxBytes=MAX_BYTES, backupCount=BACKUPS, encoding="utf-8")
    file.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    screen = logging.StreamHandler(console or sys.stderr)
    screen.setFormatter(logging.Formatter("%(asctime)s %(message)s", "%H:%M:%S"))
    lines: queue.SimpleQueue = queue.SimpleQueue()
    _console = QueueListener(lines, screen)
    _console.start()
    for handler in (file, QueueHandler(lines)):
        handler.addFilter(RedactKeys())  # before the queue: nothing unredacted waits in memory
        logger.addHandler(handler)
    return logger


def stop_logging() -> None:
    """Write out what the console still has queued and release engine.log."""
    global _console
    if _console is not None:
        _console.stop()
        _console = None
    logger = logging.getLogger(LOGGER_NAME)
    for handler in list(logger.handlers):
        handler.close()
        logger.removeHandler(handler)

