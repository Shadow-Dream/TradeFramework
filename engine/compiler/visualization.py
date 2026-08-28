"""Pure compiler for Result Visualization data-read contracts."""

from __future__ import annotations

import copy
import re

from engine.compiler import result_projection as result_projection_compiler
from engine.contracts import protocol as protocol_contracts
from engine.contracts import visualization as visualization_contracts
from engine.contracts.contract_expansion import (
    expand_contracts,
    expanded_contract_path_required,
    resolve_contract_path,
)
from engine.contracts.data_compatibility import schemas_compatible
from engine.contracts.data_model import (
    normalize_data_key_schema,
    normalize_schema,
    schema_label,
    schema_types,
)
from engine.contracts.data_path import split_data_path
from engine.contracts.exact_fields import require_exact_fields
from engine.contracts.json_schema import normalize_config_schema, validate_config


_VISUALIZER_DEFINITION_FIELDS = frozenset({
    "id",
    "label",
    "inputPorts",
    "paramsSchema",
    "params",
    "renderer",
    "capabilities",
    protocol_contracts.PROTOCOL_ID_FIELD,
})
_VISUALIZER_REQUIRED_DEFINITION_FIELDS = (
    _VISUALIZER_DEFINITION_FIELDS - {protocol_contracts.PROTOCOL_ID_FIELD}
)
_VISUALIZER_RENDERER_FIELDS = frozenset({"id", "apiVersion"})
_VISUALIZER_CAPABILITY_FIELDS = frozenset({
    "provides",
    "requires",
    "interactions",
})
_VISUALIZER_PROVIDED_CAPABILITY_FIELDS = frozenset({
    "name",
    "kind",
    "attributes",
})
_VISUALIZER_REQUIRED_CAPABILITY_FIELDS = frozenset({
    "name",
    "kind",
    "bindingParam",
    "matches",
})
_VISUALIZER_PARAM_UI_FIELDS = frozenset({
    "name",
    "label",
    "type",
    "required",
    "default",
    "min",
    "max",
    "options",
})
_CAPABILITY_IDENTIFIER_PATTERN = re.compile(
    r"^[A-Za-z][A-Za-z0-9]*(?:[._-][A-Za-z0-9]+)*$"
)
_UNSAFE_CAPABILITY_IDENTIFIERS = frozenset({
    "__proto__",
    "constructor",
    "prototype",
})


def _require_capability_identifier(value, *, label):
    if type(value) is not str or not value:
        raise ValueError(
            f"{label} must be a non-empty ASCII capability identifier."
        )
    if value in _UNSAFE_CAPABILITY_IDENTIFIERS:
        raise ValueError(
            f"{label} uses unsafe capability identifier '{value}'."
        )
    if _CAPABILITY_IDENTIFIER_PATTERN.fullmatch(value) is None:
        raise ValueError(
            f"{label} must match ASCII capability identifier grammar "
            "'^[A-Za-z][A-Za-z0-9]*(?:[._-][A-Za-z0-9]+)*$'."
        )
    return value


def _require_normalized_text_list(value, *, label):
    if type(value) is not list:
        raise ValueError(f"{label} must be an array of capability identifiers.")
    for index, item in enumerate(value):
        _require_capability_identifier(item, label=f"{label}[{index}]")
    if value != sorted(set(value)):
        raise ValueError(
            f"{label} must be a sorted array of unique capability identifiers."
        )
    return tuple(value)


def _require_string_mapping(value, *, label):
    if type(value) is not dict:
        raise ValueError(
            f"{label} must be an object mapping capability identifiers to "
            "non-empty parameter names."
        )
    for name, param_name in value.items():
        _require_capability_identifier(name, label=f"{label} key")
        _require_capability_identifier(
            param_name,
            label=f"{label}['{name}'] parameter name",
        )
    return dict(value)


