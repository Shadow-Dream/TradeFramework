"""Application orchestration for Basic Workflow market snapshots.

This service owns only application state and composition.  It publishes an
ordinary Dataset Version and Pipeline Version, then crosses the canonical
prepared Backtest submission boundary supplied by the Engine service.
"""

from __future__ import annotations

import copy
import csv
import math
import re
import secrets
import tempfile
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

from application_protocols.basic_workflow import schemas
from builtin_implementations.analysis_presets import BASIC_WORKFLOW_ANALYSIS_ID
from builtin_implementations.environment_presets import (
    BASIC_WORKFLOW_ENVIRONMENT_ID,
)
from dataset_adapters import basic_workflow as basic_dataset
from dataset_adapters import basic_workflow_v3 as basic_dataset_v3
from engine.archive import version as version_archive
from engine.compiler import pipeline_manifest as pipeline_manifest_compiler
from engine.contracts import strict_json
from engine.contracts.exact_fields import require_exact_fields
from engine.contracts import visualization as visualization_contracts
from engine.core import clock as engine_clock
from engine.core import resource_ids
from engine.repository import control_state
from engine.repository import backtest_results as result_repository
from engine.repository import datasets as dataset_repository
from engine.repository import module_definitions
from engine.repository import samplers as sampler_repository
from engine.repository import sample_results as sample_result_repository
from engine.runtime import result_stream
from engine.service import analysis as analysis_service
from engine.service import environment as environment_service
from engine.service import pipelines as pipeline_service
from engine.service import result_projection as result_projection_service
from engine.service import sample_result_projection as sample_result_projection_service
from engine.service import sample_results as sample_result_service
from engine.service import sample_visualizations as sample_visualization_service
from engine.service import visualizations as visualization_service
from engine.service.backtest_submissions import prepare_backtest_submission

from .manifest import (
    PROFILE_ID,
    PROTOCOL_ID,
    PROTOCOL_VERSION,
    V3_PROFILE_ID,
    V3_PROTOCOL_VERSION,
)
from .market_data import (
    EODHD_ADJUSTMENT_POLICY,
    EODHD_DEMO_PROVIDER_ID,
    EODHD_REVISION_POLICY,
    default_provider_registry,
)
from .scaffolds import (
    MODULE_REQUIREMENTS,
    OHLCV_UNIVERSE_MODULE_ID,
    PRICE_UNIVERSE_MODULE_ID,
    build_pipeline_scaffold,
    module_requirements,
)


_STATE_SCHEMA_VERSION = 1
_MANAGED_PIPELINE_STATE_SCHEMA_VERSION = 2
_V3_PIPELINE_GENERATION = 3
_SNAPSHOT_ID = re.compile(r"^basic-market-catalog-[0-9a-f]{24}$")
_SNAPSHOT_JOB_ID = re.compile(r"^basic-snapshot-[0-9a-f]{24}$")
_SHA256_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_MARKET_LOCK = threading.RLock()
_SNAPSHOT_WORKER_LOCK = threading.RLock()
_SNAPSHOT_CACHE_LOCK = threading.RLock()
_SNAPSHOT_WORKERS = {}
_INTERACTIVE_SNAPSHOT_KEYS = set()
_SNAPSHOT_CACHE = {}
_SNAPSHOT_CACHE_LIMIT = 8
_CHART_CATALOG_CACHE = {}
_CHART_CATALOG_CACHE_LIMIT = 4
_VERIFIED_MATERIALIZATION_RESPONSES = set()
_VERIFIED_MATERIALIZATION_RESPONSE_LIMIT = 2048
_BASIC_WORKFLOW_SAMPLER_ID = "basic-price-map-sampler"
_BASIC_WORKFLOW_V3_SAMPLER_ID = "basic-ohlcv-map-sampler"
_V3_PROVIDER_ID = EODHD_DEMO_PROVIDER_ID
_CHART_SIGNAL_MODULE_IDS = frozenset({
    "basic-price-bar-selector",
    "basic-price-close-selector",
    "sma-indicator",
    "ema-indicator",
    "wma-indicator",
    "vwma-indicator",
    "bollinger-bands-indicator",
    "rsi-indicator",
    "macd-indicator",
    "atr-indicator",
    "stochastic-indicator",
    "obv-indicator",
    "roc-indicator",
    "cci-indicator",
    "williams-r-indicator",
    "dmi-indicator",
    "supertrend-indicator",
    "mfi-indicator",
    "parabolic-sar-indicator",
    "volume-indicator",
    "anchored-vwap-indicator",
    "ichimoku-indicator",
})
DEFAULT_WATCHLIST_SYMBOLS = (
    "AAPL",
    "MSFT",
    "NVDA",
    "AMZN",
    "META",
    "TSLA",
)

_INSTRUMENT_FIELDS = {
    "instrumentId",
    "symbol",
    "name",
    "exchange",
    "currency",
    "assetType",
    "availablePeriods",
}
_SNAPSHOT_FIELDS = {
    "schemaVersion",
    "snapshotId",
    "protocolId",
    "providerId",
    "asOf",
    "contentDigest",
    "instrumentCount",
    "instruments",
}
_MANAGED_PIPELINE_STATE_FIELDS = {
    "schemaVersion",
    "protocolId",
    "pipelines",
}
_MANAGED_PIPELINE_REFERENCE_FIELDS = {
    "period",
    "profile",
    "generation",
    "pipelineId",
    "version",
    "contentDigest",
}
_V1_PROFILED_MANAGED_PIPELINE_REFERENCE_FIELDS = (
    _MANAGED_PIPELINE_REFERENCE_FIELDS - {"generation"}
)
_V1_LEGACY_MANAGED_PIPELINE_REFERENCE_FIELDS = (
    _MANAGED_PIPELINE_REFERENCE_FIELDS - {"profile", "generation"}
)
_V1_LEGACY_GENERATED_MANAGED_PIPELINE_REFERENCE_FIELDS = (
    _MANAGED_PIPELINE_REFERENCE_FIELDS - {"profile"}
)
_BAR_SNAPSHOT_STATE_FIELDS = {
    "schemaVersion",
    "protocolId",
    "barSnapshots",
}
_SNAPSHOT_JOB_STATE_FIELDS = {
    "schemaVersion",
    "protocolId",
    "jobs",
}
_SNAPSHOT_JOB_FIELDS = {
    "jobId",
    "snapshotId",
    "providerId",
    "instrumentId",
    "period",
    "status",
    "attempts",
    "queuedAt",
    "startedAt",
    "completedAt",
    "error",
    "result",
}
_SNAPSHOT_JOB_STATUSES = {"queued", "running", "completed", "failed"}
_SNAPSHOT_JOB_LIMIT = 256
_CHART_CACHE_STATE_FIELDS = {
    "schemaVersion",
    "protocolId",
    "records",
}
_CHART_CACHE_RECORD_FIELDS = {
    "snapshotId",
    "providerId",
    "instrumentId",
    "period",
    "datasetId",
    "datasetVersionId",
    "sampleResultId",
    "resultContentDigest",
    "createdAt",
    "barSnapshot",
}
_LEGACY_CHART_CACHE_RECORD_FIELDS = _CHART_CACHE_RECORD_FIELDS - {"barSnapshot"}
_MATERIALIZATION_CACHE_STATE_FIELDS = {
    "schemaVersion",
    "protocolId",
    "materializations",
}
_MATERIALIZATION_CACHE_RECORD_FIELDS = {
    "sessionDigest",
    "providerId",
    "instrumentId",
    "period",
    "dataDigest",
    "response",
}
_OPEN_RESPONSE_FIELDS = {
    "accepted",
    "protocolId",
    "snapshotId",
    "instrument",
    "barSnapshot",
    "materialization",
    "prepared",
    "job",
}
_MATERIALIZATION_FIELDS = {
    "dataset",
    "pipeline",
    "sampler",
    "environment",
    "analysis",
    "backtestRequest",
    "visualizationSaveRequest",
}
_BAR_SNAPSHOT_PROJECTION_FIELDS = {
    "catalogSnapshotId",
    "providerId",
    "instrumentId",
    "period",
    "asOf",
    "firstTime",
    "lastTime",
    "barCount",
    "contentDigest",
    "datasetId",
    "datasetVersionId",
}
_BAR_DATASET_SOURCE_FIELDS = {
    "providerId",
    "catalogSnapshotId",
    "instrumentId",
    "symbol",
    "period",
    "asOf",
    "contentDigest",
}
_V3_PROVENANCE_FIELDS = {
    "retrievedAt",
    "rawSha256",
    "adjustmentPolicy",
    "revisionPolicy",
}
_BAR_DATASET_SOURCE_FIELDS_V3 = _BAR_DATASET_SOURCE_FIELDS | _V3_PROVENANCE_FIELDS
_BAR_DATASET_METADATA_FIELDS = {
    "catalogSnapshotId",
    "instrument",
    "bars",
}
_BAR_SUMMARY_FIELDS = {
    "providerId",
    "asOf",
    "period",
    "barCount",
    "firstTime",
    "lastTime",
    "contentDigest",
}
_BAR_SUMMARY_FIELDS_V3 = _BAR_SUMMARY_FIELDS | _V3_PROVENANCE_FIELDS
_DATASET_CONFORMANCE_FIELDS = {
    "protocolId",
    "protocolVersion",
    "profile",
    "periods",
    "fileCount",
    "rowCount",
    "firstTime",
    "lastTime",
}


def _state_root(config):
    root = (
        Path(config["controlRoot"])
        / "application-protocols"
        / "basic-workflow"
        / "market"
    )
    root.mkdir(parents=True, exist_ok=True)
    if root.is_symlink() or not root.is_dir():
        raise ValueError("Basic market application state root is invalid.")
    return root


def _read_json(path, *, missing=None):
    if not path.exists():
        return copy.deepcopy(missing)
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"Basic market state path is invalid: {path.name}")
    return strict_json.loads(path.read_text(encoding="utf-8"))


def _absolute_instant(value, label):
    if type(value) is not str or not value:
        raise ValueError(f"{label} must be a non-empty absolute ISO-8601 timestamp.")
    text = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(f"{label} must be an absolute ISO-8601 timestamp.") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{label} must include an absolute timezone.")
    return parsed.astimezone(timezone.utc)


def _canonical_instant(value):
    return value.isoformat().replace("+00:00", "Z")


def _empty_managed_pipeline_state():
    return {
        "schemaVersion": _MANAGED_PIPELINE_STATE_SCHEMA_VERSION,
        "protocolId": PROTOCOL_ID,
        "pipelines": {},
    }


def _managed_pipeline_state_path(config):
    return _state_root(config) / "managed-pipelines.json"


def _validate_managed_pipeline_state(value):
    require_exact_fields(
        value,
        allowed=_MANAGED_PIPELINE_STATE_FIELDS,
        required=_MANAGED_PIPELINE_STATE_FIELDS,
        label="Basic managed Pipeline state",
    )
    source_schema_version = value["schemaVersion"]
    if source_schema_version not in {
        1,
        _MANAGED_PIPELINE_STATE_SCHEMA_VERSION,
    }:
        raise ValueError(
            "Basic managed Pipeline state schemaVersion is unsupported."
        )
    if value["protocolId"] != PROTOCOL_ID:
        raise ValueError("Basic managed Pipeline state protocolId is invalid.")
    if type(value["pipelines"]) is not dict:
        raise ValueError("Basic managed Pipeline state pipelines must be an object.")
    normalized = _empty_managed_pipeline_state()
    for pipeline_key, reference in value["pipelines"].items():
        if (
            type(pipeline_key) is not str
            or re.fullmatch(r"[A-Za-z0-9_-]+", pipeline_key) is None
        ):
            raise ValueError("Basic managed Pipeline key is invalid.")
        if type(reference) is not dict:
            raise ValueError(
                f"Basic managed Pipeline reference '{pipeline_key}' must be an object."
            )
        reference_fields = set(reference)
        if reference_fields == _V1_LEGACY_MANAGED_PIPELINE_REFERENCE_FIELDS:
            reference = {
                **copy.deepcopy(reference),
                "profile": PROFILE_ID,
                "generation": 1,
            }
        elif reference_fields == _V1_PROFILED_MANAGED_PIPELINE_REFERENCE_FIELDS:
            reference = {**copy.deepcopy(reference), "generation": 1}
        elif reference_fields == _V1_LEGACY_GENERATED_MANAGED_PIPELINE_REFERENCE_FIELDS:
            reference = {**copy.deepcopy(reference), "profile": PROFILE_ID}
        require_exact_fields(
            reference,
            allowed=_MANAGED_PIPELINE_REFERENCE_FIELDS,
            required=_MANAGED_PIPELINE_REFERENCE_FIELDS,
            label=f"Basic managed Pipeline reference '{pipeline_key}'",
        )
        profile = reference["profile"]
        period = reference["period"]
        generation = reference["generation"]
        pipeline_id = reference["pipelineId"]
        version = reference["version"]
        digest = reference["contentDigest"]
        if pipeline_key != _managed_pipeline_key(
            period,
            profile,
            generation=generation,
        ):
            raise ValueError(
                f"Basic managed Pipeline reference '{pipeline_key}' identity is invalid."
            )
        if (
            isinstance(generation, bool)
            or not isinstance(generation, int)
            or generation < 1
            or (profile == PROFILE_ID and generation != 1)
            or (profile == V3_PROFILE_ID and generation > _V3_PIPELINE_GENERATION)
        ):
            raise ValueError(
                f"Basic managed Pipeline reference '{pipeline_key}' generation is invalid."
            )
        if (
            type(pipeline_id) is not str
            or not pipeline_id.startswith("pipe_")
            or not resource_ids.is_resource_id(pipeline_id)
        ):
            raise ValueError(
                f"Basic managed Pipeline reference '{pipeline_key}' pipelineId is invalid."
            )
        if (
            type(version) is not str
            or not version.isascii()
            or not version.isdecimal()
            or version != str(int(version))
            or int(version) < 1
        ):
            raise ValueError(
                f"Basic managed Pipeline reference '{pipeline_key}' version is invalid."
            )
        if type(digest) is not str or _SHA256_DIGEST.fullmatch(digest) is None:
            raise ValueError(
                f"Basic managed Pipeline reference '{pipeline_key}' contentDigest is invalid."
            )
        normalized["pipelines"][pipeline_key] = copy.deepcopy(reference)
    return normalized


def _load_managed_pipeline_state(config):
    value = _read_json(
        _managed_pipeline_state_path(config),
        missing=_empty_managed_pipeline_state(),
    )
    return _validate_managed_pipeline_state(value)


def _bar_snapshot_state_path(config):
    return _state_root(config) / "bar-snapshots.json"


def _empty_bar_snapshot_state():
    return {
        "schemaVersion": _STATE_SCHEMA_VERSION,
        "protocolId": PROTOCOL_ID,
        "barSnapshots": [],
    }


