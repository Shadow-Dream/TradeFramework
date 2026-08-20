"""Immutable repository for versioned Analysis and Environment Graphs."""

from __future__ import annotations

import copy
from pathlib import Path

from engine.archive import version as version_archive
from engine.archive import version_evidence
from engine.archive import version_transaction
from engine.contracts.archive import require_resource_path_segment
from engine.contracts.graph_resource import graph_resource_draft_fields
from engine.repository import control_state


GRAPH_RESOURCE_SPECS = {
    "analysis": {
        "idField": "analysisId",
        "stateFile": "analyses.json",
        "releaseDir": "_analyses",
        "definitionFile": "analysis.json",
        "draftFields": graph_resource_draft_fields("analysisId"),
    },
    "environment": {
        "idField": "environmentId",
        "stateFile": "environments.json",
        "releaseDir": "_environments",
        "definitionFile": "environment.json",
        "draftFields": graph_resource_draft_fields("environmentId"),
    },
}


def verify_repository(config, resource_type, records):
    if resource_type not in GRAPH_RESOURCE_SPECS:
        raise ValueError(f"Unknown Graph resource type: {resource_type}")
    spec = GRAPH_RESOURCE_SPECS[resource_type]
    def expected_root(record):
        identity = require_resource_path_segment(
            record.get(spec["idField"]),
            label=spec["idField"],
        )
        version = require_resource_path_segment(
            record.get("version"),
            label=f"{resource_type} version",
        )
        return (
            Path(config["releaseRoot"])
            / spec["releaseDir"]
            / identity
            / version
        )

    return version_evidence.verify_record_index_locations(
        records,
        (spec["idField"],),
        managed_root=config["releaseRoot"],
        expected_root_for=expected_root,
        immutable_fields=("builtin",),
    )


def load_repository(config, resource_type):
    spec = GRAPH_RESOURCE_SPECS.get(resource_type)
    if spec is None:
        raise ValueError(f"Unknown Graph resource type: {resource_type}")
    records = control_state.load_state(config, spec["stateFile"], {})
    return verify_repository(config, resource_type, records)


def load_version(config, resource_type, identity, version):
    """Load and verify one exact immutable Graph resource version."""

    spec = GRAPH_RESOURCE_SPECS.get(resource_type)
    if spec is None:
        raise ValueError(f"Unknown Graph resource type: {resource_type}")
    identity = require_resource_path_segment(
        identity,
        label=spec["idField"],
    )
    version = require_resource_path_segment(
        version,
        label=f"{resource_type} version",
    )
    records = control_state.load_state(config, spec["stateFile"], {})
    if not isinstance(records, dict):
        raise ValueError(f"{resource_type.title()} version index must be an object.")
    key = f"{identity}/{version}"
    record = records.get(key)
    if not isinstance(record, dict):
        raise ValueError(f"Unknown {resource_type.title()}: {identity}@{version}")
    if (
        record.get(spec["idField"]) != identity
        or record.get("version") != version
    ):
        raise ValueError(
            f"{resource_type.title()} version index key does not match its record."
        )
    version_archive.verify_record_location(
        record,
        managed_root=config["releaseRoot"],
        expected_root=(
            Path(config["releaseRoot"])
            / spec["releaseDir"]
            / identity
            / version
        ),
    )
    return copy.deepcopy(record)


