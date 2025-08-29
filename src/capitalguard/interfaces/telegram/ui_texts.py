#--- START OF FILE: src/capitalguard/interfaces/telegram/ui_texts.py ---
from __future__ import annotations
from dataclasses import dataclass
from typing import Iterable, Optional

# ========= تنسيقات عامة =========
def fmt_price(x: float | int | None) -> str:
    if x is None:
        return "-"
    return f"{x:.2f}".rstrip("0").rstrip(".")

def fmt_list(items: Iterable[str], sep: str = " • ") -> str:
    return sep.join([s for s in items if s])

# ========= بطاقة توصية موحّدة =========
@dataclass
class RecCard:
    id: int
    asset: str
    side: str
    status: str
    entry: float
    stop_loss: float
    targets: list[float]
    exit_price: Optional[float] = None

    def to_text(self) -> str:
        status_emoji = {"OPEN": "🟢", "CLOSED": "🔴"}.get(str(self.status).upper(), "⚪")
        side_emoji = {"LONG": "📈", "SHORT": "📉"}.get(str(self.side).upper(), "〰️")
        tgts = fmt_list([fmt_price(t) for t in (self.targets or [])])
        exit_line = f"\n• سعر الخروج: {fmt_price(self.exit_price)}" if self.exit_price else ""
        return (
            f"{status_emoji} <b>#{self.id} — {self.asset}</b> {side_emoji}\n"
            f"• الحالة: <b>{self.status}</b>\n"
            f"• الدخول: {fmt_price(self.entry)}\n"
            f"• وقف الخسارة: {fmt_price(self.stop_loss)}\n"
            f"• الأهداف: {tgts}{exit_line}"
        )

# ========= رسائل ثابتة =========
WELCOME = (
    "👋 أهلاً بك في <b>CapitalGuard Bot</b>\n"
    "أنا مساعدك لإدارة وإغلاق التوصيات.\n"
    "الأوامر المتاحة:\n"
    "• <code>/newrec</code> — إنشاء توصية\n"
    "• <code>/open</code> — عرض المفتوحة\n"
    "• <code>/list</code> — عدّ سريع\n"
    "• <code>/analytics</code> — ملخص الأداء\n"
    "• <code>/help</code> — المساعدة"
)

HELP = (
    "<b>مساعدة سريعة</b> 💡\n"
    "• ابدأ بـ <code>/newrec</code> واتبع الخطوات.\n"
    "• لإغلاق توصية: من <code>/open</code> اضغط زر <i>إغلاق الآن</i> ثم أرسل سعر الخروج.\n"
    "• يمكنك التراجع في أي لحظة من خلال زر <i>❌ تراجع</i>."
)

ASK_EXIT_PRICE = (
    "🔻 <b>أرسل الآن سعر الخروج</b> لإغلاق التوصية.\n"
    "مثال: <code>120000</code> أو <code>120000.5</code>\n"
    "يمكنك الضغط على <i>❌ تراجع</i> لإلغاء العملية."
)

INVALID_PRICE = "⚠️ سعر غير صالح. أرسل رقمًا مثل <code>120000</code> أو <code>120000.5</code>."
CLOSE_CONFIRM = lambda rec_id, price: f"هل تريد تأكيد إغلاق التوصية <b>#{rec_id}</b> على سعر <b>{fmt_price(price)}</b>؟"
CLOSE_DONE    = lambda rec_id, price: f"✅ تم إغلاق التوصية <b>#{rec_id}</b> على سعر <b>{fmt_price(price)}</b>."
OPEN_EMPTY    = "لا توجد توصيات مفتوحة حالياً 💤"
ERROR_GENERIC = "⚠️ حدث خطأ غير متوقع. تم تسجيله. حاول مجددًا من فضلك."
#--- END OF FILE ---