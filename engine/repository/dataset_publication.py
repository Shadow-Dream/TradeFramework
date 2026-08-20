#!/usr/bin/env python3
"""Atomic immutable Dataset Version publication and recovery."""

from __future__ import annotations

import copy
import secrets
import threading
from contextlib import contextmanager

from engine.archive import dataset as dataset_archive
from engine.archive import version as version_archive
from engine.contracts import dataset as dataset_contracts
from engine.contracts import strict_json
from engine.contracts.data_model import validate_normalized_json_value
from engine.control import database as engine_database
from engine.core import clock as engine_clock
from engine.repository import dataset_staging
from engine.repository import datasets


_PENDING_STAGING_CLEANUP_LOCK = threading.RLock()
_PENDING_STAGING_CLEANUP = []


def _register_pending_staging_cleanup(staging):
    with _PENDING_STAGING_CLEANUP_LOCK:
        if any(item is staging for item in _PENDING_STAGING_CLEANUP):
            return
        _PENDING_STAGING_CLEANUP.append(staging)


def retry_pending_dataset_publication_cleanup():
    """Retry exact Dataset staging authorities retained after commit."""

    first_error = None
    with _PENDING_STAGING_CLEANUP_LOCK:
        pending = list(_PENDING_STAGING_CLEANUP)
    for staging in pending:
        try:
            dataset_staging.discard_dataset_staging(staging)
        except BaseException as error:
            first_error = _first_error(first_error, error)
            continue
        with _PENDING_STAGING_CLEANUP_LOCK:
            _PENDING_STAGING_CLEANUP[:] = [
                item for item in _PENDING_STAGING_CLEANUP if item is not staging
            ]
    if first_error is not None:
        raise first_error


def _discard_dataset_staging(staging, *, retain_on_error):
    try:
        dataset_staging.discard_dataset_staging(staging)
    except BaseException:
        if retain_on_error:
            _register_pending_staging_cleanup(staging)
        raise
    with _PENDING_STAGING_CLEANUP_LOCK:
        _PENDING_STAGING_CLEANUP[:] = [
            item for item in _PENDING_STAGING_CLEANUP if item is not staging
        ]


def _require_authoritative_dataset_lineage(config, lineage):
    authoritative = []
    for index, source in enumerate(lineage):
        upstream = datasets.verify_dataset_version_id(
            config, source["datasetVersionId"]
        )
        expected = {
            "alias": source["alias"],
            "datasetId": upstream["datasetId"],
            "datasetVersionId": upstream["datasetVersionId"],
            "contentHash": upstream["contentHash"],
        }
        if source != expected:
            raise ValueError(
                f"Dataset lineage[{index}] does not match its indexed upstream Version."
            )
        authoritative.append(expected)
    return authoritative


def _require_active_dataset_lineage(connection, lineage):
    """Linearize publication against upstream Dataset archival."""
    for index, source in enumerate(lineage):
        row = connection.execute(
            """
            SELECT d.status, v.dataset_id, v.content_hash, v.status AS version_status
            FROM dataset_versions v
            JOIN datasets d ON d.dataset_id = v.dataset_id
            WHERE v.version_id = ?
            """,
            (source["datasetVersionId"],),
        ).fetchone()
        if (
            row is None
            or row["dataset_id"] != source["datasetId"]
            or row["content_hash"] != source["contentHash"]
            or row["version_status"] != "sealed"
        ):
            raise ValueError(f"Dataset lineage[{index}] changed before publication.")
        if row["status"] != "active":
            raise ValueError(
                f"Upstream Dataset '{source['datasetId']}' is archived and "
                "cannot publish a new downstream Dataset."
            )


def _require_dataset_publication_target(
    connection,
    dataset,
    *,
    append,
    defer_exact_retry=False,
    allow_exact_retry=False,
):
    dataset_id = dataset["datasetId"]
    existing_row = connection.execute(
        "SELECT * FROM datasets WHERE dataset_id = ?", (dataset_id,)
    ).fetchone()
    if not existing_row:
        if append:
            raise ValueError(f"Unknown Dataset: {dataset_id}")
        return
    if append or defer_exact_retry or allow_exact_retry:
        existing = datasets.decode_dataset_index_row(
            {**dict(existing_row), "latest_version_id": None}
        )
        for field in ("name", "source", "metadata"):
            if existing[field] != dataset[field]:
                if not append:
                    raise ValueError(
                        f"Dataset '{dataset_id}' already exists; publish with a new "
                        "Dataset ID."
                    )
                raise ValueError(
                    f"Dataset Version publication may not redefine Dataset {field}."
                )
        if existing["status"] != "active":
            if not append:
                raise ValueError(
                    f"Dataset '{dataset_id}' already exists; publish with a new "
                    "Dataset ID."
                )
            raise ValueError(
                f"Archived Dataset '{dataset_id}' cannot receive a new Version."
            )
        if append or defer_exact_retry or allow_exact_retry:
            return
    if existing_row:
        raise ValueError(
            f"Dataset '{dataset_id}' already exists; publish with a new Dataset ID."
        )


