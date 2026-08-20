#!/usr/bin/env python3
"""Authority-bound Dataset capabilities used by Sampler runtimes."""

from __future__ import annotations

import copy
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Dict

from engine.authority import dataset as dataset_authority
from engine.contracts import dataset as dataset_contracts
from engine.contracts import strict_json
from engine.contracts.data_model import validate_normalized_json_value
from engine.contracts.exact_fields import require_exact_fields


def load_dataset_records(authority, *, limit=None):
    """Read the records capability from verified sealed Dataset storage."""
    if limit is not None and (
        isinstance(limit, bool) or not isinstance(limit, int) or limit < 1
    ):
        raise ValueError("Dataset record limit must be a positive integer.")
    (
        _dataset_id,
        dataset_version_id,
        _storage,
        _content_hash,
        capabilities,
        semantic_validated_capabilities,
        root,
    ) = dataset_authority.verified_dataset_storage_material(authority)
    capability = capabilities.get(dataset_contracts.RECORDS_CAPABILITY)
    if (
        capability is None
        or capability["protocol"] != dataset_contracts.RECORDS_PROTOCOL
    ):
        raise ValueError(
            "Dataset Version does not expose records capability: "
            f"{dataset_version_id}"
        )
    if dataset_contracts.RECORDS_CAPABILITY not in semantic_validated_capabilities:
        raise ValueError(
            "Dataset records capability was not semantically verified for this Runtime."
        )
    descriptor = capability["descriptor"]
    event_time_field = descriptor["eventTimeField"]
    available_time_field = descriptor["availableTimeField"]
    record_fields = {"sequence", event_time_field, available_time_field, "values"}
    root = Path(root).resolve()
    record_path = (root / descriptor["path"]).resolve()
    try:
        record_path.relative_to(root)
    except ValueError as exc:
        raise ValueError(
            "Dataset records capability escapes its sealed container."
        ) from exc
    records = []
    with record_path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            try:
                item = strict_json.loads(line)
            except ValueError as exc:
                raise ValueError(
                    f"Dataset records capability line {line_number} is invalid JSON."
                ) from exc
            require_exact_fields(
                item,
                allowed=record_fields,
                required=record_fields,
                label=f"Dataset record line {line_number}",
            )
            if item["sequence"] != len(records):
                raise ValueError(
                    "Dataset record sequence must be contiguous from zero."
                )
            if (
                not isinstance(item[event_time_field], str)
                or not item[event_time_field]
            ):
                raise ValueError("Dataset record event time is invalid.")
            if (
                not isinstance(item[available_time_field], str)
                or not item[available_time_field]
            ):
                raise ValueError("Dataset record available time is invalid.")
            dataset_contracts.parse_record_instant(
                item[event_time_field], label="Dataset record event time"
            )
            dataset_contracts.parse_record_instant(
                item[available_time_field], label="Dataset record available time"
            )
            if not isinstance(item["values"], dict):
                raise ValueError("Dataset record values must be an object.")
            validate_normalized_json_value(
                item["values"], {}, path=f"Dataset.records[{len(records)}].values"
            )
            records.append(
                dataset_contracts.DatasetRecord(
                    sequence=item["sequence"],
                    event_time=item[event_time_field],
                    available_at=item[available_time_field],
                    values=item["values"],
                )
            )
            if limit is not None and len(records) >= limit:
                break
    expected_count = descriptor["recordCount"]
    if limit is None and len(records) != expected_count:
        raise ValueError(
            "Dataset records capability count does not match its descriptor."
        )
    return records


class DatasetHandle:
    """Opaque Dataset capability constructed only from verified sealed storage."""

    __slots__ = (
        "_dataset_id",
        "_dataset_version_id",
        "_storage",
        "_content_hash",
        "_capabilities",
        "_semantic_validated_capabilities",
        "_root",
        "_record_loader",
        "_records",
    )

    def __init__(self, authority, *, record_loader=None):
        (
            self._dataset_id,
            self._dataset_version_id,
            self._storage,
            self._content_hash,
            self._capabilities,
            self._semantic_validated_capabilities,
            self._root,
        ) = dataset_authority.verified_dataset_storage_material(authority)
        if record_loader is not None and not callable(record_loader):
            raise TypeError("Dataset record loader must be callable.")
        self._record_loader = record_loader
        self._records = None

    @classmethod
    def from_verified_storage(cls, authority, *, record_loader=None):
        return cls(authority, record_loader=record_loader)

    @property
    def dataset_id(self) -> str:
        return self._dataset_id

    @property
    def dataset_version_id(self) -> str:
        return self._dataset_version_id

    @property
    def content_hash(self) -> str:
        return self._content_hash

    @property
    def capabilities(self) -> Mapping[str, Any]:
        return copy.deepcopy(self._capabilities)

    @property
    def storage_path(self) -> Path:
        return self._root

    @property
    def root(self) -> Path:
        return self._root

    def require_semantically_validated_capabilities(self, capabilities) -> None:
        """Fail closed unless this scoped authority proved every capability."""

        missing = sorted(
            frozenset(capabilities) - self._semantic_validated_capabilities
        )
        if missing:
            raise ValueError(
                "Dataset capability/capabilities were not semantically verified for "
                "this Runtime: " + ", ".join(missing)
            )

    def records(self) -> Sequence[dataset_contracts.DatasetRecord]:
        if self._record_loader is None:
            raise ValueError(
                f"Dataset version '{self.dataset_version_id}' does not expose the record "
                "capability required by this Sampler. Use a Sampler which reads the "
                "Dataset container."
            )
        self.require_semantically_validated_capabilities(
            (dataset_contracts.RECORDS_CAPABILITY,)
        )
        if self._records is None:
            self._records = tuple(self._record_loader())
        return self._records

    def descriptor(self) -> Dict[str, Any]:
        return {
            "datasetId": self.dataset_id,
            "datasetVersionId": self.dataset_version_id,
            "storageType": self._storage["type"],
            "storagePath": str(self.storage_path),
            "root": str(self.root),
            "contentHash": self.content_hash,
            "capabilities": copy.deepcopy(self._capabilities),
        }


def create_dataset_handle(authority):
    """Construct a Dataset Runtime capability from nominal authority."""
    return DatasetHandle.from_verified_storage(
        authority,
        record_loader=lambda: load_dataset_records(authority),
    )
