.PHONY: up down logs dev test lint format typecheck check migrate

up:
	docker compose up -d

down:
	docker compose down

logs:
	docker compose logs -f

dev:
	uv run fastapi dev app/main.py

test:
	uv run pytest -q

lint:
	uv run ruff check .

format:
	uv run ruff format .

typecheck:
	uv run mypy app

check:
	uv run ruff check .
	uv run ruff format --check .
	uv run mypy app
	uv run pytest -q

migrate:
	uv run alembic upgrade head