def _canonical_dataset_bar_snapshot(config, dataset_id, dataset_version_id):
    dataset = dataset_repository.get_dataset(config, dataset_id)
    versions = [
        value
        for value in dataset_repository.list_dataset_versions(config, dataset_id)
        if value["datasetVersionId"] == dataset_version_id
    ]
    if len(versions) != 1:
        raise ValueError(
            "Basic market bar snapshot Dataset Version is unavailable."
        )
    version = versions[0]
    manifest_dataset = version["manifest"]["dataset"]
    if (
        dataset["datasetId"] != dataset_id
        or version["datasetId"] != dataset_id
        or version["datasetVersionId"] != dataset_version_id
        or manifest_dataset.get("protocolId") != PROTOCOL_ID
    ):
        raise ValueError(
            "Basic market bar snapshot Dataset identity is incompatible."
        )
    source = manifest_dataset.get("source")
    if type(source) is not dict or source.get("type") != "basic-market-snapshot":
        raise ValueError(
            "Basic market bar snapshot Dataset source is incompatible."
        )
    details = source.get("details")
    is_v3 = type(details) is dict and details.get("providerId") == _V3_PROVIDER_ID
    require_exact_fields(
        details,
        allowed=(
            _BAR_DATASET_SOURCE_FIELDS_V3
            if is_v3
            else _BAR_DATASET_SOURCE_FIELDS
        ),
        required=(
            _BAR_DATASET_SOURCE_FIELDS_V3
            if is_v3
            else _BAR_DATASET_SOURCE_FIELDS
        ),
        label="Basic market bar snapshot Dataset source details",
    )
    metadata = manifest_dataset.get("metadata")
    if type(metadata) is not dict:
        raise ValueError("Basic market bar snapshot Dataset metadata is invalid.")
    market_snapshot = metadata.get("marketSnapshot")
    require_exact_fields(
        market_snapshot,
        allowed=_BAR_DATASET_METADATA_FIELDS,
        required=_BAR_DATASET_METADATA_FIELDS,
        label="Basic market bar snapshot Dataset metadata projection",
    )
    instrument = _validate_instrument(
        market_snapshot["instrument"],
        label="Basic market bar snapshot Dataset instrument",
    )
    bars = market_snapshot["bars"]
    require_exact_fields(
        bars,
        allowed=_BAR_SUMMARY_FIELDS_V3 if is_v3 else _BAR_SUMMARY_FIELDS,
        required=_BAR_SUMMARY_FIELDS_V3 if is_v3 else _BAR_SUMMARY_FIELDS,
        label="Basic market bar snapshot Dataset bar summary",
    )
    conformance = metadata.get("conformance")
    require_exact_fields(
        conformance,
        allowed=_DATASET_CONFORMANCE_FIELDS,
        required=_DATASET_CONFORMANCE_FIELDS,
        label="Basic market bar snapshot Dataset conformance",
    )
    period = details["period"]
    expected_periods = {
        period: {
            "instruments": [instrument["instrumentId"]],
            "rowCount": bars["barCount"],
        }
    }
    expected_version = V3_PROTOCOL_VERSION if is_v3 else PROTOCOL_VERSION
    expected_profile = V3_PROFILE_ID if is_v3 else PROFILE_ID
    capability = version.get("capabilities", {}).get("basicWorkflow")
    expected_descriptor = {
        "protocolId": PROTOCOL_ID,
        "protocolVersion": expected_version,
        "profile": expected_profile,
        "cashUnit": instrument["currency"],
        "quantityUnit": "share",
        "executionConvention": "prior-approved-intent-next-bar-open",
        "valuationConvention": "current-bar-close",
    }
    expected_capability = {
        "protocol": (
            basic_dataset_v3.CAPABILITY_PROTOCOL
            if is_v3
            else basic_dataset.CAPABILITY_PROTOCOL
        ),
        "descriptor": expected_descriptor,
    }
    provenance_matches = not is_v3 or all(
        bars[field] == details[field] for field in _V3_PROVENANCE_FIELDS
    )
    if (
        market_snapshot["catalogSnapshotId"] != details["catalogSnapshotId"]
        or instrument["instrumentId"] != details["instrumentId"]
        or instrument["symbol"] != details["symbol"]
        or bars["providerId"] != details["providerId"]
        or bars["period"] != period
        or bars["asOf"] != details["asOf"]
        or bars["contentDigest"] != details["contentDigest"]
        or not provenance_matches
        or capability != expected_capability
        or conformance["protocolId"] != PROTOCOL_ID
        or conformance["protocolVersion"] != expected_version
        or conformance["profile"] != expected_profile
        or conformance["periods"] != expected_periods
        or conformance["fileCount"] != 1
        or conformance["rowCount"] != bars["barCount"]
        or conformance["firstTime"] != bars["firstTime"]
        or conformance["lastTime"] != bars["lastTime"]
    ):
        raise ValueError(
            "Basic market bar snapshot Dataset lineage is inconsistent."
        )
    return {
        "catalogSnapshotId": details["catalogSnapshotId"],
        "providerId": details["providerId"],
        "instrumentId": details["instrumentId"],
        "period": period,
        "asOf": details["asOf"],
        "firstTime": bars["firstTime"],
        "lastTime": bars["lastTime"],
        "barCount": bars["barCount"],
        "contentDigest": details["contentDigest"],
        "datasetId": dataset_id,
        "datasetVersionId": dataset_version_id,
    }


def _validate_bar_snapshot_projection(config, value, *, label):
    require_exact_fields(
        value,
        allowed=_BAR_SNAPSHOT_PROJECTION_FIELDS,
        required=_BAR_SNAPSHOT_PROJECTION_FIELDS,
        label=label,
    )
    if type(value["catalogSnapshotId"]) is not str or not _SNAPSHOT_ID.fullmatch(
        value["catalogSnapshotId"]
    ):
        raise ValueError(f"{label}.catalogSnapshotId is invalid.")
    for field in ("providerId", "instrumentId", "period"):
        if (
            type(value[field]) is not str
            or not value[field]
            or value[field] != value[field].strip()
        ):
            raise ValueError(f"{label}.{field} must be a canonical string.")
    if re.fullmatch(r"[A-Za-z0-9_-]+", value["instrumentId"]) is None:
        raise ValueError(f"{label}.instrumentId is invalid.")
    if re.fullmatch(r"[A-Za-z0-9_-]+", value["period"]) is None:
        raise ValueError(f"{label}.period is invalid.")
    as_of = _absolute_instant(value["asOf"], f"{label}.asOf")
    first = _absolute_instant(value["firstTime"], f"{label}.firstTime")
    last = _absolute_instant(value["lastTime"], f"{label}.lastTime")
    if first > last:
        raise ValueError(f"{label} firstTime must not follow lastTime.")
    if as_of < last:
        raise ValueError(f"{label} asOf must not precede lastTime.")
    if (
        isinstance(value["barCount"], bool)
        or not isinstance(value["barCount"], int)
        or value["barCount"] < 1
    ):
        raise ValueError(f"{label}.barCount must be a positive integer.")
    if (
        type(value["contentDigest"]) is not str
        or _SHA256_DIGEST.fullmatch(value["contentDigest"]) is None
    ):
        raise ValueError(f"{label}.contentDigest is invalid.")
    dataset_id = value["datasetId"]
    if (
        type(dataset_id) is not str
        or resource_ids.normalize_resource_id(dataset_id) != dataset_id
    ):
        raise ValueError(f"{label}.datasetId is invalid.")
    dataset_version_id = value["datasetVersionId"]
    version_digest = (
        dataset_version_id[len(dataset_id) + 1 :]
        if type(dataset_version_id) is str
        and dataset_version_id.startswith(dataset_id + "@")
        else ""
    )
    if _SHA256_DIGEST.fullmatch(version_digest) is None:
        raise ValueError(f"{label}.datasetVersionId is invalid.")
    canonical = _canonical_dataset_bar_snapshot(
        config,
        dataset_id,
        dataset_version_id,
    )
    if canonical != value:
        raise ValueError(f"{label} does not match its canonical Dataset Version.")
    return copy.deepcopy(value)


def _validate_bar_snapshot_state(config, value):
    require_exact_fields(
        value,
        allowed=_BAR_SNAPSHOT_STATE_FIELDS,
        required=_BAR_SNAPSHOT_STATE_FIELDS,
        label="Basic market bar snapshot state",
    )
    if value["schemaVersion"] != _STATE_SCHEMA_VERSION:
        raise ValueError(
            "Basic market bar snapshot state schemaVersion is unsupported."
        )
    if value["protocolId"] != PROTOCOL_ID:
        raise ValueError("Basic market bar snapshot state protocolId is invalid.")
    if type(value["barSnapshots"]) is not list:
        raise ValueError("Basic market bar snapshot state must contain an array.")
    normalized = []
    keys = []
    for index, record in enumerate(value["barSnapshots"]):
        normalized.append(
            _validate_bar_snapshot_projection(
                config,
                record,
                label=f"Basic market barSnapshots[{index}]",
            )
        )
        keys.append((record["instrumentId"], record["period"]))
    if keys != sorted(keys) or len(set(keys)) != len(keys):
        raise ValueError(
            "Basic market bar snapshot state keys must be unique and sorted."
        )
    return {
        "schemaVersion": _STATE_SCHEMA_VERSION,
        "protocolId": PROTOCOL_ID,
        "barSnapshots": normalized,
    }


def _load_bar_snapshot_state(config):
    value = _read_json(
        _bar_snapshot_state_path(config),
        missing=_empty_bar_snapshot_state(),
    )
    return _validate_bar_snapshot_state(config, value)


def _project_bar_snapshots(snapshot, state):
    if snapshot is None:
        return []
    available = {
        (instrument["instrumentId"], period)
        for instrument in snapshot["instruments"]
        for period in instrument["availablePeriods"]
    }
    return [
        copy.deepcopy(record)
        for record in state["barSnapshots"]
        if (record["instrumentId"], record["period"]) in available
    ]


def _record_published_bar_snapshot(
    config,
    snapshot,
    instrument,
    summary,
    dataset,
):
    projection = _canonical_dataset_bar_snapshot(
        config,
        dataset["datasetId"],
        dataset["latestVersionId"],
    )
    expected = {
        "catalogSnapshotId": snapshot["snapshotId"],
        "providerId": snapshot["providerId"],
        "instrumentId": instrument["instrumentId"],
        "period": summary["period"],
        "asOf": summary["asOf"],
        "firstTime": summary["firstTime"],
        "lastTime": summary["lastTime"],
        "barCount": summary["barCount"],
        "contentDigest": summary["contentDigest"],
        "datasetId": dataset["datasetId"],
        "datasetVersionId": dataset["latestVersionId"],
    }
    if projection != expected:
        raise ValueError(
            "Basic market published Dataset does not match its validated bar snapshot."
        )
    key = (projection["instrumentId"], projection["period"])
    with _MARKET_LOCK, control_state.control_state_lock(config):
        state = _load_bar_snapshot_state(config)
        records = {
            (record["instrumentId"], record["period"]): record
            for record in state["barSnapshots"]
        }
        records[key] = projection
        updated = _validate_bar_snapshot_state(
            config,
            {
                "schemaVersion": _STATE_SCHEMA_VERSION,
                "protocolId": PROTOCOL_ID,
                "barSnapshots": [records[value] for value in sorted(records)],
            },
        )
        control_state.atomic_write_json(_bar_snapshot_state_path(config), updated)
    return copy.deepcopy(projection)


def _saved_bar_materialization(config, snapshot, instrument, period):
    with _MARKET_LOCK, control_state.control_state_lock(config):
        matches = [
            record
            for record in _load_bar_snapshot_state(config)["barSnapshots"]
            if record["catalogSnapshotId"] == snapshot["snapshotId"]
            and record["providerId"] == snapshot["providerId"]
            and record["instrumentId"] == instrument["instrumentId"]
            and record["period"] == period
        ]
    if len(matches) > 1:
        raise ValueError("Basic saved bar snapshot identity is ambiguous.")
    if not matches:
        return None
    projection = _validate_bar_snapshot_projection(
        config,
        matches[0],
        label="Basic saved bar snapshot",
    )
    dataset = dataset_repository.get_dataset(config, projection["datasetId"])
    if dataset.get("latestVersionId") != projection["datasetVersionId"]:
        raise ValueError("Basic saved bar snapshot is not the Dataset current version.")
    versions = [
        value
        for value in dataset_repository.list_dataset_versions(
            config,
            projection["datasetId"],
        )
        if value["datasetVersionId"] == projection["datasetVersionId"]
    ]
    if len(versions) != 1:
        raise ValueError("Basic saved bar snapshot Dataset Version is unavailable.")
    summary = copy.deepcopy(
        versions[0]["manifest"]["dataset"]["metadata"]["marketSnapshot"]["bars"]
    )
    is_v3 = projection["providerId"] == _V3_PROVIDER_ID
    require_exact_fields(
        summary,
        allowed=_BAR_SUMMARY_FIELDS_V3 if is_v3 else _BAR_SUMMARY_FIELDS,
        required=_BAR_SUMMARY_FIELDS_V3 if is_v3 else _BAR_SUMMARY_FIELDS,
        label="Basic saved bar snapshot summary",
    )
    if (
        summary["providerId"] != projection["providerId"]
        or summary["period"] != projection["period"]
        or summary["contentDigest"] != projection["contentDigest"]
    ):
        raise ValueError("Basic saved bar snapshot summary identity changed.")
    return {
        "dataset": dataset,
        "summary": summary,
        "dataDigest": projection["contentDigest"],
    }


def _snapshot_job_state_path(config):
    return _state_root(config) / "snapshot-jobs.json"


def _chart_cache_state_path(config):
    return _state_root(config) / "chart-cache.json"


def _empty_chart_cache_state():
    return {
        "schemaVersion": _STATE_SCHEMA_VERSION,
        "protocolId": PROTOCOL_ID,
        "records": [],
    }


