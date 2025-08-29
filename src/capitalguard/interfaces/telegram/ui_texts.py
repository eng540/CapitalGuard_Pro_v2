#--- START OF FILE: src/capitalguard/interfaces/telegram/ui_texts.py ---
from __future__ import annotations
from dataclasses import dataclass
from typing import Iterable, Optional

# ========== تنسيقات أساسية ==========
def fmt_price(x: float | int | None) -> str:
    if x is None:
        return "-"
    # بدون فصل آلاف للمحافظة على بساطة القراءة في تليجرام
    return f"{x:.2f}".rstrip("0").rstrip(".")

def fmt_pct(p: float | None) -> str:
    if p is None:
        return "-"
    return f"{p:+.2f}%"

def fmt_list(items: Iterable[str], sep: str = " • ") -> str:
    return sep.join([s for s in items if s])

# ========== نماذج بطاقات ==========
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
        status_emoji = {"OPEN": "🟢", "CLOSED": "🔴"}.get(self.status.upper(), "⚪")
        side_emoji = {"LONG": "📈", "SHORT": "📉"}.get(self.side.upper(), "〰️")
        tgts = fmt_list([fmt_price(t) for t in self.targets])
        exit_line = f"\n• سعر الخروج: {fmt_price(self.exit_price)}" if self.exit_price else ""
        return (
            f"{status_emoji} <b>#{self.id} — {self.asset}</b> {side_emoji}\n"
            f"• الحالة: <b>{self.status}</b>\n"
            f"• الدخول: {fmt_price(self.entry)}\n"
            f"• وقف الخسارة: {fmt_price(self.stop_loss)}\n"
            f"• الأهداف: {tgts}{exit_line}"
        )

# ========== رسائل ثابتة ==========
WELCOME = (
    "أهلاً بك في <b>CapitalGuard Pro</b> 🤖\n"
    "أنا مساعدك لإدارة التوصيات.\n"
    "الأوامر:\n"
    "/newrec — إنشاء توصية جديدة\n"
    "/open — عرض التوصيات المفتوحة\n"
    "/report — تقرير مختصر\n"
    "/analytics — ملخص الأداء\n"
    "/help — المساعدة"
)

HELP = (
    "<b>مساعدة سريعة</b> 💡\n"
    "• استخدم /newrec لبدء توصية بخطوات بسيطة.\n"
    "• عند عرض توصية، استخدم زر <i>إغلاق</i> لإدخال سعر الخروج.\n"
    "• يمكنك إلغاء أي خطوة عبر زر <i>إلغاء</i>."
)

ASK_EXIT_PRICE = (
    "🔻 <b>أرسل الآن سعر الخروج</b> لإغلاق التوصية.\n"
    "مثال: <code>120000</code> أو <code>120000.5</code>\n"
    "اضغط <i>إلغاء</i> لإلغاء العملية."
)

INVALID_PRICE = "⚠️ لم أفهم السعر. أرسل رقمًا صحيحًا مثل <code>120000</code> أو <code>120000.5</code>."

CLOSE_CONFIRM = lambda rec_id, price: (
    f"هل تريد تأكيد إغلاق التوصية <b>#{rec_id}</b> على سعر <b>{fmt_price(price)}</b>؟"
)

CLOSE_DONE = lambda rec_id, price: (
    f"✅ تم إغلاق التوصية <b>#{rec_id}</b> على سعر <b>{fmt_price(price)}</b>."
)

OPEN_EMPTY = "لا توجد توصيات مفتوحة حالياً 💤"
REPORT_HEADER = "📈 <b>تقرير مختصر</b>"
ANALYTICS_HEADER = "📊 <b>ملخص الأداء</b>"
ERROR_GENERIC = "⚠️ حدث خطأ غير متوقع. حاول مجددًا، وتم تسجيل التفاصيل."
#--- END OF FILE ---