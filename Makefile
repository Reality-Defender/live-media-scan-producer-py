.PHONY: dev lint test

dev:
	uv sync

lint:
	uv run ruff check .
	uv run pyright

test:
	uv run pytest
