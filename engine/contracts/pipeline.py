"""Pure contracts for immutable Pipeline manifests and compiled plans."""

from __future__ import annotations

from engine.contracts import strict_json
from engine.contracts.digest import canonical_json_digest, is_sha256_digest
from engine.contracts.contract_expansion import (
    expanded_contract_path_required,
    expanded_contract_root_paths,
    expand_contracts,
    resolve_expanded_contract_path,
)
from engine.contracts.contract_reducer import apply_expanded_contract_writes
from engine.contracts.data_compatibility import schemas_compatible
from engine.contracts.data_model import (
    normalize_data_key_schema,
    port_schema,
    schema_label,
)
from engine.contracts.data_path import canonical_data_key_order, split_data_path
from engine.contracts.graph import normalize_graph, validate_compiled_graph
from engine.contracts.module import (
    COMPILED_MODULE_FIELDS,
    ENGINE_MODULE_KINDS,
    MODULE_KINDS,
    normalize_ports,
    require_exact_fields,
)
from engine.contracts.observation_input import (
    OBSERVATION_INPUT_FIELDS,
    PIPELINE_CONFIG_FIELDS,
    compile_observation_projection_plan,
    normalize_pipeline_config,
)


STAGE_KINDS = {
    "universe": "Universe",
    "target": "Target",
    "constraint": "Constraint",
}
MULTI_STAGES = frozenset({"constraint"})
STAGES = {stage: list for stage in STAGE_KINDS}
PIPELINE_DRAFT_FIELDS = frozenset({
    "pipelineId",
    "name",
    "config",
    "instances",
    "stages",
    "signalGraph",
})
PIPELINE_MANIFEST_FIELDS = frozenset({
    "name",
    "config",
    "modules",
    "universe",
    "target",
    "constraint",
    "signalGraph",
    "topology",
})
PIPELINE_VERSION_FIELDS = PIPELINE_DRAFT_FIELDS | frozenset({
    "manifestHash",
    "version",
    "status",
    "contentDigest",
    "createdAt",
    "archive",
})
PIPELINE_CONTRACT_PLAN_FIELDS = frozenset({
    "inputContracts", "inputRequiredRoots", "outputContracts",
    "outputRequiredRoots", "allContracts", "allRequiredRoots",
    "observationInput", "observationProjection", "observationContractDigest",
    "signalPlan", "topology", "directPlans",
})
PIPELINE_DIRECT_PLAN_FIELDS = frozenset({"nodeId", "phase", "inputs", "outputs"})
PIPELINE_DIRECT_INPUT_FIELDS = frozenset({"port", "dataKey", "required"})
PIPELINE_DIRECT_OUTPUT_FIELDS = frozenset({"port", "dataKey"})


def pipeline_manifest_digest(payload):
    """Return the historical unprefixed digest used by Pipeline snapshots."""

    return canonical_json_digest(payload)


def require_canonical_value_match(actual, expected, *, label):
    """Require exact strict-JSON equality, including numeric representations."""

    if strict_json.dumps(actual, sort_keys=True) != strict_json.dumps(
        expected,
        sort_keys=True,
    ):
        raise ValueError(
            f"{label} does not match its verified composition authority."
        )


def _require_contract_state(container, contracts_field, roots_field, *, label):
    contracts = container[contracts_field]
    if not isinstance(contracts, dict):
        raise ValueError(f"{label}.{contracts_field} must be an object.")
    normalized = expand_contracts({
        data_key: normalize_data_key_schema(
            schema, path=f"{label}.{contracts_field}.{data_key}"
        )
        for data_key, schema in contracts.items()
        if isinstance(data_key, str) and data_key
    })
    if len(normalized) != len(contracts) or normalized != contracts:
        raise ValueError(
            f"{label}.{contracts_field} must contain normalized expanded "
            "DataKey contracts."
        )
    required_roots = container[roots_field]
    if (
        not isinstance(required_roots, list)
        or any(not isinstance(root, str) or not root for root in required_roots)
        or required_roots != sorted(set(required_roots))
        or not set(required_roots) <= set(expanded_contract_root_paths(normalized))
    ):
        raise ValueError(
            f"{label}.{roots_field} must be sorted declared root DataKeys."
        )


