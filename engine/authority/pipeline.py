"""Nominal authorities for verified and compiled Pipeline execution."""

from __future__ import annotations

import copy
import hashlib
from dataclasses import dataclass, field

from engine.authority import execution_records
from engine.archive import version as version_archive
from engine.authority import graph as graph_authority
from engine.authority import module_definition as module_definition_authority
from engine.authority import module_invocation as module_invocation_authority
from engine.contracts import strict_json
from engine.contracts.data_path import canonical_data_key_order
from engine.contracts.json_schema import validate_config
from engine.contracts.module import (
    MODULE_INSTANCE_FIELDS,
    definition_key,
    normalize_ports,
    require_exact_fields,
    validate_instance_wiring,
    validate_module_definition,
)
from engine.contracts.pipeline import (
    pipeline_manifest_digest,
    require_canonical_value_match,
    require_pipeline_plan,
    validate_pipeline_manifest,
)


_PIPELINE_TEMPLATE_TOKEN = object()
_VALIDATED_PIPELINE_PLAN_TOKEN = object()
_BOUND_PIPELINE_PLAN_TOKEN = object()
_VERIFIED_PIPELINE_DEFINITION_TOKEN = object()
_COMPILED_PIPELINE_AUTHORITY_TOKEN = object()


class _ValidatedPipelinePlan:
    """Nominal proof that the complete Pipeline plan shape was validated."""

    __slots__ = ("_plan_json", "_plan_digest", "_sealed")

    def __setattr__(self, name, value):
        if getattr(self, "_sealed", False):
            raise AttributeError("Validated Pipeline plan is immutable.")
        object.__setattr__(self, name, value)

    def __init__(self, plan_json, *, _token):
        if _token is not _VALIDATED_PIPELINE_PLAN_TOKEN or not isinstance(
            plan_json, str
        ):
            raise TypeError("Validated Pipeline plan is Engine-owned.")
        self._plan_json = plan_json
        self._plan_digest = hashlib.sha256(plan_json.encode("utf-8")).hexdigest()
        object.__setattr__(self, "_sealed", True)

    def __copy__(self):
        raise TypeError("Validated Pipeline plan cannot be copied.")

    def __deepcopy__(self, _memo):
        raise TypeError("Validated Pipeline plan cannot be copied.")

    def __reduce__(self):
        raise TypeError("Validated Pipeline plan cannot be serialized.")


def seal_validated_pipeline_plan_authority(plan):
    """Seal material whose complete shape was proved by an enclosing artifact."""

    return _ValidatedPipelinePlan(
        strict_json.dumps(plan, sort_keys=True, separators=(",", ":")),
        _token=_VALIDATED_PIPELINE_PLAN_TOKEN,
    )


def _validate_and_seal_pipeline_plan(plan):
    """Run the standalone plan gate before issuing its nominal proof."""

    return seal_validated_pipeline_plan_authority(
        require_pipeline_plan(plan)
    )


def require_validated_pipeline_plan(plan):
    if type(plan) is not _ValidatedPipelinePlan:
        raise TypeError("Validated Pipeline plan is Engine-owned.")
    if (
        not isinstance(plan._plan_json, str)
        or not isinstance(plan._plan_digest, str)
        or hashlib.sha256(plan._plan_json.encode("utf-8")).hexdigest()
        != plan._plan_digest
    ):
        raise ValueError(
            "Validated Pipeline plan does not match its canonical authority."
        )
    return plan


def validated_pipeline_plan_material(plan):
    validated_plan = require_validated_pipeline_plan(plan)
    return strict_json.loads(validated_plan._plan_json)


def validated_pipeline_plan_digest(plan):
    """Return the canonical identity of a validated Pipeline plan."""

    return require_validated_pipeline_plan(plan)._plan_digest


@dataclass(frozen=True, slots=True)
class _PipelineContractTemplate:
    """Nominal same-stack proof for invariant Pipeline contract authorities."""

    _manifest: dict
    _module_definitions: dict
    _module_definition_authorities: dict
    _modules: dict
    _topology: tuple
    _signal_plan: dict
    _pre_node_ids: tuple
    _post_node_ids: tuple
    _config: dict
    _direct_plans: tuple
    _token: object = field(repr=False, compare=False)
    _provenance: object = field(init=False, repr=False, compare=False)

    def __post_init__(self):
        if self._token is not _PIPELINE_TEMPLATE_TOKEN:
            raise TypeError("Verified Pipeline template is Engine-owned.")
        object.__setattr__(self, "_provenance", object())