def _archive_if_changed_locked(
    config,
    resource_type,
    payload,
    *,
    module_definitions,
    validate,
    engine_owned=False,
):
    if resource_type not in GRAPH_RESOURCE_SPECS:
        raise ValueError(f"Unknown Graph resource type: {resource_type}")
    if not isinstance(payload, dict):
        raise ValueError(f"{resource_type.title()} Draft must be an object.")
    forbidden = sorted(set(payload) & {
        "version",
        "status",
        "archive",
        "contentDigest",
        "createdAt",
        "builtin",
    })
    if forbidden:
        raise ValueError(
            f"{resource_type.title()} version fields are Engine-owned: "
            + ", ".join(forbidden)
        )
    spec = GRAPH_RESOURCE_SPECS[resource_type]
    unknown = sorted(set(payload) - spec["draftFields"])
    if unknown:
        raise ValueError(
            f"{resource_type.title()} Draft contains unsupported field(s): "
            + ", ".join(unknown)
        )
    identity_field = spec["idField"]
    if identity_field not in payload:
        raise ValueError(f"{identity_field} is required.")
    identity = require_resource_path_segment(
        payload[identity_field],
        label=identity_field,
    )
    records_by_key = control_state.load_state(config, spec["stateFile"], {})
    verify_repository(config, resource_type, records_by_key)
    records = [
        record
        for record in records_by_key.values()
        if record[spec["idField"]] == identity
    ]
    if not isinstance(engine_owned, bool):
        raise ValueError("Graph resource engine_owned must be a boolean.")
    if records and records[0]["builtin"] != engine_owned:
        raise ValueError(
            f"{resource_type.title()} identity ownership cannot change: {identity}"
        )

    def destination_for_version(version):
        return (
            Path(config["releaseRoot"])
            / spec["releaseDir"]
            / identity
            / version
        )

    def prepare_staging(_staging, version, _destination):
        candidate = {
            **copy.deepcopy(payload),
            "version": version,
            "builtin": engine_owned,
        }
        definition = validate(candidate, module_definitions)
        semantic_content = {
            key: value
            for key, value in definition.items()
            if key not in {
                "version",
                "status",
                "archive",
                "contentDigest",
                "createdAt",
            }
        }
        return semantic_content, definition

    def create_record(_version, definition):
        return definition

    def write_record(staging, record, _definition):
        control_state.atomic_write_json(
            staging / spec["definitionFile"],
            record,
        )

    def commit_record(record, _definition):
        key = f"{identity}/{record['version']}"
        records_by_key[key] = record
        control_state.save_state(config, spec["stateFile"], records_by_key)

    def read_committed_record(record, _definition):
        key = f"{identity}/{record['version']}"
        return control_state.load_state(config, spec["stateFile"], {}).get(key)

    result = version_transaction.archive_if_changed(
        records=records,
        identity_key=spec["idField"],
        identity=identity,
        resource_type=resource_type,
        resource_id=identity,
        managed_root=config["releaseRoot"],
        destination_for_version=destination_for_version,
        prepare_staging=prepare_staging,
        create_record=create_record,
        record_fields=(
            set(spec["draftFields"])
            | {
                "version",
                "builtin",
                "status",
                "contentDigest",
                "createdAt",
                "archive",
            }
        ),
        write_record=write_record,
        commit_record=commit_record,
        read_committed_record=read_committed_record,
        immutable_fields={"builtin"},
    )
    definition = result["record"]
    key = f"{identity}/{definition['version']}"
    if result["unchanged"]:
        return {
            "accepted": True,
            "unchanged": True,
            "resourceType": resource_type,
            "resourceKey": key,
            "definition": definition,
        }
    control_state.append_history_event(
        config,
        f"{resource_type}.archived",
        {
            "resourceType": resource_type,
            "resourceKey": key,
            "definition": definition,
        },
    )
    return {
        "accepted": True,
        "unchanged": result["unchanged"],
        "resourceType": resource_type,
        "resourceKey": key,
        "definition": definition,
    }


def archive_if_changed(
    config,
    resource_type,
    payload,
    *,
    module_definitions,
    validate,
    engine_owned=False,
):
    """Archive one Graph Draft under the repository's complete state transaction."""

    with control_state.control_state_lock(config):
        return _archive_if_changed_locked(
            config,
            resource_type,
            payload,
            module_definitions=module_definitions,
            validate=validate,
            engine_owned=engine_owned,
        )


def require_archived(definition):
    version_archive.verify_record(definition)
    return definition
