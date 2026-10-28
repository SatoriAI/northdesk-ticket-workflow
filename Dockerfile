# syntax=docker/dockerfile:1
# One image for the API, the worker and the fake helpdesk (the `app` stage, which Compose builds); only the command
# differs. The `test` stage runs the test suite with no network: `make test-offline`.
FROM python:3.14.10-slim AS base

COPY --from=ghcr.io/astral-sh/uv:0.11.12 /uv /bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH=/opt/venv/bin:$PATH \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Dependencies only; each stage copies the code last, so editing it does not invalidate the slow layers.
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev


FROM base AS test

RUN uv sync --locked
# The workflow tests use Temporal's time-skipping test server, which the SDK downloads on first use. Fetch it now,
# into a fixed directory the tests also read, so `docker run --network none` can run every test.
ENV TEMPORAL_TEST_SERVER_DIR=/opt/temporal
RUN mkdir -p "$TEMPORAL_TEST_SERVER_DIR"
RUN <<EOF
python -c '
import asyncio, os
from temporalio.testing import WorkflowEnvironment

async def fetch() -> None:
    async with await WorkflowEnvironment.start_time_skipping(download_dest_dir=os.environ["TEMPORAL_TEST_SERVER_DIR"]):
        pass

asyncio.run(fetch())
'
EOF
COPY northdesk ./northdesk
COPY helpdesk ./helpdesk
COPY .env.example ./
COPY tests ./tests
CMD ["pytest"]


FROM base AS app

COPY northdesk ./northdesk
COPY helpdesk ./helpdesk
RUN useradd --create-home --uid 10001 app && mkdir /data && chown app /data
USER app

EXPOSE 8000
CMD ["uvicorn", "northdesk.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--log-config", "northdesk/logging.json"]
