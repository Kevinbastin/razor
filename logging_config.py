"""
Structured JSON logging configuration.

Design Principle #6: Structured JSON logging from day one,
not print statements.
"""

import logging
import structlog


def setup_logging(log_level: str = "INFO") -> None:
    """
    Configure structlog for JSON output.

    Call this once at application startup (main.py).
    All modules should use `structlog.get_logger(__name__)`.
    """
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.StackInfoRenderer(),
            structlog.dev.set_exc_info,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, log_level.upper(), logging.INFO)
        ),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )
