"""Exact standalone configuration authority for the Mining process."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

from .providers.base import strict_json_loads


MINING_CONFIG_FIELDS = frozenset(
    {
        "liveRoot",
        "releaseRoot",
        "controlRoot",
        "miningRoot",
        "miningAutoStart",
        "miningExposeTestProvider",
        "miningHttpTimeout",
        "miningMaxPageBytes",
        "miningMaxPagesPerRun",
    }
)


def load_config(path: str | Path) -> dict[str, Any]:
    """Load the one current Mining config shape without defaults or aliases."""

    config = strict_json_loads(Path(path).read_bytes(), label="Mining config")
    if type(config) is not dict:
        raise ValueError("Mining config must be a JSON object.")
    fields = set(config)
    if fields != MINING_CONFIG_FIELDS:
        missing = sorted(MINING_CONFIG_FIELDS - fields)
        unknown = sorted(fields - MINING_CONFIG_FIELDS)
        details = []
        if missing:
            details.append(f"missing fields: {', '.join(missing)}")
        if unknown:
            details.append(f"unknown fields: {', '.join(unknown)}")
        raise ValueError(f"Mining config must have the exact current fields ({'; '.join(details)}).")

    loaded: dict[str, Any] = {}
    for field in ("liveRoot", "releaseRoot", "controlRoot", "miningRoot"):
        value = config[field]
        if type(value) is not str or not value.strip():
            raise ValueError(f"Mining config {field} must be a non-empty string.")
        loaded[field] = str(Path(value).expanduser())

    for field in ("miningAutoStart", "miningExposeTestProvider"):
        value = config[field]
        if type(value) is not bool:
            raise ValueError(f"Mining config {field} must be a boolean.")
        loaded[field] = value

    raw_timeout = config["miningHttpTimeout"]
    if type(raw_timeout) not in {int, float}:
        raise ValueError("Mining config miningHttpTimeout must be a finite number.")
    timeout = float(raw_timeout)
    if not math.isfinite(timeout) or not 1 <= timeout <= 300:
        raise ValueError(
            "Mining config miningHttpTimeout must be finite and between 1 and 300."
        )
    loaded["miningHttpTimeout"] = timeout

    integer_limits = {
        "miningMaxPageBytes": (1024, 1024 * 1024 * 1024),
        "miningMaxPagesPerRun": (1, 1000),
    }
    for field, (minimum, maximum) in integer_limits.items():
        value = config[field]
        if type(value) is not int:
            raise ValueError(f"Mining config {field} must be an integer.")
        if not minimum <= value <= maximum:
            raise ValueError(
                f"Mining config {field} must be between {minimum} and {maximum}."
            )
        loaded[field] = value
    return loaded
