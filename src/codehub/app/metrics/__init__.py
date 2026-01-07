"""Prometheus metrics module with multiprocess support."""

import os
import shutil
from pathlib import Path

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    generate_latest,
    multiprocess,
)
from starlette.responses import Response


def setup_metrics(multiproc_dir: str) -> None:
    """Initialize multiprocess metrics directory.

    Must be called before any metrics are created.
    Cleans up stale files from previous runs.
    """
    path = Path(multiproc_dir)
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True, exist_ok=True)
    os.environ["PROMETHEUS_MULTIPROC_DIR"] = str(path)


def get_metrics_response() -> Response:
    """Generate Prometheus metrics with multiprocess aggregation.

    Collects metrics from all worker processes and returns
    aggregated data in Prometheus text format.
    """
    registry = CollectorRegistry()
    multiprocess.MultiProcessCollector(registry)
    return Response(
        content=generate_latest(registry),
        media_type=CONTENT_TYPE_LATEST,
    )
