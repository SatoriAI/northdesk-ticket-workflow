"""Worker entrypoint: `python -m northdesk.workflows.worker`. Builds every dependency here, nothing at import."""

import asyncio
import logging

import httpx
from temporalio.worker import Worker

from northdesk.clients.helpdesk.client import HelpdeskClient
from northdesk.clients.llm.anthropic_client import AnthropicLlm
from northdesk.clients.llm.base import LlmClient
from northdesk.clients.llm.fake import FakeLlm
from northdesk.data.repository import DataRepository
from northdesk.log import configure_logging
from northdesk.settings import LlmSettings, WorkerSettings
from northdesk.workflows import log_context
from northdesk.workflows.activities import TicketActivities
from northdesk.workflows.client import connect
from northdesk.workflows.ticket_workflow import TicketWorkflow

logger = logging.getLogger(__name__)


async def run(settings: WorkerSettings) -> None:
    client = await connect(settings.temporal)
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
    assert settings.api_key is not None  # LlmSettings refuses real mode without a key  # noqa: S101
    return AnthropicLlm(
        api_key=settings.api_key.get_secret_value(), model=settings.model, timeout_seconds=settings.timeout_seconds
    )


def main() -> None:
    configure_logging()
    log_context.install(logging.getLogger())
    asyncio.run(run(WorkerSettings()))


if __name__ == "__main__":
    main()