def _dataset_publication_commit_evidence(
    config,
    *,
    operation_nonce,
    dataset_id,
    version_id,
    manifest_digest,
    build_job_id,
):
    """Return (committed, error) from this transaction's immutable receipt."""
    try:
        with engine_database.connect_database(config) as evidence_connection:
            row = evidence_connection.execute(
                """
                SELECT dataset_id, version_id, manifest_digest, build_job_id
                FROM dataset_publication_receipts WHERE operation_nonce = ?
                """,
                (operation_nonce,),
            ).fetchone()
    except BaseException as error:
        return None, error
    if row is None:
        return False, None
    return tuple(row) == (
        dataset_id,
        version_id,
        manifest_digest,
        build_job_id,
    ), None


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


@contextmanager
def dataset_publication_transaction(
    config,
    *,
    dataset,
    staging,
    capabilities,
    version_source,
    lineage=None,
    build=None,
    build_job_id="",
    append=False,
):
    """Seal one generic Dataset and expose its single atomic index transaction."""
    dataset_staging.require_dataset_staging_authority(staging)
    staging_authority = staging
    dataset = dataset_contracts.normalize_dataset_descriptor(dataset)
    dataset_id = dataset["datasetId"]
    capabilities = dataset_archive.normalize_capabilities(capabilities)
    version_source = dataset_contracts.normalize_dataset_source(version_source)
    lineage = dataset_archive.normalize_lineage([] if lineage is None else lineage)
    lineage = _require_authoritative_dataset_lineage(config, lineage)
    if build is not None:
        validate_normalized_json_value(build, {}, path="Dataset.build")
    if not isinstance(build_job_id, str):
        raise ValueError("Dataset buildJobId must be a string.")
    if not isinstance(append, bool):
        raise ValueError("Dataset publication append must be a boolean.")
    versions_root = version_archive.resolve_managed_path(
        config["releaseRoot"],
        dataset_archive.repository_root(config["releaseRoot"])
        / dataset_id
        / "versions",
        label="Dataset versions root",
    )
    staging = dataset_staging.require_dataset_staging(
        config, dataset_id, staging_authority
    )
    with engine_database.connect_database(config) as connection:
        _require_dataset_publication_target(
            connection,
            dataset,
            append=append,
            defer_exact_retry=not append,
        )
    container = None
    version_id = None
    created_destination = False
    committed = False
    connection = None
    primary_error = None
    post_commit_cleanup_error = None
    try:
        files = dataset_archive.container_files(staging)
        content_hash = dataset_archive.content_hash(files, capabilities)
        version_id = f"{dataset_id}@{content_hash}"
        container = versions_root / content_hash.split(":", 1)[1] / "container"
        now = engine_clock.utc_now()
        manifest = dataset_archive.build_manifest(
            staging,
            dataset_id=dataset_id,
            dataset=dataset,
            version_id=version_id,
            source=version_source,
            created_at=now,
            storage_uri=container,
            capabilities=capabilities,
            lineage=lineage,
            build=build,
        )
        dataset_archive.require_manifest_build_job_id(manifest, build_job_id)
        reuse_orphan = False
        reuse_indexed = False
        if container.parent.exists():
            if container.parent.is_symlink():
                raise ValueError(
                    "Dataset Version directory may not be a symbolic link."
                )
            children = list(container.parent.iterdir())
            if not children:
                container.parent.rmdir()
            elif (
                children != [container]
                or not container.is_dir()
                or container.is_symlink()
            ):
                raise ValueError(
                    f"Dataset Version destination is occupied: {version_id}"
                )
            else:
                with engine_database.connect_database(config) as recovery_connection:
                    indexed = recovery_connection.execute(
                        "SELECT * FROM dataset_versions WHERE version_id = ?",
                        (version_id,),
                    ).fetchone()
                if indexed:
                    existing_version = datasets.verify_dataset_version_id(
                        config, version_id
                    )
                    stored_manifest = existing_version["manifest"]
                else:
                    try:
                        stored_manifest = strict_json.loads(
                            (container / dataset_archive.MANIFEST_NAME).read_text(
                                encoding="utf-8"
                            )
                        )
                    except (OSError, ValueError) as exc:
                        raise ValueError(
                            f"Unindexed Dataset archive has no valid manifest: {version_id}"
                        ) from exc
                dataset_archive.require_manifest_build_job_id(
                    stored_manifest, build_job_id
                )
                expected_manifest = copy.deepcopy(manifest)
                expected_manifest["createdAt"] = stored_manifest.get("createdAt")
                expected_manifest["manifestDigest"] = version_archive.content_digest(
                    {
                        key: value
                        for key, value in expected_manifest.items()
                        if key != "manifestDigest"
                    }
                )
                if stored_manifest != expected_manifest:
                    raise ValueError(
                        "Dataset Version content address has different provenance: "
                        f"{version_id}"
                    )
                if indexed and existing_version["buildJobId"] != build_job_id:
                    raise ValueError(
                        "Dataset Version content address has a different build Job: "
                        f"{version_id}"
                    )
                if not indexed:
                    dataset_archive.verify_sealed_container(
                        container, stored_manifest, content_hash
                    )
                manifest = stored_manifest
                now = manifest["createdAt"]
                reuse_indexed = bool(indexed)
                reuse_orphan = not reuse_indexed
                _discard_dataset_staging(
                    staging_authority,
                    retain_on_error=False,
                )
        if not reuse_orphan and not reuse_indexed:
            dataset_archive.seal_staging_container(
                staging, manifest, container, content_hash
            )
        connection = engine_database.connect_database(config)
        connection.execute("BEGIN IMMEDIATE")
        operation_nonce = secrets.token_hex(32)
        _require_active_dataset_lineage(connection, lineage)
        _require_dataset_publication_target(
            connection,
            dataset,
            append=append,
            allow_exact_retry=reuse_indexed,
        )
        indexed = connection.execute(
            "SELECT 1 FROM dataset_versions WHERE version_id = ?", (version_id,)
        ).fetchone()
        if reuse_indexed:
            if not indexed:
                raise ValueError(f"Dataset Version index disappeared: {version_id}")
            latest = connection.execute(
                """
                SELECT version_id FROM dataset_versions
                WHERE dataset_id = ? ORDER BY rowid DESC LIMIT 1
                """,
                (dataset_id,),
            ).fetchone()
            if latest is None or latest["version_id"] != version_id:
                raise ValueError(
                    "Historical Dataset Version cannot be republished as an "
                    f"exact retry: {version_id}"
                )
            dataset_archive.verify_sealed_container(
                container, manifest, content_hash
            )
        elif indexed:
            raise ValueError(f"Dataset Version already exists: {version_id}")
        elif reuse_orphan:
            dataset_archive.verify_sealed_container(
                container, manifest, content_hash
            )
        else:
            if container.exists() or container.parent.exists():
                raise ValueError(
                    f"Dataset Version destination changed: {version_id}"
                )
            container.parent.mkdir(parents=True, exist_ok=False)
            created_destination = True
            dataset_archive.publish_sealed_container(
                staging,
                container,
                manifest,
                managed_root=versions_root,
                expected_content_hash=content_hash,
            )
        if not append and not reuse_indexed:
            connection.execute(
                """
                INSERT INTO datasets
                (dataset_id, name, source_json, created_at, metadata_json,
                 status, archived_at, archive_reason)
                VALUES (?, ?, ?, ?, ?, 'active', '', '')
                """,
                (
                    dataset_id,
                    dataset["name"],
                    strict_json.dumps(dataset["source"], sort_keys=True),
                    now,
                    strict_json.dumps(dataset["metadata"], sort_keys=True),
                ),
            )
        if not reuse_indexed:
            connection.execute(
                """
                INSERT INTO dataset_versions
                (version_id, dataset_id, content_hash, status, capabilities_json,
                 created_at, storage_type, storage_uri, manifest_json,
                 manifest_digest, build_job_id)
                VALUES (?, ?, ?, 'sealed', ?, ?, 'directory', ?, ?, ?, ?)
                """,
                (
                    version_id,
                    dataset_id,
                    content_hash,
                    strict_json.dumps(capabilities, sort_keys=True),
                    now,
                    str(container),
                    strict_json.dumps(manifest, sort_keys=True),
                    manifest["manifestDigest"],
                    build_job_id,
                ),
            )
        for source in (() if reuse_indexed else lineage):
            connection.execute(
                """
                INSERT INTO dataset_lineage
                (alias, upstream_dataset_id, upstream_version_id,
                 downstream_dataset_id, downstream_version_id, build_job_id, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    source["alias"],
                    source["datasetId"],
                    source["datasetVersionId"],
                    dataset_id,
                    version_id,
                    build_job_id,
                    now,
                ),
            )
        yield {
            "connection": connection,
            "datasetId": dataset_id,
            "datasetVersionId": version_id,
            "contentHash": content_hash,
            "unchanged": reuse_indexed,
        }
        connection.execute(
            """
            INSERT INTO dataset_publication_receipts
            (operation_nonce, dataset_id, version_id, manifest_digest,
             build_job_id, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                operation_nonce,
                dataset_id,
                version_id,
                manifest["manifestDigest"],
                build_job_id,
                engine_clock.utc_now(),
            ),
        )
        try:
            connection.commit()
        except BaseException as commit_error:
            commit_traceback = commit_error.__traceback__
            cleanup_error = _attempt_connection_cleanup(
                connection, rollback=True
            )
            connection = None
            evidence, evidence_error = _dataset_publication_commit_evidence(
                config,
                operation_nonce=operation_nonce,
                dataset_id=dataset_id,
                version_id=version_id,
                manifest_digest=manifest["manifestDigest"],
                build_job_id=build_job_id,
            )
            if evidence is not True:
                if evidence_error is not None:
                    _attach_secondary_error(evidence_error, cleanup_error)
                    cleanup_error = evidence_error
                _attach_secondary_error(commit_error, cleanup_error)
                raise commit_error.with_traceback(commit_traceback)
            committed = True
            post_commit_cleanup_error = cleanup_error
        else:
            committed = True
    except BaseException as error:
        primary_error = error
        error_traceback = error.__traceback__
        cleanup_error = post_commit_cleanup_error
        if connection is not None:
            cleanup_error = _attempt_connection_cleanup(
                connection, rollback=True
            )
            connection = None
        _attach_secondary_error(error, cleanup_error)
        raise error.with_traceback(error_traceback)
    finally:
        cleanup_error = None
        if connection is not None:
            cleanup_error = _attempt_connection_cleanup(
                connection, rollback=False
            )
            connection = None
        try:
            _discard_dataset_staging(
                staging_authority,
                retain_on_error=committed,
            )
        except BaseException as error:
            cleanup_error = _first_error(cleanup_error, error)
        if (
            not committed
            and created_destination
            and container is not None
            and container.parent.exists()
        ):
            indexed = None
            if version_id is not None:
                try:
                    with engine_database.connect_database(config) as recovery_connection:
                        indexed = recovery_connection.execute(
                            "SELECT 1 FROM dataset_versions WHERE version_id = ?",
                            (version_id,),
                        ).fetchone()
                except BaseException as error:
                    cleanup_error = _first_error(cleanup_error, error)
                    indexed = True
            if not indexed:
                try:
                    version_archive.discard_archive(container.parent)
                except BaseException as error:
                    cleanup_error = _first_error(cleanup_error, error)
        if cleanup_error is not None:
            if primary_error is not None:
                _attach_secondary_error(primary_error, cleanup_error)
            else:
                raise cleanup_error


def publish_dataset_version(
    config,
    *,
    dataset,
    staging,
    capabilities,
    version_source,
    lineage=None,
    build=None,
    build_job_id="",
    append=False,
):
    """Publish one generic immutable Dataset Version through the sole DB boundary."""
    with dataset_publication_transaction(
        config,
        dataset=dataset,
        staging=staging,
        capabilities=capabilities,
        version_source=version_source,
        lineage=lineage,
        build=build,
        build_job_id=build_job_id,
        append=append,
    ) as publication:
        dataset_id = publication["datasetId"]
    return datasets.get_dataset(config, dataset_id)


__all__ = (
    "dataset_publication_transaction",
    "publish_dataset_version",
    "retry_pending_dataset_publication_cleanup",
)