def _validate_chart_cache_state(config, value, *, verify_archives=True):
    require_exact_fields(
        value,
        allowed=_CHART_CACHE_STATE_FIELDS,
        required=_CHART_CACHE_STATE_FIELDS,
        label="Basic chart cache state",
    )
    if (
        value["schemaVersion"] != _STATE_SCHEMA_VERSION
        or value["protocolId"] != PROTOCOL_ID
        or type(value["records"]) is not list
    ):
        raise ValueError("Basic chart cache state is invalid.")
    records = []
    identities = []
    for index, record in enumerate(value["records"]):
        label = f"Basic chart cache records[{index}]"
        require_exact_fields(
            record,
            allowed=_CHART_CACHE_RECORD_FIELDS,
            required=_LEGACY_CHART_CACHE_RECORD_FIELDS,
            label=label,
        )
        for field in (
            "snapshotId", "providerId", "instrumentId", "period", "datasetId",
            "datasetVersionId", "sampleResultId", "resultContentDigest", "createdAt",
        ):
            if type(record[field]) is not str or not record[field]:
                raise ValueError(f"{label}.{field} is required.")
        if (
            _SNAPSHOT_ID.fullmatch(record["snapshotId"]) is None
            or _SHA256_DIGEST.fullmatch(record["sampleResultId"]) is None
            or _SHA256_DIGEST.fullmatch(record["resultContentDigest"]) is None
        ):
            raise ValueError(f"{label} identity is invalid.")
        _absolute_instant(record["createdAt"], f"{label}.createdAt")
        if verify_archives:
            view = sample_result_repository.sample_result_view(
                config,
                record["sampleResultId"],
            )
            _verify_chart_cache_result(record, view, label=label)
        normalized_record = copy.deepcopy(record)
        if "barSnapshot" in record:
            is_v3 = record["providerId"] == _V3_PROVIDER_ID
            summary = require_exact_fields(
                record["barSnapshot"],
                allowed=_BAR_SUMMARY_FIELDS_V3 if is_v3 else _BAR_SUMMARY_FIELDS,
                required=_BAR_SUMMARY_FIELDS_V3 if is_v3 else _BAR_SUMMARY_FIELDS,
                label=f"{label}.barSnapshot",
            )
            if (
                summary["providerId"] != record["providerId"]
                or summary["period"] != record["period"]
                or _SHA256_DIGEST.fullmatch(summary["contentDigest"]) is None
                or isinstance(summary["barCount"], bool)
                or not isinstance(summary["barCount"], int)
                or summary["barCount"] < 1
            ):
                raise ValueError(f"{label} bar snapshot identity is invalid.")
            for field in ("asOf", "firstTime", "lastTime"):
                _absolute_instant(summary[field], f"{label}.barSnapshot.{field}")
            normalized_record["barSnapshot"] = copy.deepcopy(summary)
        records.append(normalized_record)
        identities.append((
            record["snapshotId"], record["providerId"],
            record["instrumentId"], record["period"],
        ))
    if len(set(identities)) != len(identities):
        raise ValueError("Basic chart cache records must have unique identities.")
    return {
        "schemaVersion": _STATE_SCHEMA_VERSION,
        "protocolId": PROTOCOL_ID,
        "records": sorted(
            records,
            key=lambda item: (
                item["snapshotId"], item["providerId"],
                item["instrumentId"], item["period"],
            ),
        ),
    }


def _load_chart_cache_state(config, *, verify_archives=True):
    return _validate_chart_cache_state(
        config,
        _read_json(_chart_cache_state_path(config), missing=_empty_chart_cache_state()),
        verify_archives=verify_archives,
    )


def _verify_chart_cache_result(record, view, *, label):
    if (
        view["sampleResultId"] != record["sampleResultId"]
        or view["datasetId"] != record["datasetId"]
        or view["datasetVersionId"] != record["datasetVersionId"]
        or view["resultContentDigest"] != record["resultContentDigest"]
    ):
        raise ValueError(f"{label} immutable Sample Result identity changed.")


def _record_chart_cache(config, snapshot, instrument, period, dataset, view, summary):
    record = {
        "snapshotId": snapshot["snapshotId"],
        "providerId": snapshot["providerId"],
        "instrumentId": instrument["instrumentId"],
        "period": period,
        "datasetId": dataset["datasetId"],
        "datasetVersionId": dataset["latestVersionId"],
        "sampleResultId": view["sampleResultId"],
        "resultContentDigest": view["resultContentDigest"],
        "createdAt": engine_clock.utc_now(),
        "barSnapshot": copy.deepcopy(summary),
    }
    _verify_chart_cache_result(record, view, label="Basic chart cache record")
    with _MARKET_LOCK, control_state.control_state_lock(config):
        state = _load_chart_cache_state(config, verify_archives=False)
        identity = (
            snapshot["snapshotId"], snapshot["providerId"],
            instrument["instrumentId"], period,
        )
        records = [
            item for item in state["records"]
            if (
                item["snapshotId"], item["providerId"],
                item["instrumentId"], item["period"],
            ) != identity
        ]
        records.append(record)
        updated = _validate_chart_cache_state(
            config,
            {
                "schemaVersion": _STATE_SCHEMA_VERSION,
                "protocolId": PROTOCOL_ID,
                "records": records,
            },
            verify_archives=False,
        )
        control_state.atomic_write_json(_chart_cache_state_path(config), updated)
    return copy.deepcopy(record)


def _saved_chart_cache(config, snapshot, instrument, period):
    with _MARKET_LOCK, control_state.control_state_lock(config):
        matches = [
            record
            for record in _load_chart_cache_state(
                config,
                verify_archives=False,
            )["records"]
            if record["snapshotId"] == snapshot["snapshotId"]
            and record["providerId"] == snapshot["providerId"]
            and record["instrumentId"] == instrument["instrumentId"]
            and record["period"] == period
        ]
    if len(matches) > 1:
        raise ValueError("Basic chart cache identity is ambiguous.")
    if not matches:
        return None
    record = copy.deepcopy(matches[0])
    view = sample_result_repository.sample_result_view(config, record["sampleResultId"])
    _verify_chart_cache_result(record, view, label="Basic chart cache record")
    return record, view


def _empty_snapshot_job_state():
    return {
        "schemaVersion": _STATE_SCHEMA_VERSION,
        "protocolId": PROTOCOL_ID,
        "jobs": [],
    }


def _validate_snapshot_job_state(config, value):
    require_exact_fields(
        value,
        allowed=_SNAPSHOT_JOB_STATE_FIELDS,
        required=_SNAPSHOT_JOB_STATE_FIELDS,
        label="Basic snapshot job state",
    )
    if (
        value["schemaVersion"] != _STATE_SCHEMA_VERSION
        or value["protocolId"] != PROTOCOL_ID
        or type(value["jobs"]) is not list
    ):
        raise ValueError("Basic snapshot job state is invalid.")
    normalized = []
    ordering = []
    for index, job in enumerate(value["jobs"]):
        label = f"Basic snapshot jobs[{index}]"
        require_exact_fields(
            job,
            allowed=_SNAPSHOT_JOB_FIELDS,
            required=_SNAPSHOT_JOB_FIELDS,
            label=label,
        )
        if (
            type(job["jobId"]) is not str
            or _SNAPSHOT_JOB_ID.fullmatch(job["jobId"]) is None
            or type(job["snapshotId"]) is not str
            or _SNAPSHOT_ID.fullmatch(job["snapshotId"]) is None
            or type(job["providerId"]) is not str
            or not job["providerId"]
            or type(job["instrumentId"]) is not str
            or not job["instrumentId"]
            or type(job["period"]) is not str
            or not job["period"]
            or job["status"] not in _SNAPSHOT_JOB_STATUSES
            or isinstance(job["attempts"], bool)
            or not isinstance(job["attempts"], int)
            or job["attempts"] < 0
            or type(job["error"]) is not str
        ):
            raise ValueError(f"{label} identity or status is invalid.")
        _absolute_instant(job["queuedAt"], f"{label}.queuedAt")
        for field in ("startedAt", "completedAt"):
            if type(job[field]) is not str:
                raise ValueError(f"{label}.{field} must be a string.")
            if job[field]:
                _absolute_instant(job[field], f"{label}.{field}")
        if job["status"] == "queued" and (
            job["startedAt"] or job["completedAt"] or job["result"] is not None
        ):
            raise ValueError(f"{label} queued state is invalid.")
        if job["status"] == "running" and (
            not job["startedAt"] or job["completedAt"] or job["result"] is not None
        ):
            raise ValueError(f"{label} running state is invalid.")
        if job["status"] in {"completed", "failed"} and not job["completedAt"]:
            raise ValueError(f"{label} terminal state requires completedAt.")
        if job["status"] == "completed":
            if job["error"]:
                raise ValueError(f"{label} completed state cannot contain an error.")
            result = _validate_bar_snapshot_projection(
                config,
                job["result"],
                label=f"{label}.result",
            )
        else:
            if job["result"] is not None:
                raise ValueError(f"{label} non-completed state cannot contain a result.")
            result = None
        if job["status"] == "failed" and not job["error"]:
            raise ValueError(f"{label} failed state requires an error.")
        normalized.append({**copy.deepcopy(job), "result": result})
        ordering.append((job["queuedAt"], job["jobId"]))
    if ordering != sorted(ordering) or len({job["jobId"] for job in normalized}) != len(normalized):
        raise ValueError("Basic snapshot jobs must be unique and chronologically sorted.")
    return {
        "schemaVersion": _STATE_SCHEMA_VERSION,
        "protocolId": PROTOCOL_ID,
        "jobs": normalized,
    }


def _load_snapshot_job_state(config):
    return _validate_snapshot_job_state(
        config,
        _read_json(
            _snapshot_job_state_path(config),
            missing=_empty_snapshot_job_state(),
        ),
    )


def _write_snapshot_job_state(config, jobs):
    jobs = sorted(jobs, key=lambda job: (job["queuedAt"], job["jobId"]))
    if len(jobs) > _SNAPSHOT_JOB_LIMIT:
        removable = [
            job for job in jobs if job["status"] in {"completed", "failed"}
        ]
        remove_ids = {
            job["jobId"]
            for job in removable[: max(0, len(jobs) - _SNAPSHOT_JOB_LIMIT)]
        }
        jobs = [job for job in jobs if job["jobId"] not in remove_ids]
    state = _validate_snapshot_job_state(
        config,
        {
            "schemaVersion": _STATE_SCHEMA_VERSION,
            "protocolId": PROTOCOL_ID,
            "jobs": jobs,
        },
    )
    control_state.atomic_write_json(_snapshot_job_state_path(config), state)
    return state


def _enqueue_snapshot_jobs(config, snapshot, instrument_ids):
    by_id = {instrument["instrumentId"]: instrument for instrument in snapshot["instruments"]}
    now = _absolute_instant(engine_clock.utc_now(), "Basic snapshot queue time")
    created = []
    with _MARKET_LOCK, control_state.control_state_lock(config):
        state = _load_snapshot_job_state(config)
        active = {
            (job["snapshotId"], job["instrumentId"], job["period"])
            for job in state["jobs"]
            if job["status"] in {"queued", "running"}
        }
        jobs = list(state["jobs"])
        for instrument_id in instrument_ids:
            instrument = by_id[instrument_id]
            if "day" not in instrument["availablePeriods"]:
                continue
            key = (snapshot["snapshotId"], instrument_id, "day")
            if key in active:
                continue
            job = {
                "jobId": f"basic-snapshot-{secrets.token_hex(12)}",
                "snapshotId": snapshot["snapshotId"],
                "providerId": snapshot["providerId"],
                "instrumentId": instrument_id,
                "period": "day",
                "status": "queued",
                "attempts": 0,
                "queuedAt": _canonical_instant(
                    now + timedelta(microseconds=len(created))
                ),
                "startedAt": "",
                "completedAt": "",
                "error": "",
                "result": None,
            }
            jobs.append(job)
            created.append(copy.deepcopy(job))
            active.add(key)
        _write_snapshot_job_state(config, jobs)
    return created


def _project_snapshot_jobs(snapshot, state):
    if snapshot is None:
        return []
    available = {
        (instrument["instrumentId"], period)
        for instrument in snapshot["instruments"]
        for period in instrument["availablePeriods"]
    }
    return [
        copy.deepcopy(job)
        for job in state["jobs"]
        if (job["instrumentId"], job["period"]) in available
    ]


def _recover_snapshot_jobs(config):
    with _MARKET_LOCK, control_state.control_state_lock(config):
        state = _load_snapshot_job_state(config)
        changed = False
        jobs = []
        for job in state["jobs"]:
            record = copy.deepcopy(job)
            if record["status"] == "running":
                record.update({
                    "status": "queued",
                    "startedAt": "",
                    "completedAt": "",
                    "error": "",
                    "result": None,
                })
                changed = True
            jobs.append(record)
        if changed:
            _write_snapshot_job_state(config, jobs)


def _claim_snapshot_job(config):
    with _MARKET_LOCK, control_state.control_state_lock(config):
        state = _load_snapshot_job_state(config)
        queued_jobs = [job for job in state["jobs"] if job["status"] == "queued"]
        queued = min(
            queued_jobs,
            key=lambda job: (
                0 if _snapshot_queue_key(
                    config,
                    job["snapshotId"],
                    job["instrumentId"],
                    job["period"],
                ) in _INTERACTIVE_SNAPSHOT_KEYS else 1,
                job["queuedAt"],
                job["jobId"],
            ),
            default=None,
        )
        if queued is None:
            return None
        jobs = []
        claimed = None
        for job in state["jobs"]:
            record = copy.deepcopy(job)
            if job["jobId"] == queued["jobId"]:
                record.update({
                    "status": "running",
                    "attempts": record["attempts"] + 1,
                    "startedAt": engine_clock.utc_now(),
                    "completedAt": "",
                    "error": "",
                    "result": None,
                })
                claimed = copy.deepcopy(record)
                _INTERACTIVE_SNAPSHOT_KEYS.discard(_snapshot_queue_key(
                    config,
                    record["snapshotId"],
                    record["instrumentId"],
                    record["period"],
                ))
            jobs.append(record)
        _write_snapshot_job_state(config, jobs)
        return claimed


def _snapshot_queue_key(config, snapshot_id, instrument_id, period):
    return (
        str(_state_root(config).resolve()),
        snapshot_id,
        instrument_id,
        period,
    )


def _promote_snapshot_job(config, snapshot, instrument, period):
    """Promote or enqueue the one chart-cache job selected by an interactive open."""

    identity = (snapshot["snapshotId"], instrument["instrumentId"], period)
    queue_key = _snapshot_queue_key(config, *identity)
    with _MARKET_LOCK, control_state.control_state_lock(config):
        state = _load_snapshot_job_state(config)
        active = [
            copy.deepcopy(job) for job in state["jobs"]
            if (job["snapshotId"], job["instrumentId"], job["period"]) == identity
            and job["status"] in {"queued", "running"}
        ]
        if len(active) > 1:
            raise ValueError("Basic chart cache active job identity is ambiguous.")
        if active:
            if active[0]["status"] == "queued":
                _INTERACTIVE_SNAPSHOT_KEYS.add(queue_key)
            return active[0]
    created = _enqueue_snapshot_jobs(config, snapshot, [instrument["instrumentId"]])
    if len(created) != 1:
        raise RuntimeError("Basic chart cache job could not be enqueued.")
    with _MARKET_LOCK:
        _INTERACTIVE_SNAPSHOT_KEYS.add(queue_key)
    return created[0]


def _finish_snapshot_job(config, job_id, *, result=None, error=""):
    with _MARKET_LOCK, control_state.control_state_lock(config):
        state = _load_snapshot_job_state(config)
        matches = [job for job in state["jobs"] if job["jobId"] == job_id]
        if len(matches) != 1 or matches[0]["status"] != "running":
            raise ValueError("Basic snapshot running job identity is unavailable.")
        jobs = []
        for job in state["jobs"]:
            record = copy.deepcopy(job)
            if job["jobId"] == job_id:
                record.update({
                    "status": "completed" if result is not None else "failed",
                    "completedAt": engine_clock.utc_now(),
                    "error": "" if result is not None else error,
                    "result": copy.deepcopy(result),
                })
            jobs.append(record)
        _write_snapshot_job_state(config, jobs)


