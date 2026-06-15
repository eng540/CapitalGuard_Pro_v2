# File: src/capitalguard/infrastructure/audit/audit_logger.py
# [STEP-6A] Audit Logger — سجل الأحداث المالية الذي لا يُحذف
#
# المعيار المالي (Financial-Grade Auditability):
#   في الأنظمة المالية الخاضعة للتنظيم (MiFID II, FINRA Rule 4370)،
#   كل قرار تداول يجب أن يكون traceable بالكامل:
#       من قرَّر؟     (triggered_by: SYSTEM / MANUAL / AUTO)
#       ماذا حدث؟    (event_type: TP_HIT / SL_HIT / CLOSE / CREATION)
#       متى؟         (timestamp_utc: ISO 8601 دقيق)
#       على أي أصل؟  (item_type + item_id)
#       بأي سعر؟     (price: Decimal → str بدون تقريب)
#
# البنية:
#   - logger منفصل "capitalguard.audit" يكتب لملف منفصل
#   - كل سجل بصيغة JSON سطر واحد (NDJSON) لسهولة التحليل
#   - يكتب أيضاً لـ stdout للـ log aggregators (Datadog, Papertrail)
#   - لا يُرفع استثناء أبداً — الـ audit log يجب أن يكون silent
#
# الاستخدام:
#   from capitalguard.infrastructure.audit.audit_logger import log_trade_event
#
#   log_trade_event(
#       event_type="TP_HIT",
#       item_type="recommendation",
#       item_id=42,
#       price=Decimal("65000.00"),
#       reason="TP1_HIT",
#       triggered_by="SYSTEM",
#       extra={"target_index": 1, "symbol": "BTCUSDT"},
#   )

import json
import logging
import logging.handlers
import os
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, Optional

# ─────────────────────────────────────────────────────────────────────────────
# إعداد الـ audit logger المنفصل
# ─────────────────────────────────────────────────────────────────────────────

_audit_logger = logging.getLogger("capitalguard.audit")

# نُعدّ الـ handler مرة واحدة فقط عند الاستيراد
if not _audit_logger.handlers:
    _audit_logger.setLevel(logging.INFO)
    _audit_logger.propagate = False  # لا يذهب للـ root logger

    # ── Handler 1: stdout (للـ log aggregators في production) ───────────
    _stdout_handler = logging.StreamHandler()
    _stdout_handler.setLevel(logging.INFO)
    _stdout_handler.setFormatter(logging.Formatter("%(message)s"))
    _audit_logger.addHandler(_stdout_handler)

    # ── Handler 2: ملف دوّار (في البيئات التي تدعم الكتابة لملفات) ──────
    _log_dir = os.environ.get("AUDIT_LOG_DIR", "/tmp/capitalguard_audit")
    try:
        os.makedirs(_log_dir, exist_ok=True)
        _file_handler = logging.handlers.RotatingFileHandler(
            filename=os.path.join(_log_dir, "trades.audit.jsonl"),
            maxBytes=50 * 1024 * 1024,  # 50 MB
            backupCount=10,             # 10 ملفات احتياطية
            encoding="utf-8",
        )
        _file_handler.setLevel(logging.INFO)
        _file_handler.setFormatter(logging.Formatter("%(message)s"))
        _audit_logger.addHandler(_file_handler)
    except (OSError, PermissionError):
        # بيئة read-only (مثل بعض إعدادات Railway) — stdout يكفي
        pass


# ─────────────────────────────────────────────────────────────────────────────
# JSON Encoder للبيانات المالية
# ─────────────────────────────────────────────────────────────────────────────

class _AuditEncoder(json.JSONEncoder):
    """
    يُحوِّل الأنواع الخاصة لـ JSON:
        Decimal  → str (لا تقريب، لا فقدان دقة)
        datetime → ISO 8601
        set/tuple → list
        غير ذلك → str (fallback آمن)
    """
    def default(self, obj: Any) -> Any:
        if isinstance(obj, Decimal):
            return str(obj)
        if isinstance(obj, datetime):
            return obj.isoformat()
        if isinstance(obj, (set, frozenset, tuple)):
            return list(obj)
        return str(obj)


# ─────────────────────────────────────────────────────────────────────────────
# الدالة الرئيسية
# ─────────────────────────────────────────────────────────────────────────────

