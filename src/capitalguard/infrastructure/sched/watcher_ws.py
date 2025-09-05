# --- START OF FILE: src/capitalguard/infrastructure/sched/watcher_ws.py ---
import asyncio
import logging
import os
from dotenv import load_dotenv

# Load environment variables from .env file for local running
load_dotenv()

from capitalguard.infrastructure.market.ws_client import BinanceWS
from capitalguard.application.services.trade_service import TradeService
from capitalguard.infrastructure.db.repository import RecommendationRepository
from capitalguard.infrastructure.notify.telegram import TelegramNotifier
from capitalguard.domain.entities import RecommendationStatus, OrderType

logging.basicConfig(level=logging.INFO, format='%(asctime)s | %(name)s | %(levelname)s | %(message)s')
log = logging.getLogger(__name__)


async def main():
    """
    The main WebSocket client loop. It subscribes to price streams for all
    open recommendations and triggers actions based on price movements.
    """
    repo = RecommendationRepository()
    notifier = TelegramNotifier()
    trade_service = TradeService(repo=repo, notifier=notifier)
    ws_client = BinanceWS()

    async def on_price_update(symbol: str, price: float, _raw_data):
        """
        The core handler for every price tick received from the WebSocket.
        It checks for two main conditions:
        1. Activating PENDING recommendations.
        2. Auto-closing ACTIVE recommendations on SL hit.
        """
        # --- 1. Check for PENDING recommendations to activate ---
        pending_recs = await asyncio.to_thread(
            trade_service.list_open, symbol=symbol, status="PENDING"
        )
        for rec in pending_recs:
            entry, side, order_type = rec.entry.value, rec.side.value, rec.order_type
            is_triggered = False

            # Correct activation logic for LIMIT orders
            if order_type == OrderType.LIMIT:
                if (side == 'LONG' and price <= entry) or \
                   (side == 'SHORT' and price >= entry):
                    is_triggered = True
            
            # Correct activation logic for STOP_MARKET orders
            elif order_type == OrderType.STOP_MARKET:
                if (side == 'LONG' and price >= entry) or \
                   (side == 'SHORT' and price <= entry):
                    is_triggered = True

            if is_triggered:
                try:
                    # Activate using the intended entry price, not the live price
                    await asyncio.to_thread(trade_service.activate_recommendation, rec.id, rec.entry.value)
                except Exception as e:
                    log.error(f"Auto-activation failed for REC #{rec.id}: {e}")

        # --- 2. Check for ACTIVE recommendations to auto-close ---
        active_recs = await asyncio.to_thread(
            trade_service.list_open, symbol=symbol, status="ACTIVE"
        )
        for rec in active_recs:
            sl, side = rec.stop_loss.value, rec.side.value
            
            # This is a simplified auto-close logic, the full logic is in AlertService
            # This part ensures immediate closure on SL hit via WebSocket
            sl_hit = (side == "LONG" and price <= sl) or \
                     (side == "SHORT" and price >= sl)
            
            if sl_hit:
                try:
                    log.warning(f"SL HIT DETECTED for REC #{rec.id} ({symbol}) at price {price}. Closing...")
                    await asyncio.to_thread(trade_service.close, rec.id, price)
                except Exception as e:
                    log.error(f"Auto-close failed for REC #{rec.id}: {e}")

    while True:
        try:
            # Fetch all unique symbols for open recommendations to subscribe
            open_recs_for_symbols = await asyncio.to_thread(trade_service.list_open)
            symbols_to_watch = {rec.asset.value for rec in open_recs_for_symbols}
            
            if not symbols_to_watch:
                # Watch a default symbol to keep the connection alive if no trades are open
                symbols_to_watch = {"BTCUSDT"}
            
            log.info(f"Refreshing WebSocket connections. Symbols under watch: {symbols_to_watch}")
            
            # Create a task for each symbol's price stream
            tasks = [ws_client.mini_ticker(sym, on_price_update) for sym in symbols_to_watch]
            await asyncio.gather(*tasks)

        except websockets.exceptions.ConnectionClosedError:
            log.warning("WebSocket connection closed. Reconnecting in 10s...")
            await asyncio.sleep(10)
        except Exception as e:
            log.exception(f"Main WebSocket loop error: {e}. Reconnecting in 30s...")
            await asyncio.sleep(30)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("Watcher stopped manually.")
# --- END OF FILE ---