from __future__ import annotations
from typing import List, Optional

from capitalguard.domain.entities import Recommendation
from capitalguard.domain.value_objects import Symbol, Price, Targets, Side
from capitalguard.domain.ports import RecommendationRepoPort, NotifierPort


class TradeService:
    def __init__(self, repo: RecommendationRepoPort, notifier: Optional[NotifierPort] = None) -> None:
        self.repo = repo
        self.notifier = notifier

    def create(
        self,
        asset: str,
        side: str,
        entry: float,
        stop_loss: float,
        targets: List[float],
        channel_id: Optional[int] = None,
        user_id: Optional[int] = None,
        notes: Optional[str] = None,
    ) -> Recommendation:
        rec = Recommendation(
            asset=Symbol(asset),
            side=Side(side),
            entry=Price(entry),
            stop_loss=Price(stop_loss),
            targets=Targets(targets),
            channel_id=channel_id,
            user_id=user_id,
        )
        saved = self.repo.add(rec)

        # -------- الإشعار بتنسيق احترافي --------
        if self.notifier:
            # إن كان المرسّل يدعم الواجهة الجديدة
            if hasattr(self.notifier, "send_recommendation"):
                try:
                    self.notifier.send_recommendation(
                        rec_id=saved.id,
                        asset=saved.asset.value,
                        side=saved.side.value,               # "LONG" / "SHORT"
                        entry=saved.entry.value,
                        stop_loss=saved.stop_loss.value,
                        targets=saved.targets.values,        # List[float]
                        notes=notes,
                        chat_id=saved.channel_id,
                    )
                except Exception:
                    # لا نكسر إنشاء التوصية إن فشل الإرسال
                    pass
            else:
                # توافقيًا مع مرسّلات قديمة تملك publish(text)
                try:
                    msg = (
                        f"📌 <b>New Recommendation</b>\n"
                        f"Asset: <b>{saved.asset.value}</b> | Side: <b>{saved.side.value}</b>\n"
                        f"Entry: <b>{saved.entry.value}</b> | SL: <b>{saved.stop_loss.value}</b>\n"
                        f"Targets: <b>{', '.join(map(str, saved.targets.values))}</b>\n"
                        f"ID: <code>{saved.id}</code>"
                    )
                    self.notifier.publish(msg)  # type: ignore[attr-defined]
                except Exception:
                    pass

        return saved

    def close(self, rec_id: int, exit_price: float) -> Recommendation:
        rec = self.repo.get(rec_id)
        if not rec:
            raise ValueError("Recommendation not found")

        rec.close(exit_price)
        saved = self.repo.update(rec)

        # -------- إشعار الإغلاق --------
        if self.notifier:
            if hasattr(self.notifier, "send_close"):
                try:
                    self.notifier.send_close(
                        rec_id=saved.id,
                        asset=saved.asset.value,
                        exit_price=exit_price,
                        pnl_pct=None,               # يمكن حسابها لاحقًا إن رغبت
                        chat_id=saved.channel_id,
                    )
                except Exception:
                    pass
            else:
                try:
                    self.notifier.publish(f"✅ <b>Closed</b> | ID: <code>{saved.id}</code>")  # type: ignore[attr-defined]
                except Exception:
                    pass

        return saved

    def list_open(self, channel_id: Optional[int] = None):
        return self.repo.list_open(channel_id)

    def list_all(self, channel_id: Optional[int] = None):
        return self.repo.list_all(channel_id)