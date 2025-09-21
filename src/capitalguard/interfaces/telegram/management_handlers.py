#--- START OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE: src/capitalguard/interfaces/telegram/management_handlers.py ---
import logging
from telegram import Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    ConversationHandler,
    MessageHandler,
    CommandHandler,
    filters,
)

from capitalguard.interfaces.telegram.handlers import (
    navigate_open_recs_handler,
    show_rec_panel_handler,
    strategy_menu_handler,
    confirm_close_handler,
    cancel_close_handler,
    show_edit_menu_handler,
    start_edit_sl_handler,
    start_edit_tp_handler,
    start_profit_stop_handler,
    set_strategy_handler,
    update_private_card,
    update_public_card,
    show_close_menu_handler,
    close_at_market_handler,
    close_with_manual_price_handler,
    partial_profit_start,
    received_partial_percent,
    received_partial_price,
    cancel_partial_profit,
    unified_reply_handler,
    AWAIT_PARTIAL_PERCENT,
    AWAIT_PARTIAL_PRICE,
)


def register_management_handlers(application: Application):
    application.add_handler(CallbackQueryHandler(navigate_open_recs_handler, pattern=r"^open_nav:page:", block=False))
    application.add_handler(CallbackQueryHandler(show_rec_panel_handler, pattern=r"^rec:show_panel:", block=False))
    application.add_handler(CallbackQueryHandler(show_rec_panel_handler, pattern=r"^rec:back_to_main:", block=False))
    application.add_handler(CallbackQueryHandler(strategy_menu_handler, pattern=r"^rec:strategy_menu:", block=False))
    application.add_handler(CallbackQueryHandler(confirm_close_handler, pattern=r"^rec:confirm_close:", block=False))
    application.add_handler(CallbackQueryHandler(cancel_close_handler, pattern=r"^rec:cancel_close:", block=False))
    application.add_handler(CallbackQueryHandler(show_edit_menu_handler, pattern=r"^rec:edit_menu:", block=False))
    application.add_handler(CallbackQueryHandler(start_edit_sl_handler, pattern=r"^rec:edit_sl:", block=False))
    application.add_handler(CallbackQueryHandler(start_edit_tp_handler, pattern=r"^rec:edit_tp:", block=False))
    application.add_handler(CallbackQueryHandler(start_profit_stop_handler, pattern=r"^rec:set_profit_stop:", block=False))
    application.add_handler(CallbackQueryHandler(set_strategy_handler, pattern=r"^rec:set_strategy:", block=False))
    application.add_handler(CallbackQueryHandler(update_private_card, pattern=r"^rec:update_private:", block=False))
    application.add_handler(CallbackQueryHandler(show_close_menu_handler, pattern=r"^rec:close_menu:", block=False))
    application.add_handler(CallbackQueryHandler(close_at_market_handler, pattern=r"^rec:close_market:", block=False))
    application.add_handler(CallbackQueryHandler(close_with_manual_price_handler, pattern=r"^rec:close_manual:", block=False))

    # === Added: register handler for public update button ===
    application.add_handler(CallbackQueryHandler(update_public_card, pattern=r"^rec:update_public:", block=False))
    # ========================================================

    partial_profit_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(partial_profit_start, pattern=r"^rec:close_partial:")],
        states={
            AWAIT_PARTIAL_PERCENT: [MessageHandler(filters.TEXT & ~filters.COMMAND, received_partial_percent)],
            AWAIT_PARTIAL_PRICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, received_partial_price)],
        },
        fallbacks=[CommandHandler("cancel", cancel_partial_profit)],
        name="partial_profit_conversation",
        per_user=True,
        per_chat=True,
    )
    application.add_handler(partial_profit_conv)

    application.add_handler(MessageHandler(filters.REPLY & filters.TEXT & ~filters.COMMAND, unified_reply_handler), group=1)