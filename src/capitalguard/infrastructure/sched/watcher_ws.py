import asyncio
import logging
import os
import websockets  # explicit import for exception handling
from dotenv import load_dotenv

# Load environment variables from .env file for local running
load_dotenv()

from capitalguard.infrastructure.market.ws_client import BinanceWS
from capitalguard.application.services.trade_service import TradeService
from capitalguard.infrastructure.db.repository import RecommendationRepository
from capitalguard.infrastructure.notify.telegram import TelegramNotifier
from capitalguard.domain.entities import OrderType

# Use a named logger instead of basicConfig for better integration
log = logging.getLogger("capitalguard.watcher")


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
        1) Activating PENDING recommendations.
        2) Auto-closing ACTIVE recommendations on SL hit.
        """
        log.debug(f"[WS] {symbol} -> {price}")

        # --- 1) Activate PENDING when conditions met ---
        try:
            pending_recs = await asyncio.to_thread(
                trade_service.list_open, symbol=symbol, status="PENDING"
            )
            for rec in pending_recs:
                entry, side = rec.entry.value, rec.side.value
                order_type = rec.order_type.value  # compare using .value for consistency
                is_triggered = False

                # LIMIT: LONG when price <= entry, SHORT when price >= entry
                if order_type == OrderType.LIMIT.value:
                    if (side == "LONG" and price <= entry) or (side == "SHORT" and price >= entry):
                        is_triggered = True

                # STOP_MARKET: LONG when price >= entry, SHORT when price <= entry
                elif order_type == OrderType.STOP_MARKET.value:
                    if (side == "LONG" and price >= entry) or (side == "SHORT" and price <= entry):
                        is_triggered = True

                if is_triggered:
                    # Single source of truth: activate via TradeService (no price needed for LIMIT/STOP)
                    await asyncio.to_thread(trade_service.activate_recommendation, rec.id)

        except Exception as e:
            log.error(f"Error checking PENDING recommendations for {symbol}: {e}", exc_info=True)

        # --- 2) Fast-path SL auto-close for ACTIVE ---
        try:
            active_recs = await asyncio.to_thread(
                trade_service.list_open, symbol=symbol, status="ACTIVE"
            )
            for rec in active_recs:
                sl, side = rec.stop_loss.value, rec.side.value
                sl_hit = (side == "LONG" and price <= sl) or (side == "SHORT" and price >= sl)
                if sl_hit:
                    log.warning(f"SL HIT for REC #{rec.id} ({symbol}) at price {price}. Closing...")
                    await asyncio.to_thread(trade_service.close, rec.id, price)
        except Exception as e:
            log.error(f"Error checking ACTIVE recommendations for {symbol}: {e}", exc_info=True)

    while True:
        try:
            # Determine symbols to watch from open recommendations
            open_recs_for_symbols = await asyncio.to_thread(trade_service.list_open)
            symbols_to_watch = {rec.asset.value for rec in open_recs_for_symbols}
            if not symbols_to_watch:
                # Keep connection alive with a default symbol
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
    from capitalguard.logging_conf import setup_logging
    setup_logging()
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("Watcher stopped manually.")