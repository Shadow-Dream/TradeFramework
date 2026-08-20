"""Read models for immutable Backtest Result catalog entries."""

from engine.contracts import strict_json
from engine.contracts.digest import is_sha256_digest
import engine.contracts.result_execution as result_execution_contracts
from engine.contracts import visualization as visualization_contracts
from engine.core import resource_ids


BACKTEST_SUMMARY_SELECT = """
    SELECT
        b.backtest_id,
        b.pipeline_id,
        b.dataset_id,
        b.name,
        b.status,
        b.runner,
        b.created_at,
        b.completed_at,
        b.metrics_json,
        b.archived_at,
        b.archive_reason,
        metadata.schema_version AS indexed_result_schema_version,
        metadata.has_cycles AS indexed_result_has_cycles,
        metadata.content_digest AS indexed_result_content_digest,
        metadata.result_size AS indexed_result_size
    FROM backtests b
    LEFT JOIN backtest_result_metadata metadata
      ON metadata.backtest_id = b.backtest_id
"""

BACKTEST_RESULT_VIEW_SELECT = """
    SELECT
        b.backtest_id,
        b.pipeline_id,
        b.dataset_id,
        b.name,
        b.status,
        b.runner,
        b.created_at,
        b.completed_at,
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


def require_backtest_index_identity(row):
    for field in (
        "backtest_id",
        "pipeline_id",
        "dataset_id",
        "name",
        "status",
        "runner",
        "created_at",
        "completed_at",
        "archived_at",
        "archive_reason",
    ):
        if not isinstance(row[field], str):
            raise ValueError(f"Backtest index field '{field}' must be a string.")
    if (
        not resource_ids.is_resource_id(row["backtest_id"])
        or not row["pipeline_id"]
        or not row["dataset_id"]
        or not row["name"]
        or row["status"] not in {"completed", "archived"}
        or not row["runner"]
        or not row["created_at"]
        or not row["completed_at"]
    ):
        raise ValueError("Backtest index identity or lifecycle state is invalid.")
    if row["status"] == "completed" and (
        row["archived_at"] or row["archive_reason"]
    ):
        raise ValueError("Completed Backtest index contains archive state.")
    if row["status"] == "archived" and not row["archived_at"]:
        raise ValueError("Archived Backtest index is missing archivedAt.")


def _require_result_summary_index(row):
    required_fields = (
        "indexed_result_schema_version",
        "indexed_result_has_cycles",
        "indexed_result_content_digest",
        "indexed_result_size",
    )
    if any(
        field not in row.keys() or row[field] is None
        for field in required_fields
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


def backtest_summary(row):
    """Project one validated compact Result index row for list consumers."""

    _require_result_summary_index(row)
    require_backtest_index_identity(row)
    try:
        metrics = strict_json.loads(row["metrics_json"])
    except ValueError as exc:
        raise ValueError("Backtest index contains invalid stored JSON.") from exc
    if not isinstance(metrics, dict):
        raise ValueError("Backtest index stored JSON has invalid types.")
    return {
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
        "metrics": metrics,
        "resultSchemaVersion": row["indexed_result_schema_version"],
        "resultContentDigest": row["indexed_result_content_digest"],
        "resultSize": row["indexed_result_size"],
        "visualizable": True,
        "visualizationIssue": "",
    }


def backtest_result_view(row):
    """Project Result data needed by the UI without hidden execution evidence."""

    item = backtest_summary(row)
    try:
        visualization = strict_json.loads(row["visualization_json"])
        data_keys = strict_json.loads(row["indexed_result_data_keys_json"])
        execution_chain = strict_json.loads(
            row["indexed_result_execution_chain_json"]
        )
    except ValueError as exc:
        raise ValueError("Backtest Result view contains invalid stored JSON.") from exc
    if not isinstance(data_keys, dict) or not isinstance(execution_chain, dict):
        raise ValueError("Backtest Result view stored JSON has invalid types.")
    item["visualization"] = visualization_contracts.require_spec(visualization)
    item["dataKeys"] = data_keys
    execution_chain = result_execution_contracts.require_execution_chain(
        execution_chain
    )
    item["executionSummary"] = {
        "dataset": {
            "datasetId": execution_chain["dataset"]["datasetId"],
            "datasetVersionId": execution_chain["dataset"]["datasetVersionId"],
        },
        "sampler": {
            "samplerId": execution_chain["sampler"]["samplerId"],
            "version": execution_chain["sampler"]["version"],
        },
    }
    return item


__all__ = (
    "BACKTEST_RESULT_VIEW_SELECT",
    "BACKTEST_SUMMARY_SELECT",
    "backtest_result_view",
    "backtest_summary",
    "require_backtest_index_identity",
)
