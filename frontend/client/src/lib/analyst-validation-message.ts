export type AnalystValidationMessage = {
  field: "entry" | "stop_loss" | "targets" | "channels" | "service" | "form";
  title: string;
  message: string;
};

export function analystValidationMessage(rawError: string): AnalystValidationMessage {
  const raw = rawError.toLowerCase();
  if (raw.includes("entry price") || raw.includes('"entry"') || raw.includes("entry")) {
    return { field: "entry", title: "سعر الدخول", message: "أدخل سعراً موجباً لأمر Limit أو Stop Market. أمر Market يأخذ السعر الحي من Core تلقائياً." };
  }
  if (raw.includes("stoploss") || raw.includes("stop_loss") || raw.includes('"stop"')) {
    return { field: "stop_loss", title: "وقف الخسارة", message: "أدخل وقف خسارة موجباً ومتوافقاً مع اتجاه الصفقة." };
  }
  if (raw.includes("targets") || raw.includes("target")) {
    return { field: "targets", title: "الأهداف", message: "أدخل أهدافاً صالحة واجعل مجموع نسب الإغلاق 100%." };
  }
  if (raw.includes("channel") || raw.includes("publication")) {
    return { field: "channels", title: "القنوات", message: "اختر قنوات نشطة تملك صلاحية النشر إليها، أو احفظ التوصية بلا قنوات." };
  }
  if (raw.includes("timeout") || raw.includes("unavailable")) {
    return { field: "service", title: "اتصال Core", message: "تعذر الاتصال بـCore مؤقتاً. لم تُنشأ توصية؛ أعد المحاولة بعد لحظات." };
  }
  return { field: "form", title: "التحقق", message: "تعذرت المعاينة. راجع الحقول المطلوبة ثم حاول مجدداً؛ لم تُنشأ توصية." };
}