def require_pipeline_plan(plan, *, label="Pipeline plan"):
    """Validate the complete generic execution plan produced by the compiler."""

    if not isinstance(plan, dict):
        raise ValueError(f"{label} must be an object.")
    require_exact_fields(
        plan,
        allowed=PIPELINE_CONTRACT_PLAN_FIELDS,
        required=PIPELINE_CONTRACT_PLAN_FIELDS,
        label=label,
    )
    normalized_config = normalize_pipeline_config({
        "observationInput": plan["observationInput"]
    })["observationInput"]
    if normalized_config != plan["observationInput"]:
        raise ValueError(f"{label}.observationInput is not normalized.")
    expected_projection = compile_observation_projection_plan({
        "observationInput": plan["observationInput"]
    })
    if plan["observationProjection"] != expected_projection:
        raise ValueError(f"{label}.observationProjection is not canonical.")
    if not is_sha256_digest(plan["observationContractDigest"]):
        raise ValueError(f"{label}.observationContractDigest is invalid.")
    for contracts_field, roots_field in (
        ("inputContracts", "inputRequiredRoots"),
        ("outputContracts", "outputRequiredRoots"),
        ("allContracts", "allRequiredRoots"),
    ):
        _require_contract_state(
            plan,
            contracts_field,
            roots_field,
            label=label,
        )
    topology = plan["topology"]
    if (
        not isinstance(topology, list)
        or any(not isinstance(node_id, str) or not node_id for node_id in topology)
        or len(topology) != len(set(topology))
    ):
        raise ValueError(
            f"{label}.topology must contain unique ordered Module IDs."
        )
    direct_plans = plan["directPlans"]
    if not isinstance(direct_plans, list):
        raise ValueError(f"{label}.directPlans must be an array.")
    direct_nodes = []
    for index, direct_plan in enumerate(direct_plans):
        direct_label = f"{label}.directPlans[{index}]"
        require_exact_fields(
            direct_plan,
            allowed=PIPELINE_DIRECT_PLAN_FIELDS,
            required=PIPELINE_DIRECT_PLAN_FIELDS,
            label=direct_label,
        )
        node_id = direct_plan["nodeId"]
        if not isinstance(node_id, str) or not node_id:
            raise ValueError(f"{direct_label}.nodeId must be a non-empty string.")
        if direct_plan["phase"] not in {"pre", "post"}:
            raise ValueError(f"{direct_label}.phase must be 'pre' or 'post'.")
        direct_nodes.append(node_id)
        for field, fields in (
            ("inputs", PIPELINE_DIRECT_INPUT_FIELDS),
            ("outputs", PIPELINE_DIRECT_OUTPUT_FIELDS),
        ):
            entries = direct_plan[field]
            if not isinstance(entries, list):
                raise ValueError(f"{direct_label}.{field} must be an array.")
            for entry_index, entry in enumerate(entries):
                entry_label = f"{direct_label}.{field}[{entry_index}]"
                require_exact_fields(
                    entry,
                    allowed=fields,
                    required=fields,
                    label=entry_label,
                )
                if (
                    not isinstance(entry["port"], str)
                    or not entry["port"]
                    or not isinstance(entry["dataKey"], str)
                    or not entry["dataKey"]
                ):
                    raise ValueError(
                        f"{entry_label} must use non-empty port and DataKey strings."
                    )
                split_data_path(entry["dataKey"])
                if field == "inputs" and not isinstance(entry["required"], bool):
                    raise ValueError(f"{entry_label}.required must be a boolean.")
        expected_inputs = sorted(
            direct_plan["inputs"], key=lambda entry: entry["port"]
        )
        expected_outputs = sorted(
            direct_plan["outputs"],
            key=lambda entry: canonical_data_key_order(
                entry["dataKey"], entry["port"]
            ),
        )
        if (
            direct_plan["inputs"] != expected_inputs
            or direct_plan["outputs"] != expected_outputs
        ):
            raise ValueError(f"{direct_label} bindings are not canonical.")
        for field in ("inputs", "outputs"):
            ports = [entry["port"] for entry in direct_plan[field]]
            if len(ports) != len(set(ports)):
                raise ValueError(f"{direct_label}.{field} contains duplicate ports.")
    signal_plan = validate_compiled_graph(
        plan["signalPlan"],
        label=f"{label} Signal Graph",
    )
    signal_topology = signal_plan["topology"]
    if (
        len(direct_nodes) != len(set(direct_nodes))
        or set(direct_nodes) != set(topology) - set(signal_topology)
    ):
        raise ValueError(
            f"{label} direct plans must exactly cover non-Signal topology nodes."
        )
    pre_nodes = [
        direct_plan["nodeId"]
        for direct_plan in direct_plans
        if direct_plan["phase"] == "pre"
    ]
    post_nodes = [
        direct_plan["nodeId"]
        for direct_plan in direct_plans
        if direct_plan["phase"] == "post"
    ]
    if topology != [*pre_nodes, *signal_topology, *post_nodes]:
        raise ValueError(
            f"{label}.topology must be the explicit pre, Signal, post order."
        )
    return plan


