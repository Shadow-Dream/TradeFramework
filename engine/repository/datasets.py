#!/usr/bin/env python3
"""Dataset index queries, lifecycle transitions, and sealed Version lookup."""

from __future__ import annotations

import copy

from engine.archive import dataset as dataset_archive
from engine.contracts import dataset as dataset_contracts
from engine.contracts import digest as digest_contracts
from engine.contracts import strict_json
from engine.contracts.data_model import validate_normalized_json_value
from engine.contracts.exact_fields import require_exact_fields
from engine.control import database as engine_database
from engine.core import clock as engine_clock


def search_datasets(config, query=""):
    query = str(query or "").lower()
    local = list_datasets(config)
    if query:
        local = [
            item
            for item in local
            if query in item["datasetId"].lower() or query in item["name"].lower()
        ]
    return {"local": local}


def _json_column(row, column, *, label, value_type):
    try:
        value = strict_json.loads(row[column])
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} contains invalid stored JSON.") from exc
    if not isinstance(value, value_type):
        raise ValueError(f"{label} has an invalid stored JSON type.")
    return value


def count_datasets(config):
    with engine_database.connect_database(config) as connection:
        row = connection.execute("SELECT COUNT(*) AS count FROM datasets").fetchone()
    return int(row["count"])


def list_datasets(config, limit=None):
    sql = """
        SELECT d.*,
               (SELECT version_id FROM dataset_versions v
                WHERE v.dataset_id = d.dataset_id ORDER BY v.rowid DESC LIMIT 1) AS latest_version_id
        FROM datasets d ORDER BY d.created_at DESC
    """
    params = ()
    if limit is not None:
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 0:
            raise ValueError("Dataset list limit must be a non-negative integer.")
        sql += " LIMIT ?"
        params = (limit,)
    with engine_database.connect_database(config) as connection:
        rows = connection.execute(sql, params).fetchall()
    return [attach_dataset_lineage(config, decode_dataset_index_row(row)) for row in rows]


def get_dataset(config, dataset_id):
    with engine_database.connect_database(config) as connection:
        row = connection.execute(
            """
            SELECT d.*,
                   (SELECT version_id FROM dataset_versions v
                    WHERE v.dataset_id = d.dataset_id ORDER BY v.rowid DESC LIMIT 1) AS latest_version_id
            FROM datasets d WHERE d.dataset_id = ?
            """,
            (dataset_id,),
        ).fetchone()
    if not row:
        raise ValueError(f"Unknown dataset: {dataset_id}")
    return attach_dataset_lineage(config, decode_dataset_index_row(row))


def decode_dataset_index_row(row):
    source = _json_column(row, "source_json", label="Dataset source", value_type=dict)
    metadata = _json_column(
        row, "metadata_json", label="Dataset metadata", value_type=dict
    )
    item = {
        "datasetId": row["dataset_id"],
        "name": row["name"],
        "source": dataset_contracts.normalize_dataset_source(source),
        "createdAt": row["created_at"],
        "metadata": metadata,
        "latestVersionId": row["latest_version_id"],
        "status": row["status"],
        "archivedAt": row["archived_at"],
        "archiveReason": row["archive_reason"],
    }
    require_exact_fields(
        item,
        allowed={
            "datasetId",
            "name",
            "source",
            "createdAt",
            "metadata",
            "latestVersionId",
            "status",
            "archivedAt",
            "archiveReason",
        },
        required={
            "datasetId",
            "name",
            "source",
            "createdAt",
            "metadata",
            "latestVersionId",
            "status",
            "archivedAt",
            "archiveReason",
        },
        label="Dataset index row",
    )
    for field in (
        "datasetId",
        "name",
        "createdAt",
        "status",
        "archivedAt",
        "archiveReason",
    ):
        if not isinstance(item[field], str):
            raise ValueError(f"Dataset index field '{field}' must be a string.")
    if not item["datasetId"] or not item["name"] or not item["createdAt"]:
        raise ValueError("Dataset index identity, name, and createdAt are required.")
    if item["status"] not in {"active", "archived"}:
        raise ValueError("Dataset index status is invalid.")
    if item["latestVersionId"] is not None and (
        not isinstance(item["latestVersionId"], str)
        or not item["latestVersionId"].startswith(item["datasetId"] + "@sha256:")
    ):
        raise ValueError("Dataset index latestVersionId is invalid.")
    if item["status"] == "active" and (item["archivedAt"] or item["archiveReason"]):
        raise ValueError("Active Dataset index contains archive state.")
    if item["status"] == "archived" and not item["archivedAt"]:
        raise ValueError("Archived Dataset index is missing archivedAt.")
    validate_normalized_json_value(item["metadata"], {}, path="Dataset.metadata")
    return item


