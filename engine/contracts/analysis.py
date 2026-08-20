"""Pure contracts for versioned Analysis Graph definitions."""

from __future__ import annotations

import copy
from collections.abc import Mapping

from engine.contracts.graph import normalize_graph
from engine.contracts.graph_cycle import (
    CURRENT_PIPELINE_SOURCE,
    validate_cycle_graph_inputs,
)
from engine.contracts.graph_resource import (
    GRAPH_RESOURCE_ARCHIVE_FIELDS,
    graph_resource_draft_fields,
)


ANALYSIS_DRAFT_FIELDS = graph_resource_draft_fields("analysisId")
ANALYSIS_VERSION_FIELDS = ANALYSIS_DRAFT_FIELDS | GRAPH_RESOURCE_ARCHIVE_FIELDS


def normalize_analysis(definition, *, archived):
    if not isinstance(definition, Mapping):
        raise ValueError("Analysis definition must be an object.")
    fields = ANALYSIS_VERSION_FIELDS if archived else ANALYSIS_DRAFT_FIELDS
    unknown = sorted(set(definition) - fields)
    if unknown:
        raise ValueError(
            "Analysis definition contains unsupported field(s): " + ", ".join(unknown)
        )
    schema_version = definition.get("schemaVersion")
    if (
        isinstance(schema_version, bool)
        or not isinstance(schema_version, int)
        or schema_version != 1
    ):
        raise ValueError("Analysis definition requires schemaVersion 1.")
    required = [
        "schemaVersion", "analysisId", "name", "description", "instances", "graph",
    ]
    if archived:
        required.extend(["version", "builtin"])
    missing = sorted(set(required) - set(definition))
    if missing:
        raise ValueError(
            "Analysis definition is missing required field(s): " + ", ".join(missing)
        )
    for field in required:
        if field in {"schemaVersion", "instances", "graph"}:
            continue
        if field == "builtin":
            if not isinstance(definition[field], bool):
                raise ValueError("Analysis definition builtin must be a boolean.")
            continue
        if not isinstance(definition[field], str):
            raise ValueError(f"Analysis definition {field} must be a string.")
        if field != "description" and not definition[field].strip():
            raise ValueError(f"Analysis definition requires {field}.")
    instances = definition["instances"]
    if not isinstance(instances, dict):
        raise ValueError("Analysis instances must be an object.")
    graph = normalize_graph(
        definition["graph"],
        label="Analysis Graph",
        input_sources={CURRENT_PIPELINE_SOURCE},
    )
    validate_cycle_graph_inputs(graph, label="Analysis Graph")
    unknown = sorted(set(graph["nodes"]) - set(instances))
    if unknown:
        raise ValueError(
            "Analysis Graph references unknown instance(s): " + ", ".join(unknown)
        )
    orphaned = sorted(set(instances) - set(graph["nodes"]))
    if orphaned:
        raise ValueError(
            "Analysis contains instance(s) outside its Graph: " + ", ".join(orphaned)
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
    "ANALYSIS_DRAFT_FIELDS",
    "ANALYSIS_VERSION_FIELDS",
    "normalize_analysis",
)
