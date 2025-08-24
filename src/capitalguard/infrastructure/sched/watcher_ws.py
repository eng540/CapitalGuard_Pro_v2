import asyncio
import logging

from capitalguard.infrastructure.market.ws_client import BinanceWS
from capitalguard.application.services.trade_service import TradeService
from capitalguard.infrastructure.db.repository import RecommendationRepository
from capitalguard.infrastructure.notify.telegram import TelegramNotifier

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')


async def main():
    repo = RecommendationRepository()
    notifier = TelegramNotifier()
    trade = TradeService(repo=repo, notifier=notifier)
    ws = BinanceWS()

    async def on_price(symbol: str, price: float, _raw):
        logging.info(f"[WS] {symbol} -> {price}")
        open_recs = [r for r in trade.list_open() if r.asset.value == symbol]
        if not open_recs:
            return

        for rec in open_recs:
            sl = rec.stop_loss.value
            side = rec.side.value
            hit_sl = (side in ("LONG", "SPOT") and price <= sl) or (side == "SHORT" and price >= sl)
            if hit_sl:
                try:
                    trade.close(rec.id, price)
                    logging.warning(f"Auto-closed #{rec.id} at {price} (SL hit).")
                except Exception as e:
                    logging.error(f"Auto-close failed for #{rec.id}: {e}")

        # ملاحظة: يمكنك لاحقًا إضافة منطق تتبع الأهداف TP لإرسال إشعارات دون إغلاق.

    while True:
        try:
            symbols = {rec.asset.value for rec in trade.list_open()} or {"BTCUSDT"}
            logging.info(f"Symbols under watch: {symbols}")
            tasks = [ws.mini_ticker(sym, on_price) for sym in symbols]
            await asyncio.gather(*tasks)
        except Exception as e:
            logging.error(f"WS error: {e}. Reconnecting in 30s...")
            await asyncio.sleep(30)


if __name__ == "__main__":
    asyncio.run(main())