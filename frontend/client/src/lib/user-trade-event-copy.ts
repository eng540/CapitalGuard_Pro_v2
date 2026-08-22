export type UserTradeEventCopy = { label: string; source: "يدوي" | "آلي" | "نظام"; detail: string };

const eventCopy: Record<string, UserTradeEventCopy> = {
  MANUAL_CLOSE: { label: "إغلاق كامل", source: "يدوي", detail: "أغلق المتداول السجل بسعر موثوق من Core." },
  MANUAL_PARTIAL_CLOSE: { label: "إغلاق جزئي", source: "يدوي", detail: "أغلق المتداول جزءاً من الحجم المفعّل." },
  MANUAL_STOP_MOVED_TO_BREAKEVEN: { label: "نقل الوقف إلى التعادل", source: "يدوي", detail: "نقل Core وقف الخسارة إلى سعر الدخول المحفوظ." },
  MANUAL_PENDING_ENTRY_UPDATED: { label: "تعديل سعر الدخول", source: "يدوي", detail: "عُدّل دخول سجل غير مفعّل بعد تحقق قواعد المخاطرة." },
  PENDING_CANCELLED: { label: "إلغاء سجل معلّق", source: "يدوي", detail: "أُلغي التتبع أو الأمر المعلّق بلا سعر خروج أو PnL." },
  ACTIVATED: { label: "تفعيل الصفقة", source: "آلي", detail: "تحولت الحالة إلى مفعّلة وفق حدث Core." },
  SL: { label: "تحقق وقف الخسارة", source: "آلي", detail: "سجّل Core تحقق وقف الخسارة وفق دورة الحياة." },
};

export function userTradeEventCopy(eventType: string): UserTradeEventCopy {
  const normalized = eventType.trim().toUpperCase();
  if (eventCopy[normalized]) return eventCopy[normalized];
  if (/^TP\d+$/.test(normalized)) return { label: `تحقق ${normalized}`, source: "آلي", detail: "سجّل Core تحقق هدف ضمن دورة الحياة." };
  return { label: normalized.replaceAll("_", " "), source: "نظام", detail: "حدث مسجل من Core." };
}
