"""Worker entrypoint: `python -m northdesk.workflows.worker`. Builds every dependency here, nothing at import."""

import asyncio
import logging

import httpx
from temporalio.client import Client
from temporalio.runtime import LogForwardingConfig, LoggingConfig, Runtime, TelemetryConfig, TelemetryFilter
from temporalio.worker import Worker

from northdesk.clients.helpdesk.client import HelpdeskClient
from northdesk.clients.llm.base import LlmClient
from northdesk.clients.llm.fake import FakeLlm
from northdesk.data.repository import DataRepository
from northdesk.log import configure_logging
from northdesk.settings import LlmSettings, WorkerSettings
from northdesk.workflows import log_context
from northdesk.workflows.activities import TicketActivities
from northdesk.workflows.converter import DATA_CONVERTER
from northdesk.workflows.ticket_workflow import TicketWorkflow

logger = logging.getLogger(__name__)


async def run(settings: WorkerSettings) -> None:
    client = await Client.connect(
        settings.temporal.address,
        namespace=settings.temporal.namespace,
        data_converter=DATA_CONVERTER,
        runtime=_runtime(),
    )
    async with httpx.AsyncClient(
        base_url=settings.helpdesk.url, timeout=settings.helpdesk.timeout_seconds
    ) as helpdesk_http:
        activities = TicketActivities(
            repository=DataRepository.load(), llm=build_llm(settings.llm), helpdesk=HelpdeskClient(helpdesk_http)
        )
        worker = Worker(
            client,
            task_queue=settings.temporal.task_queue,
            workflows=[TicketWorkflow],
            activities=[activities.load_context, activities.decide, activities.act],
        )
        logger.info("worker started on task queue %s (llm mode %s)", settings.temporal.task_queue, settings.llm.mode)
        await worker.run()


def build_llm(settings: LlmSettings) -> LlmClient:
    if settings.mode == "fake":
        return FakeLlm()
    raise SystemExit("LLM_MODE=real is not available yet; use LLM_MODE=fake")


def _runtime() -> Runtime:
    """Forward the SDK core's own warnings (e.g. "Failing workflow task") into Python logging, so they are JSON
    lines like everything else instead of coloured text on stderr."""
    forwarding = LogForwardingConfig(logger=logging.getLogger("temporalio.core"), append_log_fields_to_message=False)
    logs = LoggingConfig(filter=TelemetryFilter(core_level="WARN", other_level="ERROR"), forwarding=forwarding)
    return Runtime(telemetry=TelemetryConfig(logging=logs))


def main() -> None:
    configure_logging()
    log_context.install(logging.getLogger())
    asyncio.run(run(WorkerSettings()))


if __name__ == "__main__":
    main()
