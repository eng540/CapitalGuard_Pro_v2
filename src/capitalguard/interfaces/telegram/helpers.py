#--- START OF FILE: src/capitalguard/interfaces/telegram/helpers.py ---
from telegram.ext import ContextTypes

def get_service(context: ContextTypes.DEFAULT_TYPE, service_name: str):
    """
    الوصول الآمن والموثوق إلى الخدمات المحقونة في main.py.
    هذه هي الطريقة الوحيدة التي يجب أن تصل بها المعالجات إلى الخدمات.
    """
    services = context.application.bot_data.get("services")
    if not isinstance(services, dict) or service_name not in services:
        # هذا الخطأ لا يجب أن يحدث أبدًا إذا تم الإعداد بشكل صحيح.
        raise RuntimeError(f"Service '{service_name}' not configured correctly. Check main.py.")
    return services[service_name]
#--- END OF FILE ---