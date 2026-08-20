#!/usr/bin/env python3
"""Immutable Sampler definition index and archive transactions."""

from __future__ import annotations

import shutil
import logging
from pathlib import Path

from engine.archive import sampler as sampler_archive
from engine.archive import version as version_archive
from engine.archive import version_transaction
from engine.contracts import sampler as sampler_contracts
from engine.contracts import strict_json
from engine.contracts.archive import require_resource_path_segment
from engine.contracts.data_model import (
    normalize_data_key_schema,
    normalize_schema,
    schema_types,
)
from engine.contracts.exact_fields import require_exact_fields
from engine.control import database as engine_database
from engine.core import resource_ids
from engine.repository import control_state


LOGGER = logging.getLogger(__name__)


def _first_error(current, candidate):
    return current if current is not None else candidate


def _attempt_connection_cleanup(connection, *, rollback):
    first_error = None
    if rollback:
        try:
            connection.rollback()
        except BaseException as error:
            first_error = _first_error(first_error, error)
    try:
        connection.close()
    except BaseException as error:
        first_error = _first_error(first_error, error)
    return first_error


def _attach_secondary_error(primary, secondary):
    if secondary is not None and primary.__context__ is None:
        primary.__context__ = secondary


def _sampler_from_row(config, row):
    if row["builtin"] not in (0, 1):
        raise ValueError("Sampler database builtin ownership flag is invalid.")
    sampler_id = require_resource_path_segment(row["sampler_id"], label="Sampler ID")
    if resource_ids.normalize_resource_id(sampler_id) != sampler_id:
        raise ValueError("Sampler database identity is not canonical.")
    version = require_resource_path_segment(row["version"], label="Sampler version")
    if not version.isdigit() or version != str(int(version)) or int(version) < 1:
        raise ValueError("Sampler database version is not a canonical positive integer.")
    expected_root = version_archive.resolve_managed_path(
        config["releaseRoot"],
        (
            Path(config["releaseRoot"])
            / "_samplers"
            / sampler_id
            / version
        ),
        label="Expected archived Sampler location",
    )
    if row["archive_root"] != str(expected_root):
        raise ValueError(
            "Sampler archive location is not its exact canonical repository path."
        )
    archive_root = Path(row["archive_root"])
    archived_record_path = archive_root / version_archive.RECORD_NAME
    if archived_record_path.is_symlink() or not archived_record_path.is_file():
        raise ValueError(
            f"Sampler archive is missing {version_archive.RECORD_NAME}: "
            f"{row['sampler_id']}@{row['version']}"
        )
    archived = strict_json.loads(archived_record_path.read_text(encoding="utf-8"))
    require_exact_fields(
        archived,
        allowed=sampler_contracts.SAMPLER_VERSION_FIELDS,
        required=sampler_contracts.SAMPLER_VERSION_FIELDS,
        label=f"Sampler Version {row['sampler_id']}@{row['version']}",
    )
    expected = {
        "samplerId": row["sampler_id"],
        "version": row["version"],
        "name": row["name"],
        "type": row["sampler_type"],
        "builtin": bool(row["builtin"]),
        "config": strict_json.loads(row["config_json"]),
        "parameterSchema": strict_json.loads(row["parameter_schema_json"]),
        "outputSchema": strict_json.loads(row["output_schema_json"]),
        "source": row["source_text"],
        "entryPoint": row["entry_point"],
        "status": row["status"],
        "contentDigest": row["content_digest"],
        "createdAt": row["created_at"],
    }
    if any(archived.get(field) != value for field, value in expected.items()):
        raise ValueError(
            "Sampler database index does not match its archive: "
            f"{row['sampler_id']}@{row['version']}"
        )
    archive = archived.get("archive")
    if not isinstance(archive, dict) or archive != {
        "resourceType": "sampler",
        "resourceId": row["sampler_id"],
        "root": row["archive_root"],
    }:
        raise ValueError(
            "Sampler database archive location does not match its record: "
            f"{row['sampler_id']}@{row['version']}"
        )
    archived["archive"] = {
        **archive,
        "manifestDigest": row["archive_manifest_digest"],
    }
    version_archive.verify_record(archived)
    return archived


def _verify_records(config, rows):
    definitions = [_sampler_from_row(config, row) for row in rows]
    version_archive.verify_record_collection(
        definitions,
        ("samplerId",),
        immutable_fields=("builtin",),
    )
    for definition in definitions:
        version_archive.verify_record_location(
            definition,
            managed_root=config["releaseRoot"],
            expected_root=(
                Path(config["releaseRoot"])
                / "_samplers"
                / definition["samplerId"]
                / definition["version"]
            ),
        )
    return definitions


