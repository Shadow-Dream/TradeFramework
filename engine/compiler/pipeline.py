"""Pipeline contract compiler and exact frozen-plan binder."""

from __future__ import annotations

import copy

from engine.authority import graph as graph_authority
from engine.authority import module_definition as module_definition_authority
from engine.authority import module_invocation as module_invocation_authority
from engine.authority import pipeline as pipeline_authority
from engine.authority.pipeline import (
    seal_pipeline_contract_plan as _seal_pipeline_contract_plan,
    seal_pipeline_contract_plan_authority as _seal_pipeline_contract_plan_authority,
)
from engine.compiler import graph as graph_compiler
from engine.contracts import strict_json
from engine.contracts.contract_expansion import (
    expanded_contract_root_paths,
    expand_contracts,
)
from engine.contracts.contract_reducer import ExpandedContractWriteReducer
from engine.contracts.data_model import port_schema
from engine.contracts.data_path import canonical_data_key_order
from engine.contracts.graph import compiled_graph_output_writes
from engine.contracts.json_schema import validate_config
from engine.contracts.module import (
    MODULE_INSTANCE_FIELDS,
    definition_key,
    normalize_ports,
    validate_instance_wiring,
    validate_module_definition,
)
from engine.contracts.observation_input import (
    compile_observation_projection_plan,
)
from engine.contracts.pipeline import (
    apply_pipeline_module_contract,
    require_canonical_value_match,
    validate_pipeline_manifest,
)
from engine.contracts.observation_projection import (
    observation_contract_digest,
    project_observation_contract_state,
)


def compile_pipeline_contract_template(manifest, module_definitions):
    """Strictly validate raw authorities once and capture an invariant template."""

    validate_pipeline_manifest(manifest)
    if not isinstance(module_definitions, dict):
        raise ValueError("Pipeline Module definitions must be an object.")
    required_keys = {
        definition_key(module["kind"], module["moduleId"], module["version"])
        for module in manifest["modules"]
    }
    if set(module_definitions) != required_keys:
        raise ValueError(
            "Pipeline Module definitions must exactly match its manifest."
        )
    definition_authorities = {
        key: module_definition_authority.verify_module_definition_authority(
            module_definitions[key]
        )
        for key in sorted(required_keys)
    }
    return pipeline_contract_template_from_verified_authorities(
        manifest,
        module_definitions,
        definition_authorities,
    )


def pipeline_contract_template_from_verified_authorities(
    manifest,
    module_definitions,
    definition_authorities,
):
    """Validate an exact verified authority set and capture its template."""

    validate_pipeline_manifest(manifest)
    if not isinstance(module_definitions, dict):
        raise ValueError("Pipeline Module definitions must be an object.")
    required_keys = {
        definition_key(module["kind"], module["moduleId"], module["version"])
        for module in manifest["modules"]
    }
    if set(module_definitions) != required_keys:
        raise ValueError(
            "Pipeline Module definitions must exactly match its manifest."
        )
    if not isinstance(definition_authorities, dict):
        raise TypeError("Pipeline Module Definition authorities must be an object.")
    if set(definition_authorities) != required_keys:
        raise ValueError(
            "Pipeline Module Definition authorities must exactly match its manifest."
        )
    modules = {module["key"]: module for module in manifest["modules"]}
    for node_id, module in modules.items():
        key = definition_key(
            module["kind"], module["moduleId"], module["version"]
        )
        definition = module_definitions[key]
        verified_definition = (
            module_definition_authority.verified_module_definition_material(
                definition_authorities[key]
            )
        )
        if strict_json.dumps(definition, sort_keys=True) != strict_json.dumps(
            verified_definition,
            sort_keys=True,
        ):
            raise ValueError(
                "Pipeline Module Definition authority does not match its record."
            )
        if definition_key(
            definition.get("kind"),
            definition.get("moduleId"),
            definition.get("version"),
        ) != key:
            raise ValueError(
                "Pipeline Module Definition identity does not match its index key."
            )
        validate_module_definition(definition)
        validate_config(
            module["config"],
            definition["configSchema"],
            path=f"Pipeline Module '{node_id}'.config",
        )
        validate_instance_wiring(module, definition)
    return pipeline_authority.pipeline_contract_template_from_verified_authorities(
        manifest,
        module_definitions,
        definition_authorities,
    )


