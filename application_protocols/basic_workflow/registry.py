"""Passive discovery of resources that declare Basic Workflow ownership."""

from __future__ import annotations

import copy

from engine.contracts.protocol import normalize_protocol_id

from .manifest import PROTOCOL_ID


_IDENTITIES = (
    ("module", "moduleId"),
    ("sampler", "samplerId"),
    ("pipeline", "pipelineId"),
    ("environment", "environmentId"),
    ("analysis", "analysisId"),
    ("dataset", "datasetVersionId"),
    ("visualizer", "id"),
)


def _resource_identity(record):
    if type(record) is not dict:
        raise ValueError("Protocol registry resource must be an object.")
    for resource_type, identity_field in _IDENTITIES:
        resource_id = record.get(identity_field)
        if isinstance(resource_id, str) and resource_id:
            return resource_type, identity_field, resource_id
    raise ValueError("Declared protocol resource has no supported identity.")


def build_registry(records):
    """Return a deterministic projection of exact self-declared resources.

    The registry does not infer roles, inspect ports, or claim conformance.  The
    normal Trade Engine repositories remain authoritative for each record.
    """

    if type(records) not in {list, tuple}:
        raise ValueError("Protocol registry records must be an array.")
    entries = []
    seen = set()
    for record in records:
        if type(record) is not dict:
            raise ValueError("Protocol registry resource must be an object.")
        declared = record.get("protocolId")
        if declared is None:
            continue
        declared = normalize_protocol_id(
            declared,
            label="Protocol registry resource protocolId",
        )
        if declared != PROTOCOL_ID:
            continue
        resource_type, _identity_field, resource_id = _resource_identity(record)
        version = record.get("version", "")
        if version is not None and not isinstance(version, str):
            raise ValueError("Protocol registry resource version must be a string.")
        key = (resource_type, resource_id, version or "")
        if key in seen:
            raise ValueError(
                "Duplicate protocol registry resource: "
                f"{resource_type}/{resource_id}@{version or '-'}"
            )
        seen.add(key)
        resource = {
            "type": resource_type,
            "id": resource_id,
            **(
                {"kind": record["kind"]}
                if resource_type == "module" and isinstance(record.get("kind"), str)
                else {}
            ),
            **({"version": version} if version else {}),
            **(
                {"contentDigest": record["contentDigest"]}
                if isinstance(record.get("contentDigest"), str)
                else {}
            ),
        }
        entries.append({"protocolId": PROTOCOL_ID, "resource": resource})
    return sorted(
        entries,
        key=lambda item: (
            item["resource"]["type"],
            item["resource"]["id"],
            item["resource"].get("version", ""),
        ),
    )


def registry_copy(entries):
    return copy.deepcopy(entries)


__all__ = ("build_registry", "registry_copy")
