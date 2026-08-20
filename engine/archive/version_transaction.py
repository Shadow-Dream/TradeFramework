#!/usr/bin/env python3
"""Draft-to-Archived orchestration for immutable versioned resources."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from engine.archive.version import (
    ARCHIVED_STATUS,
    COMMON_RECORD_FIELDS,
    MANIFEST_NAME,
    RECORD_NAME,
    canonical_json,
    content_digest,
    discard_archive,
    next_version,
    resolve_managed_path,
    seal_directory,
    staging_directory,
    unchanged_version,
    verify_archive,
    verify_record,
    verify_record_collection,
    verify_record_location,
    write_record_snapshot,
)
from engine.archive.version_evidence import (
    verified_record_collection_from_index_evidence,
)
from engine.contracts import strict_json
from engine.core import clock as engine_clock


def _attach_cleanup_error(primary, cleanup):
    """Retain cleanup evidence without replacing the transaction's first error."""
    cleanup.__context__ = None
    if primary.__context__ is None:
        primary.__context__ = cleanup
    elif primary.__cause__ is None:
        primary.__cause__ = cleanup
    else:
        primary.add_note(f"Additional cleanup failure: {cleanup!r}")


def _discard_after_failure(root, primary):
    try:
        if Path(root).exists():
            discard_archive(root)
    except BaseException as cleanup_error:
        _attach_cleanup_error(primary, cleanup_error)


