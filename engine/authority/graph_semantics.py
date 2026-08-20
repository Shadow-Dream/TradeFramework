"""In-place semantic proof for compiled Module Graph plans.

This module validates relations carried by a plan.  It deliberately never
constructs a second compiled plan for equality comparison.
"""

from __future__ import annotations

import copy

from engine.authority import module_definition as _module_definition_authority
from engine.authority import module_invocation as _module_invocation_authority
from engine.contracts import strict_json
from engine.contracts.contract_expansion import (
    expanded_contract_path_required,
    expand_contracts,
    resolve_expanded_contract_path,
)
from engine.contracts.contract_reducer import ExpandedContractWriteReducer
from engine.contracts.data_compatibility import schemas_compatible
from engine.contracts.data_model import port_schema
from engine.contracts.data_path import canonical_data_key_order
from engine.contracts.module import definition_key, normalize_ports


__all__ = ()


def _canonical_json(value):
    return strict_json.dumps(value, sort_keys=True, separators=(",", ":"))


def _require_match(actual, expected, *, label):
    if _canonical_json(actual) != _canonical_json(expected):
        raise ValueError(f"{label} does not match its verified authority.")


def _definition_material(definition_authorities):
    return {
        key: _module_definition_authority.verified_module_definition_material(
            authority
        )
        for key, authority in definition_authorities.items()
    }


def _require_invocations(plan, definition_authorities, invocation_authorities):
    if set(invocation_authorities) != set(plan["topology"]):
        raise TypeError(
            "Compiled Graph Module invocations must exactly match its topology."
        )
    for node_id in plan["topology"]:
        authority = invocation_authorities[node_id]
        _module_invocation_authority.require_module_invocation_authority(authority)
        binding, definition_authority = (
            _module_invocation_authority.module_invocation_material(authority)
        )
        expected = plan["bindings"][node_id]
        key = definition_key(
            expected["kind"], expected["moduleId"], expected["version"]
        )
        if binding != expected or definition_authority is not definition_authorities[key]:
            raise TypeError(
                "Compiled Graph Module invocation does not match its plan."
            )


