from typing import Literal, Self

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class ServiceSettings(BaseSettings):
    """Common base: nested settings classes don't inherit the parent's model_config."""

    # env_ignore_empty: an empty VAR= (as in .env.example) means "use the default", not "parse an empty string".
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", env_ignore_empty=True)


class TemporalSettings(ServiceSettings):
    model_config = SettingsConfigDict(env_prefix="TEMPORAL_")

    address: str = "localhost:7233"
    namespace: str = "default"
    task_queue: str = "northdesk-tickets"


class LlmSettings(ServiceSettings):
    model_config = SettingsConfigDict(env_prefix="LLM_")

    mode: Literal["fake", "real"] = "fake"
    provider: Literal["anthropic"] = "anthropic"
    model: str = "claude-opus-5-5"
    # Per model call; the whole agent loop (up to six calls) must fit in DECIDE_TIMEOUT (7 min). Generous because
    # the default model always thinks before answering.
    timeout_seconds: float = Field(default=60.0, gt=0)
    # Optional so the fake mode needs no key; the real mode requires it (checked below).
    api_key: SecretStr | None = None

    @model_validator(mode="after")
    def _require_key_in_real_mode(self) -> Self:
        if self.mode == "real" and not (self.api_key and self.api_key.get_secret_value()):
            raise ValueError("LLM_API_KEY is required when LLM_MODE=real")
        return self


class HelpdeskSettings(ServiceSettings):
    model_config = SettingsConfigDict(env_prefix="HELPDESK_")

    url: str = "http://localhost:8001"
    timeout_seconds: float = Field(default=5.0, gt=0)


class ApiSettings(ServiceSettings):
    """The API only starts workflows and reads their state, so it gets no LLM key or helpdesk URL."""

    enable_docs: bool = False
    temporal: TemporalSettings = Field(default_factory=TemporalSettings)


class WorkerSettings(ServiceSettings):
    temporal: TemporalSettings = Field(default_factory=TemporalSettings)
    llm: LlmSettings = Field(default_factory=LlmSettings)
    helpdesk: HelpdeskSettings = Field(default_factory=HelpdeskSettings)
