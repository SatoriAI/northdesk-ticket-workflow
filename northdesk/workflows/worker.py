"""Worker entrypoint: `python -m northdesk.workflows.worker`. Builds every dependency here, nothing at import."""

import asyncio
import logging

import httpx
from temporalio.client import Client
from temporalio.worker import Worker

from northdesk.clients.helpdesk.client import HelpdeskClient
from northdesk.clients.llm.fake import FakeLlm
from northdesk.data.repository import DataRepository
from northdesk.log import configure_logging
from northdesk.settings import WorkerSettings
from northdesk.workflows.activities import TicketActivities
from northdesk.workflows.converter import DATA_CONVERTER
from northdesk.workflows.ticket_workflow import TicketWorkflow

logger = logging.getLogger(__name__)


async def run(settings: WorkerSettings) -> None:
    client = await Client.connect(
        settings.temporal.address, namespace=settings.temporal.namespace, data_converter=DATA_CONVERTER
    )
    async with httpx.AsyncClient(
        base_url=settings.helpdesk.url, timeout=settings.helpdesk.timeout_seconds
    ) as helpdesk_http:
        activities = TicketActivities(
            repository=DataRepository.load(), llm=FakeLlm(), helpdesk=HelpdeskClient(helpdesk_http)
        )
        worker = Worker(
            client,
            task_queue=settings.temporal.task_queue,
            workflows=[TicketWorkflow],
            activities=[activities.load_context, activities.decide, activities.act],
        )
        logger.info("worker started on task queue %s (llm mode %s)", settings.temporal.task_queue, settings.llm.mode)
        await worker.run()


def main() -> None:
    configure_logging()
    asyncio.run(run(WorkerSettings()))


if __name__ == "__main__":
    main()
