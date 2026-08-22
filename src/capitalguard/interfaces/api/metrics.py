from prometheus_client import Counter, Histogram, generate_latest, CONTENT_TYPE_LATEST

from capitalguard.infrastructure.observability.metrics import (
    HTTP_REQUEST_LATENCY_SECONDS,
    HTTP_REQUESTS_TOTAL,
    OUTBOX_ATTEMPTS_TOTAL,
    OUTBOX_DELIVERIES_TOTAL,
    OUTBOX_QUEUE_SIZE,
)
from fastapi import APIRouter, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

router = APIRouter(prefix="/metrics", tags=["metrics"])

@router.get("")
def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
