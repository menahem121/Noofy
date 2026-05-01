.PHONY: test test-backend test-frontend test-exporter

BACKEND_PYTHON ?= .venv/bin/python
PYTEST ?= pytest

test: test-backend test-frontend test-exporter

test-backend:
	cd backend && $(BACKEND_PYTHON) -m pytest tests

test-frontend:
	cd frontend && npm test

test-exporter:
	$(PYTEST) comfyui_export2noofy_node/tests
