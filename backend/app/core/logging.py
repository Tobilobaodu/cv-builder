"""Structured JSON logging with correlation-ID support for tracing.

Every log line carries a correlation_id so a single processing_jobs.id
can be traced through API requests and across all workers.
"""

import logging
import structlog
from app.core.config import settings


def setup_logging() -> None:
    """Configure structlog for structured JSON logging.

    Correlation IDs are set per-request/worker via context variables.
    """
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.stdlib.filter_by_level,
            structlog.stdlib.add_logger_name,
            structlog.stdlib.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.dev.ConsoleRenderer()
            if settings.environment == "local"
            else structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    # Set root logger level
    logging.getLogger().setLevel(settings.log_level.upper())


def get_logger(name: str = __name__) -> structlog.stdlib.BoundLogger:
    """Get a structured logger bound to the given name."""
    return structlog.get_logger(name)