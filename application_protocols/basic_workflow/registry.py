"""Exact application registry entries for installed Basic Workflow resources."""

from __future__ import annotations

import copy
import re

from .manifest import PROFILE_ID, PROTOCOL_ID, PROTOCOL_VERSION


_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")

RESOURCE_ROLES = {
    ("sampler", "basic-price-map-sampler"): (
        "sampler.price-map",
        (),
        ("time", "price"),
    ),
    ("environment", "basic-multi-asset-paper-environment"): (
        "environment.paper-multi-asset",
        (
            "time",
            "price",
            "last.intent.approved",
        ),
        (
            "time",
            "price",
            "portfolio.account",
            "execution.orders",
        ),
    ),
    ("module", "basic-price-map-universe"): (
        "pipeline.universe.price-map",
        ("price.<period>",),
        ("universe.selected",),
    ),
    ("module", "basic-neutral-score-map"): (
        "pipeline.signal.neutral-score-map",
        ("universe.selected",),
        ("signal.scores",),
    ),
    ("module", "basic-score-map-position-target"): (
        "pipeline.target.score-map-to-position",
        ("universe.selected", "signal.scores"),
        ("intent.requested",),
    ),
    ("module", "basic-absolute-position-map-constraint"): (
        "pipeline.constraint.absolute-position-map",
        ("intent.requested",),
        ("intent.approved",),
    ),
}

MODULE_KINDS = {
    "basic-price-map-universe": "Universe",
    "basic-neutral-score-map": "Signal",
    "basic-score-map-position-target": "Target",
    "basic-absolute-position-map-constraint": "Constraint",
}


def _resource_identity(record):
    if type(record) is not dict:
        raise ValueError("Protocol registry resource must be an object.")
    if "moduleId" in record:
        resource_type = "module"
        resource_id = record["moduleId"]
        kind = record.get("kind")
    elif "samplerId" in record:
        resource_type = "sampler"
        resource_id = record["samplerId"]
        kind = None
    elif "environmentId" in record:
        resource_type = "environment"
        resource_id = record["environmentId"]
        kind = None
    elif "analysisId" in record:
        resource_type = "analysis"
        resource_id = record["analysisId"]
        kind = None
    else:
        return None
    return resource_type, resource_id, kind


def _exact_archived_identity(record, resource_type, resource_id, kind):
    version = record.get("version")
    digest = record.get("contentDigest")
    if (
        type(resource_id) is not str
        or not resource_id
        or type(version) is not str
        or not version.isascii()
        or not version.isdecimal()
        or version != str(int(version))
        or int(version) < 1
        or type(digest) is not str
        or not _SHA256.fullmatch(digest)
    ):
        raise ValueError("Protocol registry resources require exact version and digest.")
    if record.get("builtin") is not True:
        raise ValueError("Protocol registry BuiltIn resources must be Engine-owned.")
    return resource_type, resource_id, kind, version, digest


def build_registry(records):
    """Build a deterministic registry from exact archived BuiltIn records.

    Unknown BuiltIns are ignored.  Known protocol identities must occur exactly
    once and are never resolved through a symbolic ``latest`` reference.
    """

    if type(records) not in {list, tuple}:
        raise ValueError("Protocol registry records must be an array.")
    entries = []
    seen = set()
    for record in records:
        identity = _resource_identity(record)
        if identity is None:
            continue
        resource_type, resource_id, kind = identity
        contract = RESOURCE_ROLES.get((resource_type, resource_id))
        if contract is None:
            continue
        resource_type, resource_id, kind, version, digest = _exact_archived_identity(
            record, resource_type, resource_id, kind
        )
        key = (resource_type, resource_id)
        if key in seen:
            raise ValueError(f"Duplicate protocol registry resource: {resource_id}")
        seen.add(key)
        role, requires, provides = contract
        if resource_type == "module":
            expected_kind = MODULE_KINDS[resource_id]
            if kind != expected_kind:
                raise ValueError(
                    f"Protocol registry Module '{resource_id}' must have kind "
                    f"'{expected_kind}'."
                )
        resource = {
            "type": resource_type,
            **({"kind": kind} if kind is not None else {}),
            "id": resource_id,
            "version": version,
            "contentDigest": digest,
        }
        entries.append(
            {
                "protocolId": PROTOCOL_ID,
                "protocolVersion": PROTOCOL_VERSION,
                "profile": PROFILE_ID,
                "role": role,
                "resource": resource,
                "requires": list(requires),
                "provides": list(provides),
            }
        )
    return sorted(entries, key=lambda item: item["role"])


def registry_copy(entries):
    return copy.deepcopy(entries)


__all__ = ("RESOURCE_ROLES", "build_registry", "registry_copy")
