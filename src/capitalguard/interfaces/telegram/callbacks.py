# --- START OF FILE: src/capitalguard/interfaces/telegram/callbacks.py ---
from __future__ import annotations
from typing import Dict, Tuple

# توحيد صيغة callback_data لضمان توسّع مستقبلي سهل
# الشكل المبدئي الحالي (متوافق مع الموجود):
#   rec:close:<id>
#   rec:confirm_close:<id>:<exit_price>
#   rec:cancel_close:<id>
# ويمكن الترقية لاحقًا إلى:
#   rec:v1:<id>:<action>[:k=v;...]

SEP = ":"

def build_simple(kind: str, *parts: object) -> str:
    return SEP.join([kind, *[str(p) for p in parts]])

def parse_simple(data: str) -> Tuple[str, ...]:
    return tuple((data or "").split(SEP))
# --- END OF FILE ---