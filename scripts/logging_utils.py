"""
Lightweight logging utilities for the X scrape pipeline.

Usage:
    from scripts.logging_utils import get_logger
    log = get_logger("phase1")
    log.info("Scroll 3/20 — 12 new, total 45")
"""

import logging
import sys
from pathlib import Path
from datetime import datetime

PROJECT_ROOT = Path(__file__).resolve().parent.parent
LOG_DIR = PROJECT_ROOT / "outputs" / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)


class UnbufferedStreamHandler(logging.StreamHandler):
    """StreamHandler that flushes after every emit — ensures real-time output."""

    def emit(self, record):
        super().emit(record)
        self.flush()


def get_logger(name: str, log_file: str = None) -> logging.Logger:
    """
    Get a logger with console + optional file handler.
    Console is always stdout with utf-8 encoding (no GBK errors).
    File handler writes to outputs/logs/{name}.log with timestamp prefix.
    """
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger  # already configured

    logger.setLevel(logging.INFO)
    # Clear any residual handlers
    logger.handlers.clear()

    # Console handler — utf-8, no emoji to avoid GBK issues
    fmt = logging.Formatter(
        '%(asctime)s [%(levelname)s] %(message)s',
        datefmt='%H:%M:%S'
    )

    ch = UnbufferedStreamHandler(sys.stdout)
    ch.setLevel(logging.INFO)
    ch.setFormatter(fmt)
    logger.addHandler(ch)

    # File handler — full history
    fname = log_file or f"{name}.log"
    fh = logging.FileHandler(LOG_DIR / fname, encoding='utf-8')
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter(
        '%(asctime)s [%(levelname)s] %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    ))
    logger.addHandler(fh)

    return logger


def log_event(logger: logging.Logger, event: str, detail: str = None):
    """Shorthand: log an event with optional detail."""
    if detail:
        logger.info(f"{event} | {detail}")
    else:
        logger.info(event)


def step_log(logger: logging.Logger, phase: str, step: str, status: str):
    """Structured step logging: [PHASE1] STEP scroll | OK"""
    logger.info(f"[{phase.upper()}] {step} | {status}")