"""Contracts shared by immutable resource archives."""

from __future__ import annotations

from pathlib import Path


def require_resource_path_segment(value, *, label="Resource ID"):
    """Require an identity to map bijectively to one archive path segment."""

    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or value in {".", ".."}
        or "/" in value
        or "\\" in value
        or "\x00" in value
        or Path(value).parts != (value,)
    ):
        raise ValueError(
            f"{label} must be one canonical filesystem-safe path segment."
        )
    return value


__all__ = ("require_resource_path_segment",)