def _list_samplers_locked(config):
    with engine_database.connect_database(config) as connection:
        rows = connection.execute(
            "SELECT * FROM sampler_definitions ORDER BY sampler_id, "
            "CAST(version AS INTEGER)"
        ).fetchall()
    return _verify_records(config, rows)


def list_samplers(config):
    """Return one verified snapshot of every immutable Sampler version."""

    with control_state.control_state_lock(config):
        return _list_samplers_locked(config)


def _get_sampler_locked(config, sampler_id, version=""):
    sampler_id = str(sampler_id or "").strip()
    if not sampler_id:
        raise ValueError("Sampler ID is required.")
    version = str(version or "")
    with engine_database.connect_database(config) as connection:
        rows = connection.execute(
            "SELECT * FROM sampler_definitions WHERE sampler_id = ? "
            "ORDER BY CAST(version AS INTEGER)",
            (sampler_id,),
        ).fetchall()
    definitions = _verify_records(config, rows)
    matches = [
        definition
        for definition in definitions
        if not version or str(definition["version"]) == version
    ]
    if not matches:
        raise ValueError(f"Unknown Sampler: {sampler_id}@{version or 'latest'}")
    return max(matches, key=lambda definition: int(definition["version"]))


def get_sampler(config, sampler_id, version=""):
    """Return a verified exact or latest Sampler version."""

    with control_state.control_state_lock(config):
        return _get_sampler_locked(config, sampler_id, version)


def get_sampler_execution_version(config, sampler_id, version):
    """Load one exact immutable Sampler version for a frozen composition."""

    sampler_id = str(sampler_id or "").strip()
    version = str(version or "").strip()
    if not sampler_id or not version:
        raise ValueError("Sampler ID and version are required.")
    with control_state.control_state_lock(config):
        with engine_database.connect_database(config) as connection:
            rows = connection.execute(
                "SELECT * FROM sampler_definitions "
                "WHERE sampler_id = ? AND version = ?",
                (sampler_id, version),
            ).fetchall()
        if len(rows) != 1:
            raise ValueError(f"Unknown Sampler: {sampler_id}@{version}")
        definition = _sampler_from_row(config, rows[0])
        actual_root = version_archive.resolve_managed_path(
            config["releaseRoot"],
            definition["archive"]["root"],
            label="Archived Sampler location",
        )
        expected_root = version_archive.resolve_managed_path(
            config["releaseRoot"],
            Path(config["releaseRoot"]) / "_samplers" / sampler_id / version,
            label="Expected archived Sampler location",
        )
        if actual_root != expected_root:
            raise ValueError(
                "Sampler Version is outside its canonical repository destination."
            )
        return definition