class _BoundPipelineContractPlan:
    """Immutable full-plan proof issued only by the Pipeline compiler."""

    __slots__ = (
        "_plan_json",
        "_plan_digest",
        "_signal_authority",
        "_direct_invocation_authorities",
        "_template_provenance",
        "_sealed",
    )

    def __setattr__(self, name, value):
        if getattr(self, "_sealed", False):
            raise AttributeError("Bound Pipeline plan is immutable.")
        object.__setattr__(self, name, value)

    def __init__(
        self,
        template,
        plan,
        signal_authority,
        direct_invocation_authorities,
        *,
        _token,
    ):
        if _token is not _BOUND_PIPELINE_PLAN_TOKEN:
            raise TypeError("Bound Pipeline plan is Engine-owned.")
        require_pipeline_contract_template(template)
        validated_plan = require_validated_pipeline_plan(plan)
        canonical_plan = strict_json.loads(validated_plan._plan_json)
        plan_json = validated_plan._plan_json
        direct_node_ids = tuple(
            direct_plan["nodeId"]
            for direct_plan in canonical_plan["directPlans"]
        )
        if len(direct_node_ids) != len(set(direct_node_ids)):
            raise ValueError("Bound Pipeline direct node IDs must be unique.")
        expected_direct_node_ids = (
            *template._pre_node_ids,
            *template._post_node_ids,
        )
        if direct_node_ids != expected_direct_node_ids:
            raise ValueError(
                "Bound Pipeline direct plan does not match its verified template."
            )
        if tuple(canonical_plan["topology"]) != template._topology:
            raise ValueError(
                "Bound Pipeline topology does not match its verified template."
            )
        require_canonical_value_match(
            canonical_plan["directPlans"],
            list(template._direct_plans),
            label="Bound Pipeline direct plans",
        )
        graph_authority.require_compiled_graph_authority(signal_authority)
        require_canonical_value_match(
            graph_authority.compiled_graph_authority_plan(signal_authority),
            canonical_plan["signalPlan"],
            label="Bound Pipeline Signal Graph authority",
        )
        direct_authorities = dict(direct_invocation_authorities)
        if set(direct_authorities) != set(direct_node_ids):
            raise TypeError(
                "Bound Pipeline Module invocations must exactly match its direct nodes."
            )
        for authority in direct_authorities.values():
            module_invocation_authority.require_module_invocation_authority(authority)
        self._plan_json = plan_json
        self._plan_digest = hashlib.sha256(
            plan_json.encode("utf-8")
        ).hexdigest()
        self._signal_authority = signal_authority
        self._direct_invocation_authorities = tuple(
            (node_id, direct_authorities[node_id]) for node_id in direct_node_ids
        )
        self._template_provenance = template._provenance
        object.__setattr__(self, "_sealed", True)


class _VerifiedPipelineDefinition:
    """Nominal proof for immutable identity facts used by Pipeline Runtime."""

    __slots__ = ("_pipeline_id", "_version", "_manifest_hash", "_sealed")

    def __setattr__(self, name, value):
        if getattr(self, "_sealed", False):
            raise AttributeError("Verified Pipeline Definition is immutable.")
        object.__setattr__(self, name, value)

    def __init__(self, definition, *, _token):
        if _token is not _VERIFIED_PIPELINE_DEFINITION_TOKEN:
            raise TypeError("Verified Pipeline Definition is Engine-owned.")
        for name in ("pipelineId", "version", "manifestHash"):
            if not isinstance(definition.get(name), str) or not definition[name]:
                raise ValueError(
                    f"Archived Pipeline Definition requires non-empty {name}."
                )
        self._pipeline_id = definition["pipelineId"]
        self._version = definition["version"]
        self._manifest_hash = definition["manifestHash"]
        object.__setattr__(self, "_sealed", True)


