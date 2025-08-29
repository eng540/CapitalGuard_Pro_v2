# --- START OF FILE: src/capitalguard/interfaces/telegram/ui_texts.py ---
from __future__ import annotations
from dataclasses import dataclass
from typing import List, Optional

# نصوص واجهة موحّدة ومركزية لطبقة تيليجرام

WELCOME = (
    "👋 أهلاً بك في <b>CapitalGuard Bot</b>.\n"
    "استخدم /help للمساعدة."
)

HELP = (
    "<b>الأوامر المتاحة:</b>\n\n"
    "• <code>/newrec</code> — إنشاء توصية\n"
    "• <code>/open</code> — عرض المفتوحة كبطاقات قابلة للإدارة\n"
    "• <code>/list</code> — عرض مختصر/عدد\n"
    "• <code>/analytics</code> — ملخص أداء\n"
    "• <code>/ping</code> — اختبار"
)

OPEN_EMPTY = "لا توجد توصيات مفتوحة."

ASK_EXIT_PRICE = (
    "🔻 أرسل الآن <b>سعر الخروج</b> لإغلاق التوصية.\n"
    "مثال: <code>12345.6</code> (يُقبل أيضًا ١٢٣٤٥٫٦ أو 12345,6)"
)

INVALID_PRICE = (
    "⚠️ سعر غير صالح. الرجاء إدخال رقم صحيح (مثال: <code>12345.6</code>)"
)

def CLOSE_CONFIRM(rec_id: int, exit_price: float) -> str:
    return (
        f"تأكيد إغلاق التوصية <b>#{rec_id}</b>\n"
        f"سعر الخروج: <code>{exit_price:g}</code>\n"
        "هل تريد المتابعة؟"
    )

def CLOSE_DONE(rec_id: int, exit_price: float) -> str:
    return f"✅ تم إغلاق التوصية <b>#{rec_id}</b> على سعر <code>{exit_price:g}</code>."

@dataclass
class RecCard:
    id: int
    asset: str
    side: str
    status: str
    entry: float
    stop_loss: float
    targets: List[float]
    exit_price: Optional[float] = None

    def to_text(self) -> str:
        side_emoji = "📈" if self.side.upper() == "LONG" else "📉"
        status_emoji = "🟢" if self.status.upper() == "OPEN" else "🔴"
        tps = " • ".join(f"{t:g}" for t in (self.targets or [])) or "-"
        exit_line = f"\n• الخروج: <code>{self.exit_price:g}</code>" if self.exit_price is not None else ""
        return (
            f"{status_emoji} <b>#{self.id}</b> — <b>{self.asset}</b> {side_emoji}\n"
            f"• الحالة: <b>{self.status.upper()}</b>\n"
            f"• الدخول: <code>{self.entry:g}</code>\n"
            f"• وقف الخسارة: <code>{self.stop_loss:g}</code>\n"
            f"• الأهداف: <code>{tps}</code>"
            f"{exit_line}"
        )
# --- END OF FILE ---