def _sample_result_request(config, snapshot, dataset, period):
    is_v3 = snapshot["providerId"] == _V3_PROVIDER_ID
    sampler_id = (
        _BASIC_WORKFLOW_V3_SAMPLER_ID if is_v3 else _BASIC_WORKFLOW_SAMPLER_ID
    )
    sampler_output_schema = (
        schemas.OHLCV_SAMPLER_OUTPUT_SCHEMA if is_v3 else schemas.SAMPLER_OUTPUT_SCHEMA
    )
    sampler = _latest_exact(
        sampler_repository.list_samplers(config),
        "samplerId",
        sampler_id,
        "Sampler",
        predicate=lambda value: value.get("outputSchema") == sampler_output_schema,
    )
    return {
        "datasetId": dataset["datasetId"],
        "datasetVersionId": dataset["latestVersionId"],
        "sampler": {
            "samplerId": sampler["samplerId"],
            "version": sampler["version"],
            "parameters": {"decisionPeriod": period},
        },
    }


def _capture_snapshot_job(config, job, providers):
    with _MARKET_LOCK, control_state.control_state_lock(config):
        snapshot = _load_snapshot(config, job["snapshotId"])
    if snapshot["providerId"] != job["providerId"]:
        raise ValueError("Basic snapshot job provider identity changed.")
    instruments = {
        instrument["instrumentId"]: instrument
        for instrument in snapshot["instruments"]
    }
    instrument = instruments.get(job["instrumentId"])
    if instrument is None or job["period"] not in instrument["availablePeriods"]:
        raise ValueError("Basic snapshot job instrument is unavailable.")
    provider = providers.get(job["providerId"]) if type(providers) is dict else None
    if provider is None or getattr(provider, "provider_id", None) != job["providerId"]:
        raise ValueError("Basic snapshot job provider is not installed.")
    published = _saved_bar_materialization(
        config,
        snapshot,
        instrument,
        job["period"],
    )
    if published is None:
        evidence, summary = _bar_snapshot(
            job["providerId"],
            instrument,
            provider.download_bars(copy.deepcopy(instrument), job["period"]),
        )
        dataset = _publish_dataset(config, snapshot, instrument, evidence, summary)
        projection = _record_published_bar_snapshot(
            config,
            snapshot,
            instrument,
            summary,
            dataset,
        )
    else:
        dataset = published["dataset"]
        summary = published["summary"]
        projection = _validate_bar_snapshot_projection(
            config,
            {
                "catalogSnapshotId": snapshot["snapshotId"],
                "providerId": snapshot["providerId"],
                "instrumentId": instrument["instrumentId"],
                "period": job["period"],
                "asOf": summary["asOf"],
                "firstTime": summary["firstTime"],
                "lastTime": summary["lastTime"],
                "barCount": summary["barCount"],
                "contentDigest": summary["contentDigest"],
                "datasetId": dataset["datasetId"],
                "datasetVersionId": dataset["latestVersionId"],
            },
            label="Basic reused bar snapshot",
        )
    materialized = sample_result_service.materialize_sample_result(
        config,
        _sample_result_request(config, snapshot, dataset, job["period"]),
    )
    sample_result_id = materialized["view"]["sampleResultId"]
    existing_visualizations = sample_visualization_service.list_sample_visualizations(
        config,
        sample_result_id,
    )
    if not existing_visualizations:
        sample_visualization_service.save_sample_visualization(
            config,
            _sample_visualization_save_request(
                dataset["datasetId"],
                sample_result_id,
                instrument,
                job["period"],
            ),
        )
    project_sample_result_cached(
        config,
        {
            "sampleResultId": sample_result_id,
            "paths": [
                f"cycles.data.price.{job['period']}.{instrument['instrumentId']}"
            ],
            "temporaryModules": [],
        },
        _require_registered=False,
    )
    _record_chart_cache(
        config,
        snapshot,
        instrument,
        job["period"],
        dataset,
        materialized["view"],
        summary,
    )
    return projection


def run_snapshot_jobs(config, *, providers=None, limit=None):
    """Drain queued watchlist snapshots; exposed for deterministic service tests."""

    if limit is not None and (
        isinstance(limit, bool) or not isinstance(limit, int) or limit < 1
    ):
        raise ValueError("Basic snapshot job limit must be a positive integer.")
    providers = default_provider_registry() if providers is None else providers
    completed = 0
    while limit is None or completed < limit:
        job = _claim_snapshot_job(config)
        if job is None:
            break
        try:
            result = _capture_snapshot_job(config, job, providers)
        except Exception as exc:  # background boundary stores only a sanitized diagnostic
            _finish_snapshot_job(
                config,
                job["jobId"],
                error=f"{type(exc).__name__}: snapshot acquisition failed",
            )
        else:
            _finish_snapshot_job(config, job["jobId"], result=result)
        completed += 1
    return completed


def _snapshot_worker(config, wake, providers):
    _recover_snapshot_jobs(config)
    while True:
        wake.clear()
        run_snapshot_jobs(config, providers=providers)
        if not wake.wait(5.0):
            return


def _enqueue_missing_watchlist_snapshots(config):
    with _MARKET_LOCK, control_state.control_state_lock(config):
        snapshot = _current_snapshot(config)
        if snapshot is None:
            return []
        watched = _watchlist_ids(config, snapshot)
        saved = {
            (record["instrumentId"], record["period"])
            for record in _load_chart_cache_state(
                config,
                verify_archives=False,
            )["records"]
            if record["snapshotId"] == snapshot["snapshotId"]
            and record["providerId"] == snapshot["providerId"]
        }
        attempted = {
            (job["instrumentId"], job["period"])
            for job in _load_snapshot_job_state(config)["jobs"]
            if job["snapshotId"] == snapshot["snapshotId"]
            and job["providerId"] == snapshot["providerId"]
            and job["status"] in {"queued", "running"}
        }
        missing = [
            instrument_id
            for instrument_id in watched
            if (instrument_id, "day") not in saved
            and (instrument_id, "day") not in attempted
        ]
    return _enqueue_snapshot_jobs(config, snapshot, missing)


def ensure_snapshot_worker(config, *, providers=None, enqueue_watchlist=True):
    """Start or wake one daemon worker for this Basic application state root."""

    if enqueue_watchlist:
        _enqueue_missing_watchlist_snapshots(config)
    providers = default_provider_registry() if providers is None else providers
    if type(providers) is not dict:
        raise TypeError("Basic snapshot worker providers must be an exact registry.")
    with _MARKET_LOCK, control_state.control_state_lock(config):
        if not any(
            job["status"] == "queued"
            for job in _load_snapshot_job_state(config)["jobs"]
        ):
            return
    key = str(_state_root(config).resolve())
    with _SNAPSHOT_WORKER_LOCK:
        worker = _SNAPSHOT_WORKERS.get(key)
        if worker is None or not worker["thread"].is_alive():
            wake = threading.Event()
            thread = threading.Thread(
                target=_snapshot_worker,
                args=(copy.deepcopy(config), wake, providers),
                name="basic-watchlist-snapshots",
                daemon=True,
            )
            worker = {"thread": thread, "wake": wake}
            _SNAPSHOT_WORKERS[key] = worker
            thread.start()
        worker["wake"].set()


def _stable_bar_data_digest(evidence):
    material = {
        "providerId": evidence["providerId"],
        "instrument": copy.deepcopy(evidence["instrument"]),
        "period": evidence["period"],
        "bars": copy.deepcopy(evidence["bars"]),
    }
    if evidence["providerId"] == _V3_PROVIDER_ID:
        material.update({
            "adjustmentPolicy": copy.deepcopy(evidence["adjustmentPolicy"]),
            "revisionPolicy": copy.deepcopy(evidence["revisionPolicy"]),
        })
    return version_archive.content_digest(material)


def _owner_cache_digest(owner_identity):
    if type(owner_identity) is not str or not owner_identity:
        raise ValueError("Basic materialization cache owner identity is invalid.")
    return version_archive.content_digest({"ownerIdentity": owner_identity})


def _materialization_cache_state_path(config):
    return _state_root(config) / "materializations.json"


def _materialization_validation_key(config, response):
    return (
        str(Path(config["controlRoot"]).resolve()),
        version_archive.content_digest({"cachedOpenResponse": response}),
    )


def _remember_verified_materialization(config, response):
    key = _materialization_validation_key(config, response)
    with _MARKET_LOCK:
        if len(_VERIFIED_MATERIALIZATION_RESPONSES) >= (
            _VERIFIED_MATERIALIZATION_RESPONSE_LIMIT
        ):
            _VERIFIED_MATERIALIZATION_RESPONSES.clear()
        _VERIFIED_MATERIALIZATION_RESPONSES.add(key)
    return key


def _empty_materialization_cache_state():
    return {
        "schemaVersion": _STATE_SCHEMA_VERSION,
        "protocolId": PROTOCOL_ID,
        "materializations": [],
    }


def _require_cached_resource_ref(value, *, label, identity_field, digest=False):
    fields = {identity_field, "version", "protocolId"}
    if digest:
        fields.add("contentDigest")
    require_exact_fields(
        value,
        allowed=fields,
        required=fields,
        label=label,
    )
    if (
        type(value[identity_field]) is not str
        or not value[identity_field]
        or type(value["version"]) is not str
        or not value["version"].isdecimal()
        or value["version"] != str(int(value["version"]))
        or int(value["version"]) < 1
        or value["protocolId"] != PROTOCOL_ID
    ):
        raise ValueError(f"{label} identity is invalid.")
    if digest and (
        type(value["contentDigest"]) is not str
        or _SHA256_DIGEST.fullmatch(value["contentDigest"]) is None
    ):
        raise ValueError(f"{label} contentDigest is invalid.")
    return value


def _require_installed_cached_ref(records, reference, identity_field, label):
    matches = [
        value
        for value in records
        if value.get(identity_field) == reference[identity_field]
        and value.get("version") == reference["version"]
        and value.get("protocolId") == PROTOCOL_ID
    ]
    if len(matches) != 1:
        raise ValueError(f"Basic cached {label} exact version is unavailable.")


def _validate_cached_open_response(config, value, *, label):
    require_exact_fields(
        value,
        allowed=_OPEN_RESPONSE_FIELDS,
        required=_OPEN_RESPONSE_FIELDS,
        label=label,
    )
    if value["accepted"] is not True or value["protocolId"] != PROTOCOL_ID:
        raise ValueError(f"{label} protocol identity is invalid.")
    if type(value["snapshotId"]) is not str or _SNAPSHOT_ID.fullmatch(
        value["snapshotId"]
    ) is None:
        raise ValueError(f"{label}.snapshotId is invalid.")
    instrument = _validate_instrument(
        value["instrument"],
        label=f"{label}.instrument",
    )
    bars = value["barSnapshot"]
    is_v3 = type(bars) is dict and bars.get("providerId") == _V3_PROVIDER_ID
    require_exact_fields(
        bars,
        allowed=_BAR_SUMMARY_FIELDS_V3 if is_v3 else _BAR_SUMMARY_FIELDS,
        required=_BAR_SUMMARY_FIELDS_V3 if is_v3 else _BAR_SUMMARY_FIELDS,
        label=f"{label}.barSnapshot",
    )
    for field in ("asOf", "firstTime", "lastTime"):
        _absolute_instant(bars[field], f"{label}.barSnapshot.{field}")
    if (
        type(bars["providerId"]) is not str
        or bars["period"] not in instrument["availablePeriods"]
        or isinstance(bars["barCount"], bool)
        or not isinstance(bars["barCount"], int)
        or bars["barCount"] < 1
        or type(bars["contentDigest"]) is not str
        or _SHA256_DIGEST.fullmatch(bars["contentDigest"]) is None
    ):
        raise ValueError(f"{label}.barSnapshot identity is invalid.")
    materialization = value["materialization"]
    require_exact_fields(
        materialization,
        allowed=_MATERIALIZATION_FIELDS,
        required=_MATERIALIZATION_FIELDS,
        label=f"{label}.materialization",
    )
    dataset = materialization["dataset"]
    require_exact_fields(
        dataset,
        allowed={"datasetId", "datasetVersionId", "protocolId"},
        required={"datasetId", "datasetVersionId", "protocolId"},
        label=f"{label}.materialization.dataset",
    )
    if dataset["protocolId"] != PROTOCOL_ID:
        raise ValueError(f"{label} cached Dataset protocolId is invalid.")
    canonical = _canonical_dataset_bar_snapshot(
        config,
        dataset["datasetId"],
        dataset["datasetVersionId"],
    )
    if (
        canonical["catalogSnapshotId"] != value["snapshotId"]
        or canonical["providerId"] != bars["providerId"]
        or canonical["instrumentId"] != instrument["instrumentId"]
        or canonical["period"] != bars["period"]
        or canonical["asOf"] != bars["asOf"]
        or canonical["firstTime"] != bars["firstTime"]
        or canonical["lastTime"] != bars["lastTime"]
        or canonical["barCount"] != bars["barCount"]
        or canonical["contentDigest"] != bars["contentDigest"]
    ):
        raise ValueError(f"{label} cached Dataset lineage is inconsistent.")
    pipeline = _require_cached_resource_ref(
        materialization["pipeline"],
        label=f"{label}.materialization.pipeline",
        identity_field="pipelineId",
        digest=True,
    )
    details = pipeline_service.load_pipeline_version_details(
        config,
        pipeline["pipelineId"],
        pipeline["version"],
    )
    if (
        details["definition"].get("contentDigest") != pipeline["contentDigest"]
        or details["versionSummary"].get("contentDigest") != pipeline["contentDigest"]
    ):
        raise ValueError(f"{label} cached Pipeline digest is inconsistent.")
    sampler = _require_cached_resource_ref(
        materialization["sampler"],
        label=f"{label}.materialization.sampler",
        identity_field="samplerId",
    )
    environment = _require_cached_resource_ref(
        materialization["environment"],
        label=f"{label}.materialization.environment",
        identity_field="environmentId",
    )
    analysis = _require_cached_resource_ref(
        materialization["analysis"],
        label=f"{label}.materialization.analysis",
        identity_field="analysisId",
    )
    _require_installed_cached_ref(
        sampler_repository.list_samplers(config), sampler, "samplerId", "Sampler"
    )
    _require_installed_cached_ref(
        environment_service.environment_definitions(config),
        environment,
        "environmentId",
        "Environment",
    )
    _require_installed_cached_ref(
        analysis_service.analysis_definitions(config),
        analysis,
        "analysisId",
        "Analysis",
    )
    request = materialization["backtestRequest"]
    require_exact_fields(
        request,
        allowed={"pipeline", "datasetId", "datasetVersionId", "sampler", "environment", "analysis"},
        required={"pipeline", "datasetId", "datasetVersionId", "sampler", "environment", "analysis"},
        label=f"{label}.materialization.backtestRequest",
    )
    if (
        request.get("pipeline") != {
            "pipelineId": pipeline["pipelineId"],
            "version": pipeline["version"],
        }
        or request.get("datasetId") != dataset["datasetId"]
        or request.get("datasetVersionId") != dataset["datasetVersionId"]
        or request.get("sampler", {}).get("samplerId") != sampler["samplerId"]
        or request.get("sampler", {}).get("version") != sampler["version"]
        or request.get("environment", {}).get("environmentId")
        != environment["environmentId"]
        or request.get("environment", {}).get("version") != environment["version"]
        or request.get("analysis") != {
            "analysisId": analysis["analysisId"],
            "version": analysis["version"],
        }
    ):
        raise ValueError(f"{label} cached Backtest composition is inconsistent.")
    prepared = value["prepared"]
    require_exact_fields(
        prepared,
        allowed={"requestDigest", "snapshotHash"},
        required={"requestDigest", "snapshotHash"},
        label=f"{label}.prepared",
    )
    if any(
        type(prepared[field]) is not str
        or _SHA256_DIGEST.fullmatch(prepared[field]) is None
        for field in ("requestDigest", "snapshotHash")
    ):
        raise ValueError(f"{label}.prepared digests are invalid.")
    job = value["job"]
    if (
        type(job) is not dict
        or type(job.get("jobId")) is not str
        or not job["jobId"]
        or type(job.get("backtestId")) is not str
        or not job["backtestId"]
    ):
        raise ValueError(f"{label}.job identity is invalid.")
    expected_visualization = _visualization_save_request(
        dataset["datasetId"],
        job["backtestId"],
        instrument,
        bars["period"],
    )
    if materialization["visualizationSaveRequest"] != expected_visualization:
        raise ValueError(f"{label} cached Visualization request is inconsistent.")
    return copy.deepcopy(value)


