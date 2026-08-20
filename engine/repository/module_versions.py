"""Version selection for immutable Module records referenced by Graphs."""

from __future__ import annotations

import copy
from collections.abc import Mapping

from engine.archive import version_evidence
from engine.contracts import strict_json


__all__ = (
    "latest_archived_module_versions",
    "pin_graph_module_versions",
)


def _require_verified_definitions(definitions, record_evidence):
    if not isinstance(definitions, Mapping):
        raise ValueError(
            "Archived Module definitions and location evidence must be objects."
        )
    evidence_material = version_evidence.verified_record_index_material(
        record_evidence
    )
    evidence_by_key = evidence_material["locationEvidence"]
    if set(definitions) != set(evidence_by_key):
        raise ValueError(
            "Archived Module Definition evidence must exactly match its index."
        )
    for key, definition in definitions.items():
        material = version_evidence.verified_record_location_material(
            evidence_by_key[key]
        )
        if strict_json.dumps(
            material["record"], sort_keys=True
        ) != strict_json.dumps(definition, sort_keys=True):
            raise ValueError(
                f"Archived Module Definition evidence does not match: {key}"
            )
    return definitions


def latest_archived_module_versions(
    definitions, record_evidence, kind, module_ids
):
    """Resolve BuiltIn Graph templates through the normal archived Module index."""
    definitions = _require_verified_definitions(definitions, record_evidence)
    result = {}
    for module_id in module_ids:
        matches = [
            definition for definition in definitions.values()
            if definition.get("kind") == kind
            and definition.get("moduleId") == module_id
            and definition.get("status") == "archived"
        ]
        if not matches:
            raise ValueError(f"No Archived {kind} Module is available: {module_id}")
        result[module_id] = max(
            matches,
            key=lambda item: int(item["version"]),
        )["version"]
    return result


def pin_graph_module_versions(
    definition, module_definitions, module_definition_evidence
):
    """Pin a Graph draft to the current immutable version of every referenced Module."""
    if not isinstance(definition, Mapping) or not isinstance(
        module_definitions,
        Mapping,
    ):
        raise ValueError("Graph Draft and Module definitions must be objects.")
    module_definitions = _require_verified_definitions(
        module_definitions, module_definition_evidence
    )
    result = copy.deepcopy(definition)
    by_identity = {}
    for module in module_definitions.values():
        if module.get("status") != "archived":
            continue
        identity = (module.get("kind"), module.get("moduleId"))
        current = by_identity.get(identity)
        if current is None or int(module["version"]) > int(current["version"]):
            by_identity[identity] = module
    instances = result.get("instances")
    if not isinstance(instances, Mapping):
        raise ValueError("Graph Draft instances must be an object.")
    for instance_id, instance in instances.items():
        identity = (instance.get("kind"), instance.get("moduleId"))
        module = by_identity.get(identity)
        if module is None:
            raise ValueError(
                f"Graph instance '{instance_id}' references an unavailable Module: "
                f"{identity[0]}/{identity[1]}"
            )
        instance["version"] = module["version"]
    return result
