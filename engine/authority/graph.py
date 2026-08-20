"""Nominal authority for one exact compiled Module Graph plan."""

from __future__ import annotations

import copy
from collections.abc import Mapping

from engine.authority import graph_composition as _graph_composition
from engine.authority import graph_semantics as _graph_semantics
from engine.authority import module_definition as _module_definition_authority
from engine.contracts import strict_json
from engine.contracts.graph import normalize_graph, validate_compiled_graph
from engine.contracts.module import MODULE_INSTANCE_FIELDS, definition_key


__all__ = (
    "bind_compiled_graph_authority_plan",
    "compiled_graph_authority_material",
    "compiled_graph_authority_plan",
    "require_compiled_graph_authority",
)


_COMPILED_GRAPH_AUTHORITY_TOKEN = object()


def _canonical_plan_json(value):
    return strict_json.dumps(value, sort_keys=True, separators=(",", ":"))


def _require_canonical_match(actual, expected, *, label):
    if _canonical_plan_json(actual) != _canonical_plan_json(expected):
        raise ValueError(f"{label} does not match its verified composition.")


def _definition_authority_material(definition_authorities):
    return {
        key: _module_definition_authority.verified_module_definition_material(
            authority
        )
        for key, authority in definition_authorities.items()
    }


def _compiled_graph_definition_authority_subset(plan, definition_authorities):
    keys = sorted({
        definition_key(
            binding["kind"],
            binding["moduleId"],
            binding["version"],
        )
        for binding in plan["bindings"].values()
    })
    missing = [key for key in keys if key not in definition_authorities]
    if missing:
        raise ValueError(
            "Compiled Graph authority is missing Module Definition(s): "
            + ", ".join(missing)
        )
    subset = {key: definition_authorities[key] for key in keys}
    _definition_authority_material(subset)
    return subset


class _CompiledGraphAuthority:
    """Engine-owned proof that one exact plan passed Graph semantic validation."""

    __slots__ = (
        "_plan_json",
        "_definition_authorities",
        "_invocation_authorities",
        "_sealed",
    )

    def __setattr__(self, name, value):
        if getattr(self, "_sealed", False):
            raise AttributeError("Compiled Graph authority is immutable.")
        object.__setattr__(self, name, value)

    def __init__(
        self,
        plan_json,
        definition_authorities,
        invocation_authorities,
        *,
        _token,
    ):
        if _token is not _COMPILED_GRAPH_AUTHORITY_TOKEN:
            raise TypeError("Compiled Graph authority is Engine-owned.")
        self._plan_json = plan_json
        self._definition_authorities = tuple(definition_authorities.items())
        self._invocation_authorities = tuple(invocation_authorities.items())
        object.__setattr__(self, "_sealed", True)

    def _material(self):
        return (
            strict_json.loads(self._plan_json),
            dict(self._definition_authorities),
            dict(self._invocation_authorities),
        )


def require_compiled_graph_authority(authority):
    """Require the exact nominal Graph authority type without copying material."""

    if type(authority) is not _CompiledGraphAuthority:
        raise TypeError("Compiled Graph authority is Engine-owned.")
    return authority


def seal_compiled_graph_authority(
    plan,
    definition_authorities,
):
    """Seal one compiler plan through the shared semantic authority gate.

    This compiler bridge is intentionally omitted from ``__all__``.  The
    compiler authority entry points are always strict and executable.
    """

    plan = validate_compiled_graph(plan, label="Compiled Graph")
    definition_authorities = _compiled_graph_definition_authority_subset(
        plan,
        definition_authorities,
    )
    invocation_authorities = _graph_semantics.verify_compiled_graph_semantics(
        plan,
        definition_authorities,
        definition_node_order=plan["topology"],
        label="Compiled Graph",
    )
    return _CompiledGraphAuthority(
        _canonical_plan_json(plan),
        definition_authorities,
        invocation_authorities,
        _token=_COMPILED_GRAPH_AUTHORITY_TOKEN,
    )


def compiled_graph_authority_plan(authority):
    """Return an isolated plan from an Engine-owned compiled Graph authority."""

    require_compiled_graph_authority(authority)
    plan, _definition_authorities, _invocation_authorities = authority._material()
    return plan