def _shallow_cached_open_response(value, *, label):
    """Validate cache lookup identities without reopening every immutable archive."""

    require_exact_fields(
        value,
        allowed=_OPEN_RESPONSE_FIELDS,
        required=_OPEN_RESPONSE_FIELDS,
        label=label,
    )
    materialization = value.get("materialization")
    require_exact_fields(
        materialization,
        allowed=_MATERIALIZATION_FIELDS,
        required=_MATERIALIZATION_FIELDS,
        label=f"{label}.materialization",
    )
    dataset = materialization.get("dataset")
    require_exact_fields(
        dataset,
        allowed={"datasetId", "datasetVersionId", "protocolId"},
        required={"datasetId", "datasetVersionId", "protocolId"},
        label=f"{label}.materialization.dataset",
    )
    job = value.get("job")
    if (
        type(job) is not dict
        or type(job.get("jobId")) is not str
        or not job["jobId"]
        or type(job.get("backtestId")) is not str
        or not job["backtestId"]
    ):
        raise ValueError(f"{label}.job identity is invalid.")
    return copy.deepcopy(value)


def _validate_materialization_cache_state(config, value, *, deep=False):
    require_exact_fields(
        value,
        allowed=_MATERIALIZATION_CACHE_STATE_FIELDS,
        required=_MATERIALIZATION_CACHE_STATE_FIELDS,
        label="Basic materialization cache state",
    )
    if (
        value["schemaVersion"] != _STATE_SCHEMA_VERSION
        or value["protocolId"] != PROTOCOL_ID
        or type(value["materializations"]) is not list
    ):
        raise ValueError("Basic materialization cache state is invalid.")
    normalized = []
    keys = []
    for index, record in enumerate(value["materializations"]):
        label = f"Basic materialization cache[{index}]"
        require_exact_fields(
            record,
            allowed=_MATERIALIZATION_CACHE_RECORD_FIELDS,
            required=_MATERIALIZATION_CACHE_RECORD_FIELDS,
            label=label,
        )
        for field in ("sessionDigest", "dataDigest"):
            if (
                type(record[field]) is not str
                or _SHA256_DIGEST.fullmatch(record[field]) is None
            ):
                raise ValueError(f"{label}.{field} is invalid.")
        for field in ("providerId", "instrumentId", "period"):
            if type(record[field]) is not str or not record[field]:
                raise ValueError(f"{label}.{field} is invalid.")
        response = (
            _validate_cached_open_response(
                config,
                record["response"],
                label=f"{label}.response",
            )
            if deep
            else _shallow_cached_open_response(
                record["response"],
                label=f"{label}.response",
            )
        )
        if (
            response["barSnapshot"]["providerId"] != record["providerId"]
            or response["instrument"]["instrumentId"] != record["instrumentId"]
            or response["barSnapshot"]["period"] != record["period"]
        ):
            raise ValueError(f"{label} key differs from its cached response.")
        normalized.append({**copy.deepcopy(record), "response": response})
        keys.append((
            record["sessionDigest"],
            record["providerId"],
            record["instrumentId"],
            record["period"],
        ))
    if keys != sorted(keys) or len(keys) != len(set(keys)):
        raise ValueError("Basic materialization cache keys must be unique and sorted.")
    return {
        "schemaVersion": _STATE_SCHEMA_VERSION,
        "protocolId": PROTOCOL_ID,
        "materializations": normalized,
    }


def _load_materialization_cache_state(config):
    return _validate_materialization_cache_state(
        config,
        _read_json(
            _materialization_cache_state_path(config),
            missing=_empty_materialization_cache_state(),
        ),
    )


def _cached_materialization(
    config,
    *,
    session_digest,
    snapshot,
    instrument,
    period,
    data_digest,
    job_manager,
):
    state = _load_materialization_cache_state(config)
    matches = [
        record
        for record in state["materializations"]
        if record["sessionDigest"] == session_digest
        and record["providerId"] == snapshot["providerId"]
        and record["instrumentId"] == instrument["instrumentId"]
        and record["period"] == period
    ]
    if len(matches) > 1:
        raise ValueError("Basic materialization cache identity is ambiguous.")
    if not matches or matches[0]["dataDigest"] != data_digest:
        return None
    cached_response = matches[0]["response"]
    validation_key = _materialization_validation_key(config, cached_response)
    if validation_key in _VERIFIED_MATERIALIZATION_RESPONSES:
        response = _shallow_cached_open_response(
            cached_response,
            label="Basic selected materialization cache response",
        )
    else:
        response = _validate_cached_open_response(
            config,
            cached_response,
            label="Basic selected materialization cache response",
        )
        _remember_verified_materialization(config, cached_response)
    profile = V3_PROFILE_ID if snapshot["providerId"] == _V3_PROVIDER_ID else PROFILE_ID
    managed_state = _load_managed_pipeline_state(config)
    managed_pipeline = managed_state["pipelines"].get(
        _managed_pipeline_key(period, profile)
    )
    cached_pipeline = response["materialization"]["pipeline"]
    if managed_pipeline is None or cached_pipeline != {
        "pipelineId": managed_pipeline["pipelineId"],
        "version": managed_pipeline["version"],
        "contentDigest": managed_pipeline["contentDigest"],
        "protocolId": PROTOCOL_ID,
    }:
        return None
    try:
        current_job = job_manager.get(response["job"]["jobId"])
    except (OSError, TypeError, ValueError) as exc:
        raise ValueError(
            "Basic cached Backtest job is unavailable; explicit cache invalidation is required."
        ) from exc
    if current_job.get("backtestId") != response["job"]["backtestId"]:
        raise ValueError("Basic cached Backtest identity changed.")
    if current_job.get("status") == "failed":
        return None
    response["snapshotId"] = snapshot["snapshotId"]
    response["instrument"] = copy.deepcopy(instrument)
    response["job"] = copy.deepcopy(current_job)
    return response


def _record_materialization_cache(
    config,
    *,
    session_digest,
    data_digest,
    response,
):
    validated_response = _validate_cached_open_response(
        config,
        response,
        label="Basic materialization cache response",
    )
    record = {
        "sessionDigest": session_digest,
        "providerId": validated_response["barSnapshot"]["providerId"],
        "instrumentId": validated_response["instrument"]["instrumentId"],
        "period": validated_response["barSnapshot"]["period"],
        "dataDigest": data_digest,
        "response": validated_response,
    }
    key = (
        record["sessionDigest"],
        record["providerId"],
        record["instrumentId"],
        record["period"],
    )
    state = _load_materialization_cache_state(config)
    records = {
        (
            value["sessionDigest"],
            value["providerId"],
            value["instrumentId"],
            value["period"],
        ): value
        for value in state["materializations"]
    }
    records[key] = record
    updated = _validate_materialization_cache_state(
        config,
        {
            "schemaVersion": _STATE_SCHEMA_VERSION,
            "protocolId": PROTOCOL_ID,
            "materializations": [records[value] for value in sorted(records)],
        },
    )
    control_state.atomic_write_json(_materialization_cache_state_path(config), updated)
    _remember_verified_materialization(config, validated_response)


def _saved_materialization_data_digest(config, published):
    dataset_version_id = published["dataset"].get("latestVersionId")
    matches = {
        record["dataDigest"]
        for record in _load_materialization_cache_state(config)["materializations"]
        if record["response"]["materialization"]["dataset"]["datasetVersionId"]
        == dataset_version_id
    }
    if len(matches) > 1:
        raise ValueError("Basic saved bar snapshot has conflicting cache identities.")
    return next(iter(matches), published["dataDigest"])


def _canonical_projection_request(request):
    require_exact_fields(
        request,
        allowed={
            "backtestId", "paths", "temporaryModules",
            "projectionFormat", "window",
        },
        required={"backtestId", "paths", "temporaryModules"},
        label="Basic projection request",
    )
    backtest_id = request["backtestId"]
    if (
        type(backtest_id) is not str
        or not resource_ids.is_resource_id(backtest_id)
        or not backtest_id.startswith("bt_")
    ):
        raise ValueError("Basic projection request backtestId is invalid.")
    paths = request["paths"]
    if (
        type(paths) is not list
        or any(type(value) is not str or not value for value in paths)
        or len(set(paths)) != len(paths)
    ):
        raise ValueError("Basic projection request paths must be unique strings.")
    temporary_modules = request["temporaryModules"]
    if type(temporary_modules) is not list:
        raise ValueError("Basic projection request temporaryModules must be an array.")
    projection_format = request.get("projectionFormat", "rows")
    if projection_format not in {"rows", "columns-v2"}:
        raise ValueError("Basic Result projection format is unsupported.")
    window = result_stream.normalize_projection_window(request.get("window"))
    if projection_format == "rows" and window is not None:
        raise ValueError("Basic row Result projection does not support a window.")
    return {
        "backtestId": backtest_id,
        "paths": sorted(paths),
        "temporaryModules": copy.deepcopy(temporary_modules),
        "projectionFormat": projection_format,
        "window": window,
    }


def _owner_owns_materialized_backtest(config, owner_identity, backtest_id):
    session_digest = _owner_cache_digest(owner_identity)
    state = _load_materialization_cache_state(config)
    return any(
        record["sessionDigest"] == session_digest
        and record["response"]["job"]["backtestId"] == backtest_id
        for record in state["materializations"]
    )


def project_result_cached(config, request, *, session_identity):
    """Authorize Basic access to the Engine-owned exact projection cache."""

    normalized = _canonical_projection_request(request)
    backtest_id = normalized["backtestId"]
    if not _owner_owns_materialized_backtest(config, session_identity, backtest_id):
        raise ValueError(
            "Basic projection requires a Backtest materialized by this authenticated user."
        )
    meta = result_repository.get_backtest_result_view(config, backtest_id)
    result_digest = meta.get("resultContentDigest")
    if meta.get("status") != "completed" or not _SHA256_DIGEST.fullmatch(
        str(result_digest or "")
    ):
        raise ValueError("Basic projection requires one completed immutable Result.")
    with tempfile.TemporaryDirectory(prefix="trade-basic-projection-") as temporary:
        destination = Path(temporary) / "result.json"
        projected = result_projection_service.write_backtest_result_slice_cached(
            config,
            backtest_id,
            normalized["paths"],
            normalized["temporaryModules"],
            destination,
            module_definitions_loader=(
                lambda: module_definitions.load_pipeline_definitions(config)
                if normalized["temporaryModules"]
                else None
            ),
            projection_format=normalized["projectionFormat"],
            window=normalized["window"],
        )
        result = strict_json.loads(destination.read_bytes())
    if type(result) is not dict:
        raise ValueError("Basic projection runtime returned a non-object Result.")
    return {
        "backtestId": backtest_id,
        "cache": copy.deepcopy(projected["cache"]),
        "result": result,
    }


def _canonical_sample_projection_request(request):
    require_exact_fields(
        request,
        allowed={
            "sampleResultId", "paths", "temporaryModules",
            "projectionFormat", "window",
        },
        required={"sampleResultId", "paths", "temporaryModules"},
        label="Basic Sample Result projection request",
    )
    if (
        type(request["sampleResultId"]) is not str
        or _SHA256_DIGEST.fullmatch(request["sampleResultId"]) is None
    ):
        raise ValueError("Basic Sample Result projection identity is invalid.")
    if (
        type(request["paths"]) is not list
        or any(type(path) is not str or not path for path in request["paths"])
        or len(set(request["paths"])) != len(request["paths"])
    ):
        raise ValueError("Basic Sample Result projection paths must be unique strings.")
    if type(request["temporaryModules"]) is not list:
        raise ValueError("Basic Sample Result temporaryModules must be an array.")
    projection_format = request.get("projectionFormat", "rows")
    if projection_format not in {"rows", "columns-v2"}:
        raise ValueError("Basic Sample Result projection format is unsupported.")
    window = result_stream.normalize_projection_window(request.get("window"))
    if projection_format == "rows" and window is not None:
        raise ValueError("Basic row Result projection does not support a window.")
    return {
        "sampleResultId": request["sampleResultId"],
        "paths": sorted(request["paths"]),
        "temporaryModules": copy.deepcopy(request["temporaryModules"]),
        "projectionFormat": projection_format,
        "window": window,
    }


def project_sample_result_cached(config, request, *, _require_registered=True):
    """Authorize Basic access to the Engine-owned exact projection cache."""

    normalized = _canonical_sample_projection_request(request)
    sample_result_id = normalized["sampleResultId"]
    if _require_registered:
        with _MARKET_LOCK, control_state.control_state_lock(config):
            allowed = any(
                record["sampleResultId"] == sample_result_id
                for record in _load_chart_cache_state(
                    config,
                    verify_archives=False,
                )["records"]
            )
        if not allowed:
            raise ValueError("Basic projection requires one prepared chart cache.")
    with tempfile.TemporaryDirectory(prefix="trade-basic-sample-projection-") as temporary:
        destination = Path(temporary) / "result.json"
        projected = sample_result_projection_service.write_sample_result_slice_cached(
            config,
            sample_result_id,
            normalized["paths"],
            normalized["temporaryModules"],
            destination,
            projection_format=normalized["projectionFormat"],
            window=normalized["window"],
        )
        result = strict_json.loads(destination.read_bytes())
    if type(result) is not dict:
        raise ValueError("Basic Sample Result projection returned a non-object.")
    return {
        "sampleResultId": sample_result_id,
        "cache": copy.deepcopy(projected["cache"]),
        "result": result,
    }


