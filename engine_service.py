#!/usr/bin/env python3
import argparse
import hmac
import mimetypes
import os
import signal
import tempfile
import threading
import time
from datetime import timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

from engine.core import clock as engine_clock
from engine.authority import module_definition as module_definition_authority
from engine.service import control_api as control
from engine.control import database as engine_database
from engine.service import module_workspaces
from engine.control import schema as control_schema
import dataset_transfer
from engine.compiler import analysis as analysis_compiler
from engine.compiler import environment as environment_compiler
from engine.compiler import pipeline_manifest as pipeline_manifest_compiler
from engine.contracts.module import (
    ANALYSIS_MODULE_KINDS,
    ENGINE_MODULE_KINDS,
    ENVIRONMENT_MODULE_KINDS,
)
from engine.archive import dataset as dataset_archive
from engine.contracts import workspace as workspace_contract
from engine.control import auth as _auth
from engine.core import resource_ids
from engine.jobs.manager import BacktestJobManager, BacktestJobServices
from engine.service.backtest_submissions import (
    PreparedBacktestSubmissionStore,
    prepare_backtest_submission,
)
from engine.jobs.repository import ACTIVE_STATUSES
from engine.repository import backtest_results as result_repository
from engine.repository import control_state
from engine.repository import datasets as dataset_repository
from engine.repository import dataset_build_jobs
from engine.repository import dataset_recipes
from engine.repository import dataset_workspaces as dataset_workspace_repository
from engine.runtime import dataset_build as dataset_build_runtime
from engine.runtime import process_session
from engine.service import dataset_builds
from engine.service import dataset_workspaces as dataset_workspace_service
from engine.runtime import result_runtime
from engine.service import backtest_results as backtest_result_service
from engine.service import result_projection as result_projection_service
from engine.service import analysis as analysis_service
from engine.service import environment as environment_service
from engine.service import jupyter_proxy
from engine.service import jupyter_workspaces
from engine.repository import folders as repository_folders
from engine.repository import graph_resources
from engine.repository import dataset_publication
from engine.repository import module_definitions
from engine.repository import pipelines as pipeline_repository
from engine.repository import samplers as sampler_repository
from engine.service import sampler_workspaces
from engine.service import module_publication
from builtin_implementations import resources as builtin_resources
from engine.service import backtests as backtest_service
from engine.service import pipelines as pipeline_service
from engine.service import visualizations as visualization_service
from engine.archive import version as version_archive
from engine.control.owner import claim_control_owner
from builtin_implementations.visualizer_contracts import (
    visualizer_definition_map,
    visualizer_definitions,
)
from engine.contracts import strict_json


ROOT = Path(__file__).resolve().parent
WEB_ROOT = ROOT / "web"
SPA_ROUTES = {
    "/",
    "/overview",
    "/pipeline",
    "/pipeline/builder",
    "/pipeline/manifest",
    "/environment",
    "/environment-blueprint",
    "/analysis",
    "/visualizers",
    "/analysis-blueprint",
    "/signal-blueprint",
    "/modules",
    "/data",
    "/mining/k-line",
    "/backtests",
    "/result",
    "/agent",
    "/manifest",
}
PROTECTED_PAGE_PATHS = SPA_ROUTES | {"/index.html", "/chart.html"}


def normalize_public_origin(value, label):
    parsed = urlparse(str(value or ""))
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.path not in {"", "/"}
        or parsed.params
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(f"{label} must be an absolute http(s) origin without a path")
    return f"{parsed.scheme}://{parsed.netloc}"


def sanitize_agent_return_path(value):
    if value is None:
        return "/"
    if not isinstance(value, str) or len(value) > 2048:
        raise ValueError("Agent returnTo must be a relative TradeEngine URL")
    parsed = urlparse(value)
    if (
        not value.startswith("/")
        or value.startswith("//")
        or parsed.scheme
        or parsed.netloc
        or parsed.path not in SPA_ROUTES - {"/agent"}
        or parsed.fragment
    ):
        raise ValueError("Agent returnTo must identify a TradeEngine page")
    return value


def is_control_api_path(path):
    return path.startswith("/api/")


def read_json(path, default=None):
    path = Path(path)
    if not path.exists():
        return default
    with path.open(encoding="utf-8") as handle:
        return strict_json.load(handle)


def count_by_kind(definitions):
    result = {}
    for item in definitions.values():
        kind = item.get("kind", "Unknown")
        result[kind] = result.get(kind, 0) + 1
    return result


def engine_module_definitions(config):
    """Return only Pipeline kinds exposed by the Module repository."""
    return {
        key: definition
        for key, definition in module_definitions.load_pipeline_definitions(config).items()
        if definition.get("kind") in ENGINE_MODULE_KINDS
    }


def analysis_module_definitions(config):
    return module_definitions.load_analysis_definitions(config)


def environment_module_definitions(config):
    return module_definitions.load_environment_definitions(config)


def module_definition_authorities(config, instances):
    _definitions, evidence = module_definitions.load_definition_versions(
        config,
        module_definitions.module_references(instances),
    )
    return module_definition_authority.module_definition_authorities_from_record_location_evidence(
        config["releaseRoot"], evidence
    )


def handle_add_engine_module(config, payload):
    """Keep the Module endpoint limited to actual Pipeline Module kinds."""
    kind = payload.get("kind") if isinstance(payload, dict) else None
    if kind not in ENGINE_MODULE_KINDS:
        raise ValueError(
            f"Invalid Pipeline module kind: {kind}. "
            "Use the dedicated Analysis or Environment Module repository endpoint."
        )
    return module_publication.publish_module(config, payload)


def handle_add_analysis_module(config, payload):
    kind = payload.get("kind") if isinstance(payload, dict) else None
    if kind not in ANALYSIS_MODULE_KINDS:
        raise ValueError(f"Invalid Analysis Module kind: {kind}")
    return module_publication.publish_module(
        config, payload, repository="analysis"
    )


def handle_add_environment_module(config, payload):
    kind = payload.get("kind") if isinstance(payload, dict) else None
    if kind not in ENVIRONMENT_MODULE_KINDS:
        raise ValueError(f"Invalid Environment Module kind: {kind}")
    return module_publication.publish_module(
        config, payload, repository="environment"
    )


def handle_save_environment(config, payload):
    authorities = module_definition_authorities(config, payload.get("instances"))
    result = graph_resources.archive_if_changed(
        config,
        "environment",
        payload,
        module_definitions=authorities,
        validate=environment_compiler.validate_environment_definition_authority,
    )
    return {**result, "environmentKey": result["resourceKey"]}


def handle_save_analysis(config, payload):
    authorities = module_definition_authorities(config, payload.get("instances"))
    result = graph_resources.archive_if_changed(
        config,
        "analysis",
        payload,
        module_definitions=authorities,
        validate=analysis_compiler.validate_analysis_definition_authority,
    )
    return {**result, "analysisKey": result["resourceKey"]}


def validate_graph_draft(config, payload):
    control.require_exact_fields(
        payload,
        allowed={"resourceType", "draft"},
        required={"resourceType", "draft"},
        label="Graph validation request",
    )
    resource_type = str(payload["resourceType"] or "").strip()
    draft = payload["draft"]
    if resource_type == "pipeline":
        normalized = pipeline_manifest_compiler.normalize_pipeline_draft(draft)
        manifest = pipeline_service.compile_pipeline_manifest(config, normalized)
        return {
            "valid": True,
            "scope": "complete",
            "topology": manifest["topology"],
            "outputContracts": manifest["signalGraph"]["outputContracts"],
        }
    if resource_type == "environment":
        authorities = module_definition_authorities(
            config, draft.get("instances")
        )
        plan = environment_compiler.compile_environment_draft_authority(
            draft, authorities
        )
        return {
            "valid": True,
            "scope": "internal",
            "topology": plan["topology"],
            "externalInputs": "unresolved",
        }
    if resource_type == "analysis":
        authorities = module_definition_authorities(
            config, draft.get("instances")
        )
        plan = analysis_compiler.compile_analysis_draft_authority(
            draft, authorities
        )
        return {
            "valid": True,
            "scope": "internal",
            "topology": plan["topology"],
            "externalInputs": "unresolved",
        }
    raise ValueError(f"Unsupported Graph resourceType: {resource_type}")


def load_pipeline_manifest(config, pipeline_id, version=""):
    details = pipeline_service.get_pipeline_details(config, pipeline_id, version)
    return (
        details["manifest"],
        details["manifestPath"],
        details["definition"],
    )


class QueryValidationError(ValueError):
    pass


def query_value(query, name, default=None):
    values = query.get(name)
    if values is None:
        return default
    if not isinstance(values, list) or len(values) != 1:
        raise QueryValidationError(
            f"Query parameter '{name}' may only be supplied once."
        )
    value = values[0]
    if not isinstance(value, str):
        raise QueryValidationError(f"Query parameter '{name}' must be a string.")
    return value


def query_limit(query, default=100, maximum=500):
    raw_value = query_value(query, "limit", str(default))
    if not raw_value or not raw_value.isascii() or not raw_value.isdecimal():
        raise QueryValidationError("Query parameter 'limit' must be an integer.")
    try:
        value = int(raw_value)
    except ValueError as exc:
        raise QueryValidationError("Query parameter 'limit' must be an integer.") from exc
    if not 0 <= value <= maximum:
        raise QueryValidationError(
            f"Query parameter 'limit' must be between 0 and {maximum}."
        )
    return value


def query_boolean(query, name, default=False):
    raw_value = query_value(query, name, None)
    if raw_value is None:
        return default
    if raw_value not in {"true", "false"}:
        raise QueryValidationError(
            f"Query parameter '{name}' must be 'true' or 'false'."
        )
    return raw_value == "true"


def query_unique_nonempty_values(query, name):
    values = query.get(name, [])
    if not isinstance(values, list):
        raise QueryValidationError(f"Query parameter '{name}' must be a list.")
    result = []
    seen = set()
    for value in values:
        if not isinstance(value, str):
            raise QueryValidationError(f"Query parameter '{name}' must be a string.")
        if not value.strip():
            raise QueryValidationError(f"Query parameter '{name}' must not be empty.")
        if value in seen:
            raise QueryValidationError(
                f"Query parameter '{name}' contains duplicate value: {value}"
            )
        seen.add(value)
        result.append(value)
    return result


def require_query_fields(query, allowed, label):
    unknown = sorted(set(query) - set(allowed))
    if unknown:
        raise QueryValidationError(
            f"{label} contains unsupported query field(s): " + ", ".join(unknown)
        )
    return query