def _require_capability_descriptors(value, *, label, provided):
    if type(value) is not list:
        raise ValueError(f"{label} must be an array of capability descriptors.")
    descriptors = []
    names = []
    for index, descriptor in enumerate(value):
        fields = (
            _VISUALIZER_PROVIDED_CAPABILITY_FIELDS
            if provided
            else _VISUALIZER_REQUIRED_CAPABILITY_FIELDS
        )
        require_exact_fields(
            descriptor,
            allowed=fields,
            required=fields,
            label=f"{label}[{index}]",
        )
        name = _require_capability_identifier(
            descriptor["name"],
            label=f"{label}[{index}].name",
        )
        kind = _require_capability_identifier(
            descriptor["kind"],
            label=f"{label}[{index}].kind",
        )
        if provided:
            attributes = _require_string_mapping(
                descriptor["attributes"],
                label=f"{label}[{index}].attributes",
            )
            normalized = {
                "name": name,
                "kind": kind,
                "attributes": attributes,
            }
        else:
            binding_param = _require_capability_identifier(
                descriptor["bindingParam"],
                label=f"{label}[{index}].bindingParam",
            )
            matches = _require_string_mapping(
                descriptor["matches"],
                label=f"{label}[{index}].matches",
            )
            normalized = {
                "name": name,
                "kind": kind,
                "bindingParam": binding_param,
                "matches": matches,
            }
        descriptors.append(normalized)
        names.append(name)
    if names != sorted(set(names)):
        raise ValueError(
            f"{label} must be sorted by unique non-empty capability name."
        )
    return tuple(descriptors)