def bind_pipeline_contract_plan(
    template,
    initial_contracts,
    *,
    initial_required_roots=None,
):
    """Bind one contract state to an already verified Pipeline template."""

    pipeline_authority.require_pipeline_contract_template(template)
    if not isinstance(initial_contracts, dict):
        raise ValueError("Pipeline initial contracts must be an object.")
    material = pipeline_authority.pipeline_contract_template_material(template)
    modules = material["modules"]
    topology = list(material["topology"])
    signal_definition = material["signalPlan"]
    module_definitions = material["moduleDefinitions"]
    definition_authorities = material["moduleDefinitionAuthorities"]
    pre_node_ids = material["preNodeIds"]
    post_node_ids = material["postNodeIds"]
    observation = expand_contracts(initial_contracts)
    observation_contract_roots = expanded_contract_root_paths(observation)
    observation_required_roots = (
        observation_contract_roots
        if initial_required_roots is None
        else frozenset(initial_required_roots)
    )
    observation_digest = observation_contract_digest(
        observation,
        observation_required_roots,
    )
    observation_projection = compile_observation_projection_plan(
        material["config"]
    )
    available, available_required_roots = project_observation_contract_state(
        observation,
        observation_required_roots,
        material["config"],
    )
    input_contracts = copy.deepcopy(available)
    input_required_roots = frozenset(available_required_roots)
    declared_contracts = ExpandedContractWriteReducer({}, frozenset())

    def archived_definition(binding):
        return module_definitions[
            definition_key(
                binding["kind"],
                binding["moduleId"],
                binding["version"],
            )
        ]

    def capture_outputs(binding):
        definition = archived_definition(binding)
        ports = normalize_ports(definition["ports"])["outputs"]
        for port_name, data_key in sorted(
            binding["outputs"].items(),
            key=lambda item: canonical_data_key_order(item[1], item[0]),
        ):
            declared_contracts.apply(
                data_key,
                port_schema(ports[port_name]),
                required=ports[port_name]["required"],
            )

    for node_id in pre_node_ids:
        available, available_required_roots = apply_pipeline_module_contract(
            modules[node_id],
            archived_definition(modules[node_id]),
            available,
            available_required_roots,
        )
        capture_outputs(modules[node_id])
    signal_authority = graph_compiler.compile_verified_module_graph_authority(
        signal_definition,
        {
            node_id: {
                name: copy.deepcopy(modules[node_id][name])
                for name in MODULE_INSTANCE_FIELDS
            }
            for node_id in signal_definition["nodes"]
        },
        definition_authorities,
        available,
        allowed_kinds={"Signal"},
        label="Signal Graph",
        required_roots=available_required_roots,
    )
    runtime_signal_plan = graph_authority.compiled_graph_authority_plan(
        signal_authority
    )
    available_writes = ExpandedContractWriteReducer(
        available,
        available_required_roots,
    )
    for _boundary_id, data_key, schema, required in compiled_graph_output_writes(
        runtime_signal_plan
    ):
        available_writes.apply(data_key, schema, required=required)
        declared_contracts.apply(data_key, schema, required=required)
    available, available_required_roots = available_writes.materialize()
    for node_id in post_node_ids:
        available, available_required_roots = apply_pipeline_module_contract(
            modules[node_id],
            archived_definition(modules[node_id]),
            available,
            available_required_roots,
        )
        capture_outputs(modules[node_id])
    declared_contracts, declared_required_roots = declared_contracts.materialize()
    all_contracts = expand_contracts(available)
    input_contracts = expand_contracts(input_contracts)
    plan = {
        "inputContracts": input_contracts,
        "outputContracts": declared_contracts,
        "outputRequiredRoots": sorted(declared_required_roots),
        "allContracts": all_contracts,
        "allRequiredRoots": sorted(available_required_roots),
        "inputRequiredRoots": sorted(
            input_required_roots
        ),
        "observationInput": copy.deepcopy(
            material["config"]["observationInput"]
        ),
        "observationProjection": observation_projection,
        "observationContractDigest": observation_digest,
        "signalPlan": runtime_signal_plan,
        "topology": topology,
        "directPlans": copy.deepcopy(list(material["directPlans"])),
    }
    direct_node_ids = tuple(
        direct_plan["nodeId"] for direct_plan in plan["directPlans"]
    )
    direct_bindings = {
        node_id: {
            name: copy.deepcopy(modules[node_id][name])
            for name in MODULE_INSTANCE_FIELDS
        }
        for node_id in direct_node_ids
    }
    direct_authorities = (
        module_invocation_authority.bind_module_invocation_authorities(
            direct_node_ids,
            direct_bindings,
            definition_authorities,
        )
    )
    return _seal_pipeline_contract_plan(
        template,
        plan,
        signal_authority,
        direct_authorities,
    )