def limit_mapping(mapping, limit):
    return dict(list((mapping or {}).items())[:limit]) if limit else {}


def iso_utc(value):
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def build_summary(config):
    modules = engine_module_definitions(config)
    analysis_modules = analysis_module_definitions(config)
    environment_modules = environment_module_definitions(config)
    analyses = analysis_service.analysis_definitions(config)
    environments = environment_service.environment_definitions(config)
    user_modules = {
        key: definition
        for key, definition in modules.items()
        if not definition.get("builtin")
    }
    dataset_count = dataset_repository.count_datasets(config)
    backtest_count = result_repository.count_backtests(config)
    pipelines = pipeline_repository.load_pipelines(config)
    pipeline_version_count = len(
        pipeline_repository.load_pipeline_store(config)["versions"]
    )
    pipeline_instance_count = 0
    active_pipelines = {
        pipeline_id: pipeline for pipeline_id, pipeline in pipelines.items()
        if pipeline.get("status") == "active"
    }
    for pipeline_id in active_pipelines:
        definition = pipeline_repository.load_current_pipeline(config, pipeline_id)
        if definition:
            pipeline_instance_count += len(
                pipeline_manifest_compiler.active_instance_ids(definition)
            )
    return {
        "status": "ok",
        "serviceTime": engine_clock.utc_now(),
        "repositories": {
            "pipelineModuleIdentityCount": len({
                (item["kind"], item["moduleId"]) for item in modules.values()
            }),
            "pipelineModuleVersionCount": len(modules),
            "analysisModuleIdentityCount": len({
                (item["kind"], item["moduleId"]) for item in analysis_modules.values()
            }),
            "analysisModuleVersionCount": len(analysis_modules),
            "environmentModuleIdentityCount": len({
                (item["kind"], item["moduleId"]) for item in environment_modules.values()
            }),
            "environmentModuleVersionCount": len(environment_modules),
            "customPipelineModuleIdentityCount": len({
                (item["kind"], item["moduleId"]) for item in user_modules.values()
            }),
            "customPipelineModuleVersionCount": len(user_modules),
            "analysisIdentityCount": len({item["analysisId"] for item in analyses}),
            "analysisVersionCount": len(analyses),
            "environmentIdentityCount": len({item["environmentId"] for item in environments}),
            "environmentVersionCount": len(environments),
            "moduleDefinitionsByKind": count_by_kind(modules),
            "pipelineIdentityCount": len(active_pipelines),
            "pipelineVersionCount": pipeline_version_count,
            "currentPipelineModuleInstanceCount": pipeline_instance_count,
            "datasets": dataset_count,
            "backtests": backtest_count,
        },
    }


def _latest_version_definitions(definitions, identity_fields):
    latest = {}
    for definition in definitions:
        identity = tuple(definition[field] for field in identity_fields)
        current = latest.get(identity)
        if current is None or int(definition["version"]) > int(current["version"]):
            latest[identity] = definition
    return latest


def repository_items(config, repository):
    repository = repository_folders.require_repository(repository)
    if repository == "modules":
        values = engine_module_definitions(config)
        latest = _latest_version_definitions(values.values(), ("kind", "moduleId"))
        return {
            f"{definition['kind']}/{definition['moduleId']}": {
                "itemId": f"{definition['kind']}/{definition['moduleId']}",
                "versionKey": f"{definition['kind']}/{definition['moduleId']}/{definition['version']}",
                "label": definition.get("name") or definition["moduleId"],
                **definition,
                "status": definition.get("status") or "active",
            }
            for definition in latest.values()
        }
    if repository == "analysis-modules":
        values = analysis_module_definitions(config)
        latest = _latest_version_definitions(values.values(), ("kind", "moduleId"))
        return {
            f"{definition['kind']}/{definition['moduleId']}": {
                "itemId": f"{definition['kind']}/{definition['moduleId']}",
                "versionKey": f"{definition['kind']}/{definition['moduleId']}/{definition['version']}",
                "label": definition.get("name") or definition["moduleId"],
                **definition,
                "status": definition.get("status") or "active",
            }
            for definition in latest.values()
        }
    if repository == "environment-modules":
        values = environment_module_definitions(config)
        latest = _latest_version_definitions(values.values(), ("kind", "moduleId"))
        return {
            f"{definition['kind']}/{definition['moduleId']}": {
                "itemId": f"{definition['kind']}/{definition['moduleId']}",
                "versionKey": f"{definition['kind']}/{definition['moduleId']}/{definition['version']}",
                "label": definition.get("name") or definition["moduleId"],
                **definition,
                "status": definition.get("status") or "active",
            }
            for definition in latest.values()
        }
    if repository == "datasets":
        return {
            item["datasetId"]: {
                "itemId": item["datasetId"],
                "label": item.get("name") or item["datasetId"],
                **item,
            }
            for item in dataset_repository.list_datasets(config, 500)
        }
    if repository == "samplers":
        return {
            f"{item['samplerId']}::{item['version']}": {
                "itemId": f"{item['samplerId']}::{item['version']}",
                "label": item.get("name") or item["samplerId"],
                **item,
            }
            for item in sampler_repository.list_samplers(config)
        }
    if repository == "pipelines":
        return {
            pipeline_id: {
                "itemId": pipeline_id,
                "label": pipeline.get("name") or pipeline_id,
                **pipeline,
            }
            for pipeline_id, pipeline in pipeline_repository.load_pipelines(config).items()
        }
    if repository == "environments":
        latest = _latest_version_definitions(
            environment_service.environment_definitions(config),
            ("environmentId",),
        )
        return {
            item["environmentId"]: {
                "itemId": item["environmentId"],
                "versionKey": f"{item['environmentId']}::{item['version']}",
                "label": item.get("name") or item["environmentId"],
                **item,
            }
            for item in latest.values()
        }
    if repository == "analyses":
        latest = _latest_version_definitions(
            analysis_service.analysis_definitions(config),
            ("analysisId",),
        )
        return {
            item["analysisId"]: {
                "itemId": item["analysisId"],
                "versionKey": f"{item['analysisId']}::{item['version']}",
                "label": item.get("name") or item["analysisId"],
                **item,
            }
            for item in latest.values()
        }
    if repository == "scripts":
        return {
            f"{item['recipeId']}::{item['version']}": {
                "itemId": f"{item['recipeId']}::{item['version']}",
                "label": item.get("name") or item["recipeId"],
                "kind": "Submitted Script",
                **item,
            }
            for item in dataset_recipes.list_recipes(config)
        }
    if repository == "workspaces":
        return {
            item["workspaceId"]: {
                "itemId": item["workspaceId"],
                "label": item.get("name") or item["workspaceId"],
                "kind": "Workspace",
                **item,
            }
            for item in dataset_workspace_repository.list_workspaces(config)
        }
    if repository == "backtests":
        return {
            item["backtestId"]: {
                "itemId": item["backtestId"],
                "label": item.get("name") or item["backtestId"],
                **item,
            }
            for item in result_repository.list_backtests(
                config, 500, include_archived=True
            )
        }
    if repository == "data":
        source_types = {
            "datasets": "Dataset",
            "samplers": "Sampler",
            "scripts": "Script",
            "workspaces": "Workspace",
        }
        items = {}
        for source_repository, resource_type in source_types.items():
            for source_item_id, item in repository_items(config, source_repository).items():
                item_id = repository_folders.shared_item_id(source_repository, source_item_id)
                items[item_id] = {
                    **item,
                    "itemId": item_id,
                    "resourceType": resource_type,
                    "sourceRepository": source_repository,
                    "sourceItemId": source_item_id,
                }
        return items
    if repository == "backtest":
        items = {}
        for source_item_id, item in repository_items(config, "backtests").items():
            result_id = repository_folders.shared_item_id("results", source_item_id)
            items[result_id] = {
                **item,
                "itemId": result_id,
                "label": f"{item.get('name') or source_item_id} Result",
                "kind": "Visualization Result",
                "resourceType": "Result",
                "sourceRepository": "results",
                "sourceItemId": source_item_id,
                "backtestId": item.get("backtestId") or source_item_id,
            }
        return items
    raise ValueError(f"Repository item adapter is missing: {repository}")


def repository_catalog(config, repository):
    repository = repository_folders.require_repository(repository)
    with repository_folders.repository_read_snapshot(config):
        tree = repository_folders.repository_tree(config, repository)
        items = repository_items(config, repository)
        decorated = []
        for item_id, item in items.items():
            placement = repository_folders.resolve_item_folder(tree, item_id, item)
            decorated.append({**item, **placement})
    decorated.sort(key=lambda item: (
        item["folderPath"].casefold(),
        str(item.get("label") or "").casefold(),
    ))
    return {**tree, "items": decorated, "total": len(decorated)}


def request_is_secure(handler):
    if bool(handler.config.get("allowInsecureAuth")):
        return True
    forwarded = handler.headers.get("X-Forwarded-Proto", "").split(",", 1)[0].strip().lower()
    return forwarded == "https"


