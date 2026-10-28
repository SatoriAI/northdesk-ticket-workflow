"""The one way this service connects to Temporal, for the API and the worker alike."""

import logging

from temporalio.client import Client
from temporalio.runtime import LogForwardingConfig, LoggingConfig, Runtime, TelemetryConfig, TelemetryFilter

from northdesk.settings import TemporalSettings
from northdesk.workflows.converter import DATA_CONVERTER


async def connect(settings: TemporalSettings, *, lazy: bool = False) -> Client:
    """A client with the Decimal-aware data converter, so no caller can forget it."""
    return await Client.connect(
        settings.address, namespace=settings.namespace, data_converter=DATA_CONVERTER, runtime=_runtime(), lazy=lazy
    )


def _runtime() -> Runtime:
    """Route the SDK core's own warnings into Python logging, so they come out as JSON lines like everything else
    instead of coloured text on stderr (which breaks `docker compose logs | jq`). Core's fields stay in the
    message; the formatter skips the opaque `temporal_log` object."""
    forwarding = LogForwardingConfig(
        logger=logging.getLogger("temporalio.core"), append_target_to_name=False, prepend_target_on_message=False
    )
    logs = LoggingConfig(filter=TelemetryFilter(core_level="WARN", other_level="ERROR"), forwarding=forwarding)
    return Runtime(telemetry=TelemetryConfig(logging=logs))
