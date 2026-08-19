from prometheus_client import Counter, Gauge


OUTBOX_ATTEMPTS_TOTAL = Counter(
    "cg_publication_outbox_attempts_total",
    "Publication outbox delivery attempts",
    ["operation"],
)
OUTBOX_DELIVERIES_TOTAL = Counter(
    "cg_publication_outbox_deliveries_total",
    "Publication outbox delivery outcomes",
    ["operation", "status"],
)
OUTBOX_QUEUE_SIZE = Gauge(
    "cg_publication_outbox_queue_size",
    "Publication outbox records currently waiting for processing",
)
