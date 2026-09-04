"""Pushes the worker-only counters (PUSH_REGISTRY) to a Prometheus Pushgateway.

Only needed for metrics that increment inside Celery worker processes,
which Prometheus never scrapes directly (see app/core/metrics.py's
module docstring for why). Grouped by hostname+pid so each worker process
gets its own series rather than overwriting another worker's last-pushed
value under the same grouping key.

Never allowed to raise into caller code: a Pushgateway outage should not
fail a real job. Best-effort, logged on failure.
"""

import os
import socket

from prometheus_client import push_to_gateway

from app.core.config import settings
from app.core.logging import get_logger
from app.core.metrics import PUSH_REGISTRY

logger = get_logger(__name__)

_GROUPING_KEY = {"instance": f"{socket.gethostname()}:{os.getpid()}"}


def push_worker_metrics(job_name: str) -> None:
    try:
        push_to_gateway(
            settings.pushgateway_url,
            job=job_name,
            grouping_key=_GROUPING_KEY,
            registry=PUSH_REGISTRY,
            timeout=5,
        )
    except Exception as e:
        logger.warning("pushgateway_push_failed", job_name=job_name, error=str(e))