def _validate_instrument(value, *, label="Market instrument"):
    require_exact_fields(
        value,
        allowed=_INSTRUMENT_FIELDS,
        required=_INSTRUMENT_FIELDS,
        label=label,
    )
    for field in ("instrumentId", "symbol", "name", "exchange", "currency"):
        if type(value[field]) is not str or not value[field].strip():
            raise ValueError(f"{label}.{field} must be a non-empty string.")
    if not re.fullmatch(r"[A-Za-z0-9_-]+", value["instrumentId"]):
        raise ValueError(f"{label}.instrumentId is invalid.")
    if value["assetType"] not in {"stock", "etf"}:
        raise ValueError(f"{label}.assetType is unsupported.")
    if (
        type(value["availablePeriods"]) is not list
        or not value["availablePeriods"]
        or any(period != "day" for period in value["availablePeriods"])
    ):
        raise ValueError(f"{label}.availablePeriods is invalid.")
    return copy.deepcopy(value)


def _snapshot_evidence(provider_id, as_of, instruments):
    if type(provider_id) is not str or not provider_id.strip():
        raise ValueError("Market providerId must be a non-empty string.")
    if type(as_of) is not str or not as_of.strip():
        raise ValueError("Market snapshot asOf must be a non-empty timestamp.")
    if type(instruments) is not list or not instruments:
        raise ValueError("Market snapshot instruments must be a non-empty array.")
    normalized = [
        _validate_instrument(value, label=f"Market instruments[{index}]")
        for index, value in enumerate(instruments)
    ]
    if normalized != sorted(normalized, key=lambda item: item["symbol"]):
        raise ValueError("Market snapshot instruments must be sorted by symbol.")
    identities = [item["instrumentId"] for item in normalized]
    symbols = [item["symbol"] for item in normalized]
    if len(set(identities)) != len(identities) or len(set(symbols)) != len(symbols):
        raise ValueError("Market snapshot instrument identities must be unique.")
    return {
        "schemaVersion": _STATE_SCHEMA_VERSION,
        "protocolId": PROTOCOL_ID,
        "providerId": provider_id.strip(),
        "asOf": as_of,
        "instrumentCount": len(normalized),
        "instruments": normalized,
    }


def _build_snapshot(provider_id, value):
    require_exact_fields(
        value,
        allowed={"asOf", "instruments"},
        required={"asOf", "instruments"},
        label="Market provider catalog snapshot",
    )
    evidence = _snapshot_evidence(provider_id, value["asOf"], value["instruments"])
    digest = version_archive.content_digest(evidence)
    return {
        **evidence,
        "snapshotId": f"basic-market-catalog-{digest.split(':', 1)[1][:24]}",
        "contentDigest": digest,
    }


def _validate_snapshot(snapshot):
    require_exact_fields(
        snapshot,
        allowed=_SNAPSHOT_FIELDS,
        required=_SNAPSHOT_FIELDS,
        label="Basic market catalog snapshot",
    )
    if not _SNAPSHOT_ID.fullmatch(snapshot["snapshotId"]):
        raise ValueError("Basic market catalog snapshotId is invalid.")
    evidence = _snapshot_evidence(
        snapshot["providerId"],
        snapshot["asOf"],
        snapshot["instruments"],
    )
    if snapshot["instrumentCount"] != evidence["instrumentCount"]:
        raise ValueError("Basic market catalog instrumentCount is invalid.")
    digest = version_archive.content_digest(evidence)
    if snapshot["contentDigest"] != digest:
        raise ValueError("Basic market catalog contentDigest is invalid.")
    expected_id = f"basic-market-catalog-{digest.split(':', 1)[1][:24]}"
    if snapshot["snapshotId"] != expected_id:
        raise ValueError("Basic market catalog snapshotId does not match its content.")
    return copy.deepcopy(snapshot)


def _snapshot_path(config, snapshot_id):
    if type(snapshot_id) is not str or not _SNAPSHOT_ID.fullmatch(snapshot_id):
        raise ValueError("Basic market snapshotId is invalid.")
    root = _state_root(config) / "snapshots"
    root.mkdir(exist_ok=True)
    return root / f"{snapshot_id}.json"


def _state_file_fingerprint(path):
    try:
        stat = path.stat()
    except FileNotFoundError:
        return None
    return (
        stat.st_dev,
        stat.st_ino,
        stat.st_size,
        stat.st_mtime_ns,
        stat.st_ctime_ns,
    )


def _remember_snapshot_cache(path, snapshot, fingerprint):
    key = str(path.resolve())
    cached_snapshot = copy.deepcopy(snapshot)
    entry = {
        "fingerprint": fingerprint,
        "snapshot": cached_snapshot,
        "instruments": {
            instrument["instrumentId"]: instrument
            for instrument in cached_snapshot["instruments"]
        },
    }
    with _SNAPSHOT_CACHE_LOCK:
        if len(_SNAPSHOT_CACHE) >= _SNAPSHOT_CACHE_LIMIT and key not in _SNAPSHOT_CACHE:
            _SNAPSHOT_CACHE.pop(next(iter(_SNAPSHOT_CACHE)))
        _SNAPSHOT_CACHE[key] = entry
    return entry


def _snapshot_cache_entry(config, snapshot_id):
    path = _snapshot_path(config, snapshot_id)
    fingerprint = _state_file_fingerprint(path)
    if fingerprint is None:
        raise ValueError(f"Unknown Basic market snapshot: {snapshot_id}")
    key = str(path.resolve())
    with _SNAPSHOT_CACHE_LOCK:
        cached = _SNAPSHOT_CACHE.get(key)
        if cached is not None and cached["fingerprint"] == fingerprint:
            return cached
    value = _read_json(path)
    if value is None:
        raise ValueError(f"Unknown Basic market snapshot: {snapshot_id}")
    return _remember_snapshot_cache(path, _validate_snapshot(value), fingerprint)


def _load_snapshot(config, snapshot_id):
    return copy.deepcopy(_snapshot_cache_entry(config, snapshot_id)["snapshot"])


def _load_snapshot_instrument(config, snapshot_id, instrument_id):
    entry = _snapshot_cache_entry(config, snapshot_id)
    instrument = entry["instruments"].get(instrument_id)
    if instrument is None:
        raise ValueError("Basic chart instrumentId is not in the selected snapshot.")
    snapshot = entry["snapshot"]
    return {
        "snapshotId": snapshot["snapshotId"],
        "providerId": snapshot["providerId"],
        "instruments": [copy.deepcopy(instrument)],
    }, copy.deepcopy(instrument)


def _current_snapshot(config):
    index = _read_json(_state_root(config) / "current.json")
    if index is None:
        return None
    require_exact_fields(
        index,
        allowed={"schemaVersion", "snapshotId"},
        required={"schemaVersion", "snapshotId"},
        label="Basic market current snapshot",
    )
    if index["schemaVersion"] != _STATE_SCHEMA_VERSION:
        raise ValueError("Basic market current snapshot schemaVersion is unsupported.")
    return _load_snapshot(config, index["snapshotId"])


def _watchlist_ids(config, snapshot):
    if snapshot is None:
        return []
    record = _read_json(_state_root(config) / "watchlist.json")
    if record is None:
        return []
    require_exact_fields(
        record,
        allowed={"schemaVersion", "snapshotId", "instrumentIds"},
        required={"schemaVersion", "snapshotId", "instrumentIds"},
        label="Basic market watchlist",
    )
    if record["schemaVersion"] != _STATE_SCHEMA_VERSION:
        raise ValueError("Basic market watchlist schemaVersion is unsupported.")
    if record["snapshotId"] != snapshot["snapshotId"]:
        return []
    if type(record["instrumentIds"]) is not list or any(
        type(value) is not str for value in record["instrumentIds"]
    ):
        raise ValueError("Basic market watchlist instrumentIds are invalid.")
    if len(set(record["instrumentIds"])) != len(record["instrumentIds"]):
        raise ValueError("Basic market watchlist instrumentIds must be unique.")
    return list(record["instrumentIds"])


def _project_watchlist(snapshot, instrument_ids):
    by_id = {item["instrumentId"]: item for item in snapshot["instruments"]}
    unknown = [value for value in instrument_ids if value not in by_id]
    if unknown:
        raise ValueError(
            "Basic market watchlist references unknown instrumentId(s): "
            + ", ".join(unknown)
        )
    return [copy.deepcopy(by_id[value]) for value in instrument_ids]


def market_state(config):
    with _MARKET_LOCK, control_state.control_state_lock(config):
        snapshot = _current_snapshot(config)
        watchlist = [] if snapshot is None else _project_watchlist(
            snapshot,
            _watchlist_ids(config, snapshot),
        )
        bar_snapshots = _project_bar_snapshots(
            snapshot,
            _load_bar_snapshot_state(config),
        )
        snapshot_jobs = _project_snapshot_jobs(
            snapshot,
            _load_snapshot_job_state(config),
        )
        return {
            "protocolId": PROTOCOL_ID,
            "snapshot": snapshot,
            "watchlist": watchlist,
            "barSnapshots": bar_snapshots,
            "snapshotJobs": snapshot_jobs,
        }


def chart_module_catalog(config):
    """Return only exact archived Signal versions used by the Basic chart."""

    state_path = control_state.state_path(config, "modules.json")
    fingerprint = _state_file_fingerprint(state_path)
    cache_key = str(state_path.resolve())
    with _MARKET_LOCK:
        cached = _CHART_CATALOG_CACHE.get(cache_key)
        if cached is not None and cached["fingerprint"] == fingerprint:
            return copy.deepcopy(cached["catalog"])
    with control_state.control_state_lock(config):
        index = control_state.load_state(config, "modules.json", {})
    if type(index) is not dict:
        raise ValueError("Basic chart Module index is invalid.")
    latest = {}
    for definition in index.values():
        if (
            type(definition) is not dict
            or definition.get("kind") != "Signal"
            or definition.get("moduleId") not in _CHART_SIGNAL_MODULE_IDS
            or type(definition.get("version")) is not str
            or not definition["version"].isdecimal()
            or not (
                definition.get("builtin") is True
                or definition.get("protocolId") == PROTOCOL_ID
            )
        ):
            continue
        module_id = definition["moduleId"]
        if module_id not in latest or int(definition["version"]) > int(
            latest[module_id]["version"]
        ):
            latest[module_id] = definition
    missing = sorted(_CHART_SIGNAL_MODULE_IDS - set(latest))
    if missing:
        raise ValueError(
            "Basic chart requires installed Module(s): " + ", ".join(missing)
        )
    references = [
        (definition["kind"], definition["moduleId"], definition["version"])
        for definition in latest.values()
    ]
    definitions, _evidence = module_definitions.load_definition_versions(
        config,
        references,
    )
    catalog = {
        "protocolId": PROTOCOL_ID,
        "modules": sorted(
            definitions.values(),
            key=lambda definition: definition["moduleId"],
        ),
    }
    final_fingerprint = _state_file_fingerprint(state_path)
    if final_fingerprint is None or final_fingerprint != fingerprint:
        raise ValueError("Basic chart Module index changed while loading.")
    with _MARKET_LOCK:
        if (
            len(_CHART_CATALOG_CACHE) >= _CHART_CATALOG_CACHE_LIMIT
            and cache_key not in _CHART_CATALOG_CACHE
        ):
            _CHART_CATALOG_CACHE.pop(next(iter(_CHART_CATALOG_CACHE)))
        _CHART_CATALOG_CACHE[cache_key] = {
            "fingerprint": final_fingerprint,
            "catalog": copy.deepcopy(catalog),
        }
    return catalog


def sync_market(config, request, *, providers=None):
    require_exact_fields(
        request,
        allowed={"providerId"},
        required={"providerId"},
        label="Basic market sync request",
    )
    providers = default_provider_registry() if providers is None else providers
    if type(providers) is not dict:
        raise TypeError("Basic market providers must be an exact registry.")
    provider_id = request["providerId"]
    if type(provider_id) is not str or provider_id not in providers:
        raise ValueError(f"Unknown Basic market provider: {provider_id}")
    provider = providers[provider_id]
    if getattr(provider, "provider_id", None) != provider_id:
        raise ValueError("Basic market provider registry identity is invalid.")
    snapshot = _build_snapshot(provider_id, provider.sync_catalog())
    with _MARKET_LOCK, control_state.control_state_lock(config):
        previous = _current_snapshot(config)
        known = {item["instrumentId"] for item in snapshot["instruments"]}
        if previous is None:
            by_symbol = {
                item["symbol"]: item["instrumentId"]
                for item in snapshot["instruments"]
            }
            retained = [
                by_symbol[symbol]
                for symbol in DEFAULT_WATCHLIST_SYMBOLS
                if symbol in by_symbol
            ]
        else:
            retained = [
                value
                for value in _watchlist_ids(config, previous)
                if value in known
            ]
        path = _snapshot_path(config, snapshot["snapshotId"])
        existing = _read_json(path)
        if existing is not None and _validate_snapshot(existing) != snapshot:
            raise ValueError("Basic market immutable snapshot identity is occupied.")
        if existing is None:
            control_state.atomic_write_json(path, snapshot)
        fingerprint = _state_file_fingerprint(path)
        if fingerprint is None:
            raise ValueError("Basic market immutable snapshot was not persisted.")
        _remember_snapshot_cache(path, snapshot, fingerprint)
        control_state.atomic_write_json(
            _state_root(config) / "current.json",
            {
                "schemaVersion": _STATE_SCHEMA_VERSION,
                "snapshotId": snapshot["snapshotId"],
            },
        )
        control_state.atomic_write_json(
            _state_root(config) / "watchlist.json",
            {
                "schemaVersion": _STATE_SCHEMA_VERSION,
                "snapshotId": snapshot["snapshotId"],
                "instrumentIds": retained,
            },
        )
    created_jobs = _enqueue_snapshot_jobs(config, snapshot, retained)
    return {
        "accepted": True,
        "protocolId": PROTOCOL_ID,
        "snapshot": snapshot,
        "watchlist": _project_watchlist(snapshot, retained),
        "snapshotJobs": created_jobs,
    }


def set_watchlist(config, request):
    require_exact_fields(
        request,
        allowed={"snapshotId", "instrumentIds"},
        required={"snapshotId", "instrumentIds"},
        label="Basic market watchlist request",
    )
    if type(request["instrumentIds"]) is not list or any(
        type(value) is not str for value in request["instrumentIds"]
    ):
        raise ValueError("Basic market watchlist instrumentIds must be an array of strings.")
    if len(set(request["instrumentIds"])) != len(request["instrumentIds"]):
        raise ValueError("Basic market watchlist instrumentIds must be unique.")
    with _MARKET_LOCK, control_state.control_state_lock(config):
        current = _current_snapshot(config)
        if current is None or current["snapshotId"] != request["snapshotId"]:
            raise ValueError("Basic market watchlist requires the current snapshotId.")
        previous_ids = _watchlist_ids(config, current)
        watchlist = _project_watchlist(current, request["instrumentIds"])
        control_state.atomic_write_json(
            _state_root(config) / "watchlist.json",
            {
                "schemaVersion": _STATE_SCHEMA_VERSION,
                "snapshotId": current["snapshotId"],
                "instrumentIds": list(request["instrumentIds"]),
            },
        )
    added = [
        instrument_id
        for instrument_id in request["instrumentIds"]
        if instrument_id not in set(previous_ids)
    ]
    created_jobs = _enqueue_snapshot_jobs(config, current, added)
    return {
        "accepted": True,
        "protocolId": PROTOCOL_ID,
        "snapshotId": current["snapshotId"],
        "watchlist": watchlist,
        "snapshotJobs": created_jobs,
    }