def _require_visualizer_definition(repository_key, definition):
    """Validate one detached Visualizer definition before it is authoritative."""

    require_exact_fields(
        definition,
        allowed=_VISUALIZER_DEFINITION_FIELDS,
        required=_VISUALIZER_REQUIRED_DEFINITION_FIELDS,
        label=f"Visualizer definition '{repository_key}'",
    )
    if (
        type(repository_key) is not str
        or not repository_key
        or definition["id"] != repository_key
    ):
        raise ValueError(
            f"Visualizer definition '{repository_key}' identity does not match its repository key."
        )
    if type(definition["label"]) is not str or not definition["label"].strip():
        raise ValueError(f"Visualizer definition '{repository_key}' label is required.")
    if protocol_contracts.PROTOCOL_ID_FIELD in definition:
        protocol_contracts.normalize_protocol_id(
            definition[protocol_contracts.PROTOCOL_ID_FIELD],
            label=(
                f"Visualizer definition '{repository_key}' "
                f"{protocol_contracts.PROTOCOL_ID_FIELD}"
            ),
        )

    renderer = definition["renderer"]
    require_exact_fields(
        renderer,
        allowed=_VISUALIZER_RENDERER_FIELDS,
        required=_VISUALIZER_RENDERER_FIELDS,
        label=f"Visualizer definition '{repository_key}' renderer",
    )
    if renderer["id"] != repository_key:
        raise ValueError(
            f"Visualizer definition '{repository_key}' renderer identity is invalid."
        )
    if type(renderer["apiVersion"]) is not int or renderer["apiVersion"] != 1:
        raise ValueError(
            f"Visualizer definition '{repository_key}' renderer apiVersion 1 is required."
        )

    capabilities = definition["capabilities"]
    require_exact_fields(
        capabilities,
        allowed=_VISUALIZER_CAPABILITY_FIELDS,
        required=_VISUALIZER_CAPABILITY_FIELDS,
        label=f"Visualizer definition '{repository_key}' capabilities",
    )
    provides = _require_capability_descriptors(
        capabilities["provides"],
        label=f"Visualizer definition '{repository_key}' capabilities.provides",
        provided=True,
    )
    requires = _require_capability_descriptors(
        capabilities["requires"],
        label=f"Visualizer definition '{repository_key}' capabilities.requires",
        provided=False,
    )
    _require_normalized_text_list(
        capabilities["interactions"],
        label=f"Visualizer definition '{repository_key}' capabilities.interactions",
    )

    input_ports = definition["inputPorts"]
    if type(input_ports) is not dict:
        raise ValueError(
            f"Visualizer definition '{repository_key}' inputPorts must be an object."
        )
    for port_name, port in input_ports.items():
        if type(port_name) is not str or not port_name:
            raise ValueError(
                f"Visualizer definition '{repository_key}' contains an invalid input port."
            )
        require_exact_fields(
            port,
            allowed={"schema"},
            required={"schema"},
            label=f"Visualizer definition '{repository_key}' inputPorts.{port_name}",
        )
        normalize_data_key_schema(
            port["schema"],
            path=f"Visualizer {repository_key}.{port_name}",
        )

    params_schema = normalize_config_schema(definition["paramsSchema"])
    if (
        params_schema.get("type") != "object"
        or type(params_schema.get("properties")) is not dict
        or type(params_schema.get("required")) is not list
        or params_schema.get("additionalProperties") is not False
    ):
        raise ValueError(
            f"Visualizer definition '{repository_key}' paramsSchema must be an exact object contract."
        )
    param_names = set(params_schema["properties"])
    required_params = set(params_schema["required"])
    if not set(input_ports).issubset(param_names & required_params):
        raise ValueError(
            f"Visualizer definition '{repository_key}' must require every input port binding."
        )
    capability_names = [
        descriptor["name"]
        for descriptor in (*provides, *requires)
    ]
    if len(capability_names) != len(set(capability_names)):
        raise ValueError(
            f"Visualizer definition '{repository_key}' capability names must be unique."
        )
    mapped_params = [
        (f"capabilities.provides.{descriptor['name']}.attributes.{attribute}", param)
        for descriptor in provides
        for attribute, param in descriptor["attributes"].items()
    ]
    mapped_params.extend(
        (
            f"capabilities.requires.{descriptor['name']}.bindingParam",
            descriptor["bindingParam"],
        )
        for descriptor in requires
    )
    mapped_params.extend(
        (f"capabilities.requires.{descriptor['name']}.matches.{attribute}", param)
        for descriptor in requires
        for attribute, param in descriptor["matches"].items()
    )
    for mapping_path, param_name in mapped_params:
        if param_name not in param_names or param_name not in required_params:
            raise ValueError(
                f"Visualizer definition '{repository_key}' {mapping_path} must map "
                f"to a required paramsSchema property; found '{param_name}'."
            )
    for descriptor in requires:
        binding_param = descriptor["bindingParam"]
        binding_schema = params_schema["properties"][binding_param]
        if type(binding_schema) is not dict or binding_schema.get("type") != "string":
            raise ValueError(
                f"Visualizer definition '{repository_key}' capability "
                f"'{descriptor['name']}' binding parameter '{binding_param}' must be a string."
            )

    ui_params = definition["params"]
    if type(ui_params) is not list:
        raise ValueError(
            f"Visualizer definition '{repository_key}' params must be an array."
        )
    ui_names = []
    for index, field in enumerate(ui_params):
        require_exact_fields(
            field,
            allowed=_VISUALIZER_PARAM_UI_FIELDS,
            required={"name", "label", "type", "required"},
            label=f"Visualizer definition '{repository_key}' params[{index}]",
        )
        name = field["name"]
        if type(name) is not str or name not in param_names:
            raise ValueError(
                f"Visualizer definition '{repository_key}' contains an invalid UI param."
            )
        if type(field["required"]) is not bool or field["required"] != (
            name in required_params
        ):
            raise ValueError(
                f"Visualizer definition '{repository_key}' UI param '{name}' requiredness is invalid."
            )
        ui_names.append(name)
    if ui_names != list(params_schema["properties"]):
        raise ValueError(
            f"Visualizer definition '{repository_key}' UI params do not match paramsSchema."
        )
    return {
        "provides": provides,
        "requires": requires,
    }


def _visualizer_read_contract(schema):
    """Relax object width for the fields a read-only Visualizer consumes."""

    result = normalize_schema(schema)
    if result is False:
        return False
    result = copy.deepcopy(result)
    for keyword in ("allOf", "anyOf", "oneOf"):
        if keyword in result:
            result[keyword] = [
                _visualizer_read_contract(branch)
                for branch in result[keyword]
            ]
    if "properties" in result:
        result["properties"] = {
            name: _visualizer_read_contract(child)
            for name, child in result["properties"].items()
        }
    if "object" in schema_types(result):
        result["additionalProperties"] = True
    return result


