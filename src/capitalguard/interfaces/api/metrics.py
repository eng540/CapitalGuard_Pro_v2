from prometheus_client import Counter, Histogram, generate_latest, CONTENT_TYPE_LATEST

from capitalguard.infrastructure.observability.metrics import (
    OUTBOX_ATTEMPTS_TOTAL,
    OUTBOX_DELIVERIES_TOTAL,
    OUTBOX_QUEUE_SIZE,
)
from fastapi import APIRouter, Response

router = APIRouter(prefix="/metrics", tags=["metrics"])

REQUESTS = Counter("cg_requests_total", "Total API requests")
LATENCY = Histogram("cg_request_latency_seconds", "Request latency")

@router.get("")
def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)