def _bar_snapshot(provider_id, instrument, value):
    if provider_id == _V3_PROVIDER_ID:
        return _ohlcv_bar_snapshot(provider_id, instrument, value)
    require_exact_fields(
        value,
        allowed={"providerId", "asOf", "period", "bars"},
        required={"providerId", "asOf", "period", "bars"},
        label="Basic market bar provider response",
    )
    if value["providerId"] != provider_id:
        raise ValueError("Basic market bar provider identity changed.")
    as_of = _absolute_instant(
        value["asOf"],
        "Basic market bar snapshot asOf",
    )
    if type(value["period"]) is not str or value["period"] not in instrument[
        "availablePeriods"
    ]:
        raise ValueError("Basic market bar snapshot period is invalid.")
    if type(value["bars"]) is not list or not value["bars"]:
        raise ValueError("Basic market bar snapshot must contain bars.")
    times = []
    for index, bar in enumerate(value["bars"]):
        require_exact_fields(
            bar,
            allowed={"time", "open", "close", "high", "low"},
            required={"time", "open", "close", "high", "low"},
            label=f"Basic market bars[{index}]",
        )
        event_time = _absolute_instant(
            bar["time"],
            f"Basic market bars[{index}].time",
        )
        if any(
            type(bar[field]) not in {int, float}
            or not math.isfinite(bar[field])
            or bar[field] <= 0
            for field in ("open", "close", "high", "low")
        ):
            raise ValueError(f"Basic market bars[{index}] OHLC values are invalid.")
        if bar["low"] > min(bar["open"], bar["close"]):
            raise ValueError(
                f"Basic market bars[{index}] violates the OHLC lower bound."
            )
        if max(bar["open"], bar["close"]) > bar["high"]:
            raise ValueError(
                f"Basic market bars[{index}] violates the OHLC upper bound."
            )
        times.append(event_time)
    if any(current <= previous for previous, current in zip(times, times[1:])):
        raise ValueError("Basic market bar times must be unique and increasing.")
    if as_of < times[-1]:
        raise ValueError("Basic market bar snapshot asOf precedes its last eventTime.")
    evidence = {
        "providerId": provider_id,
        "asOf": value["asOf"],
        "instrument": copy.deepcopy(instrument),
        "period": value["period"],
        "bars": copy.deepcopy(value["bars"]),
    }
    digest = version_archive.content_digest(evidence)
    return evidence, {
        "providerId": provider_id,
        "asOf": value["asOf"],
        "period": value["period"],
        "barCount": len(value["bars"]),
        "firstTime": _canonical_instant(times[0]),
        "lastTime": _canonical_instant(times[-1]),
        "contentDigest": digest,
    }


def _ohlcv_bar_snapshot(provider_id, instrument, value):
    fields = {
        "providerId",
        "asOf",
        "retrievedAt",
        "rawSha256",
        "adjustmentPolicy",
        "revisionPolicy",
        "period",
        "bars",
    }
    require_exact_fields(
        value,
        allowed=fields,
        required=fields,
        label="Basic v3 OHLCV response",
    )
    if (
        value["providerId"] != provider_id
        or value["period"] != "day"
        or value["period"] not in instrument["availablePeriods"]
    ):
        raise ValueError("Basic v3 OHLCV provider identity or period is invalid.")
    as_of = _absolute_instant(value["asOf"], "Basic v3 OHLCV asOf")
    retrieved = _absolute_instant(value["retrievedAt"], "Basic v3 OHLCV retrievedAt")
    if (
        retrieved < as_of
        or type(value["rawSha256"]) is not str
        or not _SHA256_DIGEST.fullmatch(value["rawSha256"])
        or value["adjustmentPolicy"] != EODHD_ADJUSTMENT_POLICY
        or value["revisionPolicy"] != EODHD_REVISION_POLICY
    ):
        raise ValueError("Basic v3 OHLCV provenance is invalid.")
    if type(value["bars"]) is not list or not value["bars"]:
        raise ValueError("Basic v3 OHLCV bars must be non-empty.")
    times = []
    for i, bar in enumerate(value["bars"]):
        bar_fields = {
            "time", "eventTime", "open", "close", "high", "low", "volume"
        }
        require_exact_fields(
            bar,
            allowed=bar_fields,
            required=bar_fields,
            label=f"Basic v3 bars[{i}]",
        )
        event = _absolute_instant(bar["eventTime"], f"Basic v3 bars[{i}].eventTime")
        available = _absolute_instant(bar["time"], f"Basic v3 bars[{i}].time")
        if available - event != timedelta(minutes=15) or any(
            type(bar[field]) not in {int, float}
            or not math.isfinite(bar[field])
            or (bar[field] < 0 if field == "volume" else bar[field] <= 0)
            for field in ("open", "close", "high", "low", "volume")
        ):
            raise ValueError(f"Basic v3 bars[{i}] has invalid OHLCV values or timing.")
        if bar["low"] > min(bar["open"], bar["close"]) or max(bar["open"], bar["close"]) > bar["high"]:
            raise ValueError(f"Basic v3 bars[{i}] violates OHLC bounds.")
        times.append((available, event))
    if any(
        current[0] <= previous[0] or current[1] <= previous[1]
        for previous, current in zip(times, times[1:])
    ):
        raise ValueError("Basic v3 bar times and eventTimes must be strictly increasing.")
    if as_of < times[-1][0]:
        raise ValueError("Basic v3 asOf precedes last available time.")
    evidence = {
        key: copy.deepcopy(value[key])
        for key in (
            "providerId",
            "asOf",
            "retrievedAt",
            "rawSha256",
            "adjustmentPolicy",
            "revisionPolicy",
            "period",
            "bars",
        )
    }
    evidence["instrument"] = copy.deepcopy(instrument)
    digest = version_archive.content_digest(evidence)
    return evidence, {
        "providerId": provider_id,
        "asOf": value["asOf"],
        "retrievedAt": value["retrievedAt"],
        "rawSha256": value["rawSha256"],
        "adjustmentPolicy": copy.deepcopy(value["adjustmentPolicy"]),
        "revisionPolicy": copy.deepcopy(value["revisionPolicy"]),
        "period": "day",
        "barCount": len(times),
        "firstTime": _canonical_instant(times[0][0]),
        "lastTime": _canonical_instant(times[-1][0]),
        "contentDigest": digest,
    }


def _publish_dataset(config, snapshot, instrument, evidence, summary):
    digest_suffix = summary["contentDigest"].split(":", 1)[1][:24]
    dataset_id = f"basic-market-{digest_suffix}"
    is_v3 = evidence["providerId"] == _V3_PROVIDER_ID
    with tempfile.TemporaryDirectory(prefix="trade-basic-market-") as temporary:
        period_root = Path(temporary) / evidence["period"]
        period_root.mkdir()
        csv_path = period_root / f"{instrument['instrumentId']}.csv"
        with csv_path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle, lineterminator="\n")
            fields = schemas.CSV_FIELDS_V3 if is_v3 else schemas.CSV_FIELDS
            writer.writerow(fields)
            for bar in evidence["bars"]:
                writer.writerow(([bar["time"], bar["eventTime"], bar["open"], bar["close"], bar["high"], bar["low"], bar["volume"]]
                                 if is_v3 else
                                 [bar["time"], bar["open"], bar["close"], bar["high"], bar["low"]]))
        source_details = {
            "providerId": evidence["providerId"],
            "catalogSnapshotId": snapshot["snapshotId"],
            "instrumentId": instrument["instrumentId"],
            "symbol": instrument["symbol"],
            "period": evidence["period"],
            "asOf": evidence["asOf"],
            "contentDigest": summary["contentDigest"],
        }
        if is_v3:
            source_details.update({
                field: copy.deepcopy(evidence[field])
                for field in _V3_PROVENANCE_FIELDS
            })
        adapter = basic_dataset_v3 if is_v3 else basic_dataset
        return adapter.register_dataset(
            config,
            dataset_id=dataset_id,
            name=f"{instrument['symbol']} {evidence['period']} snapshot",
            source_root=temporary,
            descriptor={
                "protocolId": PROTOCOL_ID,
                "protocolVersion": V3_PROTOCOL_VERSION if is_v3 else PROTOCOL_VERSION,
                "profile": V3_PROFILE_ID if is_v3 else PROFILE_ID,
                "cashUnit": instrument["currency"],
                "quantityUnit": "share",
                "executionConvention": "prior-approved-intent-next-bar-open",
                "valuationConvention": "current-bar-close",
            },
            source={
                "type": "basic-market-snapshot",
                "details": source_details,
            },
            metadata={
                "marketSnapshot": {
                    "catalogSnapshotId": snapshot["snapshotId"],
                    "instrument": copy.deepcopy(instrument),
                    "bars": copy.deepcopy(summary),
                }
            },
            display_time_zone="America/New_York",
        )


def _managed_pipeline_generation(profile):
    if profile == PROFILE_ID:
        return 1
    if profile == V3_PROFILE_ID:
        return _V3_PIPELINE_GENERATION
    raise ValueError("Basic managed Pipeline profile is unsupported.")


def _managed_pipeline_key(period, profile, *, generation=None):
    generation = (
        _managed_pipeline_generation(profile)
        if generation is None
        else generation
    )
    if profile == PROFILE_ID:
        if generation != 1:
            raise ValueError("Basic legacy managed Pipeline generation is unsupported.")
        return period
    if profile == V3_PROFILE_ID:
        if generation == 1:
            return f"{period}-ohlcv-v3"
        if 2 <= generation <= _V3_PIPELINE_GENERATION:
            return f"{period}-ohlcv-v3-g{generation}"
        raise ValueError("Basic OHLCV managed Pipeline generation is unsupported.")
    raise ValueError("Basic managed Pipeline profile is unsupported.")


def _managed_pipeline_name(period, profile, *, generation=None):
    generation = (
        _managed_pipeline_generation(profile)
        if generation is None
        else generation
    )
    if profile == PROFILE_ID:
        return f"Basic Market Snapshot · {period}"
    if profile == V3_PROFILE_ID:
        return f"Basic OHLCV Market Snapshot · {period} · Contract {generation}"
    raise ValueError("Basic managed Pipeline profile is unsupported.")


def _pipeline_draft_matches(definition, expected):
    expected = pipeline_manifest_compiler.normalize_pipeline_draft(expected)
    if type(definition) is not dict or any(
        field not in definition for field in expected
    ):
        return False
    actual = {field: definition[field] for field in expected}
    return strict_json.dumps(actual, sort_keys=True) == strict_json.dumps(
        expected,
        sort_keys=True,
    )


def _frozen_scaffold_definitions(
    definitions,
    pipeline_definition,
    *,
    universe_module_id,
):
    instances = pipeline_definition.get("instances")
    if type(instances) is not dict:
        raise ValueError("Basic managed Pipeline instances are unavailable.")
    resolved = []
    for role, (kind, module_id) in module_requirements(
        universe_module_id
    ).items():
        instance = instances.get(role)
        if (
            type(instance) is not dict
            or instance.get("kind") != kind
            or instance.get("moduleId") != module_id
            or type(instance.get("version")) is not str
        ):
            raise ValueError(
                "Basic managed Pipeline frozen Module identity is incompatible."
            )
        matches = [
            value
            for value in definitions
            if value.get("builtin") is True
            and value.get("kind") == kind
            and value.get("moduleId") == module_id
            and value.get("version") == instance["version"]
        ]
        if len(matches) != 1:
            raise ValueError(
                "Basic managed Pipeline frozen Module version is unavailable."
            )
        resolved.append(matches[0])
    return resolved


def _managed_pipeline(config, period, profile, definitions):
    """Create once, then resolve only the Basic-owned immutable exact reference."""

    generation = _managed_pipeline_generation(profile)
    pipeline_key = _managed_pipeline_key(
        period,
        profile,
        generation=generation,
    )
    name = _managed_pipeline_name(
        period,
        profile,
        generation=generation,
    )
    universe_module_id = (
        OHLCV_UNIVERSE_MODULE_ID
        if profile == V3_PROFILE_ID
        else PRICE_UNIVERSE_MODULE_ID
    )
    with _MARKET_LOCK, control_state.control_state_lock(config):
        state = _load_managed_pipeline_state(config)
        reference = state["pipelines"].get(pipeline_key)
        if reference is None:
            identity = pipeline_service.create_pipeline(
                config,
                {"name": name, "protocolId": PROTOCOL_ID},
            )["definition"]
            draft = build_pipeline_scaffold(
                identity["pipelineId"],
                name,
                definitions,
                decision_period=period,
                universe_module_id=universe_module_id,
            )
            definition = pipeline_service.archive_pipeline_if_changed(
                config,
                draft,
            )["definition"]
            state["pipelines"][pipeline_key] = {
                "period": period,
                "profile": profile,
                "generation": generation,
                "pipelineId": definition["pipelineId"],
                "version": definition["version"],
                "contentDigest": definition["contentDigest"],
            }
            control_state.atomic_write_json(
                _managed_pipeline_state_path(config),
                _validate_managed_pipeline_state(state),
            )
            return definition

        try:
            details = pipeline_service.load_pipeline_version_details(
                config,
                reference["pipelineId"],
                reference["version"],
            )
        except (OSError, TypeError, ValueError) as exc:
            raise ValueError(
                "Basic managed Pipeline exact version is unavailable; "
                "explicit managed Pipeline rotation is required."
            ) from exc
        definition = details["definition"]
        version_summary = details["versionSummary"]
        if (
            definition.get("pipelineId") != reference["pipelineId"]
            or definition.get("version") != reference["version"]
            or definition.get("protocolId") != PROTOCOL_ID
            or definition.get("contentDigest") != reference["contentDigest"]
            or version_summary.get("contentDigest") != reference["contentDigest"]
        ):
            raise ValueError(
                "Basic managed Pipeline exact reference does not match its "
                "immutable Engine version; explicit managed Pipeline rotation "
                "is required."
            )
        expected = build_pipeline_scaffold(
            reference["pipelineId"],
            name,
            _frozen_scaffold_definitions(
                definitions,
                definition,
                universe_module_id=universe_module_id,
            ),
            decision_period=period,
            universe_module_id=universe_module_id,
        )
        if not _pipeline_draft_matches(definition, expected):
            raise ValueError(
                "Basic managed Pipeline scaffold has changed; explicit managed "
                "Pipeline rotation is required."
            )
        return definition


def _latest_exact(
    records,
    identity_field,
    expected_identity,
    label,
    *,
    predicate=None,
):
    matches = [
        value
        for value in records
        if value.get("protocolId") == PROTOCOL_ID
        and value.get(identity_field) == expected_identity
        and (predicate is None or predicate(value))
    ]
    if not matches:
        raise ValueError(
            f"Basic market requires compatible {label} '{expected_identity}'."
        )
    if any(
        type(value.get("version")) is not str
        or not value["version"].isdecimal()
        for value in matches
    ):
        raise ValueError(f"Basic market {label} versions are invalid.")
    return max(matches, key=lambda value: int(value["version"]))


