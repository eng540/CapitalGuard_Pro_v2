# --- START OF FILE: Makefile ---
.PHONY: init dev api watcher bot test migrate fmt

init:
	python -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt
	@echo "Copy .env.example to .env and edit values."

dev:
	. .venv/bin/activate && uvicorn capitalguard.interfaces.api.main:app --reload --port 8000

api:
	. .venv/bin/activate && uvicorn capitalguard.interfaces.api.main:app --host 0.0.0.0 --port 8000

watcher:
	. .venv/bin/activate && python -m capitalguard.infrastructure.sched.watcher_ws

# ✅ FIX: Added the 'bot' command for local development polling.
bot:
	. .venv/bin/activate && python -m capitalguard.interfaces.telegram.bot_polling_runner

migrate:
	. .venv/bin/activate && alembic upgrade head || (alembic revision --autogenerate -m "init" && alembic upgrade head)

test:
	. .venv/bin/activate && pytest -q

# A simple formatter target using black can be useful.
fmt:
	. .venv/bin/activate && pip install black && black src/ tests/
# --- END OF FILE ---```

---

### الخلاصة والخطوة التالية

لقد قمنا بمعالجة جميع المشاكل الحرجة والمتوسطة التي تم اكتشافها. النظام الآن أكثر كفاءة واستقرارًا وجاهزية للبيئة الإنتاجية. الأساس أصبح صلبًا وجاهزًا للمرحلة التالية.

**الرجاء القيام بالخطوات التالية:**

1.  **استبدل الملفات:** قم باستبدال محتويات الملفات التسعة المذكورة أعلاه في مشروعك.
2.  **أنشئ الملفين الجديدين:** قم بإنشاء `entrypoint.sh` و `src/capitalguard/interfaces/telegram/bot_polling_runner.py` بالمحتوى الموفر.
3.  **اجعل `entrypoint.sh` قابلًا للتنفيذ:** إذا كنت تعمل على نظام Linux/macOS، قم بتشغيل الأمر: `chmod +x entrypoint.sh`.

بعد تطبيق هذه التغييرات، يمكننا الانتقال بثقة إلى **"المرحلة 0: إعادة بناء الأساس لتعدد المستخدمين"**. هل أنت جاهز للمتابعة؟