def write_security_headers(handler, *, no_store=False):
    handler.send_header("X-Content-Type-Options", "nosniff")
    handler.send_header("X-Frame-Options", "DENY")
    handler.send_header("Referrer-Policy", "no-referrer")
    handler.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    connect_sources = ["'self'"]
    agent_origin = getattr(handler, "agent_public_url", "")
    if agent_origin:
        parsed_agent = urlparse(agent_origin)
        if parsed_agent.scheme in {"http", "https"} and parsed_agent.netloc:
            websocket_scheme = "wss" if parsed_agent.scheme == "https" else "ws"
            connect_sources.append(f"{websocket_scheme}://{parsed_agent.netloc}")
    handler.send_header(
        "Content-Security-Policy",
        "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
        f"img-src 'self' data: blob:; connect-src {' '.join(connect_sources)}; worker-src 'self' blob:; "
        "object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'",
    )
    if request_is_secure(handler) and not handler.config.get("allowInsecureAuth"):
        handler.send_header("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    if no_store:
        handler.send_header("Cache-Control", "no-store")


def write_extra_headers(handler, headers):
    for name, value in headers or []:
        handler.send_header(name, value)


def response_json(handler, status, payload, headers=None):
    body = strict_json.dumps(payload, separators=(",", ":")).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    write_security_headers(handler, no_store=True)
    write_extra_headers(handler, headers)
    handler.end_headers()
    handler.wfile.write(body)


def response_text(handler, status, content, content_type, headers=None):
    body = content if isinstance(content, bytes) else content.encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", content_type)
    handler.send_header("Content-Length", str(len(body)))
    # One server policy governs all frontend assets.  The browser must not bind
    # a source file to a manually maintained query-string version.
    write_security_headers(handler, no_store=True)
    write_extra_headers(handler, headers)
    handler.end_headers()
    handler.wfile.write(body)


def response_file(handler, path, filename, content_type="application/zip"):
    path = Path(path)
    safe_ascii = "".join(char if char.isascii() and (char.isalnum() or char in "-_.") else "_" for char in filename)
    handler.send_response(200)
    handler.send_header("Content-Type", content_type)
    handler.send_header("Content-Length", str(path.stat().st_size))
    handler.send_header(
        "Content-Disposition",
        f"attachment; filename=\"{safe_ascii or 'datasets.zip'}\"; filename*=UTF-8''{quote(filename)}",
    )
    write_security_headers(handler, no_store=True)
    handler.end_headers()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            handler.wfile.write(chunk)


def response_json_file(handler, status, path, *, prefix=b"", suffix=b""):
    """Stream one already-encoded JSON document without a host-memory copy."""
    path = Path(path)
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header(
        "Content-Length", str(len(prefix) + path.stat().st_size + len(suffix))
    )
    write_security_headers(handler, no_store=True)
    handler.end_headers()
    if prefix:
        handler.wfile.write(prefix)
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            handler.wfile.write(chunk)
    if suffix:
        handler.wfile.write(suffix)


def response_backtest_result_slice(
    handler, config, backtest_id, paths, temporary_modules
):
    with tempfile.TemporaryDirectory(prefix="trade-result-http-") as root:
        document = Path(root) / "result.json"
        result_projection_service.write_backtest_result_slice(
            config,
            backtest_id,
            paths,
            temporary_modules,
            document,
            module_definitions_loader=(
                lambda: module_definitions.load_pipeline_definitions(config)
                if temporary_modules
                else None
            ),
        )
        prefix = (
            '{"backtestId":'
            + strict_json.dumps(backtest_id, separators=(",", ":"))
            + ',"result":'
        ).encode("utf-8")
        response_json_file(
            handler, 200, document, prefix=prefix, suffix=b"}"
        )


def response_redirect(handler, location, status=303, headers=None):
    handler.send_response(status)
    handler.send_header("Location", location)
    handler.send_header("Content-Length", "0")
    write_security_headers(handler, no_store=True)
    write_extra_headers(handler, headers)
    handler.end_headers()


def read_request_json(handler, max_bytes=None):
    length = int(handler.headers.get("Content-Length", "0"))
    if length <= 0:
        return {}
    if max_bytes is not None and length > max_bytes:
        raise ValueError("Request body is too large.")
    return strict_json.loads(handler.rfile.read(length) or b"{}")


def require_empty_json_request(payload, label):
    """Require an explicit no-parameter POST contract without ignoring fields."""

    return control.require_exact_fields(
        payload,
        allowed=set(),
        required=set(),
        label=label,
    )


def split_shared_item(item_id):
    source, separator, source_item_id = str(item_id or "").partition("::")
    if not separator or not source_item_id:
        raise ValueError(f"Invalid shared repository item ID: {item_id}")
    return source, source_item_id


def rename_repository_resource(config, repository, item_id, name):
    repository = repository_folders.require_repository(repository)
    if repository == "pipelines":
        return pipeline_service.rename_pipeline(config, item_id, name)
    if repository == "data":
        source, source_item_id = split_shared_item(item_id)
        if source == "datasets":
            return dataset_repository.rename_dataset(config, source_item_id, name)
        if source == "workspaces":
            return dataset_workspace_repository.rename_workspace(
                config, source_item_id, name
            )
        raise ValueError(f"Rename is not supported for Data resource type '{source}'.")
    if repository == "backtest":
        source, source_item_id = split_shared_item(item_id)
        if source in {"backtests", "results"}:
            return result_repository.rename_backtest(config, source_item_id, name)
        raise ValueError(f"Rename is not supported for Backtest resource type '{source}'.")
    raise ValueError(f"Rename is not supported in repository '{repository}'.")


class EngineServiceHandler(BaseHTTPRequestHandler):
    config = None
    public_url = ""
    agent_public_url = ""
    backtest_job_manager = None
    prepared_backtest_submissions = None
    stopping = threading.Event()
    agent_tool_grant_store = None
    agent_bridge_token = ""
    mining_api = None

    def require_agent_bridge(self):
        expected = self.agent_bridge_token
        authorization = self.headers.get("Authorization", "")
        supplied = authorization[7:] if authorization.startswith("Bearer ") else ""
        if not expected or not supplied or not hmac.compare_digest(expected, supplied):
            response_json(self, 403, {"error": "Agent bridge authorization failed", "code": "bridge_forbidden"})
            return False
        return True

    def handle_agent_tool_grant_create(self):
        if not self.require_agent_bridge():
            return
        from trade_agent_bridge.contracts import validate_context

        payload = read_request_json(self, max_bytes=32 * 1024)
        control.require_exact_fields(
            payload,
            allowed={"ownerId", "chatId", "turnId", "contextDigest", "context", "scopes"},
            required={"ownerId", "chatId", "turnId", "contextDigest", "context", "scopes"},
            label="Agent tool grant request",
        )
        canonical = validate_context(payload["context"])
        from trade_agent_bridge.contracts import context_digest

        if not hmac.compare_digest(context_digest(canonical), str(payload["contextDigest"])):
            response_json(self, 400, {"error": "Agent tool grant Context digest mismatch", "code": "context_digest_mismatch"})
            return
        try:
            result = self.agent_tool_grant_store.create(
                owner_id=payload["ownerId"],
                chat_id=payload["chatId"],
                turn_id=payload["turnId"],
                context_digest=payload["contextDigest"],
                context=canonical,
                scopes=payload["scopes"],
            )
        except ValueError as exc:
            response_json(self, 400, {"error": str(exc), "code": "invalid_grant_request"})
            return
        response_json(self, 201, result)

    def handle_agent_tool_call(self):
        from trade_agent_bridge import ToolGrantError
        from trade_agent_bridge.tool_api import ToolCallError, execute_tool
        from trade_agent_bridge.ui_tool_bridge import UiToolBridgeError, call_ui_tool

        authorization = self.headers.get("Authorization", "")
        grant_token = authorization[7:] if authorization.startswith("Bearer ") else ""
        payload = read_request_json(self, max_bytes=256 * 1024)
        try:
            control.require_exact_fields(
                payload,
                allowed={"tool", "arguments"},
                required={"tool", "arguments"},
                label="Agent tool call",
            )
            grant = self.agent_tool_grant_store.authorize(grant_token, payload["tool"])
            def call_live_ui(tool_name, arguments):
                try:
                    return call_ui_tool(
                        self.agent_public_url,
                        self.agent_bridge_token,
                        tool_name,
                        arguments,
                    )
                except UiToolBridgeError as exc:
                    raise ToolCallError(exc.code, str(exc), retryable=exc.retryable) from exc

            result = execute_tool(
                self.config,
                grant,
                payload["tool"],
                payload["arguments"],
                ui_tool_call=call_live_ui,
            )
        except ToolGrantError as exc:
            response_json(self, 403, {"error": str(exc), "code": exc.code, "retryable": False})
            return
        except ToolCallError as exc:
            response_json(self, 400, {"error": str(exc), "code": exc.code, "retryable": exc.retryable})
            return
        response_json(self, 200, {"result": result})

    def handle_agent_tool_grant_revoke(self):
        if not self.require_agent_bridge():
            return
        payload = read_request_json(self, max_bytes=16 * 1024)
        control.require_exact_fields(
            payload,
            allowed={"turnId"},
            required={"turnId"},
            label="Agent tool grant revoke request",
        )
        turn_id = payload["turnId"]
        if not isinstance(turn_id, str) or not turn_id or len(turn_id) > 256:
            response_json(self, 400, {"error": "Agent tool grant turnId is invalid", "code": "invalid_grant_request"})
            return
        revoked = self.agent_tool_grant_store.revoke_turn(turn_id)
        response_json(self, 200, {"revoked": revoked})

    def reject_if_stopping(self):
        if not self.stopping.is_set():
            return False
        response_json(self, 503, {"error": "Engine service is stopping."})
        return True

    def client_ip(self):
        forwarded = self.headers.get("X-Forwarded-For", "")
        if forwarded:
            return forwarded.split(",")[-1].strip()
        return str(self.client_address[0] if self.client_address else "")

    def secure_page_or_redirect(self):
        if request_is_secure(self):
            return True
        target = self.public_url.rstrip("/") + self.path
        response_redirect(self, target, status=307)
        return False

    def require_secure_transport(self):
        if request_is_secure(self):
            return True
        response_json(self, 426, {"error": "HTTPS is required."})
        return False

    def require_authentication(self, *, require_csrf=False):
        if not self.require_secure_transport():
            return None
        session = _auth.authenticate(self.config, self.headers.get("Cookie", ""))
        if not session:
            response_json(
                self,
                401,
                {"error": "Authentication required."},
                headers=[
                    ("Set-Cookie", _auth.expired_session_cookie(secure=not self.config.get("allowInsecureAuth"))),
                    ("Set-Cookie", _auth.expired_csrf_cookie(secure=not self.config.get("allowInsecureAuth"))),
                ],
            )
            return None
        if require_csrf:
            header_token = self.headers.get("X-CSRF-Token", "")
            cookie_token = _auth.cookie_value(self.headers.get("Cookie", ""), _auth.CSRF_COOKIE)
            tokens_match = bool(header_token and cookie_token and hmac.compare_digest(header_token, cookie_token))
            if not tokens_match or not _auth.validate_csrf(session, header_token):
                response_json(self, 403, {"error": "CSRF validation failed."})
                return None
        self.auth_session = session
        return session

    def authenticated_page(self):
        if not self.secure_page_or_redirect():
            return False
        session = _auth.authenticate(self.config, self.headers.get("Cookie", ""))
        if session:
            self.auth_session = session
            return True
        next_path = quote(self.path if self.path.startswith("/") else "/", safe="")
        response_redirect(self, f"/login?next={next_path}", status=303)
        return False

    def redirect_to_agent(self, parsed):
        if not self.agent_public_url:
            response_json(self, 503, {"error": "Agent Web is not configured."})
            return
        try:
            query = parse_qs(parsed.query, keep_blank_values=True)
            require_query_fields(query, {"returnTo"}, "Agent entry request")
            values = query.get("returnTo", [])
            if len(values) > 1:
                raise ValueError("Agent entry request contains repeated returnTo")
            return_to = sanitize_agent_return_path(values[0] if values else "/")
        except (QueryValidationError, ValueError) as exc:
            response_json(self, 400, {"error": str(exc)})
            return
        separator = "&" if "?" in self.agent_public_url else "?"
        target = (
            f"{self.agent_public_url.rstrip('/')}/{separator}"
            f"returnTo={quote(return_to, safe='')}"
        )
        response_redirect(self, target, status=303)

    def origin_is_same_site(self):
        origin = self.headers.get("Origin", "").rstrip("/")
        if not origin:
            return True
        forwarded = self.headers.get("X-Forwarded-Proto", "").split(",", 1)[0].strip().lower()
        scheme = forwarded or ("http" if self.config.get("allowInsecureAuth") else "https")
        expected = f"{scheme}://{self.headers.get('Host', '')}".rstrip("/")
        return hmac.compare_digest(origin, expected)

    def handle_auth_session(self):
        session = self.require_authentication()
        if not session:
            return
        csrf_token = _auth.cookie_value(self.headers.get("Cookie", ""), _auth.CSRF_COOKIE)
        if not _auth.validate_csrf(session, csrf_token):
            _auth.logout(self.config, session)
            response_json(
                self,
                401,
                {"error": "Session security token is missing. Sign in again."},
                headers=[
                    ("Set-Cookie", _auth.expired_session_cookie(secure=not self.config.get("allowInsecureAuth"))),
                    ("Set-Cookie", _auth.expired_csrf_cookie(secure=not self.config.get("allowInsecureAuth"))),
                ],
            )
            return
        response_json(self, 200, {
            "authenticated": True,
            "user": session["user"],
            "csrfToken": csrf_token,
            "expiresAt": session["expiresAt"],
        })

    def handle_auth_login(self):
        if not self.require_secure_transport():
            return
        if not self.origin_is_same_site():
            response_json(self, 403, {"error": "Cross-site login is not allowed."})
            return
        if self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower() != "application/json":
            response_json(self, 415, {"error": "Login requires application/json."})
            return
        try:
            payload = read_request_json(self, max_bytes=16 * 1024)
            control.require_exact_fields(
                payload,
                allowed={"email", "password"},
                required={"email", "password"},
                label="Login request",
            )
            result = _auth.login(
                self.config,
                payload.get("email"),
                payload.get("password"),
                self.client_ip(),
            )
        except PermissionError as exc:
            response_json(self, 429, {"accepted": False, "error": str(exc)})
            return
        except ValueError:
            response_json(self, 401, {"accepted": False, "error": "Invalid email or password."})
            return
        secure_cookie = not self.config.get("allowInsecureAuth")
        response_json(
            self,
            200,
            {
                "accepted": True,
                "authenticated": True,
                "user": result["user"],
                "csrfToken": result["csrfToken"],
                "expiresAt": result["expiresAt"],
            },
            headers=[
                ("Set-Cookie", _auth.session_cookie(result["token"], secure=secure_cookie)),
                ("Set-Cookie", _auth.csrf_cookie(result["csrfToken"], secure=secure_cookie)),
            ],
        )

    def handle_auth_logout(self):
        session = self.require_authentication(require_csrf=True)
        if not session:
            return
        try:
            query = parse_qs(urlparse(self.path).query, keep_blank_values=True)
            require_query_fields(query, set(), "Logout request")
            require_empty_json_request(
                read_request_json(self, max_bytes=16 * 1024),
                "Logout request",
            )
        except ValueError as exc:
            response_json(self, 400, {"error": str(exc)})
            return
        _auth.logout(self.config, session)
        secure_cookie = not self.config.get("allowInsecureAuth")
        response_json(
            self,
            200,
            {"accepted": True},
            headers=[
                ("Set-Cookie", _auth.expired_session_cookie(secure=secure_cookie)),
                ("Set-Cookie", _auth.expired_csrf_cookie(secure=secure_cookie)),
            ],
        )

    def do_HEAD(self):
        parsed = urlparse(self.path)
        path = parsed.path
        if is_control_api_path(path) or path.startswith(workspace_contract.jupyter_base_url(self.config)):
            if not self.require_authentication():
                return
        if path in PROTECTED_PAGE_PATHS and not self.authenticated_page():
            return
        if path in {"/login", "/login.html"} and not self.secure_page_or_redirect():
            return
        if parsed.path.startswith(workspace_contract.jupyter_base_url(self.config)):
            try:
                jupyter_proxy.proxy_request(self, jupyter_workspaces.resolve_proxy_instance(self.path))
            except Exception as exc:
                response_json(self, 502, {"error": str(exc)})
            return
        if path in SPA_ROUTES or path == "/index.html":
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            write_security_headers(self, no_store=True)
            self.end_headers()
            return
        if path in {"/login", "/login.html"}:
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            write_security_headers(self, no_store=True)
            self.end_headers()
            return
        static_path = (WEB_ROOT / parsed.path.lstrip("/")).resolve()
        if static_path.is_file() and WEB_ROOT in static_path.parents:
            self.send_response(200)
            self.send_header("Content-Type", mimetypes.guess_type(static_path.name)[0] or "application/octet-stream")
            write_security_headers(self, no_store=True)
            self.end_headers()
            return
        self.send_response(404)
        self.end_headers()

    def do_OPTIONS(self):
        if self.reject_if_stopping():
            return
        path = urlparse(self.path).path
        if path == "/auth/login":
            if not self.require_secure_transport() or not self.origin_is_same_site():
                return
            self.send_response(204)
            write_security_headers(self, no_store=True)
            self.end_headers()
            return
        if is_control_api_path(path) or path.startswith("/auth/") or path.startswith(workspace_contract.jupyter_base_url(self.config)):
            if not self.require_authentication():
                return
        if path.startswith(workspace_contract.jupyter_base_url(self.config)):
            try:
                jupyter_proxy.proxy_request(self, jupyter_workspaces.resolve_proxy_instance(self.path))
            except Exception as exc:
                response_json(self, 502, {"error": str(exc)})
            return
        self.send_response(204)
        self.send_header("Access-Control-Allow-Methods", "GET,POST,DELETE,OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type,X-CSRF-Token")
        write_security_headers(self, no_store=True)
        self.end_headers()

    def do_GET(self):
        if self.reject_if_stopping():
            return
        parsed = urlparse(self.path)
        path = parsed.path
        if path == "/auth/session":
            self.handle_auth_session()
            return
        if path in {"/login", "/login.html"}:
            if not self.secure_page_or_redirect():
                return
            existing = _auth.authenticate(self.config, self.headers.get("Cookie", ""))
            if existing:
                response_redirect(self, "/", status=303)
                return
            response_text(self, 200, (WEB_ROOT / "login.html").read_text(encoding="utf-8"), "text/html; charset=utf-8")
            return
        if is_control_api_path(path) or path.startswith(workspace_contract.jupyter_base_url(self.config)):
            if not self.require_authentication():
                return
        if path in PROTECTED_PAGE_PATHS and not self.authenticated_page():
            return
        if path == "/agent":
            self.redirect_to_agent(parsed)
            return
        if path.startswith(workspace_contract.jupyter_base_url(self.config)):
            try:
                jupyter_proxy.proxy_request(self, jupyter_workspaces.resolve_proxy_instance(self.path))
            except Exception as exc:
                response_json(self, 502, {"error": str(exc)})
            return
        query = parse_qs(parsed.query, keep_blank_values=True)
        try:
            if path.startswith("/api/mining/"):
                if self.mining_api is None:
                    from mining.api import DisabledMiningApi

                    self.mining_api = DisabledMiningApi()
                try:
                    status, payload = self.mining_api.handle_get(path, query)
                except ValueError as exc:
                    response_json(self, 400, {"error": str(exc)})
                    return
                response_json(self, status, payload)
                return
            if path == "/api/health":
                require_query_fields(query, set(), "Health request")
                response_json(self, 200, {
                    "status": "ok",
                    "publicUrl": self.public_url,
                    "serviceTime": engine_clock.utc_now(),
                })
                return
            if path == "/api/ui-sync/config":
                require_query_fields(query, set(), "UI sync configuration request")
                agent = urlparse(self.agent_public_url)
                websocket_scheme = "wss" if agent.scheme == "https" else "ws"
                response_json(self, 200, {
                    "protocolVersion": 1,
                    "webSocketUrl": f"{websocket_scheme}://{agent.netloc}/ws/ui",
                })
                return
            if path == "/api/summary":
                require_query_fields(query, set(), "Summary request")
                response_json(self, 200, build_summary(self.config))
                return
            if path == "/api/visualizers":
                require_query_fields(query, set(), "Visualizer repository request")
                response_json(self, 200, {"visualizers": visualizer_definitions()})
                return
            if path == "/api/pipelines":
                require_query_fields(query, set(), "Pipeline repository request")
                pipelines = pipeline_repository.load_pipelines(self.config)
                versions = [
                    version
                    for pipeline_id in sorted(pipelines)
                    for version in pipeline_repository.pipeline_versions(
                        self.config, pipeline_id
                    )
                ]
                response_json(self, 200, {"pipelines": pipelines, "versions": versions})
                return
            pipeline_parts = [part for part in path.split("/") if part]
            if len(pipeline_parts) == 4 and pipeline_parts[:2] == ["api", "pipelines"] and pipeline_parts[3] == "versions":
                require_query_fields(query, set(), "Pipeline versions request")
                pipeline_id = pipeline_manifest_compiler.normalize_pipeline_id(
                    pipeline_parts[2]
                )
                versions = pipeline_repository.pipeline_versions(
                    self.config, pipeline_id
                )
                response_json(self, 200, {
                    "pipelineId": pipeline_id,
                    "versions": versions,
                    "currentVersion": next((row["version"] for row in versions if row["current"]), ""),
                })
                return
            if len(pipeline_parts) == 5 and pipeline_parts[:2] == ["api", "pipelines"] and pipeline_parts[3] == "versions":
                require_query_fields(query, set(), "Pipeline version request")
                pipeline_id = pipeline_manifest_compiler.normalize_pipeline_id(
                    pipeline_parts[2]
                )
                result = pipeline_service.load_pipeline_version_details(
                    self.config, pipeline_id, pipeline_parts[4]
                )
                response_json(self, 200, {"pipelineId": pipeline_id, **result})
                return
            if len(pipeline_parts) == 3 and pipeline_parts[:2] == ["api", "pipelines"]:
                require_query_fields(query, set(), "Pipeline request")
                pipeline_id = pipeline_manifest_compiler.normalize_pipeline_id(
                    pipeline_parts[2]
                )
                response_json(
                    self,
                    200,
                    pipeline_service.get_pipeline_details(
                        self.config, pipeline_id
                    ),
                )
                return
            if path == "/api/repositories":
                require_query_fields(query, {"repository"}, "Repository request")
                requested_repository = query_value(query, "repository", "modules")
                response_json(self, 200, repository_catalog(self.config, requested_repository))
                return
            if path == "/api/modules":
                require_query_fields(query, {"kind", "limit"}, "Module repository request")
                modules = engine_module_definitions(self.config)
                requested_kind = query_value(query, "kind", "")
                if requested_kind:
                    modules = {
                        key: value
                        for key, value in modules.items()
                        if str(value.get("kind", "")).lower() == requested_kind.lower()
                    }
                response_json(self, 200, {
                    "modules": limit_mapping(modules, query_limit(query, 80, 500)),
                    "total": len(modules),
                    "kind": requested_kind,
                })
                return
            if path == "/api/environment-modules":
                require_query_fields(query, {"limit"}, "Environment Module repository request")
                modules = environment_module_definitions(self.config)
                response_json(self, 200, {
                    "modules": limit_mapping(modules, query_limit(query, 80, 500)),
                    "total": len(modules),
                })
                return
            if path == "/api/analysis-modules":
                require_query_fields(query, {"limit"}, "Analysis Module repository request")
                modules = analysis_module_definitions(self.config)
                response_json(self, 200, {
                    "modules": limit_mapping(modules, query_limit(query, 80, 500)),
                    "total": len(modules),
                })
                return
            if path == "/api/data/search":
                require_query_fields(query, {"q"}, "Dataset search request")
                q = query_value(query, "q", "")
                response_json(
                    self,
                    200,
                    dataset_repository.search_datasets(self.config, q),
                )
                return
            if path == "/api/data/datasets":
                require_query_fields(query, {"limit"}, "Dataset repository request")
                limit = query_limit(query, 50, 500)
                datasets = dataset_repository.list_datasets(self.config, limit)
                response_json(self, 200, {
                    "datasets": datasets,
                    "versions": dataset_repository.list_dataset_version_summaries(
                        self.config,
                        [item["datasetId"] for item in datasets],
                    ),
                    "total": dataset_repository.count_datasets(self.config),
                })
                return
            if path == "/api/data/samplers":
                require_query_fields(query, set(), "Sampler repository request")
                samplers = sampler_repository.list_samplers(self.config)
                response_json(self, 200, {"samplers": samplers, "total": len(samplers)})
                return
            if path == "/api/environments":
                require_query_fields(query, set(), "Environment repository request")
                environments = environment_service.environment_definitions(
                    self.config
                )
                response_json(self, 200, {"environments": environments, "total": len(environments)})
                return
            if path == "/api/analyses":
                require_query_fields(query, set(), "Analysis repository request")
                analyses = analysis_service.analysis_definitions(self.config)
                response_json(self, 200, {"analyses": analyses, "total": len(analyses)})
                return
            if path == "/api/data/workspaces":
                require_query_fields(query, set(), "Dataset workspace repository request")
                workspaces = dataset_workspace_repository.list_workspaces(self.config)
                response_json(self, 200, {"workspaces": workspaces, "total": len(workspaces)})
                return
            if path == "/api/data/jupyter":
                require_query_fields(query, set(), "Dataset Jupyter status request")
                response_json(self, 200, jupyter_workspaces.status(self.config))
                return
            if path == "/api/data/recipes":
                require_query_fields(query, set(), "Dataset recipe repository request")
                recipes = dataset_recipes.list_recipes(self.config)
                response_json(self, 200, {"recipes": recipes, "total": len(recipes)})
                return
            if path == "/api/data/builds":
                require_query_fields(query, set(), "Dataset build repository request")
                jobs = dataset_build_jobs.list_build_jobs(self.config)
                response_json(self, 200, {"jobs": jobs, "total": len(jobs)})
                return
            if path == "/api/data/datasets/download":
                require_query_fields(query, {"datasetId"}, "Dataset download")
                dataset_ids = query_unique_nonempty_values(query, "datasetId")
                self.send_dataset_archive(dataset_ids)
                return
            parts = [part for part in path.split("/") if part]
            if len(parts) == 4 and parts[:3] == ["api", "data", "workspaces"]:
                require_query_fields(query, set(), "Dataset workspace request")
                response_json(
                    self, 200,
                    dataset_workspace_repository.get_workspace(self.config, parts[3]),
                )
                return
            if len(parts) == 5 and parts[:3] == ["api", "data", "workspaces"] and parts[4] == "scripts":
                require_query_fields(query, set(), "Dataset workspace scripts request")
                scripts = dataset_workspace_repository.list_workspace_scripts(
                    self.config, parts[3]
                )
                response_json(self, 200, {"workspaceId": parts[3], "scripts": scripts, "total": len(scripts)})
                return
            if len(parts) == 5 and parts[:3] == ["api", "data", "workspaces"] and parts[4] == "script":
                require_query_fields(query, {"path"}, "Dataset workspace script request")
                workspace = dataset_workspace_repository.get_workspace(
                    self.config, parts[3]
                )
                script_path = query_value(query, "path", "")
                response_json(self, 200, {
                    "workspaceId": parts[3],
                    "path": script_path,
                    "scriptText": dataset_workspace_repository.read_workspace_script(
                        workspace, script_path
                    ),
                })
                return
            if len(parts) == 4 and parts[:3] == ["api", "data", "builds"]:
                require_query_fields(query, set(), "Dataset build request")
                response_json(
                    self, 200,
                    dataset_build_jobs.get_build_job(self.config, parts[3]),
                )
                return
            if len(parts) == 4 and parts[:3] == ["api", "data", "datasets"]:
                require_query_fields(query, set(), "Dataset request")
                response_json(
                    self,
                    200,
                    dataset_repository.get_dataset(self.config, parts[3]),
                )
                return
            if len(parts) == 5 and parts[:3] == ["api", "data", "datasets"] and parts[4] == "versions":
                require_query_fields(query, set(), "Dataset versions request")
                versions = dataset_repository.list_dataset_versions(
                    self.config, parts[3]
                )
                response_json(self, 200, {
                    "dataset": dataset_repository.get_dataset(
                        self.config, parts[3]
                    ),
                    "versions": versions,
                    "total": len(versions),
                })
                return
            if len(parts) == 5 and parts[:3] == ["api", "data", "datasets"] and parts[4] == "download":
                require_query_fields(query, set(), "Dataset download")
                self.send_dataset_archive([parts[3]])
                return
            if path == "/api/backtests":
                require_query_fields(
                    query, {"limit", "includeArchived"}, "Backtest repository request"
                )
                limit = query_limit(query, 50, 500)
                include_archived = query_boolean(query, "includeArchived", False)
                backtests = result_repository.list_backtests(
                    self.config, limit, include_archived=include_archived
                )
                response_json(self, 200, {
                    "backtests": backtests,
                    "total": result_repository.count_backtests(
                        self.config, include_archived=include_archived
                    ),
                })
                return
            if path == "/api/backtest-jobs":
                require_query_fields(query, {"limit"}, "Backtest job repository request")
                limit = query_limit(query, 50, 500)
                jobs = self.backtest_job_manager.list(limit)
                response_json(self, 200, {
                    "jobs": jobs,
                    "total": len(jobs),
                    "maxConcurrent": self.backtest_job_manager.max_workers,
                    "active": sum(job["status"] in ACTIVE_STATUSES for job in jobs),
                })
                return
            if len(parts) == 3 and parts[:2] == ["api", "backtest-jobs"]:
                require_query_fields(query, set(), "Backtest job request")
                response_json(self, 200, {"job": self.backtest_job_manager.get(parts[2])})
                return
            if len(parts) == 4 and parts[:2] == ["api", "backtests"] and parts[3] == "meta":
                require_query_fields(query, set(), "Backtest metadata request")
                response_json(
                    self,
                    200,
                    result_repository.get_backtest_meta(self.config, parts[2]),
                )
                return
            if len(parts) == 4 and parts[:2] == ["api", "backtests"] and parts[3] == "view":
                require_query_fields(query, set(), "Backtest Result view request")
                response_json(
                    self,
                    200,
                    result_repository.get_backtest_result_view(self.config, parts[2]),
                )
                return
            if len(parts) == 4 and parts[:2] == ["api", "backtests"] and parts[3] == "result":
                require_query_fields(query, {"path"}, "Result slice")
                paths = query_unique_nonempty_values(query, "path")
                response_backtest_result_slice(
                    self, self.config, parts[2], paths, []
                )
                return
            if path == "/api/visualizations":
                require_query_fields(query, {"backtestId"}, "Visualization repository request")
                backtest_id = query_value(query, "backtestId", "")
                response_json(self, 200, {
                    "visualizations": visualization_service.list_visualizations(
                        self.config,
                        backtest_id,
                    ),
                })
                return
            if path == "/api/history":
                require_query_fields(query, {"limit", "full"}, "History request")
                limit = query_limit(query, 100, 500)
                payload = {
                    "events": control_state.load_sanitized_history_events(
                        self.config,
                        limit,
                    ),
                }
                if query_boolean(query, "full", False):
                    payload.update({
                        "pipelineStore": pipeline_repository.load_pipeline_store(
                            self.config
                        ),
                    })
                response_json(self, 200, payload)
                return
            if path in SPA_ROUTES or path == "/index.html":
                response_text(self, 200, (WEB_ROOT / "index.html").read_text(encoding="utf-8"), "text/html; charset=utf-8")
                return
            static_path = (WEB_ROOT / path.lstrip("/")).resolve()
            if static_path.is_file() and WEB_ROOT in static_path.parents:
                content_type = mimetypes.guess_type(static_path.name)[0] or "application/octet-stream"
                response_text(self, 200, static_path.read_bytes(), content_type)
                return
            response_json(self, 404, {"error": "not found"})
        except QueryValidationError as exc:
            response_json(self, 400, {"error": str(exc)})
        except Exception as exc:
            response_json(self, 500, {"error": str(exc)})

    def do_HEAD(self):
        if self.reject_if_stopping():
            return
        self.send_response(405)
        self.end_headers()

    def do_POST(self):
        if self.reject_if_stopping():
            return
        parsed = urlparse(self.path)
        path = parsed.path
        if path == "/auth/login":
            self.handle_auth_login()
            return
        if path == "/auth/logout":
            self.handle_auth_logout()
            return
        if path == "/api/agent-tools/grants":
            self.handle_agent_tool_grant_create()
            return
        if path == "/api/agent-tools/call":
            self.handle_agent_tool_call()
            return
        if path == "/api/agent-tools/grants/revoke":
            self.handle_agent_tool_grant_revoke()
            return
        if is_control_api_path(path):
            if not self.require_authentication(require_csrf=True):
                return
        elif path.startswith(workspace_contract.jupyter_base_url(self.config)):
            if not self.require_authentication():
                return
        if path.startswith(workspace_contract.jupyter_base_url(self.config)):
            try:
                jupyter_proxy.proxy_request(self, jupyter_workspaces.resolve_proxy_instance(self.path))
            except Exception as exc:
                response_json(self, 502, {"error": str(exc)})
            return
        try:
            query = parse_qs(parsed.query, keep_blank_values=True)
            if path.startswith("/api/mining/"):
                if self.mining_api is None:
                    from mining.api import DisabledMiningApi

                    self.mining_api = DisabledMiningApi()
                try:
                    status, result = self.mining_api.handle_post(
                        path,
                        read_request_json(self),
                        query,
                    )
                except ValueError as exc:
                    response_json(self, 400, {"error": str(exc)})
                    return
                response_json(self, status, result)
                return
            if path == "/api/data/upload":
                require_query_fields(
                    query, {"datasetId", "name", "filename"}, "Dataset upload"
                )
                content_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
                if content_type not in {"application/zip", "application/x-zip-compressed"}:
                    raise ValueError("Dataset upload accepts ZIP archives only.")
                result = self.handle_data_upload(
                    {
                        "datasetId": query_value(query, "datasetId", ""),
                        "name": query_value(query, "name", ""),
                        "filename": query_value(query, "filename", ""),
                    },
                    self.rfile,
                    self.headers.get("Content-Length", "0"),
                )
                self.append_event("data.uploaded", result["dataset"])
                response_json(self, 200, result)
                return
            raw_parts = [part for part in path.split("/") if part]
            if (len(raw_parts) == 5 and raw_parts[:3] == ["api", "data", "datasets"]
                    and raw_parts[4] == "replace"):
                require_query_fields(
                    query, {"name", "filename"}, "Dataset replacement"
                )
                content_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
                if content_type not in {"application/zip", "application/x-zip-compressed"}:
                    raise ValueError("Dataset replacement accepts ZIP archives only.")
                dataset = dataset_transfer.replace_dataset_zip_stream(
                    self.config,
                    raw_parts[3],
                    self.rfile,
                    self.headers.get("Content-Length", "0"),
                    name=query_value(query, "name", ""),
                    filename=query_value(query, "filename", ""),
                )
                self.append_event("data.dataset.replaced", dataset)
                response_json(self, 200, {"accepted": True, "dataset": dataset})
                return
            require_query_fields(query, set(), "Control API POST request")
            payload = read_request_json(self)
            parts = [part for part in path.split("/") if part]
            if path == "/api/account/password":
                control.require_exact_fields(
                    payload,
                    allowed={"currentPassword", "newPassword"},
                    required={"currentPassword", "newPassword"},
                    label="Account password request",
                )
                _auth.change_password(
                    self.config,
                    self.auth_session,
                    payload.get("currentPassword"),
                    payload.get("newPassword"),
                )
                response_json(self, 200, {"accepted": True})
                return
            if path == "/api/repository-folders":
                if not isinstance(payload, dict) or "action" not in payload:
                    raise ValueError("Repository folder request requires action.")
                action = str(payload["action"] or "").strip()
                fields = {
                    "create": ({"action", "repository", "name", "parentId"}, {"action", "repository", "name"}),
                    "rename": ({"action", "repository", "folderId", "name"}, {"action", "repository", "folderId", "name"}),
                    "delete": ({"action", "repository", "folderId"}, {"action", "repository", "folderId"}),
                    "moveFolder": ({"action", "repository", "folderId", "parentId"}, {"action", "repository", "folderId"}),
                    "moveItem": ({"action", "repository", "itemId", "folderId"}, {"action", "repository", "itemId"}),
                }
                if action not in fields:
                    raise ValueError(f"Unknown repository folder action: {action}")
                allowed, required = fields[action]
                control.require_exact_fields(
                    payload, allowed=allowed, required=required,
                    label=f"Repository folder {action} request",
                )
                repository = repository_folders.require_repository(payload.get("repository"))
                with control_state.control_state_lock(self.config):
                    if action == "create":
                        result = repository_folders.create_folder(
                            self.config,
                            repository,
                            payload.get("name"),
                            payload.get("parentId") or "",
                        )
                    elif action == "rename":
                        result = repository_folders.rename_folder(
                            self.config,
                            repository,
                            payload.get("folderId"),
                            payload.get("name"),
                        )
                    elif action == "delete":
                        result = repository_folders.delete_folder(
                            self.config,
                            repository,
                            payload.get("folderId"),
                        )
                    elif action == "moveFolder":
                        result = repository_folders.move_folder(
                            self.config,
                            repository,
                            payload.get("folderId"),
                            payload.get("parentId") or "",
                        )
                    elif action == "moveItem":
                        item_id = str(payload.get("itemId") or "")
                        items = repository_items(self.config, repository)
                        if item_id not in items:
                            raise ValueError(f"Repository item does not exist: {item_id}")
                        result = repository_folders.assign_item(
                            self.config,
                            repository,
                            item_id,
                            payload.get("folderId") or "",
                            module_definition=items[item_id],
                        )
                    else:
                        raise ValueError(f"Unknown repository folder action: {action}")
                    control_state.append_history_event(self.config, f"repository.{action}", {
                        "repository": repository,
                        "result": result,
                    })
                response_json(self, 200, {
                    "accepted": True,
                    "result": result,
                    "repository": repository_catalog(self.config, repository),
                })
                return
            if path == "/api/repository-resources":
                control.require_exact_fields(
                    payload,
                    allowed={"action", "repository", "itemId", "name"},
                    required={"action", "repository", "itemId", "name"},
                    label="Repository resource rename request",
                )
                action = str(payload.get("action") or "").strip()
                repository = repository_folders.require_repository(payload.get("repository"))
                item_id = str(payload.get("itemId") or "").strip()
                if action != "rename":
                    raise ValueError(f"Unknown repository resource action: {action}")
                with control_state.control_state_lock(self.config):
                    result = rename_repository_resource(
                        self.config, repository, item_id, payload.get("name")
                    )
                    control_state.append_history_event(self.config, "repository.resource.rename", {
                        "repository": repository,
                        "itemId": item_id,
                        "name": payload.get("name"),
                    })
                response_json(self, 200, {
                    "accepted": True,
                    "result": result,
                    "repository": repository_catalog(self.config, repository),
                })
                return
            if len(parts) == 5 and parts[:3] == ["api", "data", "workspaces"] and parts[4] == "jupyter":
                require_empty_json_request(payload, "Dataset workspace Jupyter request")
                workspace = dataset_workspace_repository.get_workspace(
                    self.config, parts[3]
                )
                url = jupyter_workspaces.workspace_url(
                    self.config,
                    self.public_url,
                    workspace["workspaceId"],
                    "dataset",
                    read_only_roots=[
                        source["storageRoot"] for source in workspace["sources"]
                    ],
                )
                result = {
                    "accepted": True,
                    "workspace": workspace,
                    "url": url,
                    "jupyter": jupyter_workspaces.status(self.config),
                }
                self.append_event("data.workspace.jupyter.opened", {"workspaceId": workspace["workspaceId"]})
                response_json(self, 200, result)
                return
            if (len(parts) == 7 and parts[:3] == ["api", "data", "samplers"]
                    and parts[4] == "versions" and parts[6] == "jupyter"):
                require_empty_json_request(payload, "Sampler Jupyter request")
                workspace = sampler_workspaces.open_edit_workspace(
                    self.config, parts[3], parts[5]
                )
                url = jupyter_workspaces.workspace_url(
                    self.config, self.public_url, workspace["workspaceId"], workspace_kind="sampler"
                )
                result = {
                    **workspace,
                    "url": url,
                    "jupyter": jupyter_workspaces.status(self.config),
                }
                self.append_event("data.sampler.workspace.jupyter.opened", {
                    "samplerKey": workspace["sourceSamplerKey"],
                    "workspaceId": workspace["workspaceId"],
                })
                response_json(self, 200, result)
                return
            if (len(parts) == 7 and parts[:3] == ["api", "data", "samplers"]
                    and parts[4] == "versions" and parts[6] == "publish"):
                require_empty_json_request(payload, "Sampler publish request")
                result = sampler_workspaces.publish_edit_workspace(
                    self.config, parts[3], parts[5]
                )
                self.append_event("data.sampler.workspace.published", {
                    "sourceSamplerKey": result["sourceSamplerKey"],
                    "publishedSamplerKey": (
                        f"{result['sampler']['samplerId']}::{result['sampler']['version']}"
                    ),
                    "workspaceId": result["workspaceId"],
                    "unchanged": result["unchanged"],
                })
                response_json(self, 200, result)
                return
            if len(parts) == 7 and parts[:2] == ["api", "modules"] and parts[4] == "versions" and parts[6] == "jupyter":
                require_empty_json_request(payload, "Pipeline Module Jupyter request")
                workspace = module_workspaces.open_edit_workspace(
                    self.config, parts[2], parts[3], parts[5]
                )
                url = jupyter_workspaces.workspace_url(
                    self.config, self.public_url, workspace["workspaceId"], workspace_kind="module"
                )
                result = {
                    **workspace,
                    "url": url,
                    "jupyter": jupyter_workspaces.status(self.config),
                }
                self.append_event("module.workspace.jupyter.opened", {
                    "moduleKey": workspace["sourceModuleKey"],
                    "workspaceId": workspace["workspaceId"],
                })
                response_json(self, 200, result)
                return
            if len(parts) == 7 and parts[:2] == ["api", "modules"] and parts[4] == "versions" and parts[6] == "publish":
                require_empty_json_request(payload, "Pipeline Module publish request")
                result = module_workspaces.publish_edit_workspace(
                    self.config, "pipeline", parts[2], parts[3], parts[5]
                )
                response_json(self, 200, result)
                return
            repository_routes = {
                "analysis-modules": "analysis",
                "environment-modules": "environment",
            }
            if (len(parts) == 7 and parts[0] == "api" and parts[1] in repository_routes
                    and parts[4] == "versions" and parts[6] == "jupyter"):
                require_empty_json_request(
                    payload,
                    f"{repository_routes[parts[1]].title()} Module Jupyter request",
                )
                repository = repository_routes[parts[1]]
                workspace = module_workspaces.open_repository_edit_workspace(
                    self.config, repository, parts[2], parts[3], parts[5]
                )
                url = jupyter_workspaces.workspace_url(
                    self.config, self.public_url, workspace["workspaceId"], workspace_kind="module"
                )
                result = {
                    **workspace,
                    "url": url,
                    "jupyter": jupyter_workspaces.status(self.config),
                }
                self.append_event(f"{repository}.module.workspace.jupyter.opened", {
                    "moduleKey": workspace["sourceModuleKey"],
                    "workspaceId": workspace["workspaceId"],
                })
                response_json(self, 200, result)
                return
            if (len(parts) == 7 and parts[0] == "api" and parts[1] in repository_routes
                    and parts[4] == "versions" and parts[6] == "publish"):
                require_empty_json_request(
                    payload,
                    f"{repository_routes[parts[1]].title()} Module publish request",
                )
                repository = repository_routes[parts[1]]
                result = module_workspaces.publish_edit_workspace(
                    self.config, repository, parts[2], parts[3], parts[5]
                )
                response_json(self, 200, result)
                return
            if path == "/api/data/datasets/download":
                control.require_exact_fields(
                    payload,
                    allowed={"datasetIds"},
                    required={"datasetIds"},
                    label="Dataset download request",
                )
                if not isinstance(payload["datasetIds"], list):
                    raise ValueError("Dataset download datasetIds must be an array.")
                self.send_dataset_archive(payload["datasetIds"])
                return
            if path == "/api/data/samplers":
                sampler = sampler_repository.save_sampler(self.config, payload)
                self.append_event("data.sampler.saved", sampler)
                response_json(self, 200, {"accepted": True, "sampler": sampler})
                return
            if path == "/api/data/workspaces":
                workspace = dataset_workspace_repository.create_workspace(
                    self.config, payload
                )
                self.append_event("data.workspace.created", workspace)
                response_json(self, 200, {"accepted": True, "workspace": workspace})
                return
            if path == "/api/data/recipes":
                recipe = dataset_recipes.save_recipe(self.config, payload)
                self.append_event("data.recipe.saved", recipe)
                response_json(self, 200, {"accepted": True, "recipe": recipe})
                return
            if path == "/api/data/builds":
                result = dataset_builds.submit_build(self.config, payload)
                self.append_event("data.build.completed", {
                    "jobId": result["job"]["jobId"],
                    "datasetId": result["dataset"]["datasetId"],
                })
                response_json(self, 200, {"accepted": True, **result})
                return
            if path == "/api/data/process":
                result = dataset_builds.process_recipe(self.config, payload)
                self.append_event("data.process.completed", {
                    "jobId": result["job"]["jobId"],
                    "datasetId": result["dataset"]["datasetId"],
                })
                response_json(self, 200, {"accepted": True, **result})
                return
            if path == "/api/backtests":
                if not isinstance(payload, dict):
                    raise ValueError("Backtest submission must be an object.")
                if "preparedSubmissionToken" in payload:
                    control.require_exact_fields(
                        payload,
                        allowed={"request", "preparedSubmissionToken"},
                        required={"request", "preparedSubmissionToken"},
                        label="Prepared Backtest submission",
                    )
                    if (
                        not isinstance(payload["preparedSubmissionToken"], str)
                        or not payload["preparedSubmissionToken"]
                    ):
                        raise ValueError(
                            "Prepared Backtest submission token must be a "
                            "non-empty string."
                        )
                    job = self.backtest_job_manager.submit(
                        payload["request"],
                        prepared_submission_token=payload[
                            "preparedSubmissionToken"
                        ],
                        session_identity=self.auth_session["tokenHash"],
                    )
                else:
                    job = self.backtest_job_manager.submit(payload)
                response_json(self, 202, {"accepted": True, "job": job})
                return
            if path == "/api/backtest-compositions/validate":
                with control_state.control_state_lock(self.config):
                    validation = backtest_service.validate_backtest_composition(
                        self.config,
                        payload,
                    )
                response_json(self, 200, validation)
                return
            if path == "/api/backtest-submissions/prepare":
                with control_state.control_state_lock(self.config):
                    prepared = prepare_backtest_submission(
                        self.config,
                        payload,
                        self.prepared_backtest_submissions,
                        session_identity=self.auth_session["tokenHash"],
                    )
                response_json(self, 200, prepared)
                return
            if path == "/api/graphs/validate":
                response_json(self, 200, validate_graph_draft(self.config, payload))
                return
            if path == "/api/visualizations":
                result = visualization_service.save_visualization(
                    self.config,
                    payload,
                    visualizer_definition_map(),
                )
                self.append_event("visualization.saved", result["visualization"])
                response_json(self, 200, result)
                return
            if len(parts) == 5 and parts[:3] == ["api", "data", "datasets"] and parts[4] == "archive":
                control.require_exact_fields(
                    payload, allowed={"reason"}, required=set(),
                    label="Dataset archive request",
                )
                result = dataset_repository.archive_dataset(
                    self.config, parts[3], payload.get("reason") or ""
                )
                self.append_event("data.dataset.archived", result)
                response_json(self, 200, {"accepted": True, **result})
                return
            if len(parts) == 4 and parts[:2] == ["api", "backtests"] and parts[3] == "archive":
                control.require_exact_fields(
                    payload, allowed={"reason"}, required=set(),
                    label="Backtest archive request",
                )
                backtest = result_repository.archive_backtest(
                    self.config, parts[2], payload.get("reason") or ""
                )
                self.append_event("backtest.archived", {
                    "backtestId": backtest["backtestId"],
                    "reason": backtest["archiveReason"],
                })
                response_json(self, 200, {"accepted": True, "backtest": backtest})
                return
            if len(parts) == 4 and parts[:2] == ["api", "backtests"] and parts[3] == "result":
                control.require_exact_fields(
                    payload,
                    allowed={"paths", "temporaryModules"},
                    required={"paths", "temporaryModules"},
                    label="Result slice request",
                )
                if not isinstance(payload["paths"], list) or not isinstance(payload["temporaryModules"], list):
                    raise ValueError("Result slice paths and temporaryModules must be arrays.")
                response_backtest_result_slice(
                    self,
                    self.config,
                    parts[2],
                    payload.get("paths") or [],
                    payload.get("temporaryModules") or [],
                )
                return
            if path == "/api/modules":
                result = handle_add_engine_module(self.config, payload)
            elif path == "/api/environment-modules":
                result = handle_add_environment_module(self.config, payload)
            elif path == "/api/analysis-modules":
                result = handle_add_analysis_module(self.config, payload)
            else:
                with control_state.control_state_lock(self.config):
                    if path == "/api/environments":
                        result = handle_save_environment(self.config, payload)
                    elif path == "/api/analyses":
                        result = handle_save_analysis(self.config, payload)
                    elif path == "/api/pipelines":
                        result = pipeline_service.create_pipeline(
                            self.config, payload
                        )
                    elif len(parts) == 4 and parts[:2] == ["api", "pipelines"] and parts[3] == "versions":
                        pipeline_id = pipeline_manifest_compiler.normalize_pipeline_id(
                            parts[2]
                        )
                        if payload.get("pipelineId") != pipeline_id:
                            raise ValueError("Pipeline Definition pipelineId must match the version URL.")
                        result = pipeline_service.archive_pipeline_if_changed(
                            self.config, payload
                        )
                    elif len(parts) == 4 and parts[:2] == ["api", "pipelines"] and parts[3] == "clone":
                        result = pipeline_service.clone_pipeline(
                            self.config, parts[2], payload
                        )
                    elif len(parts) == 4 and parts[:2] == ["api", "pipelines"] and parts[3] == "disable":
                        result = pipeline_service.disable_pipeline(
                            self.config, parts[2], payload
                        )
                    else:
                        response_json(self, 404, {"error": "not found"})
                        return
            response_json(self, 200, result)
        except Exception as exc:
            response_json(self, 400, {"accepted": False, "error": str(exc)})

    def append_event(self, event_type, payload):
        with control_state.control_state_lock(self.config):
            control_state.append_history_event(self.config, event_type, payload)

    def handle_data_upload(self, payload, stream, content_length):
        if not payload.get("datasetId") and not str(payload.get("name") or "").strip():
            raise ValueError("Dataset name is required.")
        dataset_id = payload.get("datasetId") or resource_ids.new_resource_id("dataset")
        dataset = dataset_transfer.import_dataset_zip_stream(
            self.config,
            dataset_id,
            stream,
            content_length,
            name=payload.get("name") or "",
            filename=payload.get("filename") or "",
        )
        return {"accepted": True, "dataset": dataset}

    def send_dataset_archive(self, dataset_ids):
        archive_path, filename = dataset_transfer.build_dataset_archive(self.config, dataset_ids)
        try:
            response_file(self, archive_path, filename)
        finally:
            archive_path.unlink(missing_ok=True)

    def do_DELETE(self):
        if self.reject_if_stopping():
            return
        parsed = urlparse(self.path)
        path = parsed.path
        if is_control_api_path(path):
            if not self.require_authentication(require_csrf=True):
                return
        elif path.startswith(workspace_contract.jupyter_base_url(self.config)):
            if not self.require_authentication():
                return
        if path.startswith(workspace_contract.jupyter_base_url(self.config)):
            try:
                jupyter_proxy.proxy_request(self, jupyter_workspaces.resolve_proxy_instance(self.path))
            except Exception as exc:
                response_json(self, 502, {"error": str(exc)})
            return
        query = parse_qs(parsed.query, keep_blank_values=True)
        parts = [part for part in path.split("/") if part]
        try:
            if is_control_api_path(path):
                require_query_fields(query, set(), "Control API DELETE request")
            if len(parts) == 4 and parts[:3] == ["api", "data", "workspaces"]:
                result = dataset_workspace_service.delete_workspace(
                    self.config, parts[3]
                )
                self.append_event("data.workspace.deleted", result)
                response_json(self, 200, {"accepted": True, **result})
                return
            response_json(self, 404, {"error": "not found"})
        except Exception as exc:
            response_json(self, 400, {"accepted": False, "error": str(exc)})

    def proxy_jupyter_write_method(self):
        if self.reject_if_stopping():
            return
        path = urlparse(self.path).path
        if is_control_api_path(path) and not self.require_authentication(require_csrf=True):
            return
        if path.startswith(workspace_contract.jupyter_base_url(self.config)):
            if not self.require_authentication():
                return
            try:
                jupyter_proxy.proxy_request(self, jupyter_workspaces.resolve_proxy_instance(self.path))
            except Exception as exc:
                response_json(self, 502, {"error": str(exc)})
            return
        response_json(self, 405, {"error": "method not allowed"})

    def do_PUT(self):
        self.proxy_jupyter_write_method()

    def do_PATCH(self):
        self.proxy_jupyter_write_method()

    def log_message(self, fmt, *args):
        return


class EngineThreadingHTTPServer(ThreadingHTTPServer):
    """Join every accepted handler before the Engine owner lease is released."""

    daemon_threads = False
    block_on_close = True

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.engine_serve_started = False
        self.engine_serve_finished = threading.Event()

    def serve_forever(self, *args, **kwargs):
        try:
            self.engine_serve_finished.clear()
            self.engine_serve_started = True
            return super().serve_forever(*args, **kwargs)
        finally:
            # BaseServer.shutdown deadlocks when serve_forever never reached
            # its own loop.  This independent proof also covers an async
            # interruption between our started marker and that base loop.
            self.engine_serve_finished.set()


_RETAINED_SERVICE_LIFECYCLES = {}
_RETAINED_SERVICE_LIFECYCLES_LOCK = threading.Lock()


def _shutdown_engine_service(
    server,
    manager,
    owner_lease,
    *,
    registry_configured,
    application_services=(),
):
    """Stop admission, join handlers, then release process/owner authorities."""

    EngineServiceHandler.stopping.set()
    lifecycle_key = owner_lease.token
    application_services = tuple(application_services)
    with _RETAINED_SERVICE_LIFECYCLES_LOCK:
        _RETAINED_SERVICE_LIFECYCLES[lifecycle_key] = (
            server,
            manager,
            owner_lease,
            application_services,
        )
    first_error = None
    server_started = bool(
        server is not None and server.engine_serve_started
    )
    server_finished_event = (
        None
        if server is None
        else getattr(server, "engine_serve_finished", None)
    )
    server_finished = bool(
        server_finished_event is not None
        and server_finished_event.is_set()
    )
    server_shutdown_proven = (
        server is None or not server_started or server_finished
    )
    manager_shutdown_proven = manager is None
    application_services_shutdown_proven = not application_services
    handler_shutdown_proven = server is None
    if server is not None and server_started and not server_finished:
        try:
            server.shutdown()
            if server_finished_event is not None:
                server_finished_event.wait()
            server_shutdown_proven = True
        except BaseException as exc:
            first_error = first_error or exc
    if manager is not None or server is not None:
        for action in (
            result_runtime.shutdown_result_runtimes,
            dataset_build_runtime.shutdown_build_processes,
        ):
            try:
                action()
            except BaseException as exc:
                first_error = first_error or exc
    if manager is not None:
        try:
            manager.shutdown()
            manager_shutdown_proven = True
        except BaseException as exc:
            first_error = first_error or exc
    stopped_application_services = 0
    for service in application_services:
        try:
            service.shutdown()
            stopped_application_services += 1
        except BaseException as exc:
            first_error = first_error or exc
    application_services_shutdown_proven = (
        stopped_application_services == len(application_services)
    )
    if manager is not None or server is not None:
        try:
            jupyter_workspaces.shutdown_managed_process()
        except BaseException as exc:
            first_error = first_error or exc
    if server is not None:
        # ThreadingMixIn.server_close joins all non-daemon request threads.
        try:
            server.server_close()
            handler_shutdown_proven = True
        except BaseException as exc:
            first_error = first_error or exc
    process_shutdown_proven = not process_session.PROCESS_SESSIONS.snapshot()
    release_proven = (
        server_shutdown_proven
        and manager_shutdown_proven
        and application_services_shutdown_proven
        and handler_shutdown_proven
        and process_shutdown_proven
    )
    registry_release_proven = not registry_configured
    if release_proven and registry_configured:
        try:
            process_session.PROCESS_SESSIONS.clear_inherited_supervisor_fds()
            registry_release_proven = True
        except BaseException as exc:
            first_error = first_error or exc
    if release_proven and registry_release_proven:
        try:
            owner_lease.close()
        except BaseException as exc:
            first_error = first_error or exc
        if owner_lease.closed:
            with _RETAINED_SERVICE_LIFECYCLES_LOCK:
                _RETAINED_SERVICE_LIFECYCLES.pop(lifecycle_key, None)
    elif first_error is None:
        first_error = RuntimeError(
            "Engine service shutdown has not proved application, handler, and "
            "writer quiescence."
        )
    if first_error is not None:
        raise first_error


def _run_engine_service(args):
    EngineServiceHandler.config = control.load_config(args.config)
    try:
        public_origin = normalize_public_origin(args.public_url, "public URL")
        agent_origin = normalize_public_origin(args.agent_public_url, "Agent public URL")
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    if urlparse(public_origin).hostname != urlparse(agent_origin).hostname:
        raise SystemExit("TradeEngine and Agent public URLs must use the same hostname.")
    if not EngineServiceHandler.config.get("allowInsecureAuth"):
        if urlparse(args.public_url).scheme.lower() != "https":
            raise SystemExit("Authentication requires an https:// public URL.")
        if args.host not in {"127.0.0.1", "::1", "localhost"}:
            raise SystemExit(
                "Bind the authenticated Engine service to loopback behind the HTTPS reverse proxy."
            )
    owner_lease = claim_control_owner(EngineServiceHandler.config)
    registry_configured = False
    manager = None
    server = None
    mining_supervisor = None
    primary_error = None
    primary_traceback = None
    try:
        # Clear is safe when configuration itself fails, and setting this
        # before the fallible call closes the asynchronous interruption gap.
        registry_configured = True
        process_session.PROCESS_SESSIONS.configure_inherited_supervisor_fds(
            owner_lease.child_pass_fds()
        )
        EngineServiceHandler.stopping.clear()
        with control_state.control_state_lock(EngineServiceHandler.config):
            version_archive.reconcile_staging_directories(
                (
                    EngineServiceHandler.config["controlRoot"],
                    EngineServiceHandler.config["releaseRoot"],
                    EngineServiceHandler.config["liveRoot"],
                ),
                committed_markers=(
                    version_archive.MANIFEST_NAME,
                    dataset_archive.MANIFEST_NAME,
                ),
            )
            control_schema.prepare(EngineServiceHandler.config)
            engine_database.prepare_database(EngineServiceHandler.config)
            repository_folders.prepare(EngineServiceHandler.config)
            dataset_publication.retry_pending_dataset_publication_cleanup()
            builtin_resources.install(EngineServiceHandler.config)
        dataset_workspace_repository.reconcile_workspace_directories(
            EngineServiceHandler.config
        )
        dataset_workspace_service.reconcile_deleting_workspaces(
            EngineServiceHandler.config
        )
        dataset_builds.reconcile_build_job_directories(
            EngineServiceHandler.config
        )
        dataset_builds.reconcile_interrupted_builds(EngineServiceHandler.config)
        dataset_builds.reconcile_terminal_build_workspaces(
            EngineServiceHandler.config
        )
        dataset_workspace_service.reconcile_internal_workspaces(
            EngineServiceHandler.config
        )
        _auth.ensure_default_user(EngineServiceHandler.config)

        from trade_agent_bridge import ToolGrantStore

        EngineServiceHandler.agent_tool_grant_store = ToolGrantStore()
        EngineServiceHandler.agent_bridge_token = os.environ.get(
            "TRADE_AGENT_BRIDGE_TOKEN", ""
        )
        if EngineServiceHandler.config.get("miningRoot"):
            from mining.api import MiningApi

            mining_api = MiningApi(EngineServiceHandler.config)
            if EngineServiceHandler.config.get("miningAutoStart", False):
                from mining.supervisor import MiningSupervisor

                mining_supervisor = MiningSupervisor(EngineServiceHandler.config)
                mining_supervisor.start()
            mining_api.supervisor = mining_supervisor
            EngineServiceHandler.mining_api = mining_api
        else:
            from mining.api import DisabledMiningApi

            EngineServiceHandler.mining_api = DisabledMiningApi()

        EngineServiceHandler.prepared_backtest_submissions = (
            PreparedBacktestSubmissionStore()
        )

        def append_background_event(event_type, payload):
            with control_state.control_state_lock(EngineServiceHandler.config):
                control_state.append_history_event(
                    EngineServiceHandler.config,
                    event_type,
                    payload,
                )

        def freeze_backtest_job_request(config, request):
            with control_state.control_state_lock(config):
                return backtest_service.freeze_backtest_request(config, request)

        def validate_frozen_backtest_admission(config, request):
            with control_state.control_state_lock(config):
                return backtest_service.require_frozen_backtest_admission(
                    config,
                    request,
                )

        job_services = BacktestJobServices(
            freeze_request=freeze_backtest_job_request,
            reconcile_result_staging=result_repository.reconcile_result_staging,
            recover_result_catalog=(
                backtest_result_service.recover_backtest_result_catalog
            ),
            validate_result_archive=(
                result_projection_service.validate_backtest_result_archive
            ),
            consume_prepared_request=(
                EngineServiceHandler.prepared_backtest_submissions.consume
            ),
            validate_frozen_admission=(
                validate_frozen_backtest_admission
            ),
        )
        manager = BacktestJobManager(
            EngineServiceHandler.config,
            job_services,
            event_callback=append_background_event,
        )
        EngineServiceHandler.backtest_job_manager = manager
        EngineServiceHandler.public_url = args.public_url
        EngineServiceHandler.agent_public_url = agent_origin
        server = EngineThreadingHTTPServer(
            (args.host, args.port), EngineServiceHandler
        )
        print(
            f"engine service listening on http://{args.host}:{args.port}",
            flush=True,
        )
        print(f"public url: {args.public_url}", flush=True)
        print(f"agent public url: {agent_origin}", flush=True)

        def stop_service(_signum, _frame):
            raise KeyboardInterrupt

        signal.signal(signal.SIGTERM, stop_service)
        signal.signal(signal.SIGINT, stop_service)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
    except BaseException as exc:
        primary_error = exc
        primary_traceback = exc.__traceback__
    cleanup_error = None
    try:
        _shutdown_engine_service(
            server,
            manager,
            owner_lease,
            registry_configured=registry_configured,
            application_services=(
                () if mining_supervisor is None else (mining_supervisor,)
            ),
        )
    except BaseException as exc:
        cleanup_error = exc
    if primary_error is not None:
        if cleanup_error is not None:
            raise primary_error.with_traceback(primary_traceback) from cleanup_error
        raise primary_error.with_traceback(primary_traceback)
    if cleanup_error is not None:
        raise cleanup_error


def main():
    parser = argparse.ArgumentParser(
        description="Trade Engine web service and frontend."
    )
    parser.add_argument(
        "--config", default=str(ROOT / ".runtime" / "strategy-control.json")
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=30808)
    parser.add_argument("--public-url", default="https://trade.duckduckrun.com")
    parser.add_argument("--agent-public-url", required=True)
    _run_engine_service(parser.parse_args())


if __name__ == "__main__":
    main()
