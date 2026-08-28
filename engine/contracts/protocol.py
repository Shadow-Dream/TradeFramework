"""Passive protocol ownership metadata shared by versioned resources."""

from __future__ import annotations

from collections.abc import Mapping


PROTOCOL_ID_FIELD = "protocolId"


def normalize_protocol_id(value, *, label=PROTOCOL_ID_FIELD):
    """Require one canonical opaque protocol ID without resolving its meaning."""

    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
    ):
        raise ValueError(f"{label} must be a canonical non-empty string.")
    return value


def common_protocol_id(records):
    """Return one exact declaration shared by every record, otherwise ``None``."""

    values = []
    for record in records:
        if not isinstance(record, Mapping) or PROTOCOL_ID_FIELD not in record:
            return None
        values.append(
            normalize_protocol_id(
                record[PROTOCOL_ID_FIELD],
                label="Resource protocolId",
            )
        )
    return values[0] if values and len(set(values)) == 1 else None


__all__ = (
    "PROTOCOL_ID_FIELD",
    "common_protocol_id",
    "normalize_protocol_id",
)