def attach_dataset_lineage(config, item):
    dataset_id = item["datasetId"]
    with engine_database.connect_database(config) as connection:
        upstream = connection.execute(
            """
            SELECT alias, upstream_dataset_id, upstream_version_id, build_job_id, created_at
            FROM dataset_lineage
            WHERE downstream_dataset_id = ?
            ORDER BY created_at, downstream_version_id, alias
            """,
            (dataset_id,),
        ).fetchall()
        downstream = connection.execute(
            """
            SELECT alias, downstream_dataset_id, downstream_version_id, build_job_id, created_at
            FROM dataset_lineage
            WHERE upstream_dataset_id = ?
            ORDER BY created_at, downstream_version_id, alias
            """,
            (dataset_id,),
        ).fetchall()
    item["upstream"] = [
        {
            "alias": row["alias"],
            "datasetId": row["upstream_dataset_id"],
            "datasetVersionId": row["upstream_version_id"],
            "buildJobId": row["build_job_id"],
            "createdAt": row["created_at"],
        }
        for row in upstream
    ]
    item["downstream"] = [
        {
            "alias": row["alias"],
            "datasetId": row["downstream_dataset_id"],
            "datasetVersionId": row["downstream_version_id"],
            "buildJobId": row["build_job_id"],
            "createdAt": row["created_at"],
        }
        for row in downstream
    ]
    return item


def _dataset_version_from_row(row):
    capabilities = _json_column(
        row,
        "capabilities_json",
        label="Dataset Version capabilities",
        value_type=dict,
    )
    manifest = _json_column(
        row, "manifest_json", label="Dataset Version manifest", value_type=dict
    )
    dataset_archive.validate_manifest(manifest)
    return {
        "datasetVersionId": row["version_id"],
        "datasetId": row["dataset_id"],
        "contentHash": row["content_hash"],
        "status": row["status"],
        "source": copy.deepcopy(manifest["source"]),
        "capabilities": capabilities,
        "createdAt": row["created_at"],
        "storage": {"type": row["storage_type"], "uri": row["storage_uri"]},
        "manifest": manifest,
        "manifestDigest": row["manifest_digest"],
        "buildJobId": row["build_job_id"],
    }


def verify_dataset_version_id(config, version_id):
    with engine_database.connect_database(config) as connection:
        row = connection.execute(
            "SELECT * FROM dataset_versions WHERE version_id = ?", (version_id,)
        ).fetchone()
        lineage_rows = connection.execute(
            """
            SELECT lineage.alias, lineage.upstream_dataset_id,
                   lineage.upstream_version_id, upstream.content_hash
            FROM dataset_lineage AS lineage
            JOIN dataset_versions AS upstream
              ON upstream.dataset_id = lineage.upstream_dataset_id
             AND upstream.version_id = lineage.upstream_version_id
            WHERE lineage.downstream_version_id = ?
            ORDER BY lineage.alias
            """,
            (version_id,),
        ).fetchall()
    if not row:
        raise ValueError(f"Unknown Dataset version: {version_id}")
    version = verify_dataset_version(config, _dataset_version_from_row(row))
    indexed_lineage = [
        {
            "alias": item["alias"],
            "datasetId": item["upstream_dataset_id"],
            "datasetVersionId": item["upstream_version_id"],
            "contentHash": item["content_hash"],
        }
        for item in lineage_rows
    ]
    if sorted(version["manifest"]["lineage"], key=lambda item: item["alias"]) != indexed_lineage:
        raise ValueError(
            "Dataset Version lineage index does not match its sealed manifest."
        )
    return version


def archive_dataset(config, dataset_id, reason=""):
    dataset = get_dataset(config, dataset_id)
    if dataset["status"] == "archived":
        return {"dataset": dataset, "archivedDatasetIds": []}
    archived = []
    queue = [dataset_id]
    seen = set()
    now = engine_clock.utc_now()
    with engine_database.connect_database(config) as connection:
        while queue:
            current = queue.pop(0)
            if current in seen:
                continue
            seen.add(current)
            row = connection.execute(
                "SELECT status FROM datasets WHERE dataset_id = ?", (current,)
            ).fetchone()
            if not row:
                raise ValueError(
                    f"Dataset lineage references missing Dataset '{current}'."
                )
            if row["status"] != "archived":
                connection.execute(
                    "UPDATE datasets SET status = 'archived', archived_at = ?, "
                    "archive_reason = ? WHERE dataset_id = ?",
                    (
                        now,
                        reason
                        or f"Cascaded from archived Dataset '{dataset_id}'",
                        current,
                    ),
                )
                archived.append(current)
            children = connection.execute(
                "SELECT downstream_dataset_id FROM dataset_lineage "
                "WHERE upstream_dataset_id = ?",
                (current,),
            ).fetchall()
            queue.extend(row["downstream_dataset_id"] for row in children)
        connection.commit()
    return {
        "dataset": get_dataset(config, dataset_id),
        "archivedDatasetIds": archived,
    }


