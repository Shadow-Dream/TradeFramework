"""Normalize Pipeline Definitions and rebuild their immutable manifests."""

from __future__ import annotations

from copy import deepcopy

from engine.authority import module_definition as _module_definition_authority
from engine.compiler.graph import (
    compile_module_graph,
    compile_verified_module_graph,
)
from engine.contracts import strict_json
from engine.contracts.archive import require_resource_path_segment
from engine.contracts.exact_fields import require_exact_fields
from engine.contracts.graph import compiled_graph_definition, normalize_graph
from engine.contracts.json_schema import validate_config
from engine.contracts.module import (
    ENGINE_MODULE_KINDS,
    MODULE_INSTANCE_FIELDS,
    definition_key,
    validate_instance_wiring,
    validate_module_definition,
)
from engine.contracts.pipeline import (
    MULTI_STAGES,
    PIPELINE_DRAFT_FIELDS,
    STAGES,
    STAGE_KINDS,
    normalize_pipeline_config,
    validate_pipeline_manifest,
)


def normalize_pipeline_id(value):
    return require_resource_path_segment(value, label="pipelineId")


def _get_definition(definitions, kind, module_id, version):
    key = definition_key(kind, module_id, version)
    definition = definitions.get(key)
    if definition is None:
        raise ValueError(f"Module definition does not exist: {key}")
    return definition


def normalize_module_instance_from_definitions(request, definitions):
    require_exact_fields(
        request,
        allowed=MODULE_INSTANCE_FIELDS,
        required=MODULE_INSTANCE_FIELDS,
        label="Module instance",
    )
    instance_id = request["instanceId"]
    kind = request["kind"]
    module_id = request["moduleId"]
    version = request["version"]
    if any(
        not isinstance(value, str) or not value.strip()
        for value in (instance_id, kind, module_id, version)
    ):
        raise ValueError(
            "instanceId, kind, moduleId and version must be non-empty strings."
        )
    require_resource_path_segment(instance_id, label="instanceId")

    definition = _get_definition(definitions, kind, module_id, version)
    instance = {
        "instanceId": instance_id,
        "kind": kind,
        "moduleId": module_id,
        "version": version,
        "config": deepcopy(request["config"]),
        "inputs": deepcopy(request["inputs"]),
        "outputs": deepcopy(request["outputs"]),
    }
    if not isinstance(instance["config"], dict):
        raise ValueError("instance config must be an object.")
    validate_config(
        instance["config"],
        definition["configSchema"],
        path=f"instance.{instance_id}.config",
    )
    validate_instance_wiring(instance, definition)
    return instance


def normalize_pipeline_instances_from_definitions(instances, definitions):
    if not isinstance(instances, dict):
        raise ValueError("instances must be an object keyed by instanceId.")
    normalized = {}
    for key, item in instances.items():
        if not isinstance(item, dict):
            raise ValueError(f"instances.{key} must be an object.")
        instance = normalize_module_instance_from_definitions(item, definitions)
        if instance["instanceId"] != key:
            raise ValueError(
                f"instances key '{key}' must match instanceId "
                f"'{instance['instanceId']}'."
            )
        normalized[key] = instance
    return normalized


def compile_module(instance, definition):
    return {
        "key": instance["instanceId"],
        "instanceId": instance["instanceId"],
        "kind": instance["kind"],
        "moduleId": instance["moduleId"],
        "version": definition["version"],
        "config": deepcopy(instance["config"]),
        "inputs": deepcopy(instance["inputs"]),
        "outputs": deepcopy(instance["outputs"]),
    }


def normalize_stage_references(stages):
    if not isinstance(stages, dict):
        raise ValueError("stages must be an object.")
    unknown = sorted(set(stages) - set(STAGES))
    if unknown:
        raise ValueError(
            "Pipeline stages contain unsupported stage(s): " + ", ".join(unknown)
        )
    result = {}
    for stage, expected_type in STAGES.items():
        value = stages.get(stage, [])
        if not isinstance(value, expected_type):
            raise ValueError(f"stages.{stage} must be an array.")
        if not all(
            isinstance(instance_id, str) and instance_id for instance_id in value
        ):
            raise ValueError(
                f"stages.{stage} must contain non-empty instance IDs."
            )
        if stage not in MULTI_STAGES and len(value) > 1:
            raise ValueError(f"stages.{stage} accepts at most one Module instance.")
        result[stage] = list(value)
    return result


def collect_pipeline_instance_ids(stages):
    ids = []
    for values in stages.values():
        ids.extend(values)
    return ids


