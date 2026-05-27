.PHONY: install test lint typecheck check run-api

install:
	pip install -e ".[dev]"

test:
	pytest

lint:
	ruff check src tests
	ruff format --check src tests

typecheck:
	mypy src

check: lint typecheck test

run-api:
	uvicorn nordic_hike_planner.api:app --reload --port 8000