def _execution_period_overrides(environment, period):
    overrides = {}
    for instance_id, instance in environment.get("instances", {}).items():
        config = instance.get("config")
        if type(config) is dict and "executionPeriod" in config:
            overrides[instance_id] = {"executionPeriod": period}
    if len(overrides) != 1:
        raise ValueError(
            "Basic market Environment must expose exactly one executionPeriod config."
        )
    return overrides


def _market_visualization_spec(dataset_id, instrument, period):
    data_key = f"price.{period}.{instrument['instrumentId']}"
    spec = {
        "schemaVersion": 3,
        "datasetId": dataset_id,
        "timeZone": "America/New_York",
        "panes": [
            {
                "id": "market",
                "title": f"{instrument['symbol']} · {period}",
                "role": "financial",
                "view": {
                    "start": None,
                    "end": None,
                    "logScale": False,
                    "controlsCollapsed": False,
                },
                "visualizers": [
                    {
                        "id": "market-candles",
                        "callback": "ohlc.candles",
                        "params": {
                            "dataKey": data_key,
                            "timeDomainId": "basic-market-time",
                            "priceScaleId": "basic-market-price",
                            "upColor": "#089981",
                            "downColor": "#f23645",
                        },
                    }
                ],
                "temporaryModules": [],
            }
        ],
    }
    visualization_contracts.require_spec(spec)
    return spec


def _visualization_save_request(dataset_id, backtest_id, instrument, period):
    return {
        "backtestId": backtest_id,
        "visualizationId": visualization_service.current_visualization_id(
            backtest_id
        ),
        "name": f"{instrument['symbol']} {period} snapshot",
        "expectedRevision": 0,
        "spec": _market_visualization_spec(dataset_id, instrument, period),
    }


def _sample_visualization_save_request(
    dataset_id,
    sample_result_id,
    instrument,
    period,
):
    return {
        "sampleResultId": sample_result_id,
        "visualizationId": sample_visualization_service.current_visualization_id(
            sample_result_id
        ),
        "name": f"{instrument['symbol']} {period} snapshot",
        "expectedRevision": 0,
        "spec": _market_visualization_spec(dataset_id, instrument, period),
    }


def _uncached_open_instrument(
    config,
    request,
    *,
    prepared_store,
    job_manager,
    session_identity,
    providers=None,
    downloaded=None,
    published=None,
):
    require_exact_fields(
        request,
        allowed={"snapshotId", "instrumentId", "period"},
        required={"snapshotId", "instrumentId", "period"},
        label="Basic market open instrument request",
    )
    with _MARKET_LOCK, control_state.control_state_lock(config):
        snapshot = _load_snapshot(config, request["snapshotId"])
    instruments = {
        value["instrumentId"]: value for value in snapshot["instruments"]
    }
    instrument = instruments.get(request["instrumentId"])
    if instrument is None:
        raise ValueError("Basic market instrumentId is not in the selected snapshot.")
    if request["period"] not in instrument["availablePeriods"]:
        raise ValueError("Basic market period is unavailable for this instrument.")
    providers = default_provider_registry() if providers is None else providers
    provider = providers.get(snapshot["providerId"]) if type(providers) is dict else None
    if provider is None or getattr(provider, "provider_id", None) != snapshot["providerId"]:
        raise ValueError("Basic market snapshot provider is not installed.")

    if published is not None:
        if (
            type(published) is not dict
            or set(published) != {"dataset", "summary", "dataDigest"}
            or type(published["dataset"]) is not dict
            or type(published["summary"]) is not dict
        ):
            raise ValueError("Basic saved bar materialization is invalid.")
        dataset = copy.deepcopy(published["dataset"])
        bar_summary = copy.deepcopy(published["summary"])
    elif downloaded is None:
        evidence, bar_summary = _bar_snapshot(
            snapshot["providerId"],
            instrument,
            provider.download_bars(copy.deepcopy(instrument), request["period"]),
        )
    else:
        if (
            type(downloaded) is not tuple
            or len(downloaded) != 2
            or type(downloaded[0]) is not dict
            or type(downloaded[1]) is not dict
        ):
            raise ValueError("Basic market downloaded bar evidence is invalid.")
        evidence, bar_summary = copy.deepcopy(downloaded)
    if published is None:
        dataset = _publish_dataset(config, snapshot, instrument, evidence, bar_summary)
        _record_published_bar_snapshot(
            config,
            snapshot,
            instrument,
            bar_summary,
            dataset,
        )

    definitions = [
        value
        for value in module_definitions.load_pipeline_definitions(config).values()
        if value.get("protocolId") == PROTOCOL_ID
    ]
    is_v3 = snapshot["providerId"] == _V3_PROVIDER_ID
    profile = V3_PROFILE_ID if is_v3 else PROFILE_ID
    pipeline = _managed_pipeline(
        config,
        request["period"],
        profile,
        definitions,
    )
    sampler_id = (
        _BASIC_WORKFLOW_V3_SAMPLER_ID if is_v3 else _BASIC_WORKFLOW_SAMPLER_ID
    )
    sampler_output_schema = (
        schemas.OHLCV_SAMPLER_OUTPUT_SCHEMA
        if is_v3
        else schemas.SAMPLER_OUTPUT_SCHEMA
    )
    sampler = _latest_exact(
        sampler_repository.list_samplers(config),
        "samplerId",
        sampler_id,
        "Sampler",
        predicate=lambda value: value.get("outputSchema") == sampler_output_schema,
    )
    environment = _latest_exact(
        environment_service.environment_definitions(config),
        "environmentId",
        BASIC_WORKFLOW_ENVIRONMENT_ID,
        "Environment",
        predicate=lambda value: len(
            [
                instance
                for instance in value.get("instances", {}).values()
                if type(instance.get("config")) is dict
                and "executionPeriod" in instance["config"]
            ]
        )
        == 1,
    )
    analysis = _latest_exact(
        analysis_service.analysis_definitions(config),
        "analysisId",
        BASIC_WORKFLOW_ANALYSIS_ID,
        "Analysis",
    )
    backtest_request = {
        "pipeline": {
            "pipelineId": pipeline["pipelineId"],
            "version": pipeline["version"],
        },
        "datasetId": dataset["datasetId"],
        "datasetVersionId": dataset["latestVersionId"],
        "sampler": {
            "samplerId": sampler["samplerId"],
            "version": sampler["version"],
            "parameters": {"decisionPeriod": request["period"]},
        },
        "environment": {
            "environmentId": environment["environmentId"],
            "version": environment["version"],
            "moduleConfigOverrides": _execution_period_overrides(
                environment,
                request["period"],
            ),
        },
        "analysis": {
            "analysisId": analysis["analysisId"],
            "version": analysis["version"],
        },
    }
    with control_state.control_state_lock(config):
        prepared = prepare_backtest_submission(
            config,
            backtest_request,
            prepared_store,
            session_identity=session_identity,
        )
    job = job_manager.submit(
        backtest_request,
        prepared_submission_token=prepared["preparedSubmissionToken"],
        session_identity=session_identity,
    )
    visualization_save_request = _visualization_save_request(
        dataset["datasetId"],
        job["backtestId"],
        instrument,
        request["period"],
    )
    return {
        "accepted": True,
        "protocolId": PROTOCOL_ID,
        "snapshotId": snapshot["snapshotId"],
        "instrument": copy.deepcopy(instrument),
        "barSnapshot": bar_summary,
        "materialization": {
            "dataset": {
                "datasetId": dataset["datasetId"],
                "datasetVersionId": dataset["latestVersionId"],
                "protocolId": dataset["protocolId"],
            },
            "pipeline": {
                "pipelineId": pipeline["pipelineId"],
                "version": pipeline["version"],
                "contentDigest": pipeline["contentDigest"],
                "protocolId": pipeline["protocolId"],
            },
            "sampler": {
                "samplerId": sampler["samplerId"],
                "version": sampler["version"],
                "protocolId": sampler["protocolId"],
            },
            "environment": {
                "environmentId": environment["environmentId"],
                "version": environment["version"],
                "protocolId": environment["protocolId"],
            },
            "analysis": {
                "analysisId": analysis["analysisId"],
                "version": analysis["version"],
                "protocolId": analysis["protocolId"],
            },
            "backtestRequest": copy.deepcopy(backtest_request),
            "visualizationSaveRequest": visualization_save_request,
        },
        "prepared": {
            "requestDigest": prepared["requestDigest"],
            "snapshotHash": prepared["snapshotHash"],
        },
        "job": job,
    }


def open_instrument(
    config,
    request,
    *,
    prepared_store,
    job_manager,
    session_identity,
    owner_identity=None,
    providers=None,
):
    require_exact_fields(
        request,
        allowed={"snapshotId", "instrumentId", "period"},
        required={"snapshotId", "instrumentId", "period"},
        label="Basic market open instrument request",
    )
    with _MARKET_LOCK, control_state.control_state_lock(config):
        snapshot = _load_snapshot(config, request["snapshotId"])
    instruments = {
        value["instrumentId"]: value for value in snapshot["instruments"]
    }
    instrument = instruments.get(request["instrumentId"])
    if instrument is None:
        raise ValueError("Basic market instrumentId is not in the selected snapshot.")
    if request["period"] not in instrument["availablePeriods"]:
        raise ValueError("Basic market period is unavailable for this instrument.")
    providers = default_provider_registry() if providers is None else providers
    provider = providers.get(snapshot["providerId"]) if type(providers) is dict else None
    if provider is None or getattr(provider, "provider_id", None) != snapshot["providerId"]:
        raise ValueError("Basic market snapshot provider is not installed.")
    published = _saved_bar_materialization(
        config,
        snapshot,
        instrument,
        request["period"],
    )
    if published is None:
        evidence, bar_summary = _bar_snapshot(
            snapshot["providerId"],
            instrument,
            provider.download_bars(copy.deepcopy(instrument), request["period"]),
        )
        data_digest = _stable_bar_data_digest(evidence)
        downloaded = (evidence, bar_summary)
    else:
        data_digest = _saved_materialization_data_digest(config, published)
        downloaded = None
    cache_owner = session_identity if owner_identity is None else owner_identity
    session_digest = _owner_cache_digest(cache_owner)
    with _MARKET_LOCK:
        cached = _cached_materialization(
            config,
            session_digest=session_digest,
            snapshot=snapshot,
            instrument=instrument,
            period=request["period"],
            data_digest=data_digest,
            job_manager=job_manager,
        )
        if cached is not None:
            return {
                **cached,
                "cache": {"materializationHit": True},
            }
        response = _uncached_open_instrument(
            config,
            request,
            prepared_store=prepared_store,
            job_manager=job_manager,
            session_identity=session_identity,
            providers=providers,
            downloaded=downloaded,
            published=published,
        )
        _record_materialization_cache(
            config,
            session_digest=session_digest,
            data_digest=data_digest,
            response=response,
        )
        return {
            **response,
            "cache": {"materializationHit": False},
        }


def _chart_job_for(config, snapshot, instrument, period):
    with _MARKET_LOCK, control_state.control_state_lock(config):
        matches = [
            copy.deepcopy(job)
            for job in _load_snapshot_job_state(config)["jobs"]
            if job["snapshotId"] == snapshot["snapshotId"]
            and job["providerId"] == snapshot["providerId"]
            and job["instrumentId"] == instrument["instrumentId"]
            and job["period"] == period
        ]
    return matches[-1] if matches else None


def open_chart(config, request, *, providers=None):
    """Open one chart from the cached Sampler timeline, never a Backtest."""

    require_exact_fields(
        request,
        allowed={"snapshotId", "instrumentId", "period"},
        required={"snapshotId", "instrumentId", "period"},
        label="Basic open chart request",
    )
    with _MARKET_LOCK, control_state.control_state_lock(config):
        snapshot, instrument = _load_snapshot_instrument(
            config,
            request["snapshotId"],
            request["instrumentId"],
        )
        watchlist_instrument_ids = _watchlist_ids(config, snapshot)
    period = request["period"]
    if period not in instrument["availablePeriods"]:
        raise ValueError("Basic chart period is unavailable for this instrument.")
    cached_result = _saved_chart_cache(config, snapshot, instrument, period)
    if cached_result is None:
        job = _promote_snapshot_job(config, snapshot, instrument, period)
        ensure_snapshot_worker(
            config,
            providers=providers,
            enqueue_watchlist=False,
        )
        cached_result = _saved_chart_cache(config, snapshot, instrument, period)
        if cached_result is None:
            current_job = _chart_job_for(config, snapshot, instrument, period) or job
            return {
                "accepted": True,
                "ready": False,
                "protocolId": PROTOCOL_ID,
                "snapshotId": snapshot["snapshotId"],
                "instrument": copy.deepcopy(instrument),
                "watchlistInstrumentIds": watchlist_instrument_ids,
                "cacheJob": current_job,
            }
    cached, view = cached_result
    bar_summary = cached.get("barSnapshot")
    if bar_summary is None:
        published = _saved_bar_materialization(
            config,
            snapshot,
            instrument,
            period,
        )
        if published is None:
            raise ValueError("Basic chart cache has no immutable Dataset materialization.")
        if (
            published["dataset"]["datasetId"] != cached["datasetId"]
            or published["dataset"]["latestVersionId"] != cached["datasetVersionId"]
        ):
            raise ValueError("Basic chart cache Dataset identity changed.")
        bar_summary = published["summary"]
        _record_chart_cache(
            config,
            snapshot,
            instrument,
            period,
            published["dataset"],
            view,
            bar_summary,
        )
    return {
        "accepted": True,
        "ready": True,
        "protocolId": PROTOCOL_ID,
        "snapshotId": snapshot["snapshotId"],
        "instrument": copy.deepcopy(instrument),
        "watchlistInstrumentIds": watchlist_instrument_ids,
        "barSnapshot": copy.deepcopy(bar_summary),
        "materialization": {
            "dataset": {
                "datasetId": cached["datasetId"],
                "datasetVersionId": cached["datasetVersionId"],
                "protocolId": PROTOCOL_ID,
            },
            "sampler": {
                **copy.deepcopy(view["sampler"]),
                "protocolId": PROTOCOL_ID,
            },
            "sampleResult": view,
            "visualizationSaveRequest": _sample_visualization_save_request(
                cached["datasetId"],
                cached["sampleResultId"],
                instrument,
                period,
            ),
        },
        "cacheJob": _chart_job_for(config, snapshot, instrument, period),
        "cache": {"sampleResultHit": True},
    }


__all__ = (
    "DEFAULT_WATCHLIST_SYMBOLS",
    "chart_module_catalog",
    "ensure_snapshot_worker",
    "market_state",
    "open_chart",
    "open_instrument",
    "project_sample_result_cached",
    "project_result_cached",
    "run_snapshot_jobs",
    "set_watchlist",
    "sync_market",
)
