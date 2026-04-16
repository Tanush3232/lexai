"""
Structured logging setup using structlog.

Log level is controlled via the LOG_LEVEL environment variable (default: INFO).
All logs are printed to stdout so they are always visible in the terminal / container logs.
"""
import logging
import os
import sys
import structlog


def setup_logging():
    level_name = os.getenv("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)

    # Ensure Python root logger also emits to stdout at the same level
    logging.basicConfig(
        stream=sys.stdout,
        level=level,
        format="%(message)s",
    )

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.ExceptionRenderer(),
            structlog.dev.ConsoleRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
    )


def get_logger(name: str = "lexai"):
    return structlog.get_logger(name)
