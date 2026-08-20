"""Exact, read/validate/propose-only TradeEngine Agent tool boundary."""

from __future__ import annotations

import copy
import json
import os
import re

from engine.contracts.data_path import get_data_path
from engine.repository import backtest_results, datasets, module_definitions, pipelines
from engine.runtime.result_projection import ResultCycleProcessor
from engine.runtime.result_stream import ResultArchiveReader
from engine.service import analysis, backtests, datasets as dataset_service, environment, pipelines as pipeline_service
from engine.service import result_projection

from .contracts import validate_context, validate_review_artifact


MAX_TOOL_OUTPUT_BYTES = 256 * 1024
MAX_CATALOG_RESULTS = 50
MAX_DATASET_RECORDS = 100
MAX_RESULT_CYCLES = 25
MAX_UI_PATCH_TEXT = 256 * 1024
_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
_INTERNAL_KEYS = {
    "path",
    "localPath",
    "absolutePath",
    "manifestPath",
    "workspacePath",
    "controlPath",
    "archivePath",
    "storage",
    "uri",
}


class ToolCallError(ValueError):
    def __init__(self, code, message, *, retryable=False):
        super().__init__(message)
        self.code = code
        self.retryable = retryable


def _exact(value, *, allowed, required=(), label="arguments"):
    if type(value) is not dict:
        raise ToolCallError("invalid_arguments", f"{label} must be an object")
    unknown = set(value) - set(allowed)
    missing = set(required) - set(value)
    if unknown:
        raise ToolCallError("invalid_arguments", f"{label} has unknown fields: {', '.join(sorted(unknown))}")
    if missing:
        raise ToolCallError("invalid_arguments", f"{label} is missing fields: {', '.join(sorted(missing))}")
    return value


def _text(value, label, maximum=256):
    if not isinstance(value, str) or not value or value != value.strip() or len(value) > maximum:
        raise ToolCallError("invalid_arguments", f"{label} must be a bounded non-empty string")
    return value


def _integer(value, label, *, minimum, maximum, default):
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise ToolCallError("invalid_arguments", f"{label} must be between {minimum} and {maximum}")
    return value


def _boolean(value, label, *, default=None):
    if value is None and default is not None:
        return default
    if type(value) is not bool:
        raise ToolCallError("invalid_arguments", f"{label} must be a boolean")
    return value


def _required_integer(value, label, *, minimum, maximum):
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise ToolCallError("invalid_arguments", f"{label} must be between {minimum} and {maximum}")
    return value


def _ui_state_arguments(arguments):
    _exact(arguments, allowed=set())
    return {}


def _ui_document_get_arguments(arguments):
    args = _exact(arguments, allowed={"operationId", "documentId", "includeContent"}, required={"documentId"})
    result = {
        "documentId": _text(args["documentId"], "documentId"),
        "includeContent": _boolean(args.get("includeContent"), "includeContent", default=True),
    }
    if "operationId" in args:
        result["operationId"] = _text(args["operationId"], "operationId", 128)
    return result


def _ui_document_patch_arguments(arguments):
    args = _exact(
        arguments,
        allowed={"operationId", "documentId", "baseRevision", "baseDigest", "patch", "save"},
        required={"operationId", "documentId", "baseRevision", "baseDigest", "patch", "save"},
    )
    base_revision = _required_integer(args["baseRevision"], "baseRevision", minimum=0, maximum=2**53 - 1)
    base_digest = _text(args["baseDigest"], "baseDigest", 64)
    if not _DIGEST_RE.fullmatch(base_digest):
        raise ToolCallError("invalid_arguments", "baseDigest must be a lowercase SHA-256 digest")
    patch = _exact(args["patch"], allowed={"type", "start", "end", "text"}, required={"type", "start", "end", "text"}, label="patch")
    if patch["type"] != "replace":
        raise ToolCallError("invalid_arguments", "patch.type must be replace")
    start = _required_integer(patch["start"], "patch.start", minimum=0, maximum=2**53 - 1)
    end = _required_integer(patch["end"], "patch.end", minimum=0, maximum=2**53 - 1)
    if end < start:
        raise ToolCallError("invalid_arguments", "patch.end cannot be before patch.start")
    if not isinstance(patch["text"], str) or len(patch["text"].encode("utf-8")) > MAX_UI_PATCH_TEXT:
        raise ToolCallError("invalid_arguments", "patch.text is too large")
    return {
        "operationId": _text(args["operationId"], "operationId", 128),
        "documentId": _text(args["documentId"], "documentId"),
        "baseRevision": base_revision,
        "baseDigest": base_digest,
        "patch": {"type": "replace", "start": start, "end": end, "text": patch["text"]},
        "save": _boolean(args["save"], "save"),
    }


