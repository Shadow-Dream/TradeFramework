"""Pure shape and ordered-boundary contracts for Module Graph plans."""

from __future__ import annotations

import copy
from collections.abc import Mapping

from engine.contracts.contract_expansion import (
    expand_contracts,
    expanded_contract_root_paths,
)
from engine.contracts.data_model import normalize_data_key_schema
from engine.contracts.data_path import split_data_path
from engine.contracts.module import (
    GRAPH_BOUNDARY_FIELDS,
    GRAPH_INPUT_BOUNDARY_FIELDS,
    GRAPH_FIELDS,
    MODULE_INSTANCE_FIELDS,
    require_exact_fields,
)


COMPILED_GRAPH_FIELDS = frozenset({
    "nodes", "topology", "inputs", "outputs", "bindings", "edges",
    "inputContracts", "inputRequiredRoots", "outputContracts",
    "requiredOutputs",
})
COMPILED_GRAPH_OPTIONAL_FIELDS = frozenset({"inputSources"})
COMPILED_GRAPH_BINDING_FIELDS = MODULE_INSTANCE_FIELDS


__all__ = (
    "COMPILED_GRAPH_BINDING_FIELDS",
    "COMPILED_GRAPH_FIELDS",
    "COMPILED_GRAPH_OPTIONAL_FIELDS",
    "compiled_graph_definition",
    "compiled_graph_output_plan",
    "compiled_graph_output_writes",
    "normalize_graph",
    "validate_compiled_graph",
)


def _compiled_graph_output_edges(plan, *, label="Compiled Graph"):
    """Return each public output edge once, preserving its frozen array order."""
    outputs = plan["outputs"]
    ordered = []
    seen = set()
    for edge in plan["edges"]:
        boundary_id = edge["to"]["node"]
        if boundary_id not in outputs:
            continue
        if boundary_id in seen:
            raise ValueError(
                f"{label}.edges contains multiple writes for output boundary "
                f"'{boundary_id}'."
            )
        boundary = outputs[boundary_id]
        if edge["to"]["port"] != "value" or edge["wire"] != boundary["wire"]:
            raise ValueError(
                f"{label}.edges output boundary '{boundary_id}' does not match "
                "its declared wire."
            )
        ordered.append((boundary_id, boundary, edge))
        seen.add(boundary_id)
    missing = sorted(set(outputs) - seen)
    if missing:
        raise ValueError(
            f"{label}.edges is missing output boundary write(s): "
            + ", ".join(missing)
        )
    return tuple(ordered)


def compiled_graph_output_writes(plan):
    """Return ordered boundary writes with their producer schema and presence."""
    required = set(plan["requiredOutputs"])
    return tuple(
        (
            boundary_id,
            boundary["dataKey"],
            copy.deepcopy(edge["from"]["schema"]),
            boundary_id in required,
        )
        for boundary_id, boundary, edge in _compiled_graph_output_edges(plan)
        if edge["from"]["schema"] is not False
    )


def compiled_graph_output_plan(plan):
    """Return the frozen executable output-boundary plan in edge-array order."""
    required = set(plan["requiredOutputs"])
    return tuple(
        (
            boundary_id,
            boundary["dataKey"],
            boundary["wire"],
            boundary_id in required,
        )
        for boundary_id, boundary, _edge in _compiled_graph_output_edges(plan)
    )


def compiled_graph_definition(plan, *, label="Compiled Graph"):
    """Recover a raw Graph while preserving the frozen output-edge write order."""
    ordered_outputs = _compiled_graph_output_edges(plan, label=label)
    return {
        "nodes": copy.deepcopy(plan["nodes"]),
        "inputs": copy.deepcopy(plan["inputs"]),
        "outputs": {
            boundary_id: copy.deepcopy(boundary)
            for boundary_id, boundary, _edge in ordered_outputs
        },
    }


