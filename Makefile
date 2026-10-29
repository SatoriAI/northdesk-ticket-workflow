.PHONY: check lint test test-offline

# Everything CI runs: the pre-commit hooks (file hygiene, ruff with the import bans, formatting, mypy) on every
# file, then the tests with coverage. Needs the network only once, to fetch the hook repository and the Temporal
# test server binary; both are cached afterwards.
check: lint test

# Defined once, in .pre-commit-config.yaml. Fixable problems (formatting, whitespace) are fixed and reported as a
# failure, so a rerun passes and the diff shows what changed. Like CI, it checks the files git tracks: `git add` a
# new file before relying on it being linted.
lint:
	uv run pre-commit run --all-files --show-diff-on-failure

test:
	uv run pytest --cov --cov-report=term-missing --cov-report=xml

# The whole test suite in a container with networking off: proves the tests need no network once the Temporal
# test server binary is in the image (the Dockerfile's test stage downloads it at build time).
test-offline:
	docker build --target test -t northdesk-test .
	docker run --rm --network none northdesk-test
