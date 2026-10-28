from collections.abc import AsyncIterator

import pytest
from temporalio.testing import WorkflowEnvironment

from northdesk.workflows.converter import DATA_CONVERTER


@pytest.fixture(scope="session")
async def env() -> AsyncIterator[WorkflowEnvironment]:
    async with await WorkflowEnvironment.start_time_skipping(data_converter=DATA_CONVERTER) as env:
        yield env
