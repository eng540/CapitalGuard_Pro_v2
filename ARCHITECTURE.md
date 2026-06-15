# Architecture
- Clean/Hexagonal تحت src/capitalguard: domain / application / infrastructure / interfaces.
- DB via SQLAlchemy + Alembic. Rate limiting via slowapi. Metrics via Prometheus.
- Sentry اختياري بالمتغير SENTRY_DSN.
- Webhook سري (X-TV-Secret header).
