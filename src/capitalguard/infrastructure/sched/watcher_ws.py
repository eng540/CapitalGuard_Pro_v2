# --- START OF FINAL, REVIEWED, AND ROBUST FILE (V10): src/capitalguard/infrastructure/sched/watcher_ws.py ---
import asyncio
import logging
from dotenv import load_dotenv

load_dotenv()

from capitalguard.infrastructure.market.ws_client import BinanceWS
from capitalguard.application.services.trade_service import TradeService
from capitalguard.infrastructure.db.repository import RecommendationRepository
from capitalguard.infrastructure.notify.telegram import TelegramNotifier
from capitalguard.domain.entities import OrderType, RecommendationStatus

log = logging.getLogger("capitalguard.watcher")

async def main():
    # --- Service Initialization ---
    repo = RecommendationRepository()
    notifier = TelegramNotifier()
    trade_service = TradeService(repo=repo, notifier=notifier)
    ws_client = BinanceWS()

    async def on_price_update(symbol: str, price: float, _raw_data):
        """
        Core handler for every price tick. Optimized to query only relevant data.
        """
        log.debug(f"[WS] {symbol} -> {price}")

        try:
            # --- 1. Activate PENDING recommendations ---
            # ✅ FIX: Query only for PENDING recommendations for the specific symbol
            pending_recs = await asyncio.to_thread(
                trade_service.repo.list_open, symbol=symbol, status=RecommendationStatus.PENDING
            )
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
                    # This service call now handles event logging
                    await asyncio.to_thread(trade_service.activate_recommendation, rec.id)

            # --- 2. Fast-path SL auto-close for ACTIVE recommendations ---
            # ✅ FIX: Query only for ACTIVE recommendations for the specific symbol
            active_recs = await asyncio.to_thread(
                trade_service.repo.list_open, symbol=symbol, status=RecommendationStatus.ACTIVE
            )
            for rec in active_recs:
                sl, side = rec.stop_loss.value, rec.side.value
                sl_hit = (side == "LONG" and price <= sl) or (side == "SHORT" and price >= sl)
                if sl_hit:
                    log.warning(f"SL HIT DETECTED for REC #{rec.id} ({symbol}) at price {price}. Closing...")
                    # This service call now handles event logging
                    await asyncio.to_thread(trade_service.close, rec.id, price)

        except Exception as e:
            log.error(f"Error during on_price_update for {symbol}: {e}", exc_info=True)

    # --- Main Loop ---
    while True:
        try:
            # Fetch all open recommendations once to determine which symbols to watch
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
# --- END OF FINAL, REVIEWED, AND ROBUST FILE (V10) ---