def _save_sampler_locked(config, request, *, engine_owned):
    if not isinstance(engine_owned, bool):
        raise ValueError("Sampler engine_owned must be a boolean.")
    require_exact_fields(
        request,
        allowed=sampler_contracts.SAMPLER_DRAFT_FIELDS,
        required={
            "name",
            "type",
            "config",
            "parameterSchema",
            "outputSchema",
            "source",
            "entryPoint",
        },
        label="Sampler Draft",
    )
    if "samplerId" in request and not isinstance(request["samplerId"], str):
        raise ValueError("Sampler samplerId must be a string.")
    if not isinstance(request["name"], str) or not request["name"].strip():
        raise ValueError("Sampler name must be a non-empty string.")
    if not isinstance(request["type"], str):
        raise ValueError("Sampler type must be a string.")
    if not isinstance(request["source"], str):
        raise ValueError("Sampler source must be a string.")
    if not isinstance(request["entryPoint"], str):
        raise ValueError("Sampler entryPoint must be a string.")
    requested_id = (request.get("samplerId") or "").strip()
    sampler_id = (
        resource_ids.normalize_resource_id(requested_id)
        if requested_id
        else resource_ids.new_resource_id("sampler")
    )
    sampler_type = request["type"].strip()
    if not sampler_type:
        raise ValueError("Sampler type is required.")
    if sampler_type != request["type"]:
        raise ValueError("Sampler type must be a canonical string.")
    config_payload = request["config"]
    if not isinstance(config_payload, dict):
        raise ValueError("Sampler config must be a JSON object.")
    parameter_schema = normalize_schema(request["parameterSchema"])
    if schema_types(parameter_schema) != {"object"}:
        raise ValueError("Sampler parameterSchema must describe an object.")
    output_schema = request["outputSchema"]
    if not isinstance(output_schema, dict):
        raise ValueError(
            "Sampler outputSchema must be an object typing every emitted DataKey."
        )
    if any(not isinstance(path, str) or not path for path in output_schema):
        raise ValueError("Sampler outputSchema contains an invalid DataKey.")
    output_schema = {
        path: normalize_data_key_schema(schema, path=path)
        for path, schema in output_schema.items()
    }
    source_text = request["source"]
    entry_point = request["entryPoint"]
    sampler_contracts.validate_sampler_draft_implementation(
        sampler_type,
        config_payload,
        output_schema,
        source_text,
        entry_point,
    )
    runtime, runtime_sources = sampler_archive.sampler_runtime_bundle(sampler_type)
    draft = {
        "samplerId": sampler_id,
        "name": request["name"] or sampler_id,
        "type": sampler_type,
        "config": config_payload,
        "parameterSchema": parameter_schema,
        "outputSchema": output_schema,
        "source": source_text,
        "entryPoint": entry_point,
        "runtime": runtime,
        "builtin": engine_owned,
    }
    with engine_database.connect_database(config) as connection:
        existing_rows = connection.execute(
            "SELECT * FROM sampler_definitions WHERE sampler_id = ?",
            (sampler_id,),
        ).fetchall()
    records = _verify_records(config, existing_rows)
    if records and records[0]["builtin"] != engine_owned:
        raise ValueError(f"Sampler identity ownership cannot change: {sampler_id}")

    def destination_for_version(version):
        return Path(config["releaseRoot"]) / "_samplers" / sampler_id / version

    def prepare_staging(staging, _version, _destination):
        if source_text:
            (staging / "sampler.py").write_text(source_text, encoding="utf-8")
        runtime_root = staging / "runtime"
        runtime_root.mkdir()
        for name, source_path in runtime_sources.items():
            shutil.copy2(source_path, runtime_root / name)
        return draft, None

    def create_record(_version, _context):
        return draft

    def write_record(staging, record, _context):
        (staging / "sampler.json").write_text(
            strict_json.dumps(record, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    def commit_record(record, _context):
        connection = engine_database.connect_database(config)
        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT INTO sampler_definitions
                (sampler_id, version, name, sampler_type, config_json,
                 parameter_schema_json, output_schema_json, source_text,
                 entry_point, status, content_digest, archive_root,
                 archive_manifest_digest, builtin, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'archived', ?, ?, ?, ?, ?)
                """,
                (
                    record["samplerId"],
                    record["version"],
                    record["name"],
                    record["type"],
                    strict_json.dumps(record["config"], sort_keys=True),
                    strict_json.dumps(record["parameterSchema"], sort_keys=True),
                    strict_json.dumps(record["outputSchema"], sort_keys=True),
                    record["source"],
                    record["entryPoint"],
                    record["contentDigest"],
                    record["archive"]["root"],
                    record["archive"]["manifestDigest"],
                    1 if record["builtin"] else 0,
                    record["createdAt"],
                ),
            )
            connection.commit()
        except BaseException as error:
            traceback = error.__traceback__
            cleanup_error = _attempt_connection_cleanup(connection, rollback=True)
            _attach_secondary_error(error, cleanup_error)
            raise error.with_traceback(traceback)
        cleanup_error = _attempt_connection_cleanup(connection, rollback=False)
        if cleanup_error is not None:
            raise cleanup_error

    def read_committed_record(record, _context):
        connection = engine_database.connect_database(config)
        try:
            row = connection.execute(
                "SELECT * FROM sampler_definitions "
                "WHERE sampler_id = ? AND version = ?",
                (record["samplerId"], record["version"]),
            ).fetchone()
        except BaseException as error:
            traceback = error.__traceback__
            cleanup_error = _attempt_connection_cleanup(connection, rollback=False)
            _attach_secondary_error(error, cleanup_error)
            raise error.with_traceback(traceback)
        cleanup_error = _attempt_connection_cleanup(connection, rollback=False)
        if cleanup_error is not None:
            LOGGER.warning(
                "Sampler commit evidence was read but connection cleanup failed.",
                exc_info=(
                    type(cleanup_error),
                    cleanup_error,
                    cleanup_error.__traceback__,
                ),
            )
        return _sampler_from_row(config, row) if row is not None else None

    result = version_transaction.archive_if_changed(
        records=records,
        identity_key="samplerId",
        identity=sampler_id,
        resource_type="sampler",
        resource_id=sampler_id,
        managed_root=config["releaseRoot"],
        destination_for_version=destination_for_version,
        prepare_staging=prepare_staging,
        create_record=create_record,
        record_fields=sampler_contracts.SAMPLER_VERSION_FIELDS,
        write_record=write_record,
        commit_record=commit_record,
        read_committed_record=read_committed_record,
        immutable_fields={"builtin"},
    )
    return result["record"]


def save_sampler(config, request, *, engine_owned=False):
    """Validate, archive, and index a Sampler under one repository lock."""

    with control_state.control_state_lock(config):
        return _save_sampler_locked(config, request, engine_owned=engine_owned)


__all__ = (
    "get_sampler",
    "get_sampler_execution_version",
    "list_samplers",
    "save_sampler",
)