def _result_declarations(contracts, required_roots):
    return {
        path: {
            "schema": schema,
            "required": expanded_contract_path_required(
                contracts,
                path,
                required_roots=required_roots,
            ),
        }
        for path, schema in contracts.items()
    }


def _require_unambiguous_temporary_scope(root_modules, pane):
    """Reject identities that collide in one root + Pane execution scope."""

    root_ids = {
        module["instanceId"]
        for module in root_modules
        if isinstance(module.get("instanceId"), str)
    }
    pane_ids = {
        module["instanceId"]
        for module in pane["temporaryModules"]
        if isinstance(module.get("instanceId"), str)
    }
    duplicate_ids = sorted(root_ids & pane_ids)
    if duplicate_ids:
        raise ValueError(
            f"Visualization Pane '{pane['id']}' temporary module instanceId "
            f"'{duplicate_ids[0]}' conflicts with root temporaryModules."
        )

    root_producers = {}
    for module in root_modules:
        if not isinstance(module.get("outputs"), dict):
            continue
        for data_key in module["outputs"].values():
            if isinstance(data_key, str):
                root_producers[data_key] = module.get("instanceId", "")
    pane_producers = {}
    for module in pane["temporaryModules"]:
        if not isinstance(module.get("outputs"), dict):
            continue
        for data_key in module["outputs"].values():
            if isinstance(data_key, str):
                pane_producers[data_key] = module.get("instanceId", "")
    duplicate_outputs = sorted(set(root_producers) & set(pane_producers))
    if duplicate_outputs:
        data_key = duplicate_outputs[0]
        raise ValueError(
            f"Visualization Pane '{pane['id']}' contains multiple temporary "
            f"module producers for DataKey '{data_key}': "
            f"'{root_producers[data_key]}' and '{pane_producers[data_key]}'."
        )


