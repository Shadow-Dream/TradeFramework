"""Pure contracts for versioned Environment Graph definitions."""

from __future__ import annotations

import copy
from collections.abc import Mapping

from engine.contracts.graph import normalize_graph
from engine.contracts.graph_cycle import validate_cycle_graph_inputs
from engine.contracts.graph_resource import (
    GRAPH_RESOURCE_ARCHIVE_FIELDS,
    graph_resource_draft_fields,
)


ENVIRONMENT_DRAFT_FIELDS = graph_resource_draft_fields("environmentId")
ENVIRONMENT_VERSION_FIELDS = ENVIRONMENT_DRAFT_FIELDS | GRAPH_RESOURCE_ARCHIVE_FIELDS


def normalize_environment(definition, *, archived):
    if not isinstance(definition, Mapping):
        raise ValueError("Environment definition must be an object.")
    fields = ENVIRONMENT_VERSION_FIELDS if archived else ENVIRONMENT_DRAFT_FIELDS
    unknown = sorted(set(definition) - fields)
    if unknown:
        raise ValueError(
            "Environment definition contains unsupported field(s): "
            + ", ".join(unknown)
        )
    schema_version = definition.get("schemaVersion")
    if (
        isinstance(schema_version, bool)
        or not isinstance(schema_version, int)
        or schema_version != 2
    ):
        raise ValueError("Environment definition requires schemaVersion 2.")
    required = [
        "schemaVersion", "environmentId", "name", "description", "instances", "graph",
    ]
    if archived:
        required.extend(["version", "builtin"])
    missing = sorted(set(required) - set(definition))
    if missing:
        raise ValueError(
            "Environment definition is missing required field(s): "
            + ", ".join(missing)
        )
    for field in required:
        if field in {"schemaVersion", "instances", "graph"}:
            continue
        if field == "builtin":
            if not isinstance(definition[field], bool):
                raise ValueError("Environment definition builtin must be a boolean.")
            continue
        if not isinstance(definition[field], str):
            raise ValueError(f"Environment definition {field} must be a string.")
        if field != "description" and not definition[field].strip():
            raise ValueError(f"Environment definition requires {field}.")
    instances = definition["instances"]
    if not isinstance(instances, dict):
        raise ValueError("Environment instances must be an object.")
    graph = normalize_graph(definition["graph"], label="Environment Graph")
    validate_cycle_graph_inputs(graph, label="Environment Graph")
    unknown = sorted(set(graph["nodes"]) - set(instances))
    if unknown:
        raise ValueError(
            "Environment Graph references unknown instance(s): " + ", ".join(unknown)
        )
    orphaned = sorted(set(instances) - set(graph["nodes"]))
    if orphaned:
        raise ValueError(
            "Environment contains instance(s) outside its Graph: "
            + ", ".join(orphaned)
        )
    return {
        **{
            key: copy.deepcopy(definition[key])
            for key in fields
            if key in definition
        },
        "graph": graph,
        "instances": copy.deepcopy(dict(instances)),
    }


__all__ = (
    "ENVIRONMENT_DRAFT_FIELDS",
    "ENVIRONMENT_VERSION_FIELDS",
    "normalize_environment",
)
