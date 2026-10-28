from datetime import timedelta

from northdesk.agent.loop import MAX_TURNS
from northdesk.settings import LlmSettings
from northdesk.workflows.policies import DECIDE_TIMEOUT


def test_decide_timeout__fits_every_model_call_of_one_agent_loop() -> None:
    """Changing MAX_TURNS or the per-call LLM timeout must not leave the decide activity cut off mid-loop."""
    per_call = timedelta(seconds=LlmSettings.model_fields["timeout_seconds"].default)

    assert MAX_TURNS * per_call < DECIDE_TIMEOUT