def verify_compiled_graph_semantics(
    plan,
    definition_authorities,
    *,
    definition_node_order,
    label,
):
    """Prove bindings, edges, topology, and output algebra in place."""

    definitions = _definition_material(definition_authorities)
    invocations = _module_invocation_authority.bind_module_invocation_authorities(
        plan["topology"],
        plan["bindings"],
        definition_authorities,
    )
    ports_by_node = {}
    for node_id in plan["topology"]:
        binding = plan["bindings"][node_id]
        key = definition_key(
            binding["kind"], binding["moduleId"], binding["version"]
        )
        ports_by_node[node_id] = normalize_ports(
            definitions[key]["ports"],
            label=f"{label} binding '{node_id}' ports",
        )

    expanded_inputs = expand_contracts(plan["inputContracts"])
    _require_match(
        plan["inputContracts"],
        expanded_inputs,
        label=f"{label} inputContracts",
    )
    input_states = {
        None: (expanded_inputs, frozenset(plan["inputRequiredRoots"]))
    }
    for source, state in plan.get("inputSources", {}).items():
        input_states[source] = (
            state["contracts"],
            frozenset(state["requiredRoots"]),
        )

    producers = {}
    producer_schemas = {}
    producer_presence = {}
    unknown_source_wires = set()
    missing_contract = object()
    for boundary_id, boundary in plan["inputs"].items():
        wire = boundary["wire"]
        if wire in producers:
            raise ValueError(f"{label} wire '{wire}' has multiple producers.")
        contracts, required_roots = input_states[boundary.get("source")]
        schema = resolve_expanded_contract_path(
            contracts,
            boundary["dataKey"],
            missing_contract,
        )
        if schema is missing_contract:
            schema = {}
            present = False
            unknown_source_wires.add(wire)
        else:
            present = expanded_contract_path_required(
                contracts,
                boundary["dataKey"],
                required_roots=required_roots,
            )
        producers[wire] = (boundary_id, "value")
        producer_schemas[wire] = copy.deepcopy(schema)
        producer_presence[wire] = present
    for node_id in plan["topology"]:
        binding = plan["bindings"][node_id]
        ports = ports_by_node[node_id]
        for port_name, wire in binding["outputs"].items():
            if wire in producers:
                raise ValueError(f"{label} wire '{wire}' has multiple producers.")
            producers[wire] = (node_id, port_name)
            producer_schemas[wire] = port_schema(ports["outputs"][port_name])
            producer_presence[wire] = ports["outputs"][port_name]["required"]

    edges_by_target = {}
    for index, edge in enumerate(plan["edges"]):
        target = (edge["to"]["node"], edge["to"]["port"])
        if target in edges_by_target:
            raise ValueError(
                f"{label}.edges contains multiple writes to "
                f"'{target[0]}.{target[1]}'."
            )
        edges_by_target[target] = (index, edge)
    covered_targets = set()
    dependencies = {node_id: set() for node_id in plan["topology"]}
    consumed_unknown_wires = set()

    def require_edge_relation(
        target,
        *,
        wire,
        producer,
        from_schema,
        to_schema,
    ):
        indexed = edges_by_target.get(target)
        if indexed is None:
            raise ValueError(
                f"{label}.edges is missing write to '{target[0]}.{target[1]}'."
            )
        _index, edge = indexed
        covered_targets.add(target)
        if (
            edge["wire"] != wire
            or edge["from"]["node"] != producer[0]
            or edge["from"]["port"] != producer[1]
            or edge["to"]["node"] != target[0]
            or edge["to"]["port"] != target[1]
        ):
            raise ValueError(f"{label}.edges has an invalid wire relation.")
        _require_match(
            edge["from"]["schema"],
            from_schema,
            label=f"{label} edge source schema",
        )
        _require_match(
            edge["to"]["schema"],
            to_schema,
            label=f"{label} edge target schema",
        )

    for node_id in plan["topology"]:
        binding = plan["bindings"][node_id]
        for port_name, wire in binding["inputs"].items():
            target = (node_id, port_name)
            port = ports_by_node[node_id]["inputs"][port_name]
            producer = producers.get(wire)
            if producer is None:
                if port["required"]:
                    raise ValueError(
                        f"{label} required input '{node_id}.{port_name}' "
                        f"references unknown wire '{wire}'."
                    )
                if target in edges_by_target:
                    raise ValueError(
                        f"{label}.edges writes an unproduced optional input."
                    )
                continue
            if (
                port["required"]
                and not producer_presence[wire]
            ):
                raise ValueError(
                    f"{label} required input '{node_id}.{port_name}' relies "
                    f"on optional wire '{wire}'."
                )
            if producer[0] == node_id:
                raise ValueError(
                    f"{label} Module '{node_id}' reads its own output wire '{wire}'."
                )
            required_schema = port_schema(port)
            if wire in unknown_source_wires:
                if port["required"]:
                    raise ValueError(
                        f"{label} required input '{node_id}.{port_name}' has "
                        "no composition contract."
                    )
                consumed_unknown_wires.add(wire)
                provided_schema = required_schema
            else:
                provided_schema = producer_schemas[wire]
                if not schemas_compatible(provided_schema, required_schema):
                    raise ValueError(
                        f"{label} wire '{wire}' has incompatible port schemas."
                    )
            require_edge_relation(
                target,
                wire=wire,
                producer=producer,
                from_schema=provided_schema,
                to_schema=required_schema,
            )
            if producer[0] in dependencies:
                dependencies[node_id].add(producer[0])

    for boundary_id, boundary in plan["outputs"].items():
        wire = boundary["wire"]
        producer = producers.get(wire)
        if producer is None:
            raise ValueError(
                f"{label} output '{boundary_id}' references unknown wire '{wire}'."
            )
        if wire in unknown_source_wires:
            consumed_unknown_wires.add(wire)
        schema = producer_schemas[wire]
        edge_schema = schema if schema else False
        require_edge_relation(
            (boundary_id, "value"),
            wire=wire,
            producer=producer,
            from_schema=edge_schema,
            to_schema=edge_schema,
        )
    if covered_targets != set(edges_by_target):
        raise ValueError(f"{label}.edges contains an unsupported relation.")
    unused_unknown = sorted(unknown_source_wires - consumed_unknown_wires)
    if unused_unknown:
        raise ValueError(
            f"{label} input wire '{unused_unknown[0]}' has no contracted consumer."
        )

    if (
        len(definition_node_order) != len(set(definition_node_order))
        or set(definition_node_order) != set(plan["topology"])
    ):
        raise ValueError(f"{label} Definition node order is not a permutation.")
    remaining = set(plan["topology"])
    cursor = 0
    while remaining:
        ready = [
            node_id
            for node_id in definition_node_order
            if node_id in remaining and not (dependencies[node_id] & remaining)
        ]
        if not ready:
            raise ValueError(f"{label} dependency graph is cyclic.")
        if plan["topology"][cursor:cursor + len(ready)] != ready:
            raise ValueError(
                f"{label} topology does not preserve Definition dependency order."
            )
        cursor += len(ready)
        remaining.difference_update(ready)
    if plan["nodes"] != plan["topology"]:
        raise ValueError(f"{label} nodes do not match its verified topology.")

    node_rank = {
        node_id: index for index, node_id in enumerate(plan["topology"])
    }

    def output_write_rank(item):
        boundary_id, boundary = item
        producer = producers.get(boundary["wire"], ("", ""))
        return (
            *canonical_data_key_order(boundary["dataKey"]),
            node_rank.get(producer[0], -1),
            producer[0],
            producer[1],
            boundary_id,
        )

    ordered_outputs = sorted(plan["outputs"].items(), key=output_write_rank)
    output_rank = {
        boundary_id: index
        for index, (boundary_id, _boundary) in enumerate(ordered_outputs)
    }
    input_port_rank = {
        node_id: {
            port_name: index
            for index, port_name in enumerate(
                sorted(plan["bindings"][node_id]["inputs"])
            )
        }
        for node_id in plan["topology"]
    }

    def edge_rank(edge):
        target = edge["to"]["node"]
        if target in node_rank:
            return (
                0,
                node_rank[target],
                input_port_rank[target][edge["to"]["port"]],
                edge["wire"],
                edge["from"]["node"],
                edge["from"]["port"],
            )
        return (
            1,
            output_rank[target],
            0,
            edge["wire"],
            edge["from"]["node"],
            edge["from"]["port"],
        )

    ranks = [edge_rank(edge) for edge in plan["edges"]]
    if ranks != sorted(ranks) or len(ranks) != len(set(ranks)):
        raise ValueError(f"{label}.edges is not in canonical execution order.")
    required_outputs = [
        edge["to"]["node"]
        for edge in plan["edges"]
        if edge["to"]["node"] in plan["outputs"]
        and producer_presence[edge["wire"]]
    ]
    if plan["requiredOutputs"] != required_outputs:
        raise ValueError(
            f"{label} requiredOutputs does not match producer presence."
        )
    output_contracts = ExpandedContractWriteReducer({}, frozenset())
    for edge in plan["edges"]:
        boundary_id = edge["to"]["node"]
        if boundary_id not in plan["outputs"]:
            continue
        wire = edge["wire"]
        schema = producer_schemas[wire]
        if schema:
            output_contracts.apply(
                plan["outputs"][boundary_id]["dataKey"],
                schema,
                required=producer_presence[wire],
            )
    expected_output_contracts, _required_roots = output_contracts.materialize()
    _require_match(
        plan["outputContracts"],
        expected_output_contracts,
        label=f"{label} outputContracts",
    )
    _require_invocations(plan, definition_authorities, invocations)
    return invocations