def archive_if_changed(
    *,
    records,
    identity_key,
    identity,
    resource_type,
    resource_id,
    managed_root,
    destination_for_version,
    prepare_staging,
    create_record,
    record_fields,
    write_record,
    commit_record,
    read_committed_record,
    immutable_fields,
    verified_records_evidence=None,
):
    """Run the one Draft-to-Archived transaction used by versioned resources.

    Type-specific callers validate and serialize their content through callbacks;
    version allocation, deduplication, sealing, verification and rollback live here.
    """
    if not isinstance(identity_key, str) or not identity_key:
        raise ValueError("Archived resource identity_key must be a non-empty string.")
    if not isinstance(identity, str) or not identity:
        raise ValueError("Archived resource identity must be a non-empty string.")
    if not isinstance(resource_type, str) or not resource_type:
        raise ValueError("Archived resource type must be a non-empty string.")
    if not isinstance(resource_id, str) or not resource_id:
        raise ValueError("Archived resource ID must be a non-empty string.")
    immutable_fields = tuple(immutable_fields)
    records_are_verified = verified_records_evidence is not None
    if records_are_verified:
        records = verified_record_collection_from_index_evidence(
            verified_records_evidence,
            records,
            (identity_key,),
            immutable_fields=immutable_fields,
            managed_root=managed_root,
            expected_root_for=lambda record: destination_for_version(
                record["version"]
            ),
            resource_type=resource_type,
            resource_id=resource_id,
        )
    else:
        records = verify_record_collection(
            records,
            (identity_key,),
            immutable_fields=immutable_fields,
        )
    expected_record_fields = set(record_fields) | set(COMMON_RECORD_FIELDS)
    missing_immutable_fields = sorted(set(immutable_fields) - expected_record_fields)
    if missing_immutable_fields:
        raise ValueError(
            "Archived resource immutable field(s) are outside its record contract: "
            + ", ".join(missing_immutable_fields)
        )

    def commit_or_reconcile(
        record,
        context,
        record_destination,
        *,
        discard_if_uncommitted,
    ):
        try:
            commit_record(record, context)
            return record
        except BaseException as commit_error:
            commit_traceback = commit_error.__traceback__
            try:
                committed = read_committed_record(record, context)
            except BaseException as read_error:
                # The authoritative index cannot be inspected, so deletion is
                # unsafe: it could already reference this immutable archive.
                _attach_cleanup_error(commit_error, read_error)
                raise commit_error.with_traceback(commit_traceback)
            if committed is not None:
                if not isinstance(committed, dict):
                    raise ValueError(
                        "Archived resource committed-record reader returned an invalid value."
                    ) from commit_error
                verify_record(committed)
                if canonical_json(committed) != canonical_json(record):
                    raise ValueError(
                        "Archived resource index contains a different committed record."
                    ) from commit_error
                return committed
            if discard_if_uncommitted:
                _discard_after_failure(record_destination, commit_error)
            raise commit_error.with_traceback(commit_traceback)

    def load_unindexed_record(record_destination, version):
        sealed = verify_archive(record_destination, {
            "resourceType": resource_type,
            "resourceId": resource_id,
            "version": version,
        })
        archived_record_path = record_destination / RECORD_NAME
        try:
            recovered_record = strict_json.loads(
                archived_record_path.read_text(encoding="utf-8")
            )
        except (OSError, ValueError) as exc:
            raise ValueError(
                f"Unindexed archive has no valid immutable record: {record_destination}"
            ) from exc
        if not isinstance(recovered_record, dict):
            raise ValueError(
                f"Unindexed archive record is invalid: {record_destination}"
            )
        recovered_archive = recovered_record.get("archive")
        expected_archive = {
            "resourceType": resource_type,
            "resourceId": resource_id,
            "root": str(record_destination),
        }
        if recovered_archive != expected_archive:
            raise ValueError(
                f"Unindexed archive descriptor is invalid: {record_destination}"
            )
        recovered_record["archive"] = {
            **recovered_archive,
            "manifestDigest": sealed["manifestDigest"],
        }
        if (
            set(recovered_record) != expected_record_fields
            or recovered_record.get(identity_key) != identity
            or recovered_record.get("version") != version
            or recovered_record.get("status") != ARCHIVED_STATUS
            or recovered_record.get("contentDigest") != sealed["contentDigest"]
        ):
            raise ValueError(
                f"Unindexed archive record does not match its repository contract: "
                f"{record_destination}"
            )
        verify_record_location(
            recovered_record,
            managed_root=managed_root,
            expected_root=record_destination,
        )
        return recovered_record

    # A process can terminate after the immutable rename but before its index
    # commit.  Recover every canonical next version from its self-verifying
    # archive before evaluating a new Draft, so a different retry cannot be
    # permanently blocked by the previous publication.
    while True:
        recovered_version = next_version(
            records,
            identity_key=identity_key,
            identity=identity,
        )
        recovered_destination = resolve_managed_path(
            managed_root,
            Path(destination_for_version(recovered_version)),
            label="Archived version destination",
        )
        if not recovered_destination.exists():
            break
        recovered_record = load_unindexed_record(
            recovered_destination,
            recovered_version,
        )
        recovered_history = verify_record_collection(
            [*records, recovered_record],
            (identity_key,),
            immutable_fields=immutable_fields,
        )
        recovered_record = commit_or_reconcile(
            recovered_record,
            None,
            recovered_destination,
            discard_if_uncommitted=False,
        )
        records = recovered_history

    version = next_version(records, identity_key=identity_key, identity=identity)
    raw_destination = Path(destination_for_version(version))
    destination = resolve_managed_path(
        managed_root,
        raw_destination,
        label="Archived version destination",
    )
    staging = staging_directory(destination.parent)
    context = None
    try:
        semantic_content, context = prepare_staging(staging, version, destination)
        for reserved_name in (MANIFEST_NAME, RECORD_NAME):
            if (staging / reserved_name).exists():
                raise ValueError(
                    f"Archived resource implementation may not create reserved file {reserved_name}."
                )
        digest = content_digest(semantic_content)
        unchanged = unchanged_version(
            records,
            digest,
            identity_key=identity_key,
            identity=identity,
        )
        if unchanged:
            if not records_are_verified:
                verify_record(unchanged)
            discard_archive(staging)
            return {
                "record": unchanged,
                "unchanged": True,
                "context": context,
            }
        created_record = create_record(version, context)
        if not isinstance(created_record, Mapping):
            raise ValueError("Archived resource create_record must return an object.")
        record = dict(created_record)
        record.update({
            "version": version,
            "status": ARCHIVED_STATUS,
            "contentDigest": digest,
            "createdAt": engine_clock.utc_now(),
            "archive": {
                "resourceType": resource_type,
                "resourceId": resource_id,
                "root": str(destination),
            },
        })
        if set(record) != expected_record_fields:
            unknown = sorted(set(record) - expected_record_fields)
            missing = sorted(expected_record_fields - set(record))
            details = []
            if unknown:
                details.append("unsupported field(s): " + ", ".join(unknown))
            if missing:
                details.append("missing field(s): " + ", ".join(missing))
            raise ValueError("Archived resource record schema is invalid: " + "; ".join(details))
        if record.get(identity_key) != identity:
            raise ValueError(
                f"Archived resource record {identity_key} does not match identity '{identity}'."
            )
        identity_records = [
            existing for existing in records
            if existing[identity_key] == identity
        ]
        for field in immutable_fields:
            if identity_records and any(
                existing[field] != record[field] for existing in identity_records
            ):
                raise ValueError(
                    f"Archived resource identity '{identity}' changes immutable field "
                    f"'{field}'."
                )
        if destination.exists():
            recovered_record = load_unindexed_record(destination, version)
            if (
                recovered_record.get("contentDigest") != digest
                or any(
                    recovered_record.get(field) != value
                    for field, value in created_record.items()
                )
            ):
                raise ValueError(
                    f"Unindexed archive does not match the requested publication: {destination}"
                )
            recovered_record = commit_or_reconcile(
                recovered_record,
                context,
                destination,
                discard_if_uncommitted=False,
            )
            discard_archive(staging)
            return {
                "record": recovered_record,
                "unchanged": False,
                "context": context,
            }
        write_record(staging, record, context)
        for reserved_name in (MANIFEST_NAME, RECORD_NAME):
            if (staging / reserved_name).exists():
                raise ValueError(
                    f"Archived resource writer may not create reserved file {reserved_name}."
                )
        # The common archive owns the authoritative state-record snapshot.  A
        # type-specific index (SQLite/JSON) is only an index and may never
        # silently redefine an Archived version after publication.
        write_record_snapshot(staging, record)
        sealed = seal_directory(
            staging,
            destination,
            managed_root=managed_root,
            resource_type=resource_type,
            resource_id=resource_id,
            version=version,
            digest=digest,
            record_fields=expected_record_fields,
        )
        record["archive"]["manifestDigest"] = sealed["manifestDigest"]
        verify_record(record)
        record = commit_or_reconcile(
            record,
            context,
            destination,
            discard_if_uncommitted=True,
        )
        return {
            "record": record,
            "unchanged": False,
            "context": context,
        }
    except BaseException as primary_error:
        primary_traceback = primary_error.__traceback__
        _discard_after_failure(staging, primary_error)
        raise primary_error.with_traceback(primary_traceback)