class _CompiledPipelineAuthority:
    """Bind one verified Pipeline identity to its exact execution plan."""

    __slots__ = (
        "_identity",
        "_contract_template",
        "_compiled_plan_json",
        "_signal_authority",
        "_direct_invocation_authorities",
        "_sealed",
    )

    def __setattr__(self, name, value):
        if getattr(self, "_sealed", False):
            raise AttributeError("Compiled Pipeline authority is immutable.")
        object.__setattr__(self, name, value)

    def __init__(self, identity, contract_template, bound_plan, *, _token):
        if _token is not _COMPILED_PIPELINE_AUTHORITY_TOKEN:
            raise TypeError("Compiled Pipeline authority is Engine-owned.")
        require_verified_pipeline_definition_authority(identity)
        require_pipeline_contract_template(contract_template)
        require_bound_pipeline_contract_plan(bound_plan)
        if bound_plan._template_provenance is not contract_template._provenance:
            raise ValueError(
                "Compiled Pipeline plan does not belong to its verified template."
            )
        compiled_plan, signal_authority, direct_authorities = (
            bound_pipeline_contract_plan_material(bound_plan)
        )
        compiled_plan_json = strict_json.dumps(
            compiled_plan,
            sort_keys=True,
            separators=(",", ":"),
        )
        if compiled_plan_json != bound_plan._plan_json:
            raise ValueError(
                "Compiled Pipeline plan does not match its complete canonical authority."
            )
        direct_node_ids = tuple(
            direct_plan["nodeId"] for direct_plan in compiled_plan["directPlans"]
        )
        expected_direct_node_ids = (
            *contract_template._pre_node_ids,
            *contract_template._post_node_ids,
        )
        if direct_node_ids != expected_direct_node_ids:
            raise ValueError(
                "Compiled Pipeline direct plan does not match its verified template."
            )
        if tuple(compiled_plan["topology"]) != contract_template._topology:
            raise ValueError(
                "Compiled Pipeline topology does not match its verified template."
            )
        require_canonical_value_match(
            compiled_plan["directPlans"],
            list(contract_template._direct_plans),
            label="Compiled Pipeline direct plans",
        )
        if set(direct_authorities) != set(direct_node_ids):
            raise TypeError(
                "Compiled Pipeline Module invocations must exactly match its direct nodes."
            )
        for node_id in direct_node_ids:
            invocation_binding, definition_authority = (
                module_invocation_authority.module_invocation_material(
                    direct_authorities[node_id]
                )
            )
            expected_binding = {
                name: copy.deepcopy(contract_template._modules[node_id][name])
                for name in MODULE_INSTANCE_FIELDS
            }
            key = definition_key(
                expected_binding["kind"],
                expected_binding["moduleId"],
                expected_binding["version"],
            )
            if invocation_binding != expected_binding or (
                definition_authority
                is not contract_template._module_definition_authorities[key]
            ):
                raise TypeError(
                    "Compiled Pipeline Module invocation does not match its plan."
                )
        pipeline_id, version, manifest_hash = pipeline_definition_identity(identity)
        if pipeline_manifest_digest(contract_template._manifest) != manifest_hash:
            raise ValueError(
                "Compiled Pipeline manifest does not match its verified identity."
            )
        self._identity = identity
        self._contract_template = _clone_pipeline_contract_template(
            contract_template
        )
        self._compiled_plan_json = compiled_plan_json
        require_canonical_value_match(
            graph_authority.compiled_graph_authority_plan(signal_authority),
            compiled_plan["signalPlan"],
            label="Compiled Pipeline Signal Graph authority",
        )
        self._signal_authority = signal_authority
        self._direct_invocation_authorities = tuple(
            (node_id, direct_authorities[node_id]) for node_id in direct_node_ids
        )
        object.__setattr__(self, "_sealed", True)


def require_pipeline_contract_template(template):
    if type(template) is not _PipelineContractTemplate:
        raise TypeError("Pipeline contract state binding requires a verified Pipeline template.")
    return template


def pipeline_contract_template_material(template):
    """Return isolated compiler/runtime material from one verified template."""

    require_pipeline_contract_template(template)
    return {
        "manifest": copy.deepcopy(template._manifest),
        "moduleDefinitions": copy.deepcopy(template._module_definitions),
        "moduleDefinitionAuthorities": dict(
            template._module_definition_authorities
        ),
        "modules": copy.deepcopy(template._modules),
        "topology": tuple(template._topology),
        "signalPlan": copy.deepcopy(template._signal_plan),
        "preNodeIds": tuple(template._pre_node_ids),
        "postNodeIds": tuple(template._post_node_ids),
        "config": copy.deepcopy(template._config),
        "directPlans": tuple(copy.deepcopy(template._direct_plans)),
    }


