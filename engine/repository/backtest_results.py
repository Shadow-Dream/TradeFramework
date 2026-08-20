"""Authoritative repository operations for immutable Backtest Results."""

import hashlib
import re

from engine.archive import backtest_result as backtest_result_archive
from engine.archive import version as version_archive
import engine.contracts.result_execution as result_execution_contracts
from engine.contracts import result as result_contracts
from engine.contracts import strict_json
from engine.contracts import visualization as visualization_contracts
from engine.contracts.digest import is_sha256_digest
from engine.contracts.module import require_exact_fields
from engine.control import database as engine_database
from engine.core import clock as engine_clock
from engine.core import resource_ids
from engine.repository import backtest_result_views


_BACKTEST_RESULT_STAGING_PATTERN = re.compile(
    r"^\.(bt_[0-9A-HJKMNP-TV-Z]{26})\.staging-[A-Za-z0-9_-]+$"
)
_RESULT_MANIFEST_METADATA_FIELDS = frozenset({
    "schemaVersion",
    "dataKeys",
    "metrics",
    "executionChain",
    "sampleFrameContract",
})

_BACKTEST_INDEX_SELECT = """
    SELECT
        b.backtest_id,
        b.pipeline_id,
        b.dataset_id,
        b.name,
        b.status,
        b.runner,
        b.created_at,
        b.completed_at,
        b.request_json,
        b.metrics_json,
        b.visualization_json,
        b.archived_at,
        b.archive_reason,
        metadata.schema_version AS indexed_result_schema_version,
        metadata.has_cycles AS indexed_result_has_cycles,
        metadata.data_keys_json AS indexed_result_data_keys_json,
        metadata.execution_chain_json AS indexed_result_execution_chain_json,
        metadata.content_digest AS indexed_result_content_digest,
        metadata.result_size AS indexed_result_size
    FROM backtests b
    LEFT JOIN backtest_result_metadata metadata
      ON metadata.backtest_id = b.backtest_id
"""

def require_result_manifest_metadata(metadata, *, expected_schema_version):
    """Require the exact Result metadata envelope copied into a sealed manifest."""

    require_exact_fields(
        metadata,
        allowed=_RESULT_MANIFEST_METADATA_FIELDS,
        required=_RESULT_MANIFEST_METADATA_FIELDS,
        label="Result manifest metadata",
    )
    if (
        type(expected_schema_version) is not int
        or expected_schema_version < 1
        or type(metadata["schemaVersion"]) is not int
        or metadata["schemaVersion"] != expected_schema_version
    ):
        raise ValueError("Result manifest metadata schemaVersion is invalid.")
    return metadata


def _strict_json_equal(left, right):
    return strict_json.exact_equal(left, right)


def reconcile_result_staging(config):
    """Remove incomplete Result writer directories left by abrupt Runtime exit."""
    root = backtest_result_archive.archive_root(config["releaseRoot"])
    root.mkdir(parents=True, exist_ok=True)
    allowed_exact = {"result.json", "result-manifest.json", "result-manifest.json.tmp"}
    for candidate in sorted(root.iterdir()):
        if not _BACKTEST_RESULT_STAGING_PATTERN.fullmatch(candidate.name):
            continue
        if candidate.is_symlink() or not candidate.is_dir():
            raise ValueError("Backtest Result staging entry is not a managed directory.")
        entries = tuple(candidate.iterdir())
        if any(
            entry.is_symlink()
            or (
                entry.name not in allowed_exact
                and not entry.name.startswith(".result-")
            )
            for entry in entries
        ):
            raise ValueError(
                "Backtest Result staging directory contains unexpected entries."
            )
        version_archive.discard_archive(candidate)


