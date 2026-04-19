"""Structured logging for AgentForge."""

from __future__ import annotations

import logging
import sys
from typing import Any

from agentforge.config import get_config

_CONFIGURED = False


def setup_logging() -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return
    level = getattr(logging, get_config().log_level.upper(), logging.INFO)
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s")
    )
    root = logging.getLogger("agentforge")
    root.setLevel(level)
    root.addHandler(handler)
    root.propagate = False
    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    setup_logging()
    if not name.startswith("agentforge"):
        name = f"agentforge.{name}"
    return logging.getLogger(name)


def log_event(logger: logging.Logger, event: str, **fields: Any) -> None:
    """Log a structured event."""
    rendered = " ".join(f"{k}={v!r}" for k, v in fields.items())
    logger.info("%s %s", event, rendered)