def _clone_pipeline_contract_template(template):
    material = pipeline_contract_template_material(template)
    return _PipelineContractTemplate(
        _manifest=material["manifest"],
        _module_definitions=material["moduleDefinitions"],
        _module_definition_authorities=material["moduleDefinitionAuthorities"],
        _modules=material["modules"],
        _topology=material["topology"],
        _signal_plan=material["signalPlan"],
        _pre_node_ids=material["preNodeIds"],
        _post_node_ids=material["postNodeIds"],
        _config=material["config"],
        _direct_plans=material["directPlans"],
        _token=_PIPELINE_TEMPLATE_TOKEN,
    )


def pipeline_contract_template_from_verified_authorities(
    manifest,
    module_definitions,
    module_definition_authorities,
):
    """Capture a Pipeline template from exact archived Module authorities."""

    frozen_manifest = copy.deepcopy(validate_pipeline_manifest(manifest))
    frozen_definitions = copy.deepcopy(module_definitions)
    if not isinstance(module_definition_authorities, dict):
        raise TypeError("Pipeline Module Definition authorities must be an object.")
    frozen_authorities = dict(module_definition_authorities)
    if set(frozen_authorities) != set(frozen_definitions):
        raise ValueError(
            "Pipeline Module Definition authorities must exactly match its definitions."
        )
    modules = {module["key"]: module for module in frozen_manifest["modules"]}
    required_keys = {
        definition_key(module["kind"], module["moduleId"], module["version"])
        for module in modules.values()
    }
    if set(frozen_definitions) != required_keys:
        raise ValueError(
            "Pipeline Module definitions must exactly match its manifest."
        )
    for key, authority in frozen_authorities.items():
        verified = module_definition_authority.verified_module_definition_material(
            authority
        )
        if strict_json.dumps(verified, sort_keys=True) != strict_json.dumps(
            frozen_definitions[key],
            sort_keys=True,
        ):
            raise ValueError(
                "Pipeline Module Definition authority does not match its frozen record."
            )

    pre_node_ids = tuple(frozen_manifest["universe"])
    post_node_ids = tuple((
        *frozen_manifest["target"],
        *frozen_manifest["constraint"],
    ))
    direct_plans = []
    for phase, node_ids in (("pre", pre_node_ids), ("post", post_node_ids)):
        for node_id in node_ids:
            binding = modules[node_id]
            key = definition_key(
                binding["kind"], binding["moduleId"], binding["version"]
            )
            ports = normalize_ports(frozen_definitions[key]["ports"])
            direct_plans.append({
                "nodeId": node_id,
                "phase": phase,
                "inputs": [
                    {
                        "port": port_name,
                        "dataKey": data_key,
                        "required": ports["inputs"][port_name]["required"],
                    }
                    for port_name, data_key in sorted(binding["inputs"].items())
                ],
                "outputs": [
                    {"port": port_name, "dataKey": data_key}
                    for port_name, data_key in sorted(
                        binding["outputs"].items(),
                        key=lambda item: canonical_data_key_order(item[1], item[0]),
                    )
                ],
            })
    return _PipelineContractTemplate(
        _manifest=frozen_manifest,
        _module_definitions=frozen_definitions,
        _module_definition_authorities=frozen_authorities,
        _modules=copy.deepcopy(modules),
        _topology=tuple(frozen_manifest["topology"]),
        _signal_plan=copy.deepcopy(frozen_manifest["signalGraph"]),
        _pre_node_ids=pre_node_ids,
        _post_node_ids=post_node_ids,
        _config=copy.deepcopy(frozen_manifest["config"]),
        _direct_plans=tuple(copy.deepcopy(direct_plans)),
        _token=_PIPELINE_TEMPLATE_TOKEN,
    )


