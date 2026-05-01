.PHONY: test test-backend test-frontend

test: test-backend test-frontend

test-backend:
	cd backend && pytest tests

test-frontend:
	cd frontend && npm test
