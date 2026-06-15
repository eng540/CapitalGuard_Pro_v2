# CapitalGuard Pro v2

**منتج جاهز للتشغيل** لبناء ونشر نظام إدارة توصيات وصفقات مع:
- FastAPI REST (محمية بمفتاح API + Rate limiting)
- TradingView Webhook (Secret)
- Telegram Bot
- WebSocket Price Watcher (Binance) + Polling REST fallback
- Alembic migrations
- Metrics (Prometheus) + Sentry (اختياري)
- Docker Compose + CI + اختبارات

## تشغيل سريع
```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env && edit .env
make migrate
make dev   # API at http://127.0.0.1:8000/health
make bot   # Telegram bot
make watcher
```
أو عبر Docker:
```bash
docker compose up --build
```
