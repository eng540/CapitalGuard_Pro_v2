# Makefile

.PHONY: init dev api watcher bot test migrate fmt rebuild

init:
	python -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt
	@echo "Copy .env.example to .env and edit values."

dev:
	. .venv/bin/activate && uvicorn capitalguard.interfaces.api.main:app --reload --port 8000

api:
	. .venv/bin/activate && uvicorn capitalguard.interfaces.api.main:app --host 0.0.0.0 --port 8000

watcher:
	. .venv/bin/activate && python -m capitalguard.infrastructure.sched.watcher_ws

bot:
	. .venv/bin/activate && python -m capitalguard.interfaces.telegram.bot_polling_runner

migrate:
	. .venv/bin/activate && alembic upgrade head || (alembic revision --autogenerate -m "init" && alembic upgrade head)

# ✅ UPGRADE: 'test' now points to a more comprehensive test suite command.
test:
	. .venv/bin/activate && pytest -q -v

# ✅ NEW: The command to run the full, rigorous test suite including integration tests.
full-test:
	. .venv/bin/activate && pytest -v

fmt:
	. .venv/bin/activate && pip install black && black src/ tests/

# ✅ NEW: Added a robust command for clean rebuilding of Docker containers.
# This command stops and removes old containers, then rebuilds images from scratch
# without using the cache, ensuring all code changes are applied.
rebuild:
	docker compose down
	docker compose up --build --force-recreate --no-cache