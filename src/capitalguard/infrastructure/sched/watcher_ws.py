#START FILE src/capitalguard/infrastructure/sched/watcher_ws.py
#v2

import asyncio
import logging
from dotenv import load_dotenv

load_dotenv()

# ✅ استيراد "مصنع الخدمات" المركزي
from capitalguard.boot import build_services
from capitalguard.infrastructure.market.ws_client import BinanceWS
from capitalguard.domain.entities import OrderType, RecommendationStatus

log = logging.getLogger("capitalguard.watcher")

async def main():
    # ✅ بناء جميع الخدمات مرة واحدة باستخدام الدالة المركزية
    log.info("Building services for the watcher...")
    services = build_services()
    trade_service = services["trade_service"]
    market_data_service = services["market_data_service"]
    ws_client = BinanceWS()

    # ✅ التأكد من أن cache الأصول ممتلئ قبل بدء المراقبة
    log.info("Populating initial symbols cache for the watcher...")
    await market_data_service.refresh_symbols_cache()
    log.info("Symbols cache populated. Starting main watcher loop.")

    async def on_price_update(symbol: str, price: float, _raw_data):
        """
        Core handler for every price tick. Optimized to query once and filter in memory.
        """
        log.debug(f"[WS] {symbol} -> {price}")

        try:
            # ✅ --- 1. تسجيل "تكة" السعر لكل توصية نشطة ---
            active_recs_for_symbol = await asyncio.to_thread(trade_service.repo.list_active_by_symbol, symbol)
            if active_recs_for_symbol:
                events_to_log = [
                    {"recommendation_id": rec.id, "event_type": "TICK", "event_data": {"price": price}}
                    for rec in active_recs_for_symbol
                ]
                await asyncio.to_thread(trade_service.repo.log_events_bulk, events_to_log)

            # ✅ --- 2. استخدام القائمة التي جلبناها بالفعل بدلاً من استعلام جديد ---
            all_open_recs = await asyncio.to_thread(trade_service.repo.list_open)
            pending_recs = [r for r in all_open_recs if r.asset.value == symbol and r.status == RecommendationStatus.PENDING]
            active_recs = active_recs_for_symbol  # استخدام النتيجة المحفوظة

            # --- 1. Activate PENDING recommendations ---
            for rec in pending_recs:
                entry, side = rec.entry.value, rec.side.value
                order_type_val = rec.order_type.value
                is_triggered = False

                if order_type_val == OrderType.LIMIT.value:
                    if (side == "LONG" and price <= entry) or (side == "SHORT" and price >= entry):
                        is_triggered = True
                elif order_type_val == OrderType.STOP_MARKET.value:
                    if (side == "LONG" and price >= entry) or (side == "SHORT" and price <= entry):
                        is_triggered = True

                if is_triggered:
                    await asyncio.to_thread(trade_service.activate_recommendation, rec.id)

            # --- 2. Fast-path SL auto-close for ACTIVE recommendations ---
            for rec in active_recs:
                sl, side = rec.stop_loss.value, rec.side.value
                sl_hit = (side == "LONG" and price <= sl) or (side == "SHORT" and price >= sl)
                if sl_hit:
                    log.warning(f"SL HIT DETECTED for REC #{rec.id} ({symbol}) at price {price}. Closing...")
                    await asyncio.to_thread(trade_service.close, rec.id, price)

        except Exception as e:
            log.error(f"Error during on_price_update for {symbol}: {e}", exc_info=True)

    # --- Main Loop ---
    while True:
        try:
            open_recs_for_symbols = await asyncio.to_thread(trade_service.repo.list_open)
            symbols_to_watch = {rec.asset.value for rec in open_recs_for_symbols}
            if not symbols_to_watch:
                symbols_to_watch = {"BTCUSDT"} # Default to keep connection alive

            log.info(f"Watching symbols: {symbols_to_watch}")
            
            tasks = [ws_client.mini_ticker(sym, on_price_update) for sym in symbols_to_watch]
            await asyncio.gather(*tasks)

        except websockets.ConnectionClosedError:
            log.warning("WebSocket connection closed. Reconnecting in 10s...")
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            log.info("Watcher has been cancelled. Shutting down.")
            break
        except Exception as e:
            log.exception(f"Main WebSocket loop error: {e}. Reconnecting in 30s...")
            await asyncio.sleep(30)

if __name__ == "__main__":
    from capitalguard.logging_conf import setup_logging
    setup_logging()
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("Watcher stopped manually.")
#end