def compile_visualization_contracts(
    data_keys,
    spec,
    module_definitions,
    visualizer_definitions,
):
    """Compile and prove a Visualization against verified Result declarations.

    The compiler accepts only detached contract material.  Repository/config
    lookup belongs to the service layer so this function is deterministic and
    cannot write durable state.
    """

    if not isinstance(data_keys, dict):
        raise ValueError("Result dataKeys must be an object.")
    if not isinstance(visualizer_definitions, dict):
        raise ValueError("Visualizer definitions must be an object.")
    definition_capabilities = {}

    def require_definition(identifier):
        definition = visualizer_definitions.get(identifier)
        if definition is None:
            raise ValueError(f"Unknown Visualizer contract: '{identifier}'.")
        if identifier not in definition_capabilities:
            definition_capabilities[identifier] = _require_visualizer_definition(
                identifier,
                definition,
            )
        return definition, definition_capabilities[identifier]

    visualization_contracts.require_spec(spec)
    base_contracts = expand_contracts({
        data_key: normalize_data_key_schema(
            declaration["schema"],
            path=data_key,
        )
        for data_key, declaration in data_keys.items()
    })
    base_required_roots = frozenset(
        data_key
        for data_key, declaration in data_keys.items()
        if len(split_data_path(data_key)) == 1 and declaration["required"]
    )

    _, root_contracts, root_required_roots = (
        result_projection_compiler.compile_temporary_module_plan(
            {
                "dataKeys": _result_declarations(
                    base_contracts,
                    base_required_roots,
                )
            },
            spec.get("temporaryModules") or [],
            module_definitions,
        )
    )
    missing_contract = object()
    for pane in spec["panes"]:
        _require_unambiguous_temporary_scope(
            spec.get("temporaryModules") or [],
            pane,
        )
        _, contracts, _required_roots = (
            result_projection_compiler.compile_temporary_module_plan(
                {
                    "dataKeys": _result_declarations(
                        root_contracts,
                        root_required_roots,
                    )
                },
                pane["temporaryModules"],
                module_definitions,
            )
        )
        compiled_visualizers = {}
        time_domain_ids = set()
        for visualizer in pane["visualizers"]:
            callback = visualizer["callback"]
            definition, capabilities = require_definition(callback)
            input_contracts = {
                name: normalize_data_key_schema(
                    port.get("schema"),
                    path=f"Visualizer {callback}.{name}",
                )
                for name, port in definition["inputPorts"].items()
            }
            params = visualizer["params"]
            validate_config(
                params,
                definition["paramsSchema"],
                path=f"visualizer.{visualizer['id']}.params",
            )
            if visualizer.get("visible", True):
                for provided in capabilities["provides"]:
                    domain_param = provided["attributes"].get("time-domain")
                    if domain_param is None:
                        continue
                    domain_value = params[domain_param]
                    if type(domain_value) is not str or not domain_value.strip():
                        raise ValueError(
                            f"Visualizer '{visualizer['id']}' capability "
                            f"'{provided['name']}' time-domain attribute must resolve "
                            "to a non-empty string."
                        )
                    time_domain_ids.add(domain_value)
            for port_name, required_schema in input_contracts.items():
                data_key = params.get(port_name)
                if not data_key:
                    raise ValueError(
                        f"Visualizer '{callback}' requires input '{port_name}'."
                    )
                provided_schema = resolve_contract_path(
                    contracts,
                    data_key,
                    missing_contract,
                )
                if provided_schema is missing_contract:
                    raise ValueError(
                        f"Visualizer '{callback}' references unknown DataKey "
                        f"'{data_key}'."
                    )
                if not schemas_compatible(
                    provided_schema,
                    _visualizer_read_contract(required_schema),
                ):
                    raise ValueError(
                        f"Visualizer '{callback}.{port_name}' schema mismatch: "
                        f"DataKey '{data_key}' is {schema_label(provided_schema)}, "
                        f"requires {schema_label(required_schema)}."
                    )
            compiled_visualizers[visualizer["id"]] = {
                "instance": visualizer,
                "definition": definition,
                "capabilities": capabilities,
                "params": params,
            }

        for visualizer_id, compiled in compiled_visualizers.items():
            for required in compiled["capabilities"]["requires"]:
                target_id = compiled["params"][required["bindingParam"]]
                if target_id == visualizer_id:
                    raise ValueError(
                        f"Visualizer '{visualizer_id}' capability '{required['name']}' "
                        "may not reference itself."
                    )
                target = compiled_visualizers.get(target_id)
                if target is None:
                    raise ValueError(
                        f"Visualizer '{visualizer_id}' capability '{required['name']}' "
                        f"references unknown target Visualizer '{target_id}'."
                    )
                if target["instance"].get("visible", True) is False:
                    raise ValueError(
                        f"Visualizer '{visualizer_id}' capability '{required['name']}' "
                        f"target '{target_id}' must be visible."
                    )
                providers = [
                    provided
                    for provided in target["capabilities"]["provides"]
                    if provided["kind"] == required["kind"]
                ]
                if not providers:
                    raise ValueError(
                        f"Visualizer '{visualizer_id}' target '{target_id}' does not "
                        f"provide required capability kind '{required['kind']}'."
                    )
                if len(providers) != 1:
                    raise ValueError(
                        f"Visualizer '{visualizer_id}' target '{target_id}' provides "
                        f"ambiguous capability kind '{required['kind']}'."
                    )
                provider = providers[0]
                for attribute, consumer_param in required["matches"].items():
                    provider_param = provider["attributes"].get(attribute)
                    if provider_param is None:
                        raise ValueError(
                            f"Visualizer '{visualizer_id}' target '{target_id}' capability "
                            f"kind '{required['kind']}' does not expose required attribute "
                            f"'{attribute}'."
                        )
                    if (
                        compiled["params"][consumer_param]
                        != target["params"][provider_param]
                    ):
                        raise ValueError(
                            f"Visualizer '{visualizer_id}' capability '{required['name']}' "
                            f"attribute '{attribute}' must match target '{target_id}'."
                        )
        if len(time_domain_ids) > 1:
            raise ValueError(
                f"Visualization Pane '{pane['id']}' visible provided resources must "
                "share one explicit time-domain attribute; found: "
                + ", ".join(sorted(time_domain_ids))
                + "."
            )
    return root_contracts


__all__ = ("compile_visualization_contracts",)