def validate_pipeline_topology(manifest):
    modules = {item["key"]: item for item in manifest["modules"]}
    order = [
        *manifest["universe"],
        *manifest["signalGraph"]["nodes"],
        *manifest["target"],
        *manifest["constraint"],
    ]
    if len(order) != len(set(order)):
        raise ValueError("Pipeline topology contains duplicate Module instances.")
    if set(order) != set(modules):
        raise ValueError(
            "Pipeline topology must contain every and only owned Module instance."
        )
    if manifest["topology"] != order:
        raise ValueError(
            "Pipeline topology order does not match its stage and Signal Graph order."
        )


def validate_pipeline_manifest(manifest):
    """Validate the exact normalized Engine Pipeline manifest shape."""

    require_exact_fields(
        manifest,
        allowed=PIPELINE_MANIFEST_FIELDS,
        required=PIPELINE_MANIFEST_FIELDS,
        label="Pipeline manifest",
    )
    if not isinstance(manifest["name"], str) or not manifest["name"].strip():
        raise ValueError("manifest.name is required.")
    if normalize_pipeline_config(manifest["config"]) != manifest["config"]:
        raise ValueError("manifest.config is not normalized.")
    modules = manifest["modules"]
    if not isinstance(modules, list):
        raise ValueError("manifest.modules must be an array.")

    module_keys = set()
    for module in modules:
        require_exact_fields(
            module,
            allowed=COMPILED_MODULE_FIELDS,
            required=COMPILED_MODULE_FIELDS,
            label="Compiled Pipeline Module",
        )
        key = module["key"]
        if not isinstance(key, str) or not key:
            raise ValueError("Each module requires a non-empty string key.")
        if key in module_keys:
            raise ValueError(f"Duplicate module key: {key}")
        module_keys.add(key)
        kind = module["kind"]
        if kind not in MODULE_KINDS:
            raise ValueError(f"Module '{key}' has invalid kind '{kind}'.")
        if kind not in ENGINE_MODULE_KINDS:
            raise ValueError(
                f"Module '{key}' has control-plane-only kind '{kind}' and cannot "
                "be written to Engine manifest."
            )
        if module["instanceId"] != key:
            raise ValueError(
                f"Compiled Pipeline Module '{key}' key must match instanceId."
            )
        if not isinstance(module["config"], dict):
            raise ValueError(f"Module '{key}' config must be an object.")
        inputs = module["inputs"]
        outputs = module["outputs"]
        if not isinstance(inputs, dict) or not isinstance(outputs, dict):
            raise ValueError(f"Module '{key}' inputs and outputs must be objects.")
        for direction, bindings in (("inputs", inputs), ("outputs", outputs)):
            for port_name, data_key in bindings.items():
                if not isinstance(port_name, str) or not port_name:
                    raise ValueError(
                        f"Module '{key}' has an invalid {direction} port name."
                    )
                if not isinstance(data_key, str) or not data_key:
                    raise ValueError(
                        f"Module '{key}' {direction}.{port_name} requires a DataKey."
                    )
    modules_by_key = {module["key"]: module for module in modules}
    owners = {}
    for stage, expected_type in STAGES.items():
        value = manifest[stage]
        if not isinstance(value, expected_type):
            raise ValueError(f"manifest.{stage} must be an array.")
        if stage not in MULTI_STAGES and len(value) > 1:
            raise ValueError(f"manifest.{stage} accepts at most one Module instance.")
        for key in value:
            if key not in module_keys:
                raise ValueError(
                    f"manifest.{stage} references unknown module '{key}'."
                )
            if key in owners:
                raise ValueError(f"Module '{key}' belongs to multiple Pipeline stages.")
            expected_kind = STAGE_KINDS[stage]
            if modules_by_key[key]["kind"] != expected_kind:
                raise ValueError(
                    f"manifest.{stage} requires kind '{expected_kind}', but Module "
                    f"'{key}' has kind '{modules_by_key[key]['kind']}'."
                )
            owners[key] = stage
    signal_graph = normalize_graph(
        manifest["signalGraph"],
        label="Pipeline Signal Graph",
    )
    if signal_graph != manifest["signalGraph"]:
        raise ValueError("manifest.signalGraph is not normalized.")
    for key in signal_graph["nodes"]:
        if key not in module_keys:
            raise ValueError(
                f"manifest.signalGraph references unknown Module '{key}'."
            )
        if key in owners:
            raise ValueError(
                f"Module '{key}' belongs to both a Pipeline stage and Signal Graph."
            )
        if modules_by_key[key]["kind"] != "Signal":
            raise ValueError(f"Signal Graph Module '{key}' must have kind 'Signal'.")
        owners[key] = "signalGraph"
    orphaned = sorted(module_keys - set(owners))
    if orphaned:
        raise ValueError(
            "Pipeline manifest contains unowned Module(s): " + ", ".join(orphaned)
        )
    validate_pipeline_topology(manifest)
    return manifest