def active_instance_ids(definition):
    if not isinstance(definition, dict):
        raise ValueError("Pipeline Definition must be an object.")
    stages = normalize_stage_references(definition["stages"])
    ids = collect_pipeline_instance_ids(stages)
    ids.extend(normalize_signal_graph(definition["signalGraph"])["nodes"])
    return list(dict.fromkeys(ids))


def normalize_pipeline_draft(draft):
    if not isinstance(draft, dict):
        raise ValueError("Pipeline Draft must be an object.")
    unknown_fields = sorted(set(draft) - PIPELINE_DRAFT_FIELDS)
    if unknown_fields:
        raise ValueError(
            "Pipeline Draft contains unsupported field(s): "
            + ", ".join(unknown_fields)
        )
    if "signalGraph" not in draft:
        raise ValueError("Pipeline Draft requires signalGraph.")
    required_fields = {
        "pipelineId",
        "name",
        "config",
        "instances",
        "stages",
        "signalGraph",
    }
    missing_fields = sorted(required_fields - set(draft))
    if missing_fields:
        raise ValueError(
            "Pipeline Draft is missing required field(s): "
            + ", ".join(missing_fields)
        )
    normalized = {
        key: draft[key]
        for key in (
            "pipelineId",
            "name",
            "config",
            "instances",
            "stages",
            "signalGraph",
        )
        if key in draft
    }
    normalized["pipelineId"] = normalize_pipeline_id(normalized["pipelineId"])
    if not isinstance(normalized["name"], str) or not normalized["name"].strip():
        raise ValueError("Pipeline Draft name must be a non-empty string.")
    normalized["config"] = normalize_pipeline_config(normalized["config"])
    signal_graph = normalize_signal_graph(normalized["signalGraph"])
    stages = normalize_stage_references(normalized["stages"])
    instances = normalized["instances"]
    if not isinstance(instances, dict):
        raise ValueError("Pipeline Definition instances must be an object.")
    owners = {}
    for stage, expected_kind in STAGE_KINDS.items():
        values = stages[stage]
        duplicates = sorted({value for value in values if values.count(value) > 1})
        if duplicates:
            raise ValueError(
                f"Pipeline stage '{stage}' contains duplicate instance(s): "
                + ", ".join(duplicates)
            )
        for instance_id in values:
            if instance_id in owners:
                raise ValueError(
                    f"Pipeline Module instance '{instance_id}' belongs to both "
                    f"'{owners[instance_id]}' and '{stage}'."
                )
            instance = instances.get(instance_id)
            if instance is None:
                raise ValueError(
                    f"Pipeline stage '{stage}' references unknown instance "
                    f"'{instance_id}'."
                )
            if instance.get("kind") != expected_kind:
                raise ValueError(
                    f"Pipeline stage '{stage}' requires kind '{expected_kind}', but "
                    f"instance '{instance_id}' has kind '{instance.get('kind')}'."
                )
            owners[instance_id] = stage
    for instance_id in signal_graph["nodes"]:
        if instance_id in owners:
            raise ValueError(
                f"Pipeline Module instance '{instance_id}' belongs to both "
                f"'{owners[instance_id]}' and Signal Graph."
            )
        instance = instances.get(instance_id)
        if instance is None:
            raise ValueError(
                f"Signal Graph references unknown instance '{instance_id}'."
            )
        if instance.get("kind") != "Signal":
            raise ValueError(
                f"Signal Graph requires kind 'Signal', but instance '{instance_id}' "
                f"has kind '{instance.get('kind')}'."
            )
        owners[instance_id] = "signalGraph"
    orphaned = sorted(set(instances) - set(owners))
    if orphaned:
        raise ValueError(
            "Pipeline contains Module instance(s) with no stage or Signal Graph "
            "ownership: " + ", ".join(orphaned)
        )
    normalized["signalGraph"] = signal_graph
    normalized["stages"] = stages
    return normalized


def normalize_signal_graph(graph):
    return normalize_graph(graph, label="Signal Graph")


def validate_signal_graph(
    graph,
    instances,
    definitions,
    initial_data_keys,
    *,
    required_roots=None,
):
    return compile_module_graph(
        graph,
        instances,
        definitions,
        initial_data_keys,
        allowed_kinds={"Signal"},
        label="Signal Graph",
        required_roots=required_roots,
    )


