# Runbook
## Local
1) إنشاء بيئة وتثبيت التبعيات.
2) إعداد .env.
3) تشغيل الهجرات: `make migrate`.
4) تشغيل API: `make dev`.
5) تشغيل البوت: `make bot`.
6) تشغيل الـ watcher: `make watcher`.

## API
- `GET /health`
- `POST /recommendations` (Header: X-API-Key)
- `GET /recommendations` (Header: X-API-Key)
- `POST /recommendations/{id}/close` (Header: X-API-Key)
- `GET /report` (Header: X-API-Key)
- `POST /webhook/tradingview` (Header: X-TV-Secret)

## Metrics
- `GET /metrics` (Prometheus format)
