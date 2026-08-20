"""Publisher for Basic Workflow v2 period/instrument CSV Datasets."""

from __future__ import annotations

import copy
import shutil
from collections.abc import Mapping
from pathlib import Path

from dataset_adapters.basic_workflow_conformance import (
    CAPABILITY_PROTOCOL,
    INDEX_FILE,
    require_basic_workflow_descriptor,
    validate_dataset_directory,
)
from engine.contracts import dataset as dataset_contracts
from engine.contracts import strict_json
from engine.core import resource_ids
from engine.repository import dataset_publication, dataset_staging


def _copy_csv_tree(source_root, staging_root, report):
    source_root = Path(source_root)
    files = {}
    for period, details in report["periods"].items():
        destination_period = staging_root / period
        destination_period.mkdir()
        files[period] = {}
        for instrument in details["instruments"]:
            relative = f"{period}/{instrument}.csv"
            shutil.copyfile(source_root / relative, destination_period / f"{instrument}.csv")
            files[period][instrument] = relative
    return files


def register_dataset(
    config,
    *,
    dataset_id,
    name,
    source_root,
    descriptor,
    source,
    metadata=None,
    display_time_zone="UTC",
):
    """Validate and publish one immutable Basic Workflow CSV directory."""

    descriptor = require_basic_workflow_descriptor(copy.deepcopy(descriptor))
    report = validate_dataset_directory(source_root, descriptor)
    dataset_id = resource_ids.normalize_resource_id(dataset_id)
    if type(name) is not str or not name.strip():
        raise ValueError("Basic Workflow Dataset name must be a non-empty string.")
    if not isinstance(source, Mapping) or set(source) != {"type", "details"}:
        raise ValueError("Basic Workflow Dataset source must use type/details.")
    if (
        type(source["type"]) is not str
        or not source["type"].strip()
        or source["type"].strip() != source["type"]
        or not isinstance(source["details"], Mapping)
    ):
        raise ValueError(
            "Basic Workflow Dataset source requires a non-empty type and object details."
        )
    if metadata is None:
        metadata = {}
    if not isinstance(metadata, Mapping):
        raise ValueError("Basic Workflow Dataset metadata must be an object.")
    if (
        type(display_time_zone) is not str
        or not display_time_zone.strip()
        or display_time_zone.strip() != display_time_zone
    ):
        raise ValueError("Basic Workflow display_time_zone must be a non-empty string.")

    protocol_metadata = {
        "protocolId": descriptor["protocolId"],
        "protocolVersion": descriptor["protocolVersion"],
        "protocolProfile": descriptor["profile"],
        "conformance": report,
    }
    conflicts = sorted(
        key
        for key, value in protocol_metadata.items()
        if key in metadata and metadata[key] != value
    )
    if conflicts:
        raise ValueError(
            "Basic Workflow metadata conflicts with protocol field(s): "
            + ", ".join(conflicts)
        )
    metadata = {**copy.deepcopy(dict(metadata)), **protocol_metadata}

    staging = dataset_staging.create_dataset_staging(config, dataset_id)
    try:
        files = _copy_csv_tree(source_root, staging.path, report)
        (staging.path / INDEX_FILE).write_text(
            strict_json.dumps(
                {
                    "protocolId": descriptor["protocolId"],
                    "protocolVersion": descriptor["protocolVersion"],
                    "profile": descriptor["profile"],
                    "files": files,
                },
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n",
            encoding="utf-8",
        )
        capabilities = {
            dataset_contracts.VISUALIZATION_CAPABILITY: {
                "protocol": dataset_contracts.VISUALIZATION_PROTOCOL,
                "descriptor": {"timeZone": display_time_zone},
            },
            "basicWorkflow": {
                "protocol": CAPABILITY_PROTOCOL,
                "descriptor": copy.deepcopy(descriptor),
            },
        }
        return dataset_publication.publish_dataset_version(
            config,
            dataset={
                "datasetId": dataset_id,
                "name": name.strip(),
                "source": {
                    "type": source["type"],
                    "details": copy.deepcopy(dict(source["details"])),
                },
                "metadata": metadata,
            },
            staging=staging,
            capabilities=capabilities,
            version_source={
                "type": "basic-workflow-csv-directory",
                "details": {
                    "protocolId": descriptor["protocolId"],
                    "protocolVersion": descriptor["protocolVersion"],
                    "profile": descriptor["profile"],
                },
            },
        )
    finally:
        dataset_staging.discard_dataset_staging(staging)


__all__ = ("CAPABILITY_PROTOCOL", "register_dataset")
