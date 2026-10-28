.PHONY: check lint test test-offline

# Everything CI runs: lint (including the import bans), formatting, types and the tests. Offline once the
# Temporal test server binary is cached (see README).
check: lint test

lint:
	uv run ruff check .
	uv run ruff format --check .
	uv run mypy .

test:
	uv run pytest

# The whole test suite in a container with networking off: proves the tests need no network once the Temporal
# test server binary is in the image (the Dockerfile's test stage downloads it at build time).
test-offline:
	docker build --target test -t northdesk-test .
	docker run --rm --network none northdesk-test
