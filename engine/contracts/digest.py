"""Canonical content-digest contracts shared by Engine layers."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

from engine.contracts import strict_json


SHA256_DIGEST_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")


def canonical_json_digest(value):
    """Return the historical unprefixed SHA-256 of canonical strict JSON."""

    encoded = strict_json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def is_sha256_digest(value):
    """Return whether *value* is one canonical SHA-256 evidence string."""

    return isinstance(value, str) and bool(SHA256_DIGEST_PATTERN.fullmatch(value))


def require_sha256_digest(value, *, label="SHA-256 digest"):
    """Require one canonical SHA-256 evidence string."""

    if not is_sha256_digest(value):
        raise ValueError(f"{label} is invalid.")
    return value


def sha256_file_digest(path):
    """Return the canonical SHA-256 evidence string for one immutable file."""
    return "sha256:" + hashlib.sha256(Path(path).read_bytes()).hexdigest()


__all__ = (
    "SHA256_DIGEST_PATTERN",
    "canonical_json_digest",
    "is_sha256_digest",
    "require_sha256_digest",
    "sha256_file_digest",
)
