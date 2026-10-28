# 0002. Carry money as Decimal through Temporal with a custom data converter

Status: accepted, 2026-10-28

## Context

Refund validation compares amounts, so money must stay `Decimal` end to end. Workflow inputs,
activity results and query results are dataclasses serialised by Temporal's data converter. The
default JSON converter raises `TypeError: Object of type Decimal is not JSON serializable`.

## Options

1. Store amounts as strings in the dataclasses and parse them wherever they are compared.
2. Store them as floats. Rejected: that is the bug `Decimal` exists to prevent.
3. Extend the converter: encode `Decimal` as a JSON string, and decode it back from the type hint.

## Decision

Option 3 (`northdesk/workflows/converter.py`). The domain types say what they mean
(`amount_eur: Decimal`), and validation code never parses strings. In the JSON payload the amount
is a string, `"49.0"`, so the Temporal UI shows it exactly.

## Consequences

Every Temporal client that reads or writes these types must pass `data_converter=DATA_CONVERTER`:
the API, the worker and the test environments. A client without it fails loudly with the
`TypeError` above rather than silently losing precision. `tests/unit/test_converter.py` pins the
round trip, including `Decimal | None`.
