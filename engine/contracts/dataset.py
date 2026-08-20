#!/usr/bin/env python3
"""Dataset record contracts exposed to verified Sampler runtimes."""

from __future__ import annotations

import copy
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping

from engine.contracts.data_model import validate_normalized_json_value
from engine.contracts.exact_fields import require_exact_fields
from engine.core import resource_ids


RECORDS_CAPABILITY = "records"
RECORDS_PROTOCOL = "trade.dataset.records-jsonl/v1"
VISUALIZATION_CAPABILITY = "visualization"
VISUALIZATION_PROTOCOL = "trade.dataset.visualization/v1"


def normalize_dataset_source(source):
    require_exact_fields(
        source,
        allowed={"type", "details"},
        required={"type", "details"},
        label="Dataset source",
    )
    if not isinstance(source["type"], str) or not source["type"]:
        raise ValueError("Dataset source.type must be a non-empty string.")
    if not isinstance(source["details"], dict):
        raise ValueError("Dataset source.details must be an object.")
    validate_normalized_json_value(source["details"], {}, path="Dataset.source.details")
    return copy.deepcopy(source)


def normalize_dataset_descriptor(dataset):
    require_exact_fields(
        dataset,
        allowed={"datasetId", "name", "source", "metadata"},
        required={"datasetId", "name", "source", "metadata"},
        label="Dataset publication",
    )
    dataset_id = resource_ids.normalize_resource_id(dataset["datasetId"])
    if dataset_id != dataset["datasetId"]:
        raise ValueError("Dataset publication datasetId must already be normalized.")
    if not isinstance(dataset["name"], str) or not dataset["name"].strip():
        raise ValueError("Dataset publication name is required.")
    if not isinstance(dataset["metadata"], dict):
        raise ValueError("Dataset publication metadata must be an object.")
    validate_normalized_json_value(dataset["metadata"], {}, path="Dataset.metadata")
    return {
        "datasetId": dataset_id,
        "name": dataset["name"].strip(),
        "source": normalize_dataset_source(dataset["source"]),
        "metadata": copy.deepcopy(dataset["metadata"]),
    }


def parse_record_instant(value, *, label):
    if not isinstance(value, str) or not value:
        raise ValueError(f"{label} must be a non-empty ISO timestamp.")
    text = value + "T00:00:00Z" if len(value) == 10 else value
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(f"{label} must be an ISO timestamp.") from exc
    if parsed.tzinfo is None:
        if len(value) != 10:
            raise ValueError(f"{label} must include an absolute timezone.")
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


@dataclass(frozen=True)
class DatasetRecord:
    sequence: int
    event_time: str
    available_at: str
    values: Mapping[str, Any]
