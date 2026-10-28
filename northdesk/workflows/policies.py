"""Timeouts and retry policies for the ticket activities, in one place so they can be tuned and documented."""

from datetime import timedelta

from temporalio.common import RetryPolicy

LOAD_CONTEXT_TIMEOUT = timedelta(seconds=10)

# One LLM loop, tool calls included; bounded so a hung provider call is retried rather than waited on.
DECIDE_TIMEOUT = timedelta(seconds=60)
DECIDE_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=2),
    backoff_coefficient=2.0,
    maximum_interval=timedelta(seconds=30),
    maximum_attempts=4,
)

ACT_TIMEOUT = timedelta(seconds=15)
ACT_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=1),
    backoff_coefficient=2.0,
    maximum_interval=timedelta(seconds=20),
    maximum_attempts=6,
)
