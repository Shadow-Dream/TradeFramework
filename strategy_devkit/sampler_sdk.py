#!/usr/bin/env python3
"""Small public SDK surface available to Python Script Samplers."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping
import math


def _require_json(value, path="value"):
    if value is None or isinstance(value, (str, bool)):
        return
    if isinstance(value, int) and not isinstance(value, bool):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{path} must contain only finite JSON numbers.")
        return
    if type(value) is list:
        for index, item in enumerate(value):
            _require_json(item, f"{path}[{index}]")
        return
    if type(value) is dict:
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError(f"{path} object keys must be strings.")
            _require_json(item, f"{path}.{key}")
        return
    raise ValueError(f"{path} contains a non-JSON value.")


class Dataset:
    """Read-only descriptor for one immutable Dataset version."""

    def __init__(self, descriptor: Mapping[str, Any]):
        required = {
            "datasetId", "datasetVersionId", "storageType", "storagePath",
            "root", "contentHash", "capabilities",
        }
        if type(descriptor) is not dict or set(descriptor) != required:
            raise ValueError("Dataset descriptor has an invalid schema.")
        for field in (
            "datasetId", "datasetVersionId", "storageType", "storagePath", "root",
            "contentHash",
        ):
            if not isinstance(descriptor[field], str) or not descriptor[field]:
                raise ValueError(f"Dataset descriptor {field} must be a non-empty string.")
        self.dataset_id = descriptor["datasetId"]
        self.version_id = descriptor["datasetVersionId"]
        self.storage_type = descriptor["storageType"]
        if self.storage_type != "directory":
            raise ValueError("Dataset descriptor storageType must be 'directory'.")
        self.storage_path = Path(descriptor["storagePath"]).resolve()
        self.root = Path(descriptor["root"]).resolve()
        try:
            self.storage_path.relative_to(self.root)
        except ValueError as exc:
            raise ValueError("Dataset storagePath must be inside the Dataset root.") from exc
        self.content_hash = descriptor["contentHash"]
        if type(descriptor["capabilities"]) is not dict:
            raise ValueError("Dataset capabilities must be an object.")
        self.capabilities = dict(descriptor["capabilities"])

    def path(self, relative_path: str = "") -> Path:
        """Resolve a path inside the Dataset root without permitting traversal."""
        if not isinstance(relative_path, str):
            raise ValueError("Dataset relative path must be a string.")
        candidate = (self.root / relative_path).resolve()
        try:
            candidate.relative_to(self.root)
        except ValueError as exc:
            raise ValueError("Sampler path is outside the immutable Dataset root.") from exc
        return candidate


def sample(decision_time: str, data: Mapping[str, Any], *, provenance=None, cycle_id=""):
    """Convenience constructor for one yielded Sampler item."""
    if not isinstance(decision_time, str) or not decision_time.strip():
        raise ValueError("Sampler decision_time must be a non-empty string.")
    if type(data) is not dict:
        raise ValueError("Sampler data must be an object.")
    if provenance is None:
        provenance = {}
    if type(provenance) is not dict:
        raise ValueError("Sampler provenance must be an object.")
    if any(
        not isinstance(key, str)
        or not key
        or type(value) is not dict
        for key, value in provenance.items()
    ):
        raise ValueError("Sampler provenance must map non-empty string paths to objects.")
    if not isinstance(cycle_id, str):
        raise ValueError("Sampler cycle_id must be a string.")
    _require_json(data, "Sampler data")
    _require_json(provenance, "Sampler provenance")
    return {
        "decisionTime": decision_time,
        "data": dict(data),
        "provenance": {key: dict(value) for key, value in provenance.items()},
        **({"cycleId": cycle_id} if cycle_id else {}),
    }