def apply_pipeline_module_contract(
    module,
    definition,
    available_contracts,
    required_roots,
):
    """Apply one direct-stage Module contract to the preceding Data Dict state."""

    available = expand_contracts(available_contracts)
    roots = frozenset(required_roots)
    ports = normalize_ports(definition["ports"])
    missing_contract = object()
    for port_name, data_key in sorted(module["inputs"].items()):
        input_required = ports["inputs"][port_name]["required"]
        provided_schema = resolve_expanded_contract_path(
            available,
            data_key,
            missing_contract,
        )
        if provided_schema is missing_contract:
            if not input_required:
                continue
            raise ValueError(
                f"Pipeline Module '{module['key']}' input '{port_name}' cannot read "
                f"DataKey '{data_key}' at its stage."
            )
        if input_required and not expanded_contract_path_required(
            available,
            data_key,
            required_roots=roots,
        ):
            raise ValueError(
                f"Pipeline Module '{module['key']}' required input '{port_name}' "
                f"cannot rely on optional DataKey '{data_key}'."
            )
        required_schema = port_schema(ports["inputs"][port_name])
        if not schemas_compatible(provided_schema, required_schema):
            raise ValueError(
                f"Pipeline Module '{module['key']}' input '{port_name}' requires "
                f"{schema_label(required_schema)}, but DataKey '{data_key}' is "
                f"{schema_label(provided_schema)}."
            )
    return apply_expanded_contract_writes(
        available,
        roots,
        (
            (
                data_key,
                port_schema(ports["outputs"][port_name]),
                ports["outputs"][port_name]["required"],
            )
            for port_name, data_key in sorted(
                module["outputs"].items(),
                key=lambda item: canonical_data_key_order(item[1], item[0]),
            )
        ),
    )


__all__ = (
    "MULTI_STAGES",
    "PIPELINE_CONTRACT_PLAN_FIELDS",
    "PIPELINE_CONFIG_FIELDS",
    "PIPELINE_DIRECT_INPUT_FIELDS",
    "PIPELINE_DIRECT_OUTPUT_FIELDS",
    "PIPELINE_DIRECT_PLAN_FIELDS",
    "PIPELINE_DRAFT_FIELDS",
    "PIPELINE_MANIFEST_FIELDS",
    "PIPELINE_VERSION_FIELDS",
    "OBSERVATION_INPUT_FIELDS",
    "STAGES",
    "STAGE_KINDS",
    "apply_pipeline_module_contract",
    "pipeline_manifest_digest",
    "normalize_pipeline_config",
    "require_canonical_value_match",
    "require_pipeline_plan",
    "validate_pipeline_manifest",
    "validate_pipeline_topology",
)
