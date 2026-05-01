.PHONY: test test-backend test-frontend test-exporter

test: test-backend test-frontend test-exporter

test-backend:
	cd backend && pytest tests

test-frontend:
	cd frontend && npm test

test-exporter:
	pytest comfyui_export2noofy_node/tests