def log_trade_event(
    event_type:   str,
    item_type:    str,
    item_id:      int,
    price:        Optional[Decimal]  = None,
    reason:       Optional[str]      = None,
    triggered_by: str                = "SYSTEM",
    symbol:       Optional[str]      = None,
    market:       Optional[str]      = None,
    user_id:      Optional[int]      = None,
    pnl_pct:      Optional[float]    = None,
    extra:        Optional[Dict[str, Any]] = None,
) -> None:
    """
    [STEP-6A] يُسجِّل حدثاً مالياً في audit log بصيغة JSON.

    الحقول الإلزامية:
        event_type   — نوع الحدث: TP_HIT, SL_HIT, ACTIVATION, INVALIDATION,
                       CLOSE, CREATION, MANUAL_CLOSE, USER_TRADE_TP_HIT, ...
        item_type    — نوع الكائن: "recommendation" أو "user_trade"
        item_id      — معرف الكائن في DB

    الحقول الاختيارية:
        price        — السعر عند الحدث (Decimal → str بدون تقريب)
        reason       — السبب التفصيلي (SL_HIT, TP1_HIT, MANUAL, ...)
        triggered_by — من أطلق الحدث: SYSTEM / MANUAL / AUTO_TRADE
        symbol       — رمز الأصل (BTCUSDT, ETHUSDT, ...)
        market       — نوع السوق (Futures, Spot)
        user_id      — مُعرِّف المستخدم (للأحداث اليدوية)
        pnl_pct      — نسبة الربح/الخسارة إن كانت متاحة
        extra        — أي بيانات إضافية ذات صلة

    لن يُرفع استثناء أبداً — يفشل بصمت لضمان عدم تأثير الـ audit
    على المسار الحرج للتداول.
    """
    try:
        record: Dict[str, Any] = {
            "audit_v":       1,
            "event_type":    event_type,
            "item_type":     item_type,
            "item_id":       item_id,
            "triggered_by":  triggered_by,
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        }

        # حقول اختيارية — لا نُضيف None لإبقاء السجل نظيفاً
        if price        is not None: record["price"]    = str(price)
        if reason       is not None: record["reason"]   = reason
        if symbol       is not None: record["symbol"]   = symbol
        if market       is not None: record["market"]   = market
        if user_id      is not None: record["user_id"]  = user_id
        if pnl_pct      is not None: record["pnl_pct"]  = round(pnl_pct, 4)
        if extra:                    record.update(extra)

        _audit_logger.info(
            json.dumps(record, cls=_AuditEncoder, ensure_ascii=False)
        )

    except Exception as e:
        # لا نُرفع — الـ audit log لا يجب أن يكسر المسار الحرج
        logging.getLogger(__name__).error(
            "audit_logger: failed to log event %s#%d %s: %s",
            item_type, item_id, event_type, e,
        )


# ─────────────────────────────────────────────────────────────────────────────
# دوال مختصرة للأحداث الأكثر تكراراً
# ─────────────────────────────────────────────────────────────────────────────

def log_recommendation_created(
    rec_id: int,
    symbol: str,
    market: str,
    side: str,
    entry_price: Decimal,
    analyst_id: int,
) -> None:
    """يُسجِّل إنشاء توصية جديدة."""
    log_trade_event(
        event_type="CREATION",
        item_type="recommendation",
        item_id=rec_id,
        price=entry_price,
        triggered_by="MANUAL",
        symbol=symbol,
        market=market,
        user_id=analyst_id,
        extra={"side": side},
    )


def log_tp_hit(
    item_type: str,
    item_id: int,
    target_index: int,
    price: Decimal,
    symbol: Optional[str] = None,
) -> None:
    """يُسجِّل ضرب هدف ربح."""
    log_trade_event(
        event_type="TP_HIT",
        item_type=item_type,
        item_id=item_id,
        price=price,
        reason=f"TP{target_index}_HIT",
        triggered_by="SYSTEM",
        symbol=symbol,
        extra={"target_index": target_index},
    )


def log_sl_hit(
    item_type: str,
    item_id: int,
    price: Decimal,
    symbol: Optional[str] = None,
) -> None:
    """يُسجِّل ضرب وقف الخسارة."""
    log_trade_event(
        event_type="SL_HIT",
        item_type=item_type,
        item_id=item_id,
        price=price,
        reason="SL_HIT",
        triggered_by="SYSTEM",
        symbol=symbol,
    )


def log_recommendation_closed(
    rec_id: int,
    price: Optional[Decimal],
    reason: str,
    triggered_by: str = "SYSTEM",
    pnl_pct: Optional[float] = None,
    symbol: Optional[str] = None,
) -> None:
    """يُسجِّل إغلاق توصية."""
    log_trade_event(
        event_type="CLOSE",
        item_type="recommendation",
        item_id=rec_id,
        price=price,
        reason=reason,
        triggered_by=triggered_by,
        symbol=symbol,
        pnl_pct=pnl_pct,
    )


def log_activation(
    item_type: str,
    item_id: int,
    price: Optional[Decimal] = None,
    symbol: Optional[str] = None,
) -> None:
    """يُسجِّل تفعيل توصية أو صفقة مستخدم."""
    log_trade_event(
        event_type="ACTIVATION",
        item_type=item_type,
        item_id=item_id,
        price=price,
        reason="ENTRY_PRICE_HIT",
        triggered_by="SYSTEM",
        symbol=symbol,
    )
