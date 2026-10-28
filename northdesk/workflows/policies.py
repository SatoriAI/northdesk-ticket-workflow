"""Timeouts and retry policies for the ticket activities, in one place so they can be tuned and documented."""

from datetime import timedelta

from temporalio.common import RetryPolicy

LOAD_CONTEXT_TIMEOUT = timedelta(seconds=10)
# Local data today, so a failure is a bug: retry briefly, then fail rather than sit in loading_context forever.
LOAD_CONTEXT_RETRY = RetryPolicy(initial_interval=timedelta(seconds=1), maximum_attempts=3)

# One whole agent loop: up to MAX_TURNS model calls of at most LLM_TIMEOUT_SECONDS each, plus the tools.
DECIDE_TIMEOUT = timedelta(minutes=4)
# decide heartbeats after every model turn, so one hung call is cut off well before DECIDE_TIMEOUT.
# Keep it above LLM_TIMEOUT_SECONDS (settings.py checks that).
DECIDE_HEARTBEAT_TIMEOUT = timedelta(seconds=45)
# Waits of 2, 4, 8, 16 and 30 s: a provider outage of about a minute is ridden out before the ticket escalates.
DECIDE_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=2),
    backoff_coefficient=2.0,
    maximum_interval=timedelta(seconds=30),
    maximum_attempts=6,
)

ACT_TIMEOUT = timedelta(seconds=15)
# Ten attempts: with the Compose fault rates (20% + 20%), one attempt fails about 36% of the time, so all ten
# fail for about 0.004% of tickets (six attempts left about 0.2% failed).
ACT_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=1),
    backoff_coefficient=2.0,
    maximum_interval=timedelta(seconds=20),
    maximum_attempts=10,
)

# How long a refund waits for a person before it is escalated instead (the brief's 24 h).
APPROVAL_TIMEOUT = timedelta(hours=24)
