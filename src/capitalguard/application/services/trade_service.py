import logging
from typing import List, Optional, Tuple, Dict, Any
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from capitalguard.domain.entities import Recommendation, RecommendationStatus, OrderType, ExitStrategy
from capitalguard.domain.value_objects import Symbol, Price, Targets, Side
from capitalguard.domain.ports import NotifierPort
from capitalguard.infrastructure.db.repository import RecommendationRepository, UserRepository
from capitalguard.infrastructure.db.base import SessionLocal
from capitalguard.interfaces.telegram.keyboards import public_channel_keyboard
from capitalguard.interfaces.telegram.ui_texts import _pct
from capitalguard.application.services.market_data_service import MarketDataService

log = logging.getLogger(__name__)


def _parse_int_user_id(user_id: Optional[str]) -> Optional[int]:
    try:
        return int(user_id) if user_id is not None and str(user_id).strip().isdigit() else None
    except (TypeError, ValueError):
        return None


class TradeService:
    """
    TradeService - coordinates repository, notifier, and market data service.
    - Uses repository methods with optional session parameter to keep transactions consistent.
    - All external calls are best-effort (exceptions caught & logged) so background workers don't crash.
    """

    def __init__(
        self,
        repo: RecommendationRepository,
        notifier: NotifierPort,
        market_data_service: MarketDataService,
    ):
        self.repo = repo
        self.notifier = notifier
        self.market_data_service = market_data_service

    # -------------------------
    # Internal helpers
    # -------------------------
    def _safe_call_with_session(self, func, *, session: Optional[Session] = None, **kwargs):
        """
        Try calling func with session=keyword if supported; otherwise call without session.
        This keeps compatibility with older repo implementations.
        """
        try:
            if session is not None:
                return func(session=session, **kwargs)
        except TypeError:
            return func(**kwargs)
        return func(**kwargs)

    # -------------------------
    # Notifications / Card updates
    # -------------------------
    def _notify_all_channels(self, rec_id: int, text: str, session: Optional[Session] = None) -> None:
        try:
            published_messages = self._safe_call_with_session(self.repo.get_published_messages, session=session, rec_id=rec_id)
        except Exception:
            log.exception("Failed to fetch published messages for rec #%s", rec_id)
            published_messages = []

        for msg_meta in (published_messages or []):
            try:
                self.notifier.post_notification_reply(
                    chat_id=msg_meta.telegram_channel_id,
                    message_id=msg_meta.telegram_message_id,
                    text=text
                )
            except Exception:
                log.exception("Failed to send reply notification for rec #%s to channel %s", rec_id, getattr(msg_meta, "telegram_channel_id", "?"))

    def _update_all_cards(self, rec: Recommendation, session: Optional[Session] = None) -> None:
        try:
            published_messages = self._safe_call_with_session(self.repo.get_published_messages, session=session, rec_id=rec.id)
        except Exception:
            log.exception("Failed to fetch published messages for updating cards for rec #%s", rec.id)
            published_messages = []

        if not published_messages:
            return

        log.info("Updating %d cards for rec #%s...", len(published_messages), rec.id)
        keyboard = public_channel_keyboard(rec.id) if rec.status != RecommendationStatus.CLOSED else None

        for msg_meta in published_messages:
            try:
                self.notifier.edit_recommendation_card_by_ids(
                    channel_id=msg_meta.telegram_channel_id,
                    message_id=msg_meta.telegram_message_id,
                    rec=rec,
                    keyboard=keyboard
                )
            except Exception:
                log.exception("Failed to update card for rec #%s in channel %s", rec.id, getattr(msg_meta, "telegram_channel_id", "?"))

    # -------------------------
    # Validation
    # -------------------------
    def _validate_sl_vs_entry_on_create(self, side: str, entry: float, sl: float) -> None:
        side_upper = (side or "").upper()
        if side_upper == "LONG" and not (sl < entry):
            raise ValueError("For new LONG trades, Stop Loss must be < Entry Price.")
        if side_upper == "SHORT" and not (sl > entry):
            raise ValueError("For new SHORT trades, Stop Loss must be > Entry Price.")

    # -------------------------
    # CRUD / Business logic
    # -------------------------
    def create_recommendation(self, **kwargs) -> Recommendation:
        """
        Create a new Recommendation entity and persist it with an initial CREATE event.
        kwargs expected: asset, entry, stop_loss, targets, side, order_type, user_id, market, notes, live_price, exit_strategy, profit_stop_price
        """
        asset = kwargs.get('asset', '').strip().upper()
        market = kwargs.get('market', 'Futures')
        if not self.market_data_service.is_valid_symbol(asset, market):
            raise ValueError(f"The symbol '{asset}' is not valid or available in the '{market}' market.")

        order_type_enum = OrderType(kwargs.get('order_type', 'LIMIT').upper())
        if order_type_enum == OrderType.MARKET:
            if kwargs.get('live_price') is None:
                raise ValueError("Live price is required for Market orders.")
            status, final_entry = RecommendationStatus.ACTIVE, float(kwargs['live_price'])
        else:
            status, final_entry = RecommendationStatus.PENDING, float(kwargs['entry'])

        self._validate_sl_vs_entry_on_create(kwargs.get('side', 'LONG'), final_entry, float(kwargs['stop_loss']))

        targets_vo = Targets(kwargs.get('targets') or [])
        for target in targets_vo.values:
            if (kwargs.get('side', '').upper() == 'LONG' and target.price <= final_entry) or \
               (kwargs.get('side', '').upper() == 'SHORT' and target.price >= final_entry):
                raise ValueError(f"Target price {target.price} is not valid for a {kwargs.get('side')} trade with entry {final_entry}.")

        recommendation_entity = Recommendation(
            asset=Symbol(asset),
            side=Side(kwargs.get('side', 'LONG')),
            entry=Price(final_entry),
            stop_loss=Price(float(kwargs['stop_loss'])),
            targets=targets_vo,
            order_type=order_type_enum,
            status=status,
            market=market,
            notes=kwargs.get('notes'),
            user_id=kwargs.get('user_id'),
            activated_at=datetime.now(timezone.utc) if status == RecommendationStatus.ACTIVE else None,
            exit_strategy=kwargs.get('exit_strategy', ExitStrategy.CLOSE_AT_FINAL_TP),
            profit_stop_price=kwargs.get('profit_stop_price'),
            open_size_percent=100.0,
        )

        if recommendation_entity.status == RecommendationStatus.ACTIVE:
            recommendation_entity.highest_price_reached = recommendation_entity.entry.value
            recommendation_entity.lowest_price_reached = recommendation_entity.entry.value

        # Persist using repo (repo handles session/events)
        return self.repo.add_with_event(recommendation_entity)

    def close(self, rec_id: int, exit_price: float, reason: str = "MANUAL_CLOSE", session: Optional[Session] = None) -> Recommendation:
        rec = self._safe_call_with_session(self.repo.get, session=session, rec_id=rec_id)
        if not rec:
            raise ValueError(f"Recommendation {rec_id} not found.")
        if rec.status == RecommendationStatus.CLOSED:
            log.warning("Attempted to close an already closed recommendation: #%d", rec_id)
            return rec

        rec.open_size_percent = 0.0
        old_status = rec.status
        rec.close(exit_price)
        pnl = _pct(rec.entry.value, exit_price, rec.side.value)

        if pnl > 0.001:
            close_status = "PROFIT"
        elif pnl < -0.001:
            close_status = "LOSS"
        else:
            close_status = "BREAKEVEN"

        updated_rec = self._safe_call_with_session(
            self.repo.update_with_event,
            session=session,
            rec=rec,
            event_type="CLOSED",
            event_data={
                "old_status": getattr(old_status, "value", str(old_status)),
                "exit_price": exit_price,
                "closed_at": getattr(rec, "closed_at", datetime.now(timezone.utc)).isoformat(),
                "reason": reason,
                "close_status": close_status,
            },
        )

        # update cards & notify channels
        try:
            self._update_all_cards(updated_rec, session=session)
        except Exception:
            log.exception("Failed updating cards after close for rec #%s", rec_id)

        emoji, r_text = ("🏆", "Profit") if close_status == "PROFIT" else (("💔", "Loss") if close_status == "LOSS" else ("🛡️", "Breakeven"))

        close_notification = (
            f"<b>{emoji} Trade Closed #{getattr(updated_rec, 'asset', '').value}</b>\n"
            f"Closed at {exit_price:g} for a result of <b>{pnl:+.2f}%</b> ({r_text})."
        )

        try:
            self._notify_all_channels(rec_id, close_notification, session=session)
        except Exception:
            log.exception("Failed to notify channels after close for rec #%s", rec_id)

        return updated_rec

    def take_partial_profit(self, rec_id: int, close_percent: float, price: float, triggered_by: str = "MANUAL", session: Optional[Session] = None) -> Recommendation:
        rec = self._safe_call_with_session(self.repo.get, session=session, rec_id=rec_id)
        if not rec or rec.status != RecommendationStatus.ACTIVE:
            raise ValueError("Partial profit can only be taken on active recommendations.")
        if not (0 < close_percent <= rec.open_size_percent):
            raise ValueError(f"Invalid percentage. Must be between 0 and {rec.open_size_percent}.")

        rec.open_size_percent -= close_percent
        pnl_on_part = _pct(rec.entry.value, price, rec.side.value)
        event_type = "PARTIAL_PROFIT_AUTO" if triggered_by.upper() == "AUTO" else "PARTIAL_PROFIT_MANUAL"
        event_data = {
            "price": price,
            "closed_percent": close_percent,
            "remaining_percent": rec.open_size_percent,
            "pnl_on_part": pnl_on_part,
            "triggered_by": triggered_by,
        }

        updated_rec = self._safe_call_with_session(self.repo.update_with_event, session=session, rec=rec, event_type=event_type, event_data=event_data)

        notification_text = (
            f"💰 **Partial Profit Taken** | Signal #{rec.id}\n\n"
            f"Closed **{close_percent:.2f}%** of **{rec.asset.value}** at **{price:g}** for a **{pnl_on_part:+.2f}%** profit.\n\n"
            f"<i>Remaining open size: {rec.open_size_percent:.2f}%</i>"
        )

        try:
            self._notify_all_channels(rec_id, notification_text, session=session)
            self._update_all_cards(updated_rec, session=session)
        except Exception:
            log.exception("Failed to notify/update after partial profit for rec #%s", rec_id)

        if updated_rec.open_size_percent <= 0.01:
            log.info("Recommendation #%s fully closed via partial profits. Marking as closed.", rec_id)
            reason = "AUTO_PARTIAL_FULL_CLOSE" if triggered_by.upper() == "AUTO" else "MANUAL_PARTIAL_FULL_CLOSE"
            return self.close(rec_id, price, reason=reason, session=session)

        return updated_rec

    def update_price_tracking(self, rec_id: int, current_price: float, session: Optional[Session] = None) -> Optional[Recommendation]:
        rec = self._safe_call_with_session(self.repo.get, session=session, rec_id=rec_id)
        if not rec or rec.status != RecommendationStatus.ACTIVE:
            return None

        updated = False
        if rec.highest_price_reached is None or current_price > rec.highest_price_reached:
            rec.highest_price_reached = current_price
            updated = True
        if rec.lowest_price_reached is None or current_price < rec.lowest_price_reached:
            rec.lowest_price_reached = current_price
            updated = True

        if updated:
            try:
                return self._safe_call_with_session(self.repo.update, session=session, rec=rec)
            except Exception:
                log.exception("Failed to persist price tracking update for rec #%s", rec_id)
                return None
        return None

    def get_recent_assets_for_user(self, user_id: str, limit: int = 5) -> List[str]:
        uid_int = _parse_int_user_id(user_id)
        if not uid_int:
            return []
        # repo method should accept session optional; provide a short-lived session for read
        with SessionLocal() as s:
            try:
                return self._safe_call_with_session(self.repo.get_recent_assets_for_user, session=s, user_telegram_id=uid_int, limit=limit)
            except Exception:
                log.exception("Failed to get recent assets for user %s", uid_int)
                return []