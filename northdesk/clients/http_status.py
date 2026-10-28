"""Which HTTP failures are worth retrying, shared by every outbound client."""

from http import HTTPStatus

# 4xx statuses that say "not now" rather than "never": the same request may succeed later. 409 is how an
# idempotency-key server says "a request with this key is still in flight" (IETF Idempotency-Key draft).
_TEMPORARY_CLIENT_ERRORS = frozenset({HTTPStatus.REQUEST_TIMEOUT, HTTPStatus.CONFLICT, HTTPStatus.TOO_MANY_REQUESTS})


def is_temporary(status: int) -> bool:
    """True when retrying the same request may succeed: any 5xx (529 "overloaded" included), 408, 409 or 429."""
    return status >= HTTPStatus.INTERNAL_SERVER_ERROR or status in _TEMPORARY_CLIENT_ERRORS
