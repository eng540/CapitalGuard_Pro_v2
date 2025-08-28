# --- START OF FILE: src/capitalguard/interfaces/telegram/auth.py ---
from telegram.ext import filters
from capitalguard.config import settings

# المستخدمين المصرح لهم من الإعدادات
ALLOWED_USERS = {
    int(uid.strip())
    for uid in (settings.TELEGRAM_ALLOWED_USERS or "").split(",")
    if uid.strip()
}
ALLOWED_FILTER = filters.User(list(ALLOWED_USERS)) if ALLOWED_USERS else filters.ALL
# --- END OF FILE ---