def normalize_graph(graph, *, label="Graph", input_sources=None):
    if not isinstance(graph, Mapping):
        raise ValueError(f"{label} must be an object.")
    require_exact_fields(
        dict(graph),
        allowed=GRAPH_FIELDS,
        required=GRAPH_FIELDS,
        label=label,
    )
    nodes = graph["nodes"]
    inputs = graph["inputs"]
    outputs = graph["outputs"]
    if not isinstance(nodes, list) or not all(
        isinstance(item, str) and item for item in nodes
    ):
        raise ValueError(f"{label}.nodes must be an array of non-empty instance IDs.")
    if input_sources is None:
        allowed_input_sources = frozenset()
    else:
        source_names = (
            input_sources.keys()
            if isinstance(input_sources, Mapping)
            else input_sources
        )
        if (
            not isinstance(input_sources, (Mapping, set, frozenset, list, tuple))
            or any(not isinstance(source, str) or not source for source in source_names)
        ):
            raise ValueError(f"{label} input source names must be non-empty strings.")
        allowed_input_sources = frozenset(source_names)
    normalized_boundaries = {}
    boundary_ids = set()
    for direction, boundaries in (("inputs", inputs), ("outputs", outputs)):
        if not isinstance(boundaries, Mapping):
            raise ValueError(
                f"{label}.{direction} must be an object keyed by boundary node ID."
            )
        normalized = {}
        for boundary_id, boundary in boundaries.items():
            if not isinstance(boundary_id, str) or not boundary_id:
                raise ValueError(
                    f"{label}.{direction} contains an invalid boundary node ID."
                )
            if boundary_id in boundary_ids or boundary_id in nodes:
                raise ValueError(f"{label} contains duplicate node ID '{boundary_id}'.")
            if not isinstance(boundary, Mapping):
                raise ValueError(f"{label}.{direction}.{boundary_id} must be an object.")
            allowed_fields = (
                GRAPH_INPUT_BOUNDARY_FIELDS
                if direction == "inputs"
                else GRAPH_BOUNDARY_FIELDS
            )
            require_exact_fields(
                dict(boundary),
                allowed=allowed_fields,
                required=GRAPH_BOUNDARY_FIELDS,
                label=f"{label}.{direction}.{boundary_id}",
            )
            data_key = boundary["dataKey"]
            wire = boundary["wire"]
            if (
                not isinstance(data_key, str)
                or not data_key.strip()
                or not isinstance(wire, str)
                or not wire.strip()
            ):
                raise ValueError(
                    f"{label}.{direction}.{boundary_id} requires string dataKey and wire."
                )
            try:
                normalized_data_key = ".".join(split_data_path(data_key))
            except ValueError as exc:
                raise ValueError(
                    f"{label}.{direction}.{boundary_id} has an invalid DataKey path."
                ) from exc
            if normalized_data_key != data_key:
                raise ValueError(
                    f"{label}.{direction}.{boundary_id} DataKey path is not normalized."
                )
            normalized_boundary = {"dataKey": data_key, "wire": wire}
            if "source" in boundary:
                source = boundary["source"]
                if not isinstance(source, str) or not source:
                    raise ValueError(
                        f"{label}.{direction}.{boundary_id} source must be a non-empty string."
                    )
                if source not in allowed_input_sources:
                    raise ValueError(
                        f"{label}.{direction}.{boundary_id} references unknown input source "
                        f"'{source}'."
                    )
                normalized_boundary["source"] = source
            normalized[boundary_id] = normalized_boundary
            boundary_ids.add(boundary_id)
        normalized_boundaries[direction] = normalized
    return {"nodes": list(nodes), **normalized_boundaries}