def rename_dataset(config, dataset_id, name):
    name = str(name or "").strip()
    if not name:
        raise ValueError("Dataset name is required.")
    if len(name) > 160:
        raise ValueError("Dataset name must be 160 characters or fewer.")
    get_dataset(config, dataset_id)
    with engine_database.connect_database(config) as connection:
        connection.execute(
            "UPDATE datasets SET name = ? WHERE dataset_id = ?", (name, dataset_id)
        )
        connection.commit()
    return get_dataset(config, dataset_id)


def list_dataset_versions(config, dataset_id):
    get_dataset(config, dataset_id)
    with engine_database.connect_database(config) as connection:
        rows = connection.execute(
            "SELECT * FROM dataset_versions WHERE dataset_id = ? ORDER BY rowid DESC",
            (dataset_id,),
        ).fetchall()
    return [
        dataset_archive.validate_sealed_version_descriptor(
            _dataset_version_from_row(row)
        )
        for row in rows
    ]


def list_dataset_version_summaries(config, dataset_ids):
    if not isinstance(dataset_ids, list) or any(
        not isinstance(dataset_id, str) or not dataset_id
        for dataset_id in dataset_ids
    ):
        raise ValueError("Dataset Version summary IDs must be non-empty strings.")
    ids = list(dict.fromkeys(dataset_ids))
    if not ids:
        return []
    placeholders = ", ".join("?" for _ in ids)
    with engine_database.connect_database(config) as connection:
        rows = connection.execute(
            f"""
            SELECT version_id, dataset_id, content_hash, status, capabilities_json, created_at
            FROM dataset_versions
            WHERE dataset_id IN ({placeholders})
            ORDER BY rowid DESC
            """,
            tuple(ids),
        ).fetchall()
    summaries = []
    for row in rows:
        capabilities = _json_column(
            row,
            "capabilities_json",
            label="Dataset Version capabilities",
            value_type=dict,
        )
        dataset_archive.normalize_capabilities(capabilities)
        summary = {
            "datasetVersionId": row["version_id"],
            "datasetId": row["dataset_id"],
            "contentHash": row["content_hash"],
            "status": row["status"],
            "capabilities": capabilities,
            "createdAt": row["created_at"],
        }
        if (
            not isinstance(summary["datasetId"], str)
            or not summary["datasetId"]
            or summary["datasetVersionId"]
            != f"{summary['datasetId']}@{summary['contentHash']}"
            or not isinstance(summary["contentHash"], str)
            or not digest_contracts.is_sha256_digest(summary["contentHash"])
            or summary["status"] != "sealed"
            or not isinstance(summary["createdAt"], str)
            or not summary["createdAt"]
        ):
            raise ValueError("Dataset Version summary index is invalid.")
        summaries.append(summary)
    return summaries


def verify_dataset_version(config, version):
    return dataset_archive.verify_version_storage(config["releaseRoot"], version)


def ensure_dataset_version(config, dataset_id, version_id=""):
    if not isinstance(dataset_id, str) or not dataset_id:
        raise ValueError("Dataset ID must be a non-empty string.")
    if not isinstance(version_id, str):
        raise ValueError("Dataset Version ID must be a string.")
    dataset_state = get_dataset(config, dataset_id)
    if dataset_state["status"] != "active":
        raise ValueError(f"Dataset '{dataset_id}' is archived and cannot be used.")
    if version_id:
        with engine_database.connect_database(config) as connection:
            row = connection.execute(
                "SELECT version_id FROM dataset_versions "
                "WHERE dataset_id = ? AND version_id = ?",
                (dataset_id, version_id),
            ).fetchone()
        if not row:
            raise ValueError(f"Unknown Dataset version: {version_id}")
        return verify_dataset_version_id(config, row["version_id"])
    versions = list_dataset_versions(config, dataset_id)
    if versions:
        return verify_dataset_version_id(config, versions[0]["datasetVersionId"])
    raise ValueError(f"Dataset '{dataset_id}' has no sealed version.")
