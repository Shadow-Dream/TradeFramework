"""Strict contracts for immutable Sampler-only DataKey timelines."""

from __future__ import annotations

from engine.contracts import result as result_contracts
from engine.contracts import strict_json
from engine.contracts.data_model import validate_normalized_json_value
from engine.contracts.digest import is_sha256_digest
from engine.contracts.exact_fields import require_exact_fields


SAMPLE_RESULT_SCHEMA_VERSION = 1
SAMPLE_RESULT_FIELDS = frozenset({
    "cycles",
    "schemaVersion",
    "dataKeys",
    "execution",
    "sampleFrameContract",
})
SAMPLE_RESULT_METADATA_FIELDS = SAMPLE_RESULT_FIELDS - {"cycles"}
SAMPLE_RESULT_MANIFEST_FIELDS = frozenset({
    "schemaVersion",
    "sampleResultId",
    "resultFile",
    "contentDigest",
    "size",
    "resultMetadata",
})


def require_sample_result_id(value, label="Sample Result ID"):
    if not isinstance(value, str) or not is_sha256_digest(value):
        raise ValueError(f"{label} must be a canonical SHA-256 digest.")
    return value


def require_execution(value, *, sample_result_id=None):
    require_exact_fields(
        value,
        allowed={"schemaVersion", "sampleResultId", "engineRuntime", "dataset", "sampler"},
        required={"schemaVersion", "sampleResultId", "engineRuntime", "dataset", "sampler"},
        label="Sample Result execution",
    )
    if value["schemaVersion"] != SAMPLE_RESULT_SCHEMA_VERSION:
        raise ValueError("Sample Result execution schemaVersion 1 is required.")
    require_sample_result_id(value["sampleResultId"])
    if sample_result_id is not None and value["sampleResultId"] != sample_result_id:
        raise ValueError("Sample Result execution identity changed.")
    if not isinstance(value["engineRuntime"], dict) or not value["engineRuntime"]:
        raise ValueError("Sample Result engineRuntime identity is required.")
    validate_normalized_json_value(
        value["engineRuntime"], {}, path="SampleResult.execution.engineRuntime"
    )
    dataset = require_exact_fields(
        value["dataset"],
        allowed={"datasetId", "datasetVersionId", "contentHash"},
        required={"datasetId", "datasetVersionId", "contentHash"},
        label="Sample Result Dataset",
    )
    for field in ("datasetId", "datasetVersionId"):
        if not isinstance(dataset[field], str) or not dataset[field]:
            raise ValueError(f"Sample Result Dataset {field} is required.")
    if not is_sha256_digest(dataset["contentHash"]):
        raise ValueError("Sample Result Dataset contentHash is invalid.")
    sampler = require_exact_fields(
        value["sampler"],
        allowed={"samplerId", "version", "contentDigest", "parameters"},
        required={"samplerId", "version", "contentDigest", "parameters"},
        label="Sample Result Sampler",
    )
    for field in ("samplerId", "version"):
        if not isinstance(sampler[field], str) or not sampler[field]:
            raise ValueError(f"Sample Result Sampler {field} is required.")
    if not is_sha256_digest(sampler["contentDigest"]):
        raise ValueError("Sample Result Sampler contentDigest is invalid.")
    if not isinstance(sampler["parameters"], dict):
        raise ValueError("Sample Result Sampler parameters must be an object.")
    validate_normalized_json_value(
        sampler["parameters"], {}, path="SampleResult.execution.sampler.parameters"
    )
    return value