def validate_compiled_graph(plan, *, label="Compiled Graph"):
    """Validate the pure shape and explicit order of one frozen Graph plan."""
    if not isinstance(plan, Mapping):
        raise ValueError(f"{label} must be an object.")
    plan = dict(plan)
    require_exact_fields(
        plan,
        allowed=COMPILED_GRAPH_FIELDS | COMPILED_GRAPH_OPTIONAL_FIELDS,
        required=COMPILED_GRAPH_FIELDS,
        label=label,
    )
    nodes = plan["nodes"]
    topology = plan["topology"]
    if (
        not isinstance(nodes, list)
        or not all(isinstance(node, str) and node for node in nodes)
        or len(nodes) != len(set(nodes))
        or topology != nodes
    ):
        raise ValueError(
            f"{label} nodes and topology must be the same unique ordered IDs."
        )
    input_sources = plan.get("inputSources", {})
    if not isinstance(input_sources, Mapping):
        raise ValueError(f"{label}.inputSources must be an object.")
    normalized_source_contracts = {}
    normalized_source_required_roots = {}
    for source, state in input_sources.items():
        if not isinstance(source, str) or not source:
            raise ValueError(f"{label}.inputSources contains an invalid source name.")
        require_exact_fields(
            state,
            allowed={"contracts", "requiredRoots"},
            required={"contracts", "requiredRoots"},
            label=f"{label}.inputSources.{source}",
        )
        contracts = state["contracts"]
        if not isinstance(contracts, Mapping):
            raise ValueError(
                f"{label}.inputSources.{source}.contracts must be an object."
            )
        normalized_contracts = expand_contracts({
            data_key: normalize_data_key_schema(
                schema,
                path=f"{label}.inputSources.{source}.contracts.{data_key}",
            )
            for data_key, schema in contracts.items()
        })
        if normalized_contracts != contracts:
            raise ValueError(
                f"{label}.inputSources.{source}.contracts are not normalized."
            )
        required = state["requiredRoots"]
        if (
            not isinstance(required, list)
            or any(not isinstance(root, str) or not root for root in required)
            or required != sorted(set(required))
            or not set(required) <= set(
                expanded_contract_root_paths(normalized_contracts)
            )
        ):
            raise ValueError(
                f"{label}.inputSources.{source}.requiredRoots must be sorted declared root DataKeys."
            )
        normalized_source_contracts[source] = normalized_contracts
        normalized_source_required_roots[source] = required
    normalized_graph = normalize_graph(
        {"nodes": nodes, "inputs": plan["inputs"], "outputs": plan["outputs"]},
        label=label,
        input_sources=normalized_source_contracts,
    )
    if normalized_graph != {
        "nodes": nodes,
        "inputs": plan["inputs"],
        "outputs": plan["outputs"],
    }:
        raise ValueError(f"{label} boundaries are not normalized.")
    bindings = plan["bindings"]
    if not isinstance(bindings, Mapping) or set(bindings) != set(topology):
        raise ValueError(f"{label} bindings must exactly match its topology.")
    instances = {}
    for node_id in topology:
        binding = bindings[node_id]
        require_exact_fields(
            binding,
            allowed=COMPILED_GRAPH_BINDING_FIELDS,
            required=COMPILED_GRAPH_BINDING_FIELDS,
            label=f"{label} binding '{node_id}'",
        )
        instances[node_id] = {
            field: copy.deepcopy(binding[field])
            for field in MODULE_INSTANCE_FIELDS
        }
    edges = plan["edges"]
    if not isinstance(edges, list):
        raise ValueError(f"{label}.edges must be an array.")
    for index, edge in enumerate(edges):
        require_exact_fields(
            edge,
            allowed={"wire", "from", "to"},
            required={"wire", "from", "to"},
            label=f"{label}.edges[{index}]",
        )
        if not isinstance(edge["wire"], str) or not edge["wire"]:
            raise ValueError(f"{label}.edges[{index}].wire is invalid.")
        for endpoint in ("from", "to"):
            require_exact_fields(
                edge[endpoint],
                allowed={"node", "port", "schema"},
                required={"node", "port", "schema"},
                label=f"{label}.edges[{index}].{endpoint}",
            )
            if (
                not isinstance(edge[endpoint]["node"], str)
                or not edge[endpoint]["node"]
            ):
                raise ValueError(
                    f"{label}.edges[{index}].{endpoint}.node is invalid."
                )
            if (
                not isinstance(edge[endpoint]["port"], str)
                or not edge[endpoint]["port"]
            ):
                raise ValueError(
                    f"{label}.edges[{index}].{endpoint}.port is invalid."
                )
            if normalize_data_key_schema(
                edge[endpoint]["schema"],
                path=f"{label}.edges[{index}].{endpoint}.schema",
            ) != edge[endpoint]["schema"]:
                raise ValueError(
                    f"{label}.edges[{index}].{endpoint}.schema is not normalized."
                )
    output_edges = _compiled_graph_output_edges(plan, label=label)
    required_roots = plan["inputRequiredRoots"]
    if (
        not isinstance(required_roots, list)
        or any(not isinstance(root, str) or not root for root in required_roots)
        or required_roots != sorted(set(required_roots))
        or not set(required_roots) <= set(
            expanded_contract_root_paths(plan["inputContracts"])
        )
    ):
        raise ValueError(
            f"{label}.inputRequiredRoots must be sorted declared root DataKeys."
        )
    required_outputs = plan["requiredOutputs"]
    ordered_output_ids = [
        boundary_id for boundary_id, _boundary, _edge in output_edges
    ]
    if (
        not isinstance(required_outputs, list)
        or any(not isinstance(item, str) for item in required_outputs)
        or required_outputs != [
            boundary_id
            for boundary_id in ordered_output_ids
            if boundary_id in set(required_outputs)
        ]
    ):
        raise ValueError(
            f"{label}.requiredOutputs must be ordered Graph output boundary IDs."
        )
    for field in ("inputContracts", "outputContracts"):
        contracts = plan[field]
        if not isinstance(contracts, Mapping):
            raise ValueError(f"{label}.{field} must be an object.")
        for data_key, schema in contracts.items():
            if not isinstance(data_key, str) or not data_key:
                raise ValueError(f"{label}.{field} contains an invalid DataKey.")
            if normalize_data_key_schema(schema, path=data_key) != schema:
                raise ValueError(f"{label}.{field}.{data_key} is not normalized.")
    return plan