def compile_pipeline_manifest_from_definition_authorities(
    definition,
    definitions,
    definition_authorities,
):
    """Rebuild a Pipeline manifest from exact verified Module authorities."""

    definition = normalize_pipeline_draft(
        {key: definition[key] for key in PIPELINE_DRAFT_FIELDS if key in definition}
    )
    instances = normalize_pipeline_instances_from_definitions(
        definition["instances"], definitions
    )
    required_definition_keys = {
        definition_key(instance["kind"], instance["moduleId"], instance["version"])
        for instance in instances.values()
    }
    if not isinstance(definition_authorities, dict):
        raise TypeError("Pipeline Module Definition authorities must be an object.")
    if set(definition_authorities) != required_definition_keys:
        raise ValueError(
            "Pipeline Module Definition authorities must exactly match its instances."
        )
    active_definitions = {
        key: _get_definition(definitions, *key.split("/", 2))
        for key in sorted(required_definition_keys)
    }
    for key, module_definition in active_definitions.items():
        authority_definition = (
            _module_definition_authority.verified_module_definition_material(
                definition_authorities[key]
            )
        )
        if strict_json.dumps(
            authority_definition,
            sort_keys=True,
        ) != strict_json.dumps(module_definition, sort_keys=True):
            raise ValueError(
                "Pipeline Module Definition authority does not match its record."
            )
        validate_module_definition(module_definition)

    stages = normalize_stage_references(definition["stages"])
    referenced_ids = collect_pipeline_instance_ids(stages)
    referenced_ids.extend(definition["signalGraph"]["nodes"])
    referenced_ids = list(dict.fromkeys(referenced_ids))

    modules = []
    module_keys = set()
    for instance_id in referenced_ids:
        instance = instances.get(instance_id)
        if instance is None:
            raise ValueError(
                f"Pipeline Definition references unknown instance '{instance_id}'."
            )
        if instance["kind"] not in ENGINE_MODULE_KINDS:
            raise ValueError(
                f"Pipeline execution references non-Pipeline Module '{instance_id}'."
            )
        module_definition = _get_definition(
            definitions,
            instance["kind"],
            instance["moduleId"],
            instance["version"],
        )
        module = compile_module(instance, module_definition)
        if module["key"] not in module_keys:
            modules.append(module)
            module_keys.add(module["key"])

    signal_instance_ids = definition["signalGraph"]["nodes"]
    signal_instances = {
        instance_id: instances[instance_id] for instance_id in signal_instance_ids
    }
    preliminary_signal_plan = compile_verified_module_graph(
        definition["signalGraph"],
        signal_instances,
        definition_authorities,
        {},
        allowed_kinds={"Signal"},
        label="Signal Graph",
        strict_sources=False,
        required_roots=frozenset(),
    )
    signal_graph = compiled_graph_definition(
        preliminary_signal_plan,
        label="Signal Graph",
    )

    manifest = {
        "name": definition["name"],
        "config": deepcopy(definition["config"]),
        "modules": modules,
        "universe": stages["universe"],
        "target": stages["target"],
        "constraint": stages["constraint"],
        "signalGraph": signal_graph,
        "topology": [
            *stages["universe"],
            *preliminary_signal_plan["topology"],
            *stages["target"],
            *stages["constraint"],
        ],
    }
    return validate_pipeline_manifest(manifest)


def compile_pipeline_manifest_from_definitions(definition, definitions):
    """Strict raw compiler for a Pipeline and its referenced Module records."""

    normalized = normalize_pipeline_draft(
        {key: definition[key] for key in PIPELINE_DRAFT_FIELDS if key in definition}
    )
    instances = normalize_pipeline_instances_from_definitions(
        normalized["instances"], definitions
    )
    required = {
        definition_key(instance["kind"], instance["moduleId"], instance["version"])
        for instance in instances.values()
    }
    authorities = {
        key: _module_definition_authority.verify_module_definition_authority(
            definitions[key]
        )
        for key in sorted(required)
    }
    return compile_pipeline_manifest_from_definition_authorities(
        normalized,
        definitions,
        authorities,
    )


__all__ = (
    "active_instance_ids",
    "collect_pipeline_instance_ids",
    "compile_module",
    "compile_pipeline_manifest_from_definition_authorities",
    "compile_pipeline_manifest_from_definitions",
    "normalize_module_instance_from_definitions",
    "normalize_pipeline_draft",
    "normalize_pipeline_id",
    "normalize_pipeline_instances_from_definitions",
    "normalize_signal_graph",
    "normalize_stage_references",
    "validate_signal_graph",
)
