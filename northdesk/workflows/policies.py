"""Timeouts and retry policies for the ticket activities, in one place so they can be tuned and documented."""

from datetime import timedelta

from temporalio.common import RetryPolicy

LOAD_CONTEXT_TIMEOUT = timedelta(seconds=10)
# Local data today, so a failure is a bug: retry briefly, then fail rather than sit in loading_context forever.
LOAD_CONTEXT_RETRY = RetryPolicy(initial_interval=timedelta(seconds=1), maximum_attempts=3)

# One LLM loop, tool calls included; bounded so a hung provider call is retried rather than waited on.
DECIDE_TIMEOUT = timedelta(seconds=60)
# Waits of 2, 4, 8, 16 and 30 s: a provider outage of about a minute is ridden out before the ticket escalates.
DECIDE_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=2),
    backoff_coefficient=2.0,
    maximum_interval=timedelta(seconds=30),
    maximum_attempts=6,
)

ACT_TIMEOUT = timedelta(seconds=15)
ACT_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=1),
    backoff_coefficient=2.0,
    maximum_interval=timedelta(seconds=20),
    maximum_attempts=6,
)

# How long a refund waits for a person before it is escalated instead (the brief's 24 h).
APPROVAL_TIMEOUT = timedelta(hours=24)
