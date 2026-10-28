.PHONY: check lint test

# Everything CI runs: lint (including the import bans), formatting, types and the tests. Offline once the
# Temporal test server binary is cached (see README).
check: lint test

lint:
	uv run ruff check .
	uv run ruff format --check .
	uv run mypy .

test:
	uv run pytest
