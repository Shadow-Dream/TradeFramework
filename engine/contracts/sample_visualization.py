"""Strict saved Visualization envelopes keyed by immutable Sample Results."""

from __future__ import annotations

from engine.contracts import visualization
from engine.contracts.digest import is_sha256_digest
from engine.contracts.exact_fields import require_exact_fields


SAVE_FIELDS = frozenset({
    "sampleResultId", "expectedRevision", "visualizationId", "name", "spec",
})
RECORD_FIELDS = frozenset({
    "visualizationId", "sampleResultId", "name", "createdAt", "revision", "spec",
})


def _common(value, *, record):
    fields = RECORD_FIELDS if record else SAVE_FIELDS
    require_exact_fields(
        value,
        allowed=fields,
        required=fields,
        label="Sample Visualization record" if record else "Sample Visualization save request",
    )
    if not is_sha256_digest(value["sampleResultId"]):
        raise ValueError("Sample Visualization sampleResultId is invalid.")
    for field in ("visualizationId", "name"):
        if type(value[field]) is not str or not value[field].strip():
            raise ValueError(f"Sample Visualization {field} is required.")
    revision_field = "revision" if record else "expectedRevision"
    revision = value[revision_field]
    minimum = 1 if record else 0
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < minimum:
        raise ValueError(f"Sample Visualization {revision_field} is invalid.")
    if record and (type(value["createdAt"]) is not str or not value["createdAt"]):
        raise ValueError("Sample Visualization createdAt is required.")
    visualization.require_spec(value["spec"])
    return value


def require_save_request(value):
    return _common(value, record=False)


def require_record(value):
    return _common(value, record=True)


__all__ = ("RECORD_FIELDS", "SAVE_FIELDS", "require_record", "require_save_request")
