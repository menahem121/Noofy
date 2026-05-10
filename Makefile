.PHONY: install run run-backend doctor test test-backend test-frontend test-exporter phase5e-real-smoke memory-governor-linux-validation

BACKEND_PYTHON ?= .venv/bin/python
BACKEND_VENV_DIR ?= $(CURDIR)/backend/.venv
BACKEND_VENV_ACTIVATE ?= $(BACKEND_VENV_DIR)/bin/activate
ROOT_BACKEND_PYTHON ?= backend/.venv/bin/python
PYTEST ?= $(ROOT_BACKEND_PYTHON) -m pytest
NOOFY_PYTHON ?= $(shell \
  for py in python3.13 python3.12 python3.11 python3.14 python3; do \
    if command -v $$py >/dev/null 2>&1 && $$py -c "import sys; sys.exit(0 if sys.version_info >= (3,11) else 1)" 2>/dev/null; then \
      echo $$py; break; \
    fi; \
  done)
NOOFY_SCRIPT ?= scripts/noofy.py

define REQUIRE_PYTHON
@test -n "$(NOOFY_PYTHON)" || { \
  printf '\nError: Noofy source-checkout install requires Python 3.11 or newer,\n'; \
  printf 'but no compatible Python was found in PATH.\n\n'; \
  printf '  macOS:   brew install python@3.13\n'; \
  printf '  Ubuntu:  sudo apt install python3.13\n'; \
  printf '  Fedora:  sudo dnf install python3.13\n'; \
  printf '  All:     https://www.python.org/downloads\n\n'; \
  exit 1; \
}
endef
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
	$(REQUIRE_PYTHON)
	$(NOOFY_PYTHON) $(NOOFY_SCRIPT) install --data-dir "$(NOOFY_DATA_DIR)"

run:
	@backend_venv="$(BACKEND_VENV_DIR)"; \
	backend_activate="$(BACKEND_VENV_ACTIVATE)"; \
	if [ ! -f "$$backend_activate" ]; then \
		printf 'Backend venv is missing. Run: make install\n' >&2; \
		exit 1; \
	fi; \
	backend_real="$$(cd "$$backend_venv" && pwd -P)"; \
	active_real=""; \
	if [ -n "$${VIRTUAL_ENV:-}" ]; then \
		active_real="$$(cd "$$VIRTUAL_ENV" 2>/dev/null && pwd -P || true)"; \
	fi; \
	if [ "$$active_real" != "$$backend_real" ]; then \
		. "$$backend_activate"; \
	fi; \
	python $(NOOFY_SCRIPT) run --data-dir "$(NOOFY_DATA_DIR)"

run-backend:
	$(REQUIRE_PYTHON)
	-$(NOOFY_PYTHON) $(NOOFY_SCRIPT) serve --data-dir "$(NOOFY_DATA_DIR)"

doctor:
	$(REQUIRE_PYTHON)
	$(NOOFY_PYTHON) $(NOOFY_SCRIPT) doctor --data-dir "$(NOOFY_DATA_DIR)"

test: test-backend test-frontend test-exporter

test-backend:
	cd backend && $(BACKEND_PYTHON) -m pytest tests

test-frontend:
	cd frontend && npm test

test-exporter:
	$(PYTEST) comfyui_export2noofy_node/tests

phase5e-real-smoke:
	cd backend && $(BACKEND_PYTHON) -m tools.validation.phase5e_real_smoke \
		--comfyui-source-dir $(COMFYUI_SOURCE_DIR) \
		--python-executable $(COMFYUI_PYTHON) \
		--test-workflows-dir ../test_workflows \
		--work-dir $(PHASE5E_SMOKE_WORK_DIR) \
		--model-view-dir $(COMFYUI_MODEL_VIEW_DIR) \
		--input-dir $(COMFYUI_INPUT_DIR) \
		--clean \
		--json-output $(PHASE5E_SMOKE_SUMMARY)

memory-governor-linux-validation:
	cd backend && $(BACKEND_PYTHON) -m tools.validation.memory_governor_hardware_validation \
		--data-dir $(MEMORY_GOVERNOR_VALIDATION_DATA_DIR) \
		--json-output $(MEMORY_GOVERNOR_VALIDATION_OUTPUT)
