"""Structured logging configuration using structlog.

Provides JSON output for production, colored console output for development,
and optional file rotation via Python's RotatingFileHandler.

Call `setup_logging()` early in application startup before any log calls.
"""

import logging
import sys
from logging.handlers import RotatingFileHandler

import structlog


def setup_logging(
    level: str = "INFO",
    json_output: bool = True,
    log_file: str | None = None,
) -> None:
    """Configure structlog and stdlib logging for the application.

    Args:
        level: Log level string (DEBUG, INFO, WARNING, ERROR).
        json_output: If True, render logs as JSON (production).
                     If False, use colored console output (development).
        log_file: Optional file path for log output with rotation
                  (max 10 MB per file, 5 backups kept).
    """
    log_level = getattr(logging, level.upper(), logging.INFO)

    # Shared processors applied to every log event
    shared_processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.UnicodeDecoder(),
    ]

    if json_output:
        renderer: structlog.types.Processor = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer(colors=True)

    # Configure structlog
    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    # Build stdlib formatter that applies structlog processors
    formatter = structlog.stdlib.ProcessorFormatter(
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            renderer,
        ],
    )

    # Root logger setup
    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)

    # Clear existing handlers to avoid duplicates on re-init
    root_logger.handlers.clear()

    # Console handler (always present)
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    console_handler.setLevel(log_level)
    root_logger.addHandler(console_handler)

    # File handler with rotation (optional)
    if log_file:
        file_handler = RotatingFileHandler(
            filename=log_file,
            maxBytes=10 * 1024 * 1024,  # 10 MB
            backupCount=5,
            encoding="utf-8",
        )
        file_handler.setFormatter(formatter)
        file_handler.setLevel(log_level)
        root_logger.addHandler(file_handler)


def get_logger(**initial_context: object) -> structlog.stdlib.BoundLogger:
    """Return a structlog logger, optionally bound with initial context.

    Thin wrapper around structlog.get_logger() for consistent imports.
    """
    return structlog.get_logger(**initial_context)
