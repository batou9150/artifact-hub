EMU ?= 127.0.0.1:8681

.PHONY: install emulator api web seed test test-emulator build

install:
	cd backend && uv venv --python 3.12 .venv && uv pip install --python .venv/bin/python -e ".[dev]"
	cd frontend && npm ci

emulator:  ## Firestore emulator (needs Java 21+)
	gcloud emulators firestore start --host-port=$(EMU)

api:  ## API on :8080, dev auth, Firestore emulator
	cd backend && FIRESTORE_EMULATOR_HOST=$(EMU) STORE_BACKEND=firestore .venv/bin/uvicorn artifact_hub.api:create_root_app --factory --port 8080 --reload

api-memory:  ## API on :8080, dev auth, in-memory store
	cd backend && .venv/bin/uvicorn artifact_hub.api:create_root_app --factory --port 8080 --reload

web:  ## Vite dev server on :5173
	cd frontend && npm run dev

seed:
	cd backend && .venv/bin/python scripts/seed_demo.py

test:
	cd backend && .venv/bin/python -m pytest -q
	cd frontend && npm test && npm run build

test-emulator:
	cd backend && FIRESTORE_EMULATOR_HOST=$(EMU) .venv/bin/python -m pytest -q

build:
	docker build -t artifact-hub:local .
