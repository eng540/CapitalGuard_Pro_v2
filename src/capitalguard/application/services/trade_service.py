# --- START OF FILE: src/capitalguard/application/services/trade_service.py ---
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterable

@dataclass
class TradeService:
    repo: Any
    notifier: Any

    # ---------- Utilities ----------
    @staticmethod
    def _extract_msg_ids(posted) -> tuple[int | None, int | None]:
        """
        يقبل إما (chat_id, message_id) كزوج قديم،
        أو قاموس {'ok': True, 'chat_id': x, 'message_id': y}.
        يعيد (chat_id, message_id) أو (None, None) لو تعذر.
        """
        if isinstance(posted, tuple) and len(posted) >= 2:
            try:
                ch, msg = int(posted[0]), int(posted[1])
                return ch, msg
            except Exception:
                return None, None
        if isinstance(posted, dict):
            ch = posted.get("chat_id")
            msg = posted.get("message_id")
            try:
                return (int(ch) if ch is not None else None,
                        int(msg) if msg is not None else None)
            except Exception:
                return None, None
        return None, None

    # ---------- CRUD / Actions ----------
    def get(self, rec_id: int):
        return self.repo.get(rec_id)

    def create(self, *, asset: str, side: str, market: str,
               entry: float, stop_loss: float, targets: Iterable[float] | None = None,
               notes: str | None = None, user_id: int | None = None):
        # التحقق الأساسي
        if side.upper() == "LONG" and not (stop_loss < entry):
            raise ValueError("SL must be < Entry for LONG")
        if side.upper() == "SHORT" and not (stop_loss > entry):
            raise ValueError("SL must be > Entry for SHORT")
        tps = list(targets or [])
        # يمكن فرض ترتيب منطقي (اختياريًا)
        # if side.upper() == "LONG": tps.sort()
        # else: tps.sort(reverse=True)

        # إنشاء في المستودع
        rec = self.repo.create(
            asset=asset, side=side, market=market,
            entry=entry, stop_loss=stop_loss, targets=tps,
            notes=notes, user_id=user_id, status="OPEN",
            published_at=datetime.utcnow()
        )

        # نشر البطاقة
        try:
            posted = self.notifier.post_recommendation_card(rec)
        except Exception as e:
            # لا نكسر الإنشاء—نترك النشر للفشل الناعم
            posted = {"ok": False, "msg": str(e)}

        ch_id, msg_id = self._extract_msg_ids(posted)
        if ch_id and msg_id:
            # حفظ مرجع الرسالة بالقناة
            # ندعم كلا الواجهتين إن وُجدت:
            updated = None
            if hasattr(self.repo, "attach_channel_message"):
                updated = self.repo.attach_channel_message(rec.id, ch_id, msg_id)
            elif hasattr(self.repo, "set_channel_message"):
                updated = self.repo.set_channel_message(rec.id, ch_id, msg_id)
            else:
                # آخر حل: عدّل الحقول ثم حدّث
                try:
                    rec.channel_id = ch_id
                    rec.message_id = msg_id
                    updated = self.repo.update(rec)
                except Exception:
                    pass
            if updated is not None:
                rec = updated
        return rec

    def update_sl(self, rec_id: int, new_sl: float, publish: bool = False):
        rec = self.repo.get(rec_id)
        if not rec:
            raise ValueError("Recommendation not found")
        side  = str(getattr(rec.side, "value", rec.side)).upper()
        entry = float(getattr(rec.entry, "value", rec.entry))
        if side == "LONG" and not (new_sl < entry):
            raise ValueError("SL must be < Entry for LONG")
        if side == "SHORT" and not (new_sl > entry):
            raise ValueError("SL must be > Entry for SHORT")
        # تحديث
        if hasattr(self.repo, "update_sl"):
            rec = self.repo.update_sl(rec_id, new_sl)
        else:
            rec.stop_loss = new_sl
            rec = self.repo.update(rec)
        if publish and hasattr(self.notifier, "publish_or_update"):
            try:
                self.notifier.publish_or_update(rec)
            except Exception:
                pass
        return rec

    def update_targets(self, rec_id: int, tps: Iterable[float], publish: bool = False):
        rec = self.repo.get(rec_id)
        if not rec:
            raise ValueError("Recommendation not found")
        tps = list(tps or [])
        side = str(getattr(rec.side, "value", rec.side)).upper()
        # يمكن فرض الترتيب المنطقي (اختياري)
        # if side == "LONG": tps.sort()
        # else: tps.sort(reverse=True)
        if hasattr(self.repo, "update_targets"):
            rec = self.repo.update_targets(rec_id, tps)
        else:
            rec.targets = tps
            rec = self.repo.update(rec)
        if publish and hasattr(self.notifier, "publish_or_update"):
            try:
                self.notifier.publish_or_update(rec)
            except Exception:
                pass
        return rec

    def close(self, rec_id: int, exit_price: float):
        rec = self.repo.get(rec_id)
        if not rec:
            raise ValueError("Recommendation not found")
        # إغلاق
        if hasattr(self.repo, "close"):
            rec = self.repo.close(rec_id, exit_price)
        else:
            rec.status = "CLOSED"
            rec.exit_price = exit_price
            rec.closed_at = datetime.utcnow()
            rec = self.repo.update(rec)
        # تحديث البطاقة في القناة (تحرير/إعادة نشر)
        if hasattr(self.notifier, "publish_or_update"):
            try:
                self.notifier.publish_or_update(rec)
            except Exception:
                pass
        return rec
# --- END OF FILE ---