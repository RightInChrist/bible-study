.PHONY: install dev dev-api dev-web test test-api test-web build-web import migrate clean seed-evals

PYTHON ?= .venv/bin/python
PIP    ?= .venv/bin/pip
PYTEST ?= .venv/bin/pytest
ALEMBIC ?= .venv/bin/alembic

install:
	python3 -m venv .venv
	$(PIP) install --upgrade pip
	$(PIP) install -r requirements.txt
	cd web && npm ci

migrate:
	$(ALEMBIC) -c api/migrations/alembic.ini upgrade head

import: migrate
	$(PYTHON) -m scripts.import_fixtures

# `make dev` runs the FastAPI server and the Vite dev server in parallel.
# `make -j2 dev-api dev-web` would also work; the explicit recipe below
# uses a tiny bash subshell so a single `make dev` does the right thing
# without relying on Make's `-j` flag. If concurrent output is noisy,
# split into two terminals via `make dev-api` and `make dev-web`.
dev:
	@echo "Starting FastAPI on 127.0.0.1:8000 and Vite dev server on http://localhost:5173"
	@(trap 'kill 0' INT TERM EXIT; \
		$(PYTHON) -m scripts.serve & \
		(cd web && npm run dev) & \
		wait)

dev-api:
	$(PYTHON) -m scripts.serve

dev-web:
	cd web && npm run dev

build-web:
	cd web && npm run build

test: test-api test-web

test-api:
	$(PYTEST) -q

test-web:
	cd web && npm test

seed-evals:
	@curl -sf http://localhost:4002/health > /dev/null || \
		(echo "evals service at http://localhost:4002 is not reachable; start it before seeding" && exit 1)
	$(PYTHON) ~/github/pagehub-io/platform/evals/seeds/bible_study_idempotent_import.py

clean:
	rm -rf data/*.db data/*.db-wal data/*.db-shm
	rm -rf web/dist web/.vite
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
