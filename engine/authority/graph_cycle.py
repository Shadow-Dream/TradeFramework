"""Nominal authority for one verified causal Cycle Graph definition."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy

from engine.archive import version as version_archive
from engine.authority import execution_records
from engine.authority.graph import (
    compiled_graph_authority_plan,
    require_compiled_graph_authority,
)
from engine.contracts import strict_json
from engine.contracts.graph import compiled_graph_definition
from engine.contracts.graph_cycle import validate_cycle_graph_inputs
from engine.contracts.module import MODULE_INSTANCE_FIELDS


__all__ = (
    "bind_compiled_cycle_graph_authority",
    "bind_verified_compiled_cycle_graph_authority",
    "compiled_cycle_graph_authority_material",
    "verified_cycle_graph_definition_material",
    "verified_cycle_graph_execution_material",
    "verify_cycle_graph_definition_authority",
    "verify_managed_cycle_graph_definition_authority",
)


_COMPILED_CYCLE_GRAPH_AUTHORITY_TOKEN = object()
_VERIFIED_CYCLE_GRAPH_DEFINITION_TOKEN = object()


class _VerifiedCycleGraphDefinition:
    """Nominal proof that one archived Cycle Graph Definition was verified."""

    __slots__ = ("_definition_json", "_sealed")

    def __setattr__(self, name, value):
        if getattr(self, "_sealed", False):
            raise AttributeError("Verified Cycle Graph Definition is immutable.")
        object.__setattr__(self, name, value)

    def __init__(self, definition, *, _token):
        if _token is not _VERIFIED_CYCLE_GRAPH_DEFINITION_TOKEN:
            raise TypeError("Verified Cycle Graph Definition is Engine-owned.")
        self._definition_json = strict_json.dumps(
            definition,
            sort_keys=True,
            separators=(",", ":"),
        )
        object.__setattr__(self, "_sealed", True)

    def _material(self):
        return strict_json.loads(self._definition_json)


def _verified_cycle_graph_definition_authority(definition):
    return _VerifiedCycleGraphDefinition(
        definition,
        _token=_VERIFIED_CYCLE_GRAPH_DEFINITION_TOKEN,
    )


def verify_cycle_graph_definition_authority(definition):
    version_archive.verify_record(definition)
    return _verified_cycle_graph_definition_authority(definition)


def verify_managed_cycle_graph_definition_authority(
    release_root,
    definition,
    *,
    resource_type,
    expected_identity=None,
    expected_version=None,
):
    """Verify exact repository identity/location before reading a frozen Graph."""

    execution_records.verify_cycle_graph_record(
        release_root,
        definition,
        resource_type=resource_type,
        expected_identity=expected_identity,
        expected_version=expected_version,
    )
    return _verified_cycle_graph_definition_authority(definition)


def verified_cycle_graph_definition_material(authority):
    if type(authority) is not _VerifiedCycleGraphDefinition:
        raise TypeError("Verified Cycle Graph Definition is Engine-owned.")
    return authority._material()


def verified_cycle_graph_execution_material(authority, *, graph_label):
    """Return detached Graph fields only after their outer execution shape exists."""

    definition = verified_cycle_graph_definition_material(authority)
    if not isinstance(definition.get("graph"), Mapping):
        raise ValueError(f"{graph_label} Definition graph must be an object.")
    if not isinstance(definition.get("instances"), Mapping):
        raise ValueError(f"{graph_label} Definition instances must be an object.")
    return definition


class _CompiledCycleGraphAuthority:
    """Bind one verified Cycle definition to the exact Graph authority it compiled."""

    __slots__ = (
        "_definition_json",
        "_compiled_graph",
        "_allowed_kind",
        "_graph_label",
        "_identity_field",
        "_runtime_type",
        "_sealed",
    )

    def __setattr__(self, name, value):
        if getattr(self, "_sealed", False):
            raise AttributeError("Compiled Cycle Graph authority is immutable.")
        object.__setattr__(self, name, value)

    def __init__(
        self,
        definition,
        compiled_graph,
        *,
        allowed_kind,
        graph_label,
        identity_field,
        runtime_type,
        _token,
    ):
        if _token is not _COMPILED_CYCLE_GRAPH_AUTHORITY_TOKEN:
            raise TypeError("Compiled Cycle Graph authority is Engine-owned.")
        self._definition_json = strict_json.dumps(
            definition,
            sort_keys=True,
            separators=(",", ":"),
        )
        self._compiled_graph = compiled_graph
        self._allowed_kind = allowed_kind
        self._graph_label = graph_label
        self._identity_field = identity_field
        self._runtime_type = runtime_type
        object.__setattr__(self, "_sealed", True)

    def _runtime_material(
        self, *, allowed_kind, graph_label, identity_field, runtime_type
    ):
        expected = (allowed_kind, graph_label, identity_field, runtime_type)
        actual = (
            self._allowed_kind,
            self._graph_label,
            self._identity_field,
            self._runtime_type,
        )
        if actual != expected:
            raise TypeError("Compiled Cycle Graph authority has the wrong runtime kind.")
        return strict_json.loads(self._definition_json), self._compiled_graph


def compiled_cycle_graph_authority_material(
    authority,
    *,
    allowed_kind,
    graph_label,
    identity_field,
    runtime_type,
):
    """Return detached runtime material after enforcing the exact Cycle kind."""
    if type(authority) is not _CompiledCycleGraphAuthority:
        raise TypeError("Cycle Graph Runtime requires compiled Cycle Graph authority.")
    return authority._runtime_material(
        allowed_kind=allowed_kind,
        graph_label=graph_label,
        identity_field=identity_field,
        runtime_type=runtime_type,
    )


def _bind_verified_compiled_cycle_graph_authority(
    definition_authority,
    compiled_graph,
    *,
    allowed_kind,
    graph_label,
    identity_field,
    runtime_type,
):
    """Prove that one compiled Graph and one Cycle definition are the same authority."""
    require_compiled_graph_authority(compiled_graph)
    definition = verified_cycle_graph_execution_material(
        definition_authority,
        graph_label=graph_label,
    )
    validate_cycle_graph_inputs(definition["graph"], label=graph_label)
    plan = compiled_graph_authority_plan(compiled_graph)
    recovered_graph = compiled_graph_definition(plan, label=graph_label)
    definition_graph = definition["graph"]
    definition_nodes = set(definition_graph["nodes"])
    definition_instances = set(definition["instances"])
    if definition_instances != definition_nodes:
        missing = sorted(definition_nodes - definition_instances)
        orphan = sorted(definition_instances - definition_nodes)
        details = []
        if missing:
            details.append("missing instance(s): " + ", ".join(missing))
        if orphan:
            details.append("instance(s) outside graph.nodes: " + ", ".join(orphan))
        raise ValueError(
            f"{graph_label} Definition instances must exactly match graph.nodes: "
            + "; ".join(details)
        )
    same_graph = (
        set(recovered_graph["nodes"]) == set(definition_graph["nodes"])
        and recovered_graph["inputs"] == definition_graph["inputs"]
        and recovered_graph["outputs"] == definition_graph["outputs"]
    )
    if not same_graph:
        raise ValueError(
            f"{graph_label} compiled authority does not match its Definition Graph."
        )
    expected_bindings = {
        node_id: {
            field: deepcopy(definition["instances"][node_id][field])
            for field in MODULE_INSTANCE_FIELDS
        }
        for node_id in definition_graph["nodes"]
    }
    if strict_json.dumps(plan["bindings"], sort_keys=True) != strict_json.dumps(
        expected_bindings, sort_keys=True
    ):
        raise ValueError(
            f"{graph_label} compiled authority does not match its Definition instances."
        )
    dependencies = {node_id: set() for node_id in definition_graph["nodes"]}
    for edge in plan["edges"]:
        producer = edge["from"]["node"]
        consumer = edge["to"]["node"]
        if producer in dependencies and consumer in dependencies:
            dependencies[consumer].add(producer)
    remaining = set(dependencies)
    expected_topology = []
    while remaining:
        ready = [
            node_id
            for node_id in definition_graph["nodes"]
            if node_id in remaining and not (dependencies[node_id] & remaining)
        ]
        if not ready:
            raise ValueError(f"{graph_label} Definition dependency graph is cyclic.")
        expected_topology.extend(ready)
        remaining.difference_update(ready)
    if plan["topology"] != expected_topology:
        raise ValueError(
            f"{graph_label} compiled authority does not match its Definition node order."
        )
    if any(
        binding["kind"] != allowed_kind for binding in plan["bindings"].values()
    ):
        raise ValueError(
            f"{graph_label} compiled authority contains a non-{allowed_kind} Module."
        )
    if not isinstance(definition.get(identity_field), str) or not definition[
        identity_field
    ]:
        raise ValueError(f"{graph_label} Definition identity is invalid.")
    return _CompiledCycleGraphAuthority(
        definition,
        compiled_graph,
        allowed_kind=allowed_kind,
        graph_label=graph_label,
        identity_field=identity_field,
        runtime_type=runtime_type,
        _token=_COMPILED_CYCLE_GRAPH_AUTHORITY_TOKEN,
    )


def bind_verified_compiled_cycle_graph_authority(
    definition_authority,
    compiled_graph,
    *,
    allowed_kind,
    graph_label,
    identity_field,
    runtime_type,
):
    """Bind a Graph plan to a Cycle Definition verified in this call stack."""
    return _bind_verified_compiled_cycle_graph_authority(
        definition_authority,
        compiled_graph,
        allowed_kind=allowed_kind,
        graph_label=graph_label,
        identity_field=identity_field,
        runtime_type=runtime_type,
    )


def bind_compiled_cycle_graph_authority(
    definition,
    compiled_graph,
    *,
    allowed_kind,
    graph_label,
    identity_field,
    runtime_type,
):
    """Strict raw entry for a Cycle Definition and its compiled Graph."""
    require_compiled_graph_authority(compiled_graph)
    if not isinstance(definition, Mapping):
        raise ValueError(f"{graph_label} Definition must be an object.")
    definition_authority = verify_cycle_graph_definition_authority(definition)
    return _bind_verified_compiled_cycle_graph_authority(
        definition_authority,
        compiled_graph,
        allowed_kind=allowed_kind,
        graph_label=graph_label,
        identity_field=identity_field,
        runtime_type=runtime_type,
    )
