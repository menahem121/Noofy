.PHONY: test test-backend test-frontend test-exporter phase5e-real-smoke

BACKEND_PYTHON ?= .venv/bin/python
PYTEST ?= pytest
COMFYUI_SOURCE_DIR ?= /home/ubuntu/ComfyUI
COMFYUI_PYTHON ?= $(COMFYUI_SOURCE_DIR)/venv/bin/python
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
		--model-view-dir $(COMFYUI_SOURCE_DIR)/models \
		--input-dir $(COMFYUI_SOURCE_DIR)/input \
		--clean \
		--json-output $(PHASE5E_SMOKE_SUMMARY)
