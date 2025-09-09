# --- START OF COMPLETE MODIFIED FILE: src/capitalguard/infrastructure/sched/watcher_ws.py ---
import asyncio
import logging
import os
import websockets
from dotenv import load_dotenv

# Load environment variables from .env file for local running
load_dotenv()

from capitalguard.infrastructure.market.ws_client import BinanceWS
from capitalguard.application.services.trade_service import TradeService
from capitalguard.infrastructure.db.repository import RecommendationRepository
from capitalguard.infrastructure.notify.telegram import TelegramNotifier
from capitalguard.domain.entities import OrderType

# Use a named logger for consistency
log = logging.getLogger("capitalguard.watcher")


async def main():
    """
    The main WebSocket client loop. Subscribes to price streams for all
    open recommendations and triggers actions based on price movements.
    """
    # Note: In a larger application, these would be injected, but for a standalone
    # script, direct instantiation is acceptable.
    repo = RecommendationRepository()
    notifier = TelegramNotifier()
    trade_service = TradeService(repo=repo, notifier=notifier)
    ws_client = BinanceWS()

    async def on_price_update(symbol: str, price: float, _raw_data):
        """
        Core handler for every price tick. It checks for two main conditions:
        1. Activating PENDING recommendations with the correct logic.
        2. Rapidly auto-closing ACTIVE recommendations on SL hit.
        """
        log.debug(f"[WS] {symbol} -> {price}")

        # --- 1. Activate PENDING recommendations ---
        try:
            # ✅ FIX: Call the repository method directly for a cleaner separation of concerns.
            pending_recs = await asyncio.to_thread(
                trade_service.repo.list_open, symbol=symbol, status="PENDING"
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
                    # Use the dedicated activation function in TradeService
                    await asyncio.to_thread(trade_service.activate_recommendation, rec.id)

        except Exception as e:
            log.error(f"Error checking PENDING recommendations for {symbol}: {e}", exc_info=True)

        # --- 2. Fast-path SL auto-close for ACTIVE recommendations ---
        try:
            # ✅ FIX: Call the repository method directly here as well.
            active_recs = await asyncio.to_thread(
                trade_service.repo.list_open, symbol=symbol, status="ACTIVE"
            )
            for rec in active_recs:
                sl, side = rec.stop_loss.value, rec.side.value
                sl_hit = (side == "LONG" and price <= sl) or (side == "SHORT" and price >= sl)
                if sl_hit:
                    log.warning(f"SL HIT DETECTED for REC #{rec.id} ({symbol}) at price {price}. Closing...")
                    await asyncio.to_thread(trade_service.close, rec.id, price)
        except Exception as e:
            log.error(f"Error checking ACTIVE recommendations for {symbol}: {e}", exc_info=True)

    while True:
        try:
            # ✅ FIX: Correctly call the repository's list_open method.
            open_recs_for_symbols = await asyncio.to_thread(trade_service.repo.list_open)
            symbols_to_watch = {rec.asset.value for rec in open_recs_for_symbols}
            
            if not symbols_to_watch:
                # To keep the connection alive, watch a default symbol if no trades are open.
                symbols_to_watch = {"BTCUSDT"}

            log.info(f"Watching symbols: {symbols_to_watch}")
            
            tasks = [ws_client.mini_ticker(sym, on_price_update) for sym in symbols_to_watch]
            await asyncio.gather(*tasks)

        # ✅ FIX: Use the correct exception path for modern websockets library versions.
        except websockets.ConnectionClosedError:
            log.warning("WebSocket connection closed. Reconnecting in 10s...")
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            log.info("Watcher has been cancelled. Shutting down.")
            break # Exit the loop gracefully
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
# --- END OF COMPLETE MODIFIED FILE ---