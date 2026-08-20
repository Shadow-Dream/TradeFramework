"""Build-identified public schemas for Basic Workflow v2 resources.

The Engine treats these as ordinary recursive DataKey schemas. Protocol
identity and business meaning remain application-layer concerns.
"""

from __future__ import annotations

import copy


NUMBER = {"type": "number"}
NULLABLE_NUMBER = {"type": ["number", "null"]}
STRING = {"type": "string"}


def _closed_object(properties, required=None):
    return {
        "type": "object",
        "properties": copy.deepcopy(properties),
        "required": list(properties if required is None else required),
        "additionalProperties": False,
    }


def _object_map(value_schema):
    return {
        "type": "object",
        "additionalProperties": copy.deepcopy(value_schema),
    }


BAR_SCHEMA = _closed_object(
    {
        "open": NUMBER,
        "close": NUMBER,
        "high": NUMBER,
        "low": NUMBER,
    }
)

INSTRUMENT_PRICE_MAP_SCHEMA = _object_map(BAR_SCHEMA)
PRICE_SCHEMA = _object_map(INSTRUMENT_PRICE_MAP_SCHEMA)

POSITION_MAP_SCHEMA = _object_map(NUMBER)
PORTFOLIO_ACCOUNT_SCHEMA = _closed_object(
    {
        "cash": NUMBER,
        "positions": POSITION_MAP_SCHEMA,
        "equity": NUMBER,
    }
)

EXECUTION_ORDER_SCHEMA = _closed_object(
    {
        "side": {"type": "string", "enum": ["buy", "sell"]},
        "quantity": NUMBER,
        "price": NUMBER,
        "fee": NUMBER,
    }
)
EXECUTION_ORDERS_SCHEMA = _object_map(EXECUTION_ORDER_SCHEMA)

UNIVERSE_SELECTION_SCHEMA = _object_map(
    {"type": "boolean", "enum": [True]}
)
SIGNAL_SCORE_SCHEMA = _object_map(NULLABLE_NUMBER)
REQUESTED_INTENT_SCHEMA = _object_map(NUMBER)
APPROVED_INTENT_SCHEMA = _object_map(NUMBER)

SAMPLER_OUTPUT_SCHEMA = {
    "time": STRING,
    "price": PRICE_SCHEMA,
}

CSV_FIELDS = ("time", "open", "close", "high", "low")


def schema_copy(value):
    return copy.deepcopy(value)


__all__ = tuple(
    name
    for name in globals()
    if name.endswith("_SCHEMA")
    or name in {"CSV_FIELDS", "schema_copy"}
)
