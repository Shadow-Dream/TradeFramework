#!/usr/bin/env python3
"""Domain-neutral exact object-shape validation."""


def require_exact_fields(value, *, allowed, required=(), label="Object"):
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object.")
    unknown = sorted(set(value) - set(allowed))
    if unknown:
        raise ValueError(
            f"{label} contains unsupported field(s): " + ", ".join(unknown)
        )
    missing = sorted(set(required) - set(value))
    if missing:
        raise ValueError(
            f"{label} is missing required field(s): " + ", ".join(missing)
        )
    return value