def _public(value):
    """Remove server filesystem identities from a detached response."""
    if isinstance(value, dict):
        return {
            key: _public(item)
            for key, item in value.items()
            if key not in _INTERNAL_KEYS and not key.lower().endswith("path")
        }
    if isinstance(value, list):
        return [_public(item) for item in value]
    if isinstance(value, tuple):
        return [_public(item) for item in value]
    if isinstance(value, str) and os.path.isabs(value):
        return "<internal>"
    return copy.deepcopy(value)


def _bounded(result):
    encoded = json.dumps(result, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(encoded) > MAX_TOOL_OUTPUT_BYTES:
        raise ToolCallError(
            "tool_output_too_large",
            f"Tool result exceeds the {MAX_TOOL_OUTPUT_BYTES}-byte limit; narrow the query",
        )
    return result


def _resource_summary(kind, item):
    if kind == "pipeline":
        return {key: item.get(key) for key in ("pipelineId", "name", "status", "currentVersion", "manifestHash", "updatedAt")}
    if kind == "dataset":
        return {key: item.get(key) for key in ("datasetId", "name", "status", "latestVersionId", "createdAt")}
    if kind in {"environment", "analysis"}:
        prefix = "environment" if kind == "environment" else "analysis"
        return {key: item.get(key) for key in (f"{prefix}Id", "name", "version", "contentDigest") if key in item}
    if kind == "module":
        return {key: item.get(key) for key in ("kind", "moduleId", "name", "version", "builtin") if key in item}
    if kind in {"backtest", "result"}:
        return {key: item.get(key) for key in ("backtestId", "name", "status", "createdAt", "completedAt", "resultAvailable") if key in item}
    return {}


def _catalog(config, kind):
    if kind == "pipeline":
        return list(pipelines.load_pipelines(config).values())
    if kind == "dataset":
        return datasets.list_datasets(config, 500)
    if kind == "environment":
        return environment.environment_definitions(config)
    if kind == "analysis":
        return analysis.analysis_definitions(config)
    if kind == "module":
        return list(module_definitions.load_all_definitions(config).values())
    if kind in {"backtest", "result"}:
        return backtest_results.list_backtests(config, 500, include_archived=False)
    raise ToolCallError("invalid_arguments", f"Unsupported catalog kind: {kind}")


def _context_reference(config, reference):
    kind = reference["kind"]
    if kind == "pipeline":
        details = pipeline_service.get_pipeline_details(config, reference["id"], reference["version"])
        return {"reference": reference, "summary": _resource_summary(kind, details["pipeline"]), "topology": details["manifest"].get("topology")}
    if kind == "dataset":
        version = datasets.verify_dataset_version_id(config, reference["version"])
        if version["datasetId"] != reference["id"]:
            raise ToolCallError("reference_mismatch", "Dataset version does not belong to the referenced dataset")
        return {"reference": reference, "summary": _resource_summary(kind, datasets.get_dataset(config, reference["id"])), "version": _public({key: version.get(key) for key in ("datasetVersionId", "contentHash", "status", "capabilities", "createdAt", "manifestDigest")})}
    if kind == "environment":
        item = environment.get_environment_definition(config, reference["id"], reference["version"])
        return {"reference": reference, "summary": _resource_summary(kind, item)}
    if kind == "analysis":
        item = analysis.get_analysis_definition(config, reference["id"], reference["version"])
        return {"reference": reference, "summary": _resource_summary(kind, item)}
    if kind.startswith("module:"):
        module_kind = kind.rsplit(":", 1)[-1]
        records, _evidence = module_definitions.load_definition_versions(
            config, [(module_kind, reference["id"], reference["version"])]
        )
        item = next(iter(records.values()))
        return {"reference": reference, "summary": _resource_summary("module", item)}
    if kind in {"backtest", "result"}:
        item = backtest_results.get_backtest_meta(config, reference["id"])
        return {"reference": reference, "summary": _resource_summary(kind, item)}
    raise ToolCallError("invalid_reference", f"Unsupported Context reference kind: {kind}")


def _context_get(config, grant, arguments):
    _exact(arguments, allowed=())
    context = validate_context(grant["context"])
    return {
        "context": context,
        "contextDigest": grant["contextDigest"],
        "resources": [_context_reference(config, reference) for reference in context["references"]],
        "capabilities": [
            "read exact Context resources",
            "find compatible catalog candidates",
            "inspect bounded Dataset and Result data",
            "validate drafts",
            "create display-only review artifacts",
        ],
        "mutationsAllowed": False,
    }


def _catalog_find(config, _grant, arguments):
    args = _exact(arguments, allowed={"kind", "query", "limit"}, required={"kind"})
    kind = _text(args["kind"], "kind", 64)
    query = str(args.get("query") or "").strip().lower()
    limit = _integer(args.get("limit"), "limit", minimum=1, maximum=MAX_CATALOG_RESULTS, default=20)
    candidates = []
    for item in _catalog(config, kind):
        summary = _resource_summary(kind, item)
        searchable = json.dumps(summary, ensure_ascii=False).lower()
        if query and query not in searchable:
            continue
        candidates.append(summary)
        if len(candidates) >= limit:
            break
    return {"kind": kind, "candidates": candidates, "count": len(candidates), "truncated": len(candidates) == limit}


def _dataset_inspect(config, _grant, arguments):
    args = _exact(
        arguments,
        allowed={"datasetId", "version", "limit"},
        required={"datasetId", "version"},
    )
    dataset_id = _text(args["datasetId"], "datasetId", 512)
    version_id = _text(args["version"], "version", 512)
    limit = _integer(args.get("limit"), "limit", minimum=1, maximum=MAX_DATASET_RECORDS, default=20)
    item = datasets.get_dataset(config, dataset_id)
    version = datasets.verify_dataset_version_id(config, version_id)
    if version["datasetId"] != dataset_id:
        raise ToolCallError("reference_mismatch", "Dataset version does not belong to datasetId")
    records = dataset_service.get_dataset_records(config, version_id, limit=limit)
    return {
        "dataset": _resource_summary("dataset", item),
        "version": _public({key: version.get(key) for key in ("datasetVersionId", "contentHash", "status", "capabilities", "createdAt", "manifestDigest", "manifest")}),
        "records": _public(records),
        "limit": limit,
    }


def _validate(config, _grant, arguments):
    args = _exact(arguments, allowed={"kind", "draft"}, required={"kind", "draft"})
    kind = _text(args["kind"], "kind", 64)
    draft = args["draft"]
    if kind in {"pipeline", "environment", "analysis"}:
        # Import lazily to avoid an engine_service -> bridge -> engine_service cycle at boot.
        from engine_service import validate_graph_draft

        return validate_graph_draft(config, {"resourceType": kind, "draft": draft})
    if kind == "backtest-composition":
        return backtests.validate_backtest_composition(config, draft)
    if kind == "module":
        from engine.contracts.module import validate_module_definition

        return {"valid": True, "definition": validate_module_definition(draft)}
    raise ToolCallError("invalid_arguments", f"Unsupported validation kind: {kind}")


def _backtest_get(config, _grant, arguments):
    args = _exact(arguments, allowed={"backtestId"}, required={"backtestId"})
    backtest_id = _text(args["backtestId"], "backtestId", 512)
    meta = backtest_results.get_backtest_meta(config, backtest_id)
    result = {"backtest": _public(meta)}
    try:
        result["result"] = _public(backtest_results.get_backtest_result_view(config, backtest_id))
        result["resultAvailable"] = True
    except ValueError:
        result["resultAvailable"] = False
    return result


def _project_cycle(cycle, paths):
    projected = {
        key: copy.deepcopy(cycle[key])
        for key in ("schemaVersion", "cycleId", "decisionTime")
        if key in cycle
    }
    values = {}
    missing = object()
    for requested in paths:
        relative = requested[len("cycles."):]
        value = get_data_path(cycle, relative, missing)
        if value is not missing:
            values[requested] = copy.deepcopy(value)
    projected["values"] = values
    return projected


def _result_cycles(config, backtest_id, paths, offset, limit):
    validation = result_projection.validate_backtest_result_archive(config, backtest_id)
    evidence = backtest_results.load_result_archive_evidence(config, backtest_id, verify_digest=False)
    with ResultCycleProcessor(evidence["dataKeys"]) as processor:
        processor.require_projection_paths(paths)
        cycles = []
        total = 0
        with ResultArchiveReader(
            evidence["path"],
            expected_digest=evidence["contentDigest"],
            expected_size=evidence["resultSize"],
        ) as reader:
            for index, cycle in enumerate(reader.cycles()):
                normalized = processor.prepare_cycle(index, cycle)
                if index >= offset and len(cycles) < limit:
                    cycles.append(_project_cycle(normalized, paths))
                total = index + 1
            processor.finalize()
    return {"cycles": cycles, "offset": offset, "limit": limit, "total": total, "validation": validation}


def _result_query(config, _grant, arguments):
    args = _exact(
        arguments,
        allowed={"backtestId", "mode", "paths", "offset", "limit"},
        required={"backtestId", "mode"},
    )
    backtest_id = _text(args["backtestId"], "backtestId", 512)
    mode = _text(args["mode"], "mode", 32)
    view = backtest_results.get_backtest_result_view(config, backtest_id)
    if mode == "describe":
        return {"result": _public(view), "validation": result_projection.validate_backtest_result_archive(config, backtest_id)}
    if mode == "fields":
        return {"backtestId": backtest_id, "dataKeys": _public(view.get("dataKeys", {}))}
    if mode != "cycles":
        raise ToolCallError("invalid_arguments", "mode must be describe, fields, or cycles")
    paths = args.get("paths", [])
    if not isinstance(paths, list) or not 1 <= len(paths) <= 16 or len(paths) != len(set(paths)):
        raise ToolCallError("invalid_arguments", "paths must contain 1-16 unique Result paths")
    for index, item in enumerate(paths):
        _text(item, f"paths[{index}]", 256)
        if not item.startswith("cycles.") or item == "cycles.":
            raise ToolCallError("invalid_arguments", "cycle query paths must start with cycles.")
    offset = _integer(args.get("offset"), "offset", minimum=0, maximum=10_000, default=0)
    limit = _integer(args.get("limit"), "limit", minimum=1, maximum=MAX_RESULT_CYCLES, default=10)
    return _result_cycles(config, backtest_id, paths, offset, limit)


def _proposal_create(_config, grant, arguments):
    args = _exact(arguments, allowed={"artifact"}, required={"artifact"})
    artifact = validate_review_artifact(args["artifact"])
    referenced = {
        (reference["kind"], reference["id"], reference.get("version", ""), reference.get("digest", ""))
        for reference in grant["context"]["references"]
    }
    artifact_references = []
    brief = artifact.get("analysisBrief", {})
    for fact in brief.get("confirmedFacts", []):
        artifact_references.extend(fact["references"])
    for calculation in brief.get("calculations", []):
        artifact_references.extend(calculation["references"])
    artifact_references.extend(artifact.get("proposal", {}).get("references", []))
    for reference in artifact_references:
        identity = (reference["kind"], reference["id"], reference.get("version", ""), reference.get("digest", ""))
        if identity not in referenced:
            raise ToolCallError("reference_not_in_context", "Review artifact references must come from this Turn Context")
    return {"artifact": artifact, "displayOnly": True, "contextDigest": grant["contextDigest"]}


_HANDLERS = {
    "trade_context_get": _context_get,
    "trade_catalog_find": _catalog_find,
    "trade_dataset_inspect": _dataset_inspect,
    "trade_validate": _validate,
    "trade_backtest_get": _backtest_get,
    "trade_result_query": _result_query,
    "trade_proposal_create": _proposal_create,
}


_UI_ARGUMENT_VALIDATORS = {
    "trade_ui_state_get": _ui_state_arguments,
    "trade_ui_document_get": _ui_document_get_arguments,
    "trade_ui_document_patch": _ui_document_patch_arguments,
}


def execute_tool(config, grant, tool_name, arguments, *, ui_tool_call=None):
    ui_validator = _UI_ARGUMENT_VALIDATORS.get(tool_name)
    if ui_validator is not None:
        if ui_tool_call is None:
            raise ToolCallError("bridge_unavailable", "Agent UI bridge is unavailable", retryable=True)
        return _bounded(ui_tool_call(tool_name, ui_validator(arguments)))
    handler = _HANDLERS.get(tool_name)
    if handler is None:
        raise ToolCallError("tool_not_allowed", "Unknown TradeEngine Agent tool")
    try:
        return _bounded(_public(handler(config, grant, arguments)))
    except ToolCallError:
        raise
    except (KeyError, TypeError, ValueError) as exc:
        raise ToolCallError("engine_validation_failed", str(exc)) from exc


TOOL_NAMES = tuple(_HANDLERS) + tuple(_UI_ARGUMENT_VALIDATORS)
