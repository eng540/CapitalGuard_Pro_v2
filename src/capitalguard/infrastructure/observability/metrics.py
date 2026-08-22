from prometheus_client import Counter, Gauge, Histogram


HTTP_REQUESTS_TOTAL = Counter(
    "cg_http_requests_total",
    "Core HTTP requests completed, excluding the metrics scrape endpoint",
    ["method", "route", "status"],
)
HTTP_REQUEST_LATENCY_SECONDS = Histogram(
    "cg_http_request_latency_seconds",
    "Core HTTP request latency, excluding the metrics scrape endpoint",
    ["method", "route", "status"],
)


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
