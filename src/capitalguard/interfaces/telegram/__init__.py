# --- START OF FILE: src/capitalguard/interfaces/telegram/__init__.py ---
"""
حزمة واجهة Telegram.

✳️ مهم:
- هذا الملف متعمّد أن يكون خفيفًا دون أي استيرادات جانبية (مثل boot/build_services)
  لتجنّب حلقات الاستيراد الدائرية مع capitalguard.boot و Telegram Notifier.
- استورد الوحدات الفرعية مباشرة عند الحاجة، مثل:
    from capitalguard.interfaces.telegram.handlers import register_all_handlers
    from capitalguard.interfaces.telegram.ui_texts import RecCard
"""

__all__ = [
    # لا نصدّر شيئًا إفتراضيًا من هنا لتجنّب side-effects.
]
# --- END OF FILE ---