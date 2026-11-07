UV ?= uv

.PHONY: setup api check contracts docker-api

setup:
	$(UV) sync --project backend --locked

api:
	$(UV) run --project backend --locked uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1 --no-access-log

check:
	$(UV) run --project backend --locked ruff check backend contracts tools
	$(UV) run --project backend --locked ruff format --check backend contracts tools
	$(UV) run --project backend --locked python tools/export_contracts.py --check
	$(UV) run --project backend --locked pytest backend/tests

contracts:
	$(UV) run --project backend --locked python tools/export_contracts.py

docker-api:
	docker compose -f compose.api.yaml up --build
