import asyncio
from capitalguard.infrastructure.market.ws_client import BinanceWS
from capitalguard.infrastructure.notify.telegram import TelegramNotifier
from capitalguard.application.services.trade_service import TradeService
from capitalguard.infrastructure.db.repository import RecommendationRepository

async def main():
    repo = RecommendationRepository()
    notifier = TelegramNotifier()
    trade = TradeService(repo=repo, notifier=notifier)
    ws = BinanceWS()

    async def handle(symbol: str, price: float, _raw):
        for rec in trade.list_open():
            if rec.asset.value != symbol: continue
            hit_target = any(price >= t for t in rec.targets.values) if rec.side.value in ("LONG","SPOT") else any(price <= t for t in rec.targets.values)
            hit_sl = (price <= rec.stop_loss.value) if rec.side.value in ("LONG","SPOT") else (price >= rec.stop_loss.value)
            if hit_target:
                notifier.publish(f"🎯 Target hit for {symbol} at {price}. Rec ID={rec.id}")
            if hit_sl:
                notifier.publish(f"🛑 Stop-loss reached for {symbol} at {price}. Rec ID={rec.id}")

    # Subscribe to unique symbols from open recs
    symbols = {rec.asset.value for rec in trade.list_open()}
    if not symbols:
        symbols = {"BTCUSDT"}
    await asyncio.gather(*(ws.mini_ticker(sym, handle) for sym in symbols))

if __name__ == "__main__":
    asyncio.run(main())