def compiled_graph_authority_material(authority):
    """Return detached execution material for authority-bound Runtime construction."""

    require_compiled_graph_authority(authority)
    plan, definition_authorities, invocation_authorities = authority._material()
    return (
        plan,
        _definition_authority_material(definition_authorities),
        invocation_authorities,
    )


def bind_compiled_graph_authority_plan(authority, plan, *, label="Compiled Graph"):
    """Bind an exact frozen plan to an equivalent compiler authority."""

    require_compiled_graph_authority(authority)
    plan = validate_compiled_graph(plan, label=label)
    if _canonical_plan_json(plan) != authority._plan_json:
        raise ValueError(f"{label} does not match its compiled authority.")
    _compiled_plan, definition_authorities, invocation_authorities = (
        authority._material()
    )
    return _CompiledGraphAuthority(
        _canonical_plan_json(plan),
        definition_authorities,
        invocation_authorities,
        _token=_COMPILED_GRAPH_AUTHORITY_TOKEN,
    )


def bind_frozen_composition_graph_authority(
    plan,
    graph,
    instances,
    definition_authorities,
    initial_contracts,
    *,
    allowed_kinds,
    label,
    required_roots,
    source_contracts=None,
    source_required_roots=None,
):
    """Bind a frozen artifact directly; never compile or rebuild a second plan.

    This frozen-worker bridge is intentionally omitted from ``__all__``.
    """

    plan = validate_compiled_graph(plan, label=label)
    source_contracts = {} if source_contracts is None else source_contracts
    source_required_roots = (
        {} if source_required_roots is None else source_required_roots
    )
    if not isinstance(initial_contracts, Mapping):
        raise ValueError(f"{label} initial contracts must be an object.")
    if not isinstance(source_contracts, Mapping):
        raise ValueError(f"{label} source contracts must be an object.")
    if not isinstance(source_required_roots, Mapping):
        raise ValueError(f"{label} source required roots must be an object.")
    if set(source_required_roots) - set(source_contracts):
        raise ValueError(f"{label} required roots reference an unknown source.")
    graph = normalize_graph(
        graph,
        label=label,
        input_sources=source_contracts,
    )
    if len(graph["nodes"]) != len(set(graph["nodes"])):
        raise ValueError(f"{label} contains duplicate nodes.")
    if not isinstance(instances, Mapping) or set(instances) != set(graph["nodes"]):
        raise ValueError(f"{label} instances must exactly match graph.nodes.")
    if not isinstance(definition_authorities, Mapping):
        raise TypeError("Verified Module Definition authorities must be an object.")
    required_definition_keys = {
        definition_key(
            binding["kind"], binding["moduleId"], binding["version"]
        )
        for binding in plan["bindings"].values()
    }
    if set(definition_authorities) != required_definition_keys:
        raise ValueError(
            f"{label} Module Definition authorities must exactly match its bindings."
        )
    definition_authorities = _compiled_graph_definition_authority_subset(
        plan,
        dict(definition_authorities),
    )
    if set(plan["nodes"]) != set(graph["nodes"]):
        raise ValueError(f"{label} nodes do not match its Definition Graph.")
    _require_canonical_match(
        plan["inputs"],
        graph["inputs"],
        label=f"{label} inputs",
    )
    _require_canonical_match(
        plan["outputs"],
        graph["outputs"],
        label=f"{label} outputs",
    )
    expected_bindings = {
        node_id: {
            field: copy.deepcopy(instances[node_id][field])
            for field in MODULE_INSTANCE_FIELDS
        }
        for node_id in graph["nodes"]
    }
    _require_canonical_match(
        plan["bindings"],
        expected_bindings,
        label=f"{label} bindings",
    )
    if allowed_kinds is not None and any(
        binding["kind"] not in set(allowed_kinds)
        for binding in expected_bindings.values()
    ):
        raise ValueError(f"{label} contains an invalid Module kind.")
    _graph_composition.verify_frozen_boundary_contracts(
        plan,
        graph,
        initial_contracts,
        required_roots,
        source_contracts,
        source_required_roots,
        label=label,
    )
    invocation_authorities = _graph_semantics.verify_compiled_graph_semantics(
        plan,
        definition_authorities,
        definition_node_order=graph["nodes"],
        label=label,
    )
    return _CompiledGraphAuthority(
        _canonical_plan_json(plan),
        definition_authorities,
        invocation_authorities,
        _token=_COMPILED_GRAPH_AUTHORITY_TOKEN,
    )
