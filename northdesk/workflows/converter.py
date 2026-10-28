"""Temporal data converter that carries Decimal as a JSON string, so money never becomes a float.

Every Temporal client that sends or reads these dataclasses (API, worker, tests) must use `DATA_CONVERTER`.
"""

import dataclasses
from decimal import Decimal
from typing import Any

from temporalio.converter import (
    AdvancedJSONEncoder,
    CompositePayloadConverter,
    DataConverter,
    DefaultPayloadConverter,
    JSONPlainPayloadConverter,
    JSONTypeConverter,
)


class _DecimalEncoder(AdvancedJSONEncoder):
    def default(self, o: Any) -> Any:
        if isinstance(o, Decimal):
            return str(o)
        return super().default(o)


class _DecimalTypeConverter(JSONTypeConverter):
    def to_typed_value(self, hint: type, value: Any) -> Any:
        if hint is Decimal:
            # A JSON number (e.g. a model that wrote "amount_eur": 49.1) goes through str(), the float's shortest
            # round-trip text, so 49.1 stays 49.1 rather than 49.10000000000000142...; the refund rules then
            # reject anything finer than a cent. Raising here instead would fail the workflow task inside the
            # workflow, where it is retried forever and the ticket never escalates.
            if isinstance(value, bool) or not isinstance(value, (str, int, float)):
                raise TypeError(f"cannot read a Decimal from {type(value).__name__}")
            return Decimal(str(value))
        return JSONTypeConverter.Unhandled


class _PayloadConverter(CompositePayloadConverter):
    def __init__(self) -> None:
        super().__init__(
            *(
                JSONPlainPayloadConverter(encoder=_DecimalEncoder, custom_type_converters=[_DecimalTypeConverter()])
                if isinstance(converter, JSONPlainPayloadConverter)
                else converter
                for converter in DefaultPayloadConverter.default_encoding_payload_converters
            )
        )


DATA_CONVERTER = dataclasses.replace(DataConverter.default, payload_converter_class=_PayloadConverter)