def bind_validated_pipeline_contract_plan(
    template,
    frozen_plan,
    initial_contracts,
    *,
    initial_required_roots,
    label="Validated Pipeline plan",
):
    """Semantically bind a validated Pipeline plan without recompiling its Graph."""

    try:
        pipeline_authority.require_pipeline_contract_template(template)
    except TypeError as exc:
        raise TypeError(
            "Frozen Pipeline binding requires a verified Pipeline template."
        ) from exc
    pipeline_authority.require_validated_pipeline_plan(frozen_plan)
    validated_frozen_plan = frozen_plan
    frozen_plan = pipeline_authority.validated_pipeline_plan_material(
        frozen_plan
    )
    if not isinstance(initial_contracts, dict):
        raise ValueError("Pipeline initial contracts must be an object.")
    material = pipeline_authority.pipeline_contract_template_material(template)
    observation = expand_contracts(initial_contracts)
    observation_contract_roots = expanded_contract_root_paths(observation)
    observation_required_roots = frozenset(initial_required_roots)
    if not observation_required_roots <= observation_contract_roots:
        raise ValueError(
            "Pipeline initial required roots must be declared by its contracts."
        )
    observation_digest = observation_contract_digest(
        observation,
        observation_required_roots,
    )
    observation_projection = compile_observation_projection_plan(
        material["config"]
    )
    available, available_required_roots = project_observation_contract_state(
        observation,
        observation_required_roots,
        material["config"],
    )
    input_contracts = copy.deepcopy(available)
    input_required_roots = frozenset(available_required_roots)

    modules = material["modules"]
    module_definitions = material["moduleDefinitions"]
    definition_authorities = material["moduleDefinitionAuthorities"]
    declared_contracts = ExpandedContractWriteReducer({}, frozenset())

    def archived_definition(binding):
        return module_definitions[
            definition_key(
                binding["kind"],
                binding["moduleId"],
                binding["version"],
            )
        ]

    def capture_outputs(binding):
        ports = normalize_ports(archived_definition(binding)["ports"])["outputs"]
        for port_name, data_key in sorted(
            binding["outputs"].items(),
            key=lambda item: canonical_data_key_order(item[1], item[0]),
        ):
            declared_contracts.apply(
                data_key,
                port_schema(ports[port_name]),
                required=ports[port_name]["required"],
            )

    for node_id in material["preNodeIds"]:
        binding = modules[node_id]
        available, available_required_roots = apply_pipeline_module_contract(
            binding,
            archived_definition(binding),
            available,
            available_required_roots,
        )
        capture_outputs(binding)

    signal_definition = copy.deepcopy(material["signalPlan"])
    signal_instances = {
        node_id: {
            name: copy.deepcopy(modules[node_id][name])
            for name in MODULE_INSTANCE_FIELDS
        }
        for node_id in signal_definition["nodes"]
    }
    signal_keys = {
        definition_key(
            binding["kind"], binding["moduleId"], binding["version"]
        )
        for binding in signal_instances.values()
    }
    signal_definition_authorities = {
        key: definition_authorities[key] for key in signal_keys
    }
    signal_authority = graph_authority.bind_frozen_composition_graph_authority(
        frozen_plan["signalPlan"],
        signal_definition,
        signal_instances,
        signal_definition_authorities,
        available,
        allowed_kinds={"Signal"},
        label=f"{label} Signal Graph",
        required_roots=available_required_roots,
    )
    signal_plan = graph_authority.compiled_graph_authority_plan(signal_authority)
    available_writes = ExpandedContractWriteReducer(
        available,
        available_required_roots,
    )
    for _boundary_id, data_key, schema, required in compiled_graph_output_writes(
        signal_plan
    ):
        available_writes.apply(data_key, schema, required=required)
        declared_contracts.apply(data_key, schema, required=required)
    available, available_required_roots = available_writes.materialize()
    for node_id in material["postNodeIds"]:
        binding = modules[node_id]
        available, available_required_roots = apply_pipeline_module_contract(
            binding,
            archived_definition(binding),
            available,
            available_required_roots,
        )
        capture_outputs(binding)

    declared_contracts, declared_required_roots = declared_contracts.materialize()
    input_contracts = expand_contracts(input_contracts)
    all_contracts = expand_contracts(available)
    expected_fields = {
        "inputContracts": input_contracts,
        "inputRequiredRoots": sorted(input_required_roots),
        "observationInput": copy.deepcopy(
            material["config"]["observationInput"]
        ),
        "observationProjection": observation_projection,
        "observationContractDigest": observation_digest,
        "outputContracts": declared_contracts,
        "outputRequiredRoots": sorted(declared_required_roots),
        "allContracts": all_contracts,
        "allRequiredRoots": sorted(available_required_roots),
        "topology": list(material["topology"]),
        "directPlans": list(material["directPlans"]),
    }
    for name, expected in expected_fields.items():
        require_canonical_value_match(
            frozen_plan[name],
            expected,
            label=f"{label}.{name}",
        )
    require_canonical_value_match(
        frozen_plan["signalPlan"],
        signal_plan,
        label=f"{label}.signalPlan",
    )
    direct_node_ids = tuple(
        direct_plan["nodeId"] for direct_plan in frozen_plan["directPlans"]
    )
    direct_bindings = {
        node_id: {
            name: copy.deepcopy(modules[node_id][name])
            for name in MODULE_INSTANCE_FIELDS
        }
        for node_id in direct_node_ids
    }
    direct_authorities = (
        module_invocation_authority.bind_module_invocation_authorities(
            direct_node_ids,
            direct_bindings,
            definition_authorities,
        )
    )
    return _seal_pipeline_contract_plan_authority(
        template,
        validated_frozen_plan,
        signal_authority,
        direct_authorities,
    )


def compile_pipeline_contract_plan(
    manifest,
    module_definitions,
    initial_contracts,
    *,
    initial_required_roots=None,
):
    """Compile one raw Pipeline and return its isolated contract plan."""

    template = compile_pipeline_contract_template(manifest, module_definitions)
    bound = bind_pipeline_contract_plan(
        template,
        initial_contracts,
        initial_required_roots=initial_required_roots,
    )
    plan, _signal_authority, _direct_authorities = (
        pipeline_authority.bound_pipeline_contract_plan_material(bound)
    )
    return plan


__all__ = (
    "bind_validated_pipeline_contract_plan",
    "bind_pipeline_contract_plan",
    "compile_pipeline_contract_plan",
    "compile_pipeline_contract_template",
    "pipeline_contract_template_from_verified_authorities",
)