def pipeline_contract_template_from_validated_plan(
    manifest,
    module_definitions,
    module_definition_authorities,
    validated_plan,
    *,
    label="Validated Pipeline plan",
):
    """Bind invariant Pipeline facts to a separately validated complete plan."""

    template = pipeline_contract_template_from_verified_authorities(
        manifest,
        module_definitions,
        module_definition_authorities,
    )
    require_validated_pipeline_plan(validated_plan)
    frozen_plan = validated_pipeline_plan_material(validated_plan)
    require_canonical_value_match(
        frozen_plan["topology"],
        list(template._topology),
        label=f"{label} topology",
    )
    require_canonical_value_match(
        frozen_plan["directPlans"],
        list(template._direct_plans),
        label=f"{label} direct plans",
    )
    signal_definition = copy.deepcopy(template._signal_plan)
    frozen_signal_plan = frozen_plan["signalPlan"]
    if set(frozen_signal_plan["nodes"]) != set(signal_definition["nodes"]):
        raise ValueError(
            f"{label} Signal Graph nodes do not "
            "match its frozen manifest."
        )
    require_canonical_value_match(
        frozen_signal_plan["inputs"],
        signal_definition["inputs"],
        label=f"{label} Signal Graph inputs",
    )
    require_canonical_value_match(
        frozen_signal_plan["outputs"],
        signal_definition["outputs"],
        label=f"{label} Signal Graph outputs",
    )
    expected_bindings = {
        node_id: {
            name: copy.deepcopy(template._modules[node_id][name])
            for name in MODULE_INSTANCE_FIELDS
        }
        for node_id in signal_definition["nodes"]
    }
    require_canonical_value_match(
        frozen_signal_plan["bindings"],
        expected_bindings,
        label=f"{label} Signal Graph bindings",
    )
    return template


def seal_pipeline_contract_plan_authority(
    template,
    plan,
    signal_authority,
    direct_invocation_authorities,
):
    """Seal one compiler-verified complete plan; never expose a raw re-binder."""

    return _BoundPipelineContractPlan(
        template,
        plan,
        signal_authority,
        direct_invocation_authorities,
        _token=_BOUND_PIPELINE_PLAN_TOKEN,
    )


def seal_pipeline_contract_plan(
    template,
    plan,
    signal_authority,
    direct_invocation_authorities,
):
    """Strict compiler entry which validates before sealing a Pipeline plan."""

    return seal_pipeline_contract_plan_authority(
        template,
        _validate_and_seal_pipeline_plan(plan),
        signal_authority,
        direct_invocation_authorities,
    )


def require_bound_pipeline_contract_plan(bound_plan):
    if type(bound_plan) is not _BoundPipelineContractPlan:
        raise TypeError("Compiled Pipeline authority requires bound plan.")
    return bound_plan


def bound_pipeline_contract_plan_material(bound_plan):
    require_bound_pipeline_contract_plan(bound_plan)
    if (
        not isinstance(bound_plan._plan_json, str)
        or hashlib.sha256(
            bound_plan._plan_json.encode("utf-8")
        ).hexdigest()
        != bound_plan._plan_digest
    ):
        raise ValueError(
            "Bound Pipeline plan does not match its complete canonical authority."
        )
    return (
        strict_json.loads(bound_plan._plan_json),
        bound_plan._signal_authority,
        dict(bound_plan._direct_invocation_authorities),
    )


def bound_pipeline_contract_plan(bound_plan):
    """Return an isolated plan from one Engine-owned bound Pipeline proof."""

    plan, _signal_authority, _direct_authorities = (
        bound_pipeline_contract_plan_material(bound_plan)
    )
    return plan


def verify_pipeline_definition_authority(definition):
    version_archive.verify_record(definition)
    return _VerifiedPipelineDefinition(
        definition,
        _token=_VERIFIED_PIPELINE_DEFINITION_TOKEN,
    )


def require_verified_pipeline_definition_authority(identity):
    if type(identity) is not _VerifiedPipelineDefinition:
        raise TypeError("Compiled Pipeline authority requires verified identity.")
    return identity


def pipeline_definition_identity(identity):
    require_verified_pipeline_definition_authority(identity)
    return identity._pipeline_id, identity._version, identity._manifest_hash


def bind_compiled_pipeline_authority(identity, contract_template, bound_plan):
    return _CompiledPipelineAuthority(
        identity,
        contract_template,
        bound_plan,
        _token=_COMPILED_PIPELINE_AUTHORITY_TOKEN,
    )


def require_compiled_pipeline_authority(authority):
    if type(authority) is not _CompiledPipelineAuthority:
        raise TypeError("Pipeline Runtime requires compiled Pipeline authority.")
    return authority


