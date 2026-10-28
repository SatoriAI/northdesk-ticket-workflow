# One image for the API, the worker and the fake helpdesk; only the command differs (see docker-compose.yml).
FROM python:3.14.10-slim

COPY --from=ghcr.io/astral-sh/uv:0.11.12 /uv /bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH=/opt/venv/bin:$PATH \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Dependencies first, so code changes don't invalidate this layer.
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev

COPY northdesk ./northdesk
COPY helpdesk ./helpdesk

RUN useradd --create-home --uid 10001 app && mkdir /data && chown app /data
USER app

EXPOSE 8000
CMD ["uvicorn", "northdesk.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--log-config", "northdesk/logging.json"]