def require_frame_contract(
    value,
    *,
    cycle_count,
    first_cycle_id,
    last_cycle_id,
):
    require_exact_fields(
        value,
        allowed={
            "schemaVersion",
            "frameCount",
            "firstCycleId",
            "lastCycleId",
            "causalityRule",
        },
        required={
            "schemaVersion",
            "frameCount",
            "firstCycleId",
            "lastCycleId",
            "causalityRule",
        },
        label="Sample Result sampleFrameContract",
    )
    if value["schemaVersion"] != 1:
        raise ValueError("Sample Result sampleFrameContract schemaVersion 1 is required.")
    if (
        isinstance(value["frameCount"], bool)
        or not isinstance(value["frameCount"], int)
        or value["frameCount"] < 0
        or value["frameCount"] != cycle_count
    ):
        raise ValueError("Sample Result frameCount does not match cycles.")
    if value["causalityRule"] != "Sampler owns decisionTime and as-of visibility":
        raise ValueError("Sample Result causalityRule is invalid.")
    if (
        value["firstCycleId"] != first_cycle_id
        or value["lastCycleId"] != last_cycle_id
    ):
        raise ValueError("Sample Result cycle boundaries do not match cycles.")
    for field in ("firstCycleId", "lastCycleId"):
        if value[field] is not None and (
            not isinstance(value[field], str) or not value[field]
        ):
            raise ValueError(f"Sample Result {field} is invalid.")
    return value


def require_metadata(
    metadata,
    *,
    sample_result_id,
    cycle_count,
    first_cycle_id,
    last_cycle_id,
    verified_cycle_validator=None,
):
    require_exact_fields(
        metadata,
        allowed=SAMPLE_RESULT_METADATA_FIELDS,
        required=SAMPLE_RESULT_METADATA_FIELDS,
        label="Sample Result metadata",
    )
    if metadata["schemaVersion"] != SAMPLE_RESULT_SCHEMA_VERSION:
        raise ValueError("Sample Result schemaVersion 1 is required.")
    if not isinstance(metadata["dataKeys"], dict):
        raise ValueError("Sample Result dataKeys must be an object.")
    validator = (
        result_contracts.compile_cycle_validator(metadata["dataKeys"])
        if verified_cycle_validator is None
        else verified_cycle_validator
    )
    if not callable(validator):
        raise TypeError("Sample Result cycle validator must be callable.")
    require_execution(metadata["execution"], sample_result_id=sample_result_id)
    require_frame_contract(
        metadata["sampleFrameContract"],
        cycle_count=cycle_count,
        first_cycle_id=first_cycle_id,
        last_cycle_id=last_cycle_id,
    )
    return validator


def require_manifest(value, *, sample_result_id=None):
    require_exact_fields(
        value,
        allowed=SAMPLE_RESULT_MANIFEST_FIELDS,
        required=SAMPLE_RESULT_MANIFEST_FIELDS,
        label="Sample Result manifest",
    )
    if value["schemaVersion"] != SAMPLE_RESULT_SCHEMA_VERSION:
        raise ValueError("Sample Result manifest schemaVersion 1 is required.")
    require_sample_result_id(value["sampleResultId"])
    if sample_result_id is not None and value["sampleResultId"] != sample_result_id:
        raise ValueError("Sample Result manifest identity changed.")
    if value["resultFile"] != "sample-result.json":
        raise ValueError("Sample Result manifest resultFile is invalid.")
    if not is_sha256_digest(value["contentDigest"]):
        raise ValueError("Sample Result manifest contentDigest is invalid.")
    if (
        isinstance(value["size"], bool)
        or not isinstance(value["size"], int)
        or value["size"] < 1
    ):
        raise ValueError("Sample Result manifest size is invalid.")
    require_exact_fields(
        value["resultMetadata"],
        allowed=SAMPLE_RESULT_METADATA_FIELDS,
        required=SAMPLE_RESULT_METADATA_FIELDS,
        label="Sample Result manifest metadata",
    )
    strict_json.dumps(value, sort_keys=True)
    return value


__all__ = (
    "SAMPLE_RESULT_FIELDS",
    "SAMPLE_RESULT_METADATA_FIELDS",
    "SAMPLE_RESULT_SCHEMA_VERSION",
    "require_execution",
    "require_frame_contract",
    "require_manifest",
    "require_metadata",
    "require_sample_result_id",
)
