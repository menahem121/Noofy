.PHONY: install run run-backend doctor test test-backend test-frontend test-exporter phase5e-real-smoke memory-governor-linux-validation

BACKEND_PYTHON ?= .venv/bin/python
ROOT_BACKEND_PYTHON ?= backend/.venv/bin/python
PYTEST ?= $(ROOT_BACKEND_PYTHON) -m pytest
NOOFY_PYTHON ?= python3
NOOFY_SCRIPT ?= scripts/noofy.py
NOOFY_DATA_DIR ?= $(CURDIR)/.noofy-runtime/data
COMFYUI_SOURCE_DIR ?= $(CURDIR)/third_party/comfyui
COMFYUI_PYTHON ?= $(NOOFY_DATA_DIR)/runtime/comfyui-venv/bin/python
COMFYUI_MODEL_VIEW_DIR ?= $(NOOFY_DATA_DIR)/models
COMFYUI_INPUT_DIR ?= $(NOOFY_DATA_DIR)/input
PHASE5E_SMOKE_WORK_DIR ?= /tmp/noofy-phase5e-real-smoke
PHASE5E_SMOKE_SUMMARY ?= $(PHASE5E_SMOKE_WORK_DIR)/summary.json
MEMORY_GOVERNOR_VALIDATION_DATA_DIR ?= $(CURDIR)/.noofy-runtime/data
MEMORY_GOVERNOR_VALIDATION_OUTPUT ?= $(CURDIR)/.noofy-runtime/validation/memory-governor-linux-validation.json

install:
	$(NOOFY_PYTHON) $(NOOFY_SCRIPT) install --data-dir "$(NOOFY_DATA_DIR)"

run:
	$(NOOFY_PYTHON) $(NOOFY_SCRIPT) run --data-dir "$(NOOFY_DATA_DIR)"

run-backend:
	$(NOOFY_PYTHON) $(NOOFY_SCRIPT) serve --data-dir "$(NOOFY_DATA_DIR)"

doctor:
	$(NOOFY_PYTHON) $(NOOFY_SCRIPT) doctor --data-dir "$(NOOFY_DATA_DIR)"

test: test-backend test-frontend test-exporter

test-backend:
	cd backend && $(BACKEND_PYTHON) -m pytest tests

test-frontend:
	cd frontend && npm test

test-exporter:
	$(PYTEST) comfyui_export2noofy_node/tests

phase5e-real-smoke:
	cd backend && $(BACKEND_PYTHON) -m app.runtime.phase5e_real_smoke \
		--comfyui-source-dir $(COMFYUI_SOURCE_DIR) \
		--python-executable $(COMFYUI_PYTHON) \
		--test-workflows-dir ../test_workflows \
		--work-dir $(PHASE5E_SMOKE_WORK_DIR) \
		--model-view-dir $(COMFYUI_MODEL_VIEW_DIR) \
		--input-dir $(COMFYUI_INPUT_DIR) \
		--clean \
		--json-output $(PHASE5E_SMOKE_SUMMARY)

memory-governor-linux-validation:
	cd backend && $(BACKEND_PYTHON) -m app.runtime.memory_governor_hardware_validation \
		--data-dir $(MEMORY_GOVERNOR_VALIDATION_DATA_DIR) \
		--json-output $(MEMORY_GOVERNOR_VALIDATION_OUTPUT)