def compiled_pipeline_authority_material(authority):
    """Return detached execution material for authority-bound Runtime creation."""

    require_compiled_pipeline_authority(authority)
    pipeline_id, version, manifest_hash = pipeline_definition_identity(
        authority._identity
    )
    return (
        pipeline_id,
        version,
        manifest_hash,
        authority._contract_template,
        strict_json.loads(authority._compiled_plan_json),
        authority._signal_authority,
        dict(authority._direct_invocation_authorities),
    )


def verify_frozen_pipeline_execution_snapshot(
    config,
    pipeline_id,
    pipeline_snapshot,
    *,
    pipeline_version,
):
    """Verify one sealed Pipeline snapshot without rebuilding its compiled plan."""

    require_exact_fields(
        pipeline_snapshot,
        allowed={
            "pipelineId",
            "version",
            "manifestHash",
            "definition",
            "manifest",
            "moduleDefinitions",
        },
        required={
            "pipelineId",
            "version",
            "manifestHash",
            "definition",
            "manifest",
            "moduleDefinitions",
        },
        label="Frozen Pipeline snapshot",
    )
    if (
        pipeline_snapshot["pipelineId"] != pipeline_id
        or pipeline_snapshot["version"] != pipeline_version
    ):
        raise ValueError("Frozen Pipeline snapshot identity does not match its request.")
    definition = copy.deepcopy(pipeline_snapshot["definition"])
    manifest = copy.deepcopy(pipeline_snapshot["manifest"])
    definitions = copy.deepcopy(pipeline_snapshot["moduleDefinitions"])
    expected_hash = pipeline_snapshot["manifestHash"]
    validate_pipeline_manifest(manifest)
    if (
        not isinstance(expected_hash, str)
        or pipeline_manifest_digest(manifest) != expected_hash
    ):
        raise ValueError("Frozen Pipeline manifest hash does not match its snapshot.")
    execution_records.verify_pipeline_record(
        config["releaseRoot"],
        definition,
        pipeline_id=pipeline_id,
        version=pipeline_version,
    )
    definition_authority = _VerifiedPipelineDefinition(
        definition,
        _token=_VERIFIED_PIPELINE_DEFINITION_TOKEN,
    )
    if (
        definition["pipelineId"] != pipeline_id
        or definition["version"] != pipeline_version
        or definition["manifestHash"] != expected_hash
    ):
        raise ValueError(
            "Frozen Pipeline Definition identity does not match its snapshot."
        )
    required_keys = {
        definition_key(module["kind"], module["moduleId"], module["version"])
        for module in manifest["modules"]
    }
    if not isinstance(definitions, dict) or set(definitions) != required_keys:
        raise ValueError(
            "Frozen Pipeline Module definitions must exactly match its manifest."
        )
    modules = {module["key"]: module for module in manifest["modules"]}
    definition_authorities = {}
    for module in manifest["modules"]:
        key = definition_key(
            module["kind"], module["moduleId"], module["version"]
        )
        module_definition = definitions[key]
        if key not in definition_authorities:
            definition_authorities[key] = (
                module_definition_authority.verify_managed_module_definition_authority(
                    config["releaseRoot"],
                    module_definition
                )
            )
            validate_module_definition(module_definition)
        validate_config(
            module["config"],
            module_definition["configSchema"],
            path=f"Pipeline Module '{module['key']}'.config",
        )
        validate_instance_wiring(module, module_definition)
    return (
        definition_authority,
        manifest,
        definitions,
        definition_authorities,
        expected_hash,
    )


__all__ = (
    "bind_compiled_pipeline_authority",
    "bound_pipeline_contract_plan",
    "bound_pipeline_contract_plan_material",
    "compiled_pipeline_authority_material",
    "pipeline_contract_template_from_validated_plan",
    "pipeline_contract_template_from_verified_authorities",
    "pipeline_contract_template_material",
    "pipeline_definition_identity",
    "require_bound_pipeline_contract_plan",
    "require_compiled_pipeline_authority",
    "require_pipeline_contract_template",
    "require_validated_pipeline_plan",
    "require_verified_pipeline_definition_authority",
    "validated_pipeline_plan_digest",
    "validated_pipeline_plan_material",
    "verify_frozen_pipeline_execution_snapshot",
    "verify_pipeline_definition_authority",
)
