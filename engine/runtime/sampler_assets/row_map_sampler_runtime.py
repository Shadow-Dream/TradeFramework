#!/usr/bin/env python3
"""Immutable, standard-library-only record mapping used by row-map Sampler bundles."""

from __future__ import annotations

import copy


def _set_path(target, path, value):
    parts = str(path).split(".")
    cursor = target
    for part in parts[:-1]:
        child = cursor.get(part)
        if child is None:
            child = {}
            cursor[part] = child
        if not isinstance(child, dict):
            raise ValueError(f"Sampler DataKey '{path}' collides with scalar path '{part}'.")
        cursor = child
    cursor[parts[-1]] = value


def map_record(
    values,
    *,
    sequence,
    event_time,
    available_at,
    mapping,
    include_unmapped_fields,
    unmapped_prefix,
    source_fields,
):
    """Map one already-causal Dataset record into data and provenance objects."""
    missing = sorted({field for field in mapping.values() if field not in values})
    if missing:
        raise ValueError("Dataset record is missing Sampler field(s): " + ", ".join(missing))

    data = {}
    provenance = {}
    # ``mapping`` crosses canonical JSON boundaries.  Object member order is
    # therefore never an execution authority; DataKey order is the row-map
    # protocol's deterministic parent/child write order.
    for data_key in sorted(mapping):
        field = mapping[data_key]
        _set_path(data, data_key, copy.deepcopy(values[field]))
        provenance[data_key] = {
            "recordSequence": sequence,
            "sourceField": field,
            "eventTime": event_time,
            "availableAt": available_at,
        }

    if include_unmapped_fields:
        mapped_fields = set(mapping.values())
        unknown_fields = sorted(set(values) - set(source_fields))
        if unknown_fields:
            raise ValueError(
                "Dataset field(s) have no declared type: "
                + ", ".join(unknown_fields)
            )
        for field_name in sorted(source_fields):
            if field_name in mapped_fields:
                continue
            if field_name not in values:
                continue
            value = values[field_name]
            data_key = f"{unmapped_prefix}{field_name}"
            _set_path(data, data_key, copy.deepcopy(value))
            provenance[data_key] = {
                "recordSequence": sequence,
                "sourceField": field_name,
                "eventTime": event_time,
                "availableAt": available_at,
            }
    return data, provenance
