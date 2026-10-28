"""Static limits and names. Never env-driven."""

# Base URI for RFC 9457 problem `type` values; the error `code` is appended.
ERROR_TYPE_BASE = "https://errors.northdesk.example/"

# One workflow per ticket; the ID doubles as the duplicate guard.
WORKFLOW_ID_PREFIX = "ticket-"

# Readiness must answer well inside a probe timeout.
READINESS_TEMPORAL_TIMEOUT_SECONDS = 2.0
