--- START OF FILE: Makefile ---  
.PHONY: init dev api watcher test migrate fmt  
init:  
	python -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt  
	echo "Copy .env.example to .env and edit values."  
dev:  
	. .venv/bin/activate && uvicorn capitalguard.interfaces.api.main:app --reload --port 8000  
api:  
	. .venv/bin/activate && uvicorn capitalguard.interfaces.api.main:app --host 0.0.0.0 --port 8000  
watcher:  
	. .venv/bin/activate && python -m capitalguard.infrastructure.sched.watcher_ws  
migrate:  
	. .venv/bin/activate && alembic upgrade head || alembic revision --autogenerate -m "init" && alembic upgrade head  
test:  
	. .venv/bin/activate && pytest -q  
--- END OF FILE ---