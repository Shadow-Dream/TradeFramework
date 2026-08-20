"""Canonical Engine service orchestration API."""
import math
from datetime import datetime, timezone
from pathlib import Path

from engine.contracts.data_compatibility import schemas_compatible
from engine.contracts.module import (
    require_exact_fields,
)
from engine.contracts.pipeline import (
    PIPELINE_DRAFT_FIELDS,
    apply_pipeline_module_contract as _apply_pipeline_module_contract,
    validate_pipeline_manifest as _validate_pipeline_manifest,
    validate_pipeline_topology as _validate_pipeline_topology,
)
from engine.contracts import digest as digest_contracts
from engine.compiler import pipeline as _pipeline_compiler
from engine.compiler import pipeline_manifest as _pipeline_manifest_compiler
from engine.authority import pipeline as _pipeline_authority
from engine.archive import version as version_archive
from engine.contracts import strict_json
from engine.repository import module_definitions
from engine.repository import pipelines as _pipeline_repository
from engine.service import pipelines as _pipeline_service


def load_config(path):
    with open(path, encoding="utf-8") as handle:
        config = strict_json.load(handle)
    require_exact_fields(
        config,
        allowed={
            "liveRoot", "releaseRoot", "controlRoot", "allowInsecureAuth",
            "backtestMaxWorkers", "jupyterHost", "jupyterBaseUrl",
            "miningRoot", "miningAutoStart", "miningExposeTestProvider",
            "miningHttpTimeout", "miningMaxPageBytes",
            "miningMaxPagesPerRun", "miningStandbyRetrySeconds",
        },
        required={"liveRoot", "releaseRoot", "controlRoot"},
        label="Control API config",
    )

    live_root = config["liveRoot"]
    release_root = config["releaseRoot"]
    for field, value in (("liveRoot", live_root), ("releaseRoot", release_root)):
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"Control API config {field} must be a non-empty string.")
    control_root = config["controlRoot"]
    if not isinstance(control_root, str) or not control_root.strip():
        raise ValueError("Control API config controlRoot must be a non-empty string.")

    loaded = {
        "liveRoot": str(Path(live_root).expanduser()),
        "releaseRoot": str(Path(release_root).expanduser()),
        "controlRoot": str(Path(control_root).expanduser()),
    }
    if "allowInsecureAuth" in config:
        if not isinstance(config["allowInsecureAuth"], bool):
            raise ValueError("Control API config allowInsecureAuth must be a boolean.")
        loaded["allowInsecureAuth"] = config["allowInsecureAuth"]
    if "backtestMaxWorkers" in config:
        value = config["backtestMaxWorkers"]
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ValueError("Control API config backtestMaxWorkers must be a positive integer.")
        loaded["backtestMaxWorkers"] = value
    for field in ("jupyterHost", "jupyterBaseUrl"):
        if field in config:
            if not isinstance(config[field], str) or not config[field].strip():
                raise ValueError(f"Control API config {field} must be a non-empty string.")
            loaded[field] = config[field]
    if "miningRoot" in config:
        value = config["miningRoot"]
        if not isinstance(value, str) or not value.strip():
            raise ValueError("Control API config miningRoot must be a non-empty string.")
        loaded["miningRoot"] = str(Path(value).expanduser())
    for field in ("miningAutoStart", "miningExposeTestProvider"):
        if field in config:
            if not isinstance(config[field], bool):
                raise ValueError(f"Control API config {field} must be a boolean.")
            loaded[field] = config[field]
    for field in ("miningHttpTimeout", "miningStandbyRetrySeconds"):
        if field not in config:
            continue
        raw_value = config[field]
        if isinstance(raw_value, bool) or not isinstance(raw_value, (int, float)):
            raise ValueError(f"Control API config {field} must be a finite number.")
        value = float(raw_value)
        if not math.isfinite(value) or not 1 <= value <= 300:
            raise ValueError(
                f"Control API config {field} must be finite and between 1 and 300."
            )
        loaded[field] = value
    integer_limits = {
        "miningMaxPageBytes": (1024, 1024 * 1024 * 1024),
        "miningMaxPagesPerRun": (1, 1000),
    }
    for field, (minimum, maximum) in integer_limits.items():
        if field not in config:
            continue
        value = config[field]
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"Control API config {field} must be an integer.")
        if not minimum <= value <= maximum:
            raise ValueError(
                f"Control API config {field} must be between {minimum} and {maximum}."
            )
        loaded[field] = value
    return loaded


def timestamp_iso(seconds):
    return datetime.fromtimestamp(seconds, timezone.utc).isoformat().replace("+00:00", "Z")


def json_digest(payload):
    return digest_contracts.canonical_json_digest(payload)


def normalize_pipeline_id(value):
    return _pipeline_manifest_compiler.normalize_pipeline_id(value)


def load_pipeline_execution_version(config, pipeline_id, version):
    """Load one exact immutable Pipeline version without scanning unrelated history."""
    return _pipeline_repository.load_pipeline_execution_version(
        config,
        pipeline_id,
        version,
    )


def validate_manifest(manifest):
    """Control-plane entry for the shared Engine Pipeline manifest contract."""

    return _validate_pipeline_manifest(manifest)


def validate_manifest_against_definition_authorities(
    manifest, definitions, definition_authorities
):
    """Control-plane entry for exact verified Pipeline authorities."""

    _pipeline_compiler.pipeline_contract_template_from_verified_authorities(
        manifest,
        definitions,
        definition_authorities,
    )
    return manifest


def validate_manifest_definition_authorities(manifest, definitions):
    """Strictly verify each unique Pipeline Module record exactly once."""

    template = _pipeline_compiler.compile_pipeline_contract_template(
        manifest,
        definitions,
    )
    material = _pipeline_authority.pipeline_contract_template_material(template)
    return material["moduleDefinitionAuthorities"]


def validate_manifest_against_definitions(manifest, definitions):
    """Strict raw entry for one frozen Pipeline and its Module Definitions."""
    validate_manifest_definition_authorities(manifest, definitions)
    return manifest


def validate_pipeline_topology(manifest):
    """Control-plane entry for the shared Engine Pipeline topology contract."""

    return _validate_pipeline_topology(manifest)


def port_type_is_compatible(output_schema, input_schema):
    return schemas_compatible(output_schema, input_schema)


def normalize_module_instance(config, request):
    return normalize_module_instance_from_definitions(
        request, module_definitions.load_all_definitions(config)
    )


def normalize_module_instance_from_definitions(request, definitions):
    return _pipeline_manifest_compiler.normalize_module_instance_from_definitions(
        request,
        definitions,
    )


def normalize_pipeline_instances(config, instances):
    return normalize_pipeline_instances_from_definitions(
        instances, module_definitions.load_all_definitions(config)
    )


def normalize_pipeline_instances_from_definitions(instances, definitions):
    return _pipeline_manifest_compiler.normalize_pipeline_instances_from_definitions(
        instances,
        definitions,
    )


def compile_module(instance, definition):
    version_archive.verify_record(definition)
    return _pipeline_manifest_compiler.compile_module(instance, definition)


def normalize_stage_references(stages):
    return _pipeline_manifest_compiler.normalize_stage_references(stages)


def collect_pipeline_instance_ids(stages):
    return _pipeline_manifest_compiler.collect_pipeline_instance_ids(stages)


def active_instance_ids(definition):
    return _pipeline_manifest_compiler.active_instance_ids(definition)


def normalize_pipeline_draft(draft):
    return _pipeline_manifest_compiler.normalize_pipeline_draft(draft)


def pipeline_control_snapshot(
    definition,
    manifest,
    definitions,
    definition_authorities,
):
    return _pipeline_service.pipeline_control_snapshot(
        definition,
        manifest,
        definitions,
        definition_authorities,
    )


def normalize_signal_graph(graph):
    return _pipeline_manifest_compiler.normalize_signal_graph(graph)


def validate_signal_graph(
    graph, instances, definitions, initial_data_keys, *, required_roots=None
):
    return _pipeline_manifest_compiler.validate_signal_graph(
        graph,
        instances,
        definitions,
        initial_data_keys,
        required_roots=required_roots,
    )


def apply_pipeline_module_contract(
    module, definition, available_contracts, required_roots
):
    """Control-plane entry for the shared Pipeline stage contract."""

    return _apply_pipeline_module_contract(
        module,
        definition,
        available_contracts,
        required_roots,
    )


def compile_pipeline_manifest(config, definition):
    return _pipeline_service.compile_pipeline_manifest(config, definition)


def compile_pipeline_manifest_from_definition_authorities(
    definition, definitions, definition_authorities
):
    """Compile a Pipeline from exact Module records verified in this call stack."""
    return _pipeline_manifest_compiler.compile_pipeline_manifest_from_definition_authorities(
        definition,
        definitions,
        definition_authorities,
    )


def compile_pipeline_manifest_from_definitions(definition, definitions):
    """Strict raw compiler for a Pipeline and its referenced Module records."""
    return _pipeline_manifest_compiler.compile_pipeline_manifest_from_definitions(
        definition,
        definitions,
    )


def main():
    raise SystemExit(
        "The standalone strategy submission API is retired because it bypasses platform authentication. "
        "Use engine_service.py and its authenticated /api routes over HTTPS."
    )


if __name__ == "__main__":
    main()
