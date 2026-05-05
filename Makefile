.PHONY: test test-backend test-frontend test-exporter phase5e-real-smoke

BACKEND_PYTHON ?= .venv/bin/python
ROOT_BACKEND_PYTHON ?= backend/.venv/bin/python
PYTEST ?= $(ROOT_BACKEND_PYTHON) -m pytest
NOOFY_DATA_DIR ?= $(CURDIR)/.noofy-runtime/data
COMFYUI_SOURCE_DIR ?= $(CURDIR)/third_party/comfyui
COMFYUI_PYTHON ?= $(NOOFY_DATA_DIR)/runtime/comfyui-venv/bin/python
COMFYUI_MODEL_VIEW_DIR ?= $(NOOFY_DATA_DIR)/models
COMFYUI_INPUT_DIR ?= $(NOOFY_DATA_DIR)/input
PHASE5E_SMOKE_WORK_DIR ?= /tmp/noofy-phase5e-real-smoke
PHASE5E_SMOKE_SUMMARY ?= $(PHASE5E_SMOKE_WORK_DIR)/summary.json

test: test-backend test-frontend test-exporter

test-backend:
	cd backend && $(BACKEND_PYTHON) -m pytest tests

test-frontend:
	cd frontend && npm test

test-exporter:
	$(PYTEST) comfyui_export2noofy_node/tests

phase5e-real-smoke:
	cd backend && $(COMFYUI_PYTHON) -m app.runtime.phase5e_real_smoke \
		--comfyui-source-dir $(COMFYUI_SOURCE_DIR) \
		--python-executable $(COMFYUI_PYTHON) \
		--test-workflows-dir ../test_workflows \
		--work-dir $(PHASE5E_SMOKE_WORK_DIR) \
		--model-view-dir $(COMFYUI_MODEL_VIEW_DIR) \
		--input-dir $(COMFYUI_INPUT_DIR) \
		--clean \
		--json-output $(PHASE5E_SMOKE_SUMMARY)