def save_result_metadata(
    conn, backtest_id, result, *, content_digest, result_size
):
    if not isinstance(backtest_id, str) or not backtest_id:
        raise ValueError("Result metadata backtest ID must be a non-empty string.")
    if not is_sha256_digest(content_digest):
        raise ValueError("Result metadata content digest is invalid.")
    if isinstance(result_size, bool) or not isinstance(result_size, int) or result_size < 1:
        raise ValueError("Result metadata size must be a positive integer.")
    metadata = result_contracts.result_metadata_payload(result)
    conn.execute(
        """
        INSERT INTO backtest_result_metadata
        (backtest_id, schema_version, has_cycles, data_keys_json, execution_chain_json,
         content_digest, result_size)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            backtest_id,
            metadata["schemaVersion"],
            int(metadata["hasCycles"]),
            strict_json.dumps(metadata["dataKeys"], sort_keys=True),
            strict_json.dumps(metadata["executionChain"], sort_keys=True),
            content_digest,
            result_size,
        ),
    )


def _insert_completed_result_catalog(
    conn,
    backtest_id,
    catalog,
    result,
    *,
    content_digest,
    result_size,
):
    conn.execute(
        """
        INSERT INTO backtests
        (backtest_id, pipeline_id, dataset_id, name, status, runner,
         created_at, completed_at, request_json, metrics_json,
         visualization_json)
        VALUES (?, ?, ?, ?, 'completed', ?, ?, ?, ?, ?, ?)
        """,
        (
            backtest_id,
            catalog["pipelineId"],
            catalog["datasetId"],
            catalog["name"],
            catalog["runner"],
            catalog["createdAt"],
            catalog["completedAt"],
            strict_json.dumps(catalog["request"], sort_keys=True),
            strict_json.dumps(catalog["metrics"], sort_keys=True),
            strict_json.dumps(catalog["visualization"], sort_keys=True),
        ),
    )
    save_result_metadata(
        conn,
        backtest_id,
        result,
        content_digest=content_digest,
        result_size=result_size,
    )


def catalog_commit_state(config, backtest_id, *, content_digest, result_size):
    """Read authoritative catalog evidence after an uncertain Result commit."""
    with engine_database.connect_database(config) as conn:
        backtest = conn.execute(
            "SELECT status FROM backtests WHERE backtest_id = ?",
            (backtest_id,),
        ).fetchone()
        metadata = conn.execute(
            """
            SELECT content_digest, result_size
            FROM backtest_result_metadata
            WHERE backtest_id = ?
            """,
            (backtest_id,),
        ).fetchone()
    if backtest is None and metadata is None:
        return "absent"
    if (
        backtest is not None
        and backtest["status"] == "completed"
        and metadata is not None
        and metadata["content_digest"] == content_digest
        and metadata["result_size"] == result_size
    ):
        return "committed"
    return "conflict"


def result_catalog_exists(config, backtest_id):
    """Return whether a Backtest catalog row already owns this identity."""
    with engine_database.connect_database(config) as conn:
        return conn.execute(
            "SELECT 1 FROM backtests WHERE backtest_id = ?",
            (backtest_id,),
        ).fetchone() is not None


def load_unindexed_result_archive(config, backtest_id):
    """Read the exact sealed manifest for one not-yet-indexed Result archive."""
    directory = backtest_result_archive.archive_directory(
        config["releaseRoot"],
        backtest_id,
        label="Backtest Result recovery directory",
    )
    path = directory / backtest_result_archive.RESULT_FILE_NAME
    manifest_path = directory / backtest_result_archive.MANIFEST_FILE_NAME
    if not directory.exists():
        return None
    if directory.is_symlink() or not directory.is_dir():
        raise ValueError("Backtest Result recovery directory is invalid.")
    entries = set(directory.iterdir())
    if entries != {path, manifest_path}:
        # A writable, manifest-less writer staging directory is not completion
        # evidence.  Its active Job may be failed normally.
        if not manifest_path.exists():
            return None
        raise ValueError(
            "Sealed Backtest Result recovery archive has unexpected entries."
        )
    if path.is_symlink() or manifest_path.is_symlink():
        raise ValueError(
            "Backtest Result recovery archive may not contain symlinks."
        )
    if (
        path.stat().st_mode & 0o222
        or manifest_path.stat().st_mode & 0o222
        or directory.stat().st_mode & 0o222
    ):
        raise ValueError(
            "Backtest Result recovery archive is not sealed read-only."
        )
    manifest = strict_json.loads(manifest_path.read_text(encoding="utf-8"))
    require_exact_fields(
        manifest,
        allowed={
            "schemaVersion",
            "backtestId",
            "resultFile",
            "contentDigest",
            "size",
            "catalog",
            "resultMetadata",
        },
        required={
            "schemaVersion",
            "backtestId",
            "resultFile",
            "contentDigest",
            "size",
            "catalog",
            "resultMetadata",
        },
        label="Recovered Result manifest",
    )
    if (
        type(manifest["schemaVersion"]) is not int
        or manifest["schemaVersion"] != 4
        or manifest["backtestId"] != backtest_id
        or manifest["resultFile"] != "result.json"
        or not isinstance(manifest["contentDigest"], str)
        or not is_sha256_digest(manifest["contentDigest"])
        or type(manifest["size"]) is not int
        or manifest["size"] < 1
    ):
        raise ValueError("Recovered Result manifest is invalid.")
    if path.stat().st_size != manifest["size"]:
        raise ValueError("Recovered Backtest Result content digest is invalid.")
    return {
        "path": path,
        "manifest": manifest,
        "contentDigest": manifest["contentDigest"],
        "resultSize": manifest["size"],
    }


def commit_recovered_result_catalog(
    config,
    backtest_id,
    catalog,
    metadata,
    *,
    content_digest,
    result_size,
):
    """Atomically index a recovered Result and reconcile a lost commit ACK."""
    commit_error = None
    try:
        with engine_database.connect_database(config) as conn:
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute(
                "SELECT 1 FROM backtests WHERE backtest_id = ?",
                (backtest_id,),
            ).fetchone()
            if existing is None:
                _insert_completed_result_catalog(
                    conn,
                    backtest_id,
                    catalog,
                    {**metadata, "cycles": []},
                    content_digest=content_digest,
                    result_size=result_size,
                )
            conn.commit()
    except BaseException as exc:
        commit_error = exc
    if commit_error is None:
        return
    try:
        commit_state = catalog_commit_state(
            config,
            backtest_id,
            content_digest=content_digest,
            result_size=result_size,
        )
    except BaseException:
        raise commit_error
    if commit_state != "committed":
        raise commit_error


def require_recovered_result_commit(
    config,
    backtest_id,
    catalog,
    metadata,
    *,
    content_digest,
    result_size,
    archive_identity,
):
    """Bind one pre-commit strict proof to the exact durable catalog rows."""

    directory = backtest_result_archive.archive_directory(
        config["releaseRoot"],
        backtest_id,
        label="Recovered Backtest Result receipt directory",
    )
    actual_identity = backtest_result_archive.sealed_archive_identity(
        directory,
        label="Recovered Backtest Result durable archive",
    )
    if actual_identity != archive_identity:
        raise ValueError("Recovered Backtest Result changed after strict validation.")
    with engine_database.connect_database(config) as conn:
        row = conn.execute(
            """
            SELECT b.pipeline_id, b.dataset_id, b.name, b.status, b.runner,
                   b.created_at, b.completed_at, b.request_json,
                   b.metrics_json, b.visualization_json,
                   m.schema_version, m.has_cycles, m.data_keys_json,
                   m.execution_chain_json, m.content_digest, m.result_size
            FROM backtests b
            JOIN backtest_result_metadata m ON m.backtest_id = b.backtest_id
            WHERE b.backtest_id = ?
            """,
            (backtest_id,),
        ).fetchone()
    expected = {
        "pipeline_id": catalog["pipelineId"],
        "dataset_id": catalog["datasetId"],
        "name": catalog["name"],
        "status": "completed",
        "runner": catalog["runner"],
        "created_at": catalog["createdAt"],
        "completed_at": catalog["completedAt"],
        "request_json": strict_json.dumps(catalog["request"], sort_keys=True),
        "metrics_json": strict_json.dumps(catalog["metrics"], sort_keys=True),
        "visualization_json": strict_json.dumps(
            catalog["visualization"], sort_keys=True
        ),
        "schema_version": metadata["schemaVersion"],
        "has_cycles": 1,
        "data_keys_json": strict_json.dumps(metadata["dataKeys"], sort_keys=True),
        "execution_chain_json": strict_json.dumps(
            metadata["executionChain"], sort_keys=True
        ),
        "content_digest": content_digest,
        "result_size": result_size,
    }
    if row is None or any(row[field] != value for field, value in expected.items()):
        raise ValueError("Recovered Backtest Result durable receipt is invalid.")
    # Return fresh owners decoded from the exact durable rows just compared.
    # This is both the committed source of truth and avoids copying the much
    # larger caller-owned frozen request a second time after commit.
    durable_request = strict_json.loads(row["request_json"])
    durable_metrics = strict_json.loads(row["metrics_json"])
    durable_visualization = strict_json.loads(row["visualization_json"])
    durable_data_keys = strict_json.loads(row["data_keys_json"])
    durable_execution_chain = strict_json.loads(row["execution_chain_json"])
    return {
        "backtestId": backtest_id,
        "pipelineId": catalog["pipelineId"],
        "datasetId": catalog["datasetId"],
        "name": catalog["name"],
        "status": "completed",
        "runner": catalog["runner"],
        "createdAt": catalog["createdAt"],
        "completedAt": catalog["completedAt"],
        "archivedAt": "",
        "archiveReason": "",
        "request": durable_request,
        "metrics": durable_metrics,
        "resultSchemaVersion": metadata["schemaVersion"],
        "visualizable": True,
        "visualizationIssue": "",
        "visualization": durable_visualization,
        "dataKeys": durable_data_keys,
        "executionChain": durable_execution_chain,
    }


def count_backtests(config, include_archived=False):
    where = "" if include_archived else " WHERE status != 'archived'"
    with engine_database.connect_database(config) as conn:
        row = conn.execute(f"SELECT COUNT(*) AS count FROM backtests{where}").fetchone()
    return int(row["count"])


def list_backtests(config, limit=None, include_archived=False):
    sql = backtest_result_views.BACKTEST_SUMMARY_SELECT
    if not include_archived:
        sql += " WHERE b.status != 'archived'"
    sql += " ORDER BY b.created_at DESC"
    params = ()
    if limit is not None:
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 0:
            raise ValueError("Backtest list limit must be a non-negative integer.")
        sql += " LIMIT ?"
        params = (limit,)
    with engine_database.connect_database(config) as conn:
        rows = conn.execute(sql, params).fetchall()
    return [backtest_result_views.backtest_summary(row) for row in rows]


def archive_backtest(config, backtest_id, reason=""):
    backtest = get_backtest_meta(config, backtest_id)
    if backtest["status"] == "archived":
        return backtest
    now = engine_clock.utc_now()
    with engine_database.connect_database(config) as conn:
        conn.execute(
            "UPDATE backtests SET status = 'archived', archived_at = ?, "
            "archive_reason = ? WHERE backtest_id = ?",
            (now, str(reason or "").strip(), backtest_id),
        )
        conn.commit()
    return get_backtest_meta(config, backtest_id)


def rename_backtest(config, backtest_id, name):
    name = str(name or "").strip()
    if not name:
        raise ValueError("Backtest name is required.")
    if len(name) > 160:
        raise ValueError("Backtest name must be 160 characters or fewer.")
    get_backtest_meta(config, backtest_id)
    with engine_database.connect_database(config) as conn:
        conn.execute(
            "UPDATE backtests SET name = ? WHERE backtest_id = ?",
            (name, backtest_id),
        )
        conn.commit()
    return get_backtest_meta(config, backtest_id)


def load_result_archive_evidence(config, backtest_id, *, verify_digest):
    if not isinstance(backtest_id, str) or not resource_ids.is_resource_id(backtest_id):
        raise ValueError("Backtest Result ID must be an Engine-issued resource ID.")
    directory = backtest_result_archive.archive_directory(
        config["releaseRoot"],
        backtest_id,
        label="Backtest Result directory",
    )
    path = directory / backtest_result_archive.RESULT_FILE_NAME
    manifest_path = directory / backtest_result_archive.MANIFEST_FILE_NAME
    if directory.is_symlink():
        raise ValueError(f"Backtest '{backtest_id}' Result archive may not contain symlinks.")
    if directory.is_dir() and set(directory.iterdir()) != {path, manifest_path}:
        raise ValueError(f"Backtest '{backtest_id}' Result archive has unexpected entries.")
    if not path.is_file() or not manifest_path.is_file():
        raise ValueError(f"Backtest '{backtest_id}' has no immutable Result file.")
    if directory.is_symlink() or path.is_symlink() or manifest_path.is_symlink():
        raise ValueError(f"Backtest '{backtest_id}' Result archive may not contain symlinks.")
    if (
        path.stat().st_mode & 0o222
        or manifest_path.stat().st_mode & 0o222
        or directory.stat().st_mode & 0o222
    ):
        raise ValueError(f"Backtest '{backtest_id}' Result archive is writable.")
    with engine_database.connect_database(config) as conn:
        row = conn.execute(
            """
            SELECT m.schema_version, m.has_cycles, m.data_keys_json,
                   m.execution_chain_json, m.content_digest, m.result_size,
                   b.pipeline_id, b.dataset_id, b.runner,
                   b.created_at, b.completed_at, b.request_json, b.metrics_json
            FROM backtest_result_metadata m
            JOIN backtests b ON b.backtest_id = m.backtest_id
            WHERE m.backtest_id = ?
            """,
            (backtest_id,),
        ).fetchone()
    if row is None:
        raise ValueError(f"Backtest '{backtest_id}' Result digest index is missing.")
    if row["schema_version"] != 8 or row["has_cycles"] != 1:
        raise ValueError(f"Backtest '{backtest_id}' Result metadata index is invalid.")
    actual_size = path.stat().st_size
    with manifest_path.open(encoding="utf-8") as handle:
        manifest = strict_json.load(handle)
    require_exact_fields(
        manifest,
        allowed={
            "schemaVersion",
            "backtestId",
            "resultFile",
            "contentDigest",
            "size",
            "catalog",
            "resultMetadata",
        },
        required={
            "schemaVersion",
            "backtestId",
            "resultFile",
            "contentDigest",
            "size",
            "catalog",
            "resultMetadata",
        },
        label="Result manifest",
    )
    expected_manifest_fields = {
        "backtestId": backtest_id,
        "resultFile": "result.json",
        "contentDigest": row["content_digest"],
    }
    if (
        type(manifest["schemaVersion"]) is not int
        or manifest["schemaVersion"] != 4
        or type(manifest["size"]) is not int
        or manifest["size"] < 1
        or manifest["size"] != row["result_size"]
        or any(
            manifest.get(key) != value
            for key, value in expected_manifest_fields.items()
        )
    ):
        raise ValueError(
            f"Backtest '{backtest_id}' Result manifest does not match its index."
        )
    if actual_size != row["result_size"]:
        raise ValueError(f"Backtest '{backtest_id}' Result content size mismatch.")
    manifest_metadata = require_result_manifest_metadata(
        manifest["resultMetadata"],
        expected_schema_version=row["schema_version"],
    )
    if verify_digest:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            while chunk := handle.read(1024 * 1024):
                digest.update(chunk)
        if "sha256:" + digest.hexdigest() != row["content_digest"]:
            raise ValueError(
                f"Backtest '{backtest_id}' Result content digest mismatch."
            )
    try:
        request = strict_json.loads(row["request_json"])
        indexed_metrics = strict_json.loads(row["metrics_json"])
        indexed_data_keys = strict_json.loads(row["data_keys_json"])
        indexed_execution_chain = strict_json.loads(row["execution_chain_json"])
    except ValueError as exc:
        raise ValueError(
            f"Backtest '{backtest_id}' has invalid stored request JSON."
        ) from exc
    if (
        not isinstance(request, dict)
        or "executionSnapshot" not in request
        or not isinstance(request["executionSnapshot"], dict)
    ):
        raise ValueError(f"Backtest '{backtest_id}' has no stored execution snapshot.")
    catalog = result_contracts.require_catalog(
        manifest["catalog"], backtest_id=backtest_id
    )
    if (
        catalog["pipelineId"] != row["pipeline_id"]
        or catalog["datasetId"] != row["dataset_id"]
        or catalog["runner"] != row["runner"]
        or catalog["createdAt"] != row["created_at"]
        or catalog["completedAt"] != row["completed_at"]
        or not _strict_json_equal(catalog["request"], request)
        or not _strict_json_equal(catalog["metrics"], indexed_metrics)
    ):
        raise ValueError(
            f"Backtest '{backtest_id}' Result catalog evidence does not match its index."
        )
    if not isinstance(indexed_data_keys, dict) or not isinstance(
        indexed_execution_chain, dict
    ):
        raise ValueError(f"Backtest '{backtest_id}' Result metadata index is invalid.")
    result_contracts.compile_cycle_validator(indexed_data_keys)
    result_execution_contracts.require_execution_chain(indexed_execution_chain)
    result_execution_contracts.require_snapshot_match(
        indexed_execution_chain, request["executionSnapshot"]
    )
    if (
        not _strict_json_equal(manifest_metadata["dataKeys"], indexed_data_keys)
        or not _strict_json_equal(
            manifest_metadata["executionChain"], indexed_execution_chain
        )
        or not _strict_json_equal(manifest_metadata["metrics"], indexed_metrics)
    ):
        raise ValueError(
            f"Backtest '{backtest_id}' Result manifest metadata does not match its index."
        )
    return {
        "path": path,
        "manifest": manifest,
        "contentDigest": row["content_digest"],
        "resultSize": row["result_size"],
        "request": request,
        "metrics": indexed_metrics,
        "dataKeys": indexed_data_keys,
        "executionChain": indexed_execution_chain,
    }


def get_backtest_meta(config, backtest_id):
    with engine_database.connect_database(config) as conn:
        row = conn.execute(
            _BACKTEST_INDEX_SELECT + " WHERE b.backtest_id = ?", (backtest_id,)
        ).fetchone()
    if not row:
        raise ValueError(f"Unknown backtest: {backtest_id}")
    return _backtest_row(row, include_visualization=True, include_data_keys=True)


def get_backtest_result_view(config, backtest_id):
    """Return the complete UI Result metadata without hidden execution evidence."""

    with engine_database.connect_database(config) as conn:
        row = conn.execute(
            backtest_result_views.BACKTEST_RESULT_VIEW_SELECT
            + " WHERE b.backtest_id = ?",
            (backtest_id,),
        ).fetchone()
    if not row:
        raise ValueError(f"Unknown backtest: {backtest_id}")
    return backtest_result_views.backtest_result_view(row)


def _backtest_row(row, include_visualization=False, include_data_keys=False):
    required_index_fields = (
        "indexed_result_schema_version",
        "indexed_result_has_cycles",
        "indexed_result_data_keys_json",
        "indexed_result_execution_chain_json",
        "indexed_result_content_digest",
        "indexed_result_size",
    )
    if any(
        field not in row.keys() or row[field] is None
        for field in required_index_fields
    ):
        raise ValueError("Backtest Result metadata is missing.")
    if (
        row["indexed_result_schema_version"] != 8
        or row["indexed_result_has_cycles"] != 1
        or not is_sha256_digest(row["indexed_result_content_digest"])
        or isinstance(row["indexed_result_size"], bool)
        or not isinstance(row["indexed_result_size"], int)
        or row["indexed_result_size"] < 1
    ):
        raise ValueError("Backtest Result metadata index is invalid.")
    result_metadata = {
        "schemaVersion": row["indexed_result_schema_version"],
        "hasCycles": True,
        "dataKeys": {},
        "executionChain": {},
    }
    if include_data_keys:
        try:
            result_metadata["dataKeys"] = strict_json.loads(
                row["indexed_result_data_keys_json"]
            )
            result_metadata["executionChain"] = strict_json.loads(
                row["indexed_result_execution_chain_json"]
            )
        except ValueError as exc:
            raise ValueError(
                "Backtest Result metadata contains invalid stored JSON."
            ) from exc
        if not isinstance(result_metadata["dataKeys"], dict) or not isinstance(
            result_metadata["executionChain"], dict
        ):
            raise ValueError(
                "Backtest Result metadata stored JSON has invalid types."
            )
        result_execution_contracts.require_execution_chain(
            result_metadata["executionChain"]
        )
    try:
        raw_visualization = strict_json.loads(row["visualization_json"])
        raw_request = strict_json.loads(row["request_json"])
        metrics = strict_json.loads(row["metrics_json"])
    except ValueError as exc:
        raise ValueError("Backtest index contains invalid stored JSON.") from exc
    if (
        not isinstance(raw_visualization, dict)
        or not isinstance(raw_request, dict)
        or not isinstance(metrics, dict)
    ):
        raise ValueError("Backtest index stored JSON has invalid types.")
    if include_data_keys:
        if "executionSnapshot" not in raw_request or not isinstance(
            raw_request["executionSnapshot"], dict
        ):
            raise ValueError(
                "Backtest index request is missing its execution snapshot."
            )
        result_execution_contracts.require_snapshot_match(
            result_metadata["executionChain"], raw_request["executionSnapshot"]
        )
    if result_metadata["schemaVersion"] != 8 or not result_metadata["hasCycles"]:
        raise ValueError(
            "This Result contract is archived and cannot be loaded by the current runtime."
        )
    visualization_contracts.require_spec(raw_visualization)
    backtest_result_views.require_backtest_index_identity(row)
    item = {
        "backtestId": row["backtest_id"],
        "pipelineId": row["pipeline_id"],
        "datasetId": row["dataset_id"],
        "name": row["name"],
        "status": row["status"],
        "runner": row["runner"],
        "createdAt": row["created_at"],
        "completedAt": row["completed_at"],
        "archivedAt": row["archived_at"],
        "archiveReason": row["archive_reason"],
        "request": raw_request,
        "metrics": metrics,
        "resultSchemaVersion": result_metadata["schemaVersion"],
        "visualizable": True,
        "visualizationIssue": "",
    }
    if include_visualization:
        item["visualization"] = visualization_contracts.require_spec(
            raw_visualization
        )
    if include_data_keys:
        item["dataKeys"] = result_metadata["dataKeys"]
        item["executionChain"] = result_metadata["executionChain"]
    return item


__all__ = (
    "archive_backtest",
    "catalog_commit_state",
    "commit_recovered_result_catalog",
    "count_backtests",
    "get_backtest_meta",
    "get_backtest_result_view",
    "list_backtests",
    "load_result_archive_evidence",
    "load_unindexed_result_archive",
    "reconcile_result_staging",
    "rename_backtest",
    "require_recovered_result_commit",
    "require_result_manifest_metadata",
    "result_catalog_exists",
    "save_result_metadata",
)
