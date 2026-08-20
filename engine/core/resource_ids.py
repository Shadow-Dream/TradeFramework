#!/usr/bin/env python3
"""Opaque, time-sortable identifiers for Engine-owned resources."""

import re
import secrets
import time


_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_PREFIXES = {
    "dataset": "ds",
    "module": "mod",
    "pipeline": "pipe",
    "workspace": "ws",
    "script": "script",
    "sampler": "sampler",
    "environment": "env",
    "backtest": "bt",
    "job": "job",
    "visualization": "viz",
}
_RESOURCE_ID_PATTERN = re.compile(
    rf"^(?:{'|'.join(re.escape(prefix) for prefix in _PREFIXES.values())})_[{_ALPHABET}]{{26}}$"
)


def _encode(value, length):
    characters = []
    for _ in range(length):
        characters.append(_ALPHABET[value & 31])
        value >>= 5
    return "".join(reversed(characters))


def new_resource_id(kind):
    """Return a prefixed ULID-style identifier without using user-facing names."""
    normalized = str(kind or "").strip().casefold()
    prefix = _PREFIXES.get(normalized)
    if not prefix:
        raise ValueError(f"Unknown resource ID kind: {kind}")
    timestamp = int(time.time() * 1000) & ((1 << 48) - 1)
    randomness = secrets.randbits(80)
    return f"{prefix}_{_encode((timestamp << 80) | randomness, 26)}"


def is_resource_id(value):
    """Return whether *value* is an Engine-issued opaque resource identifier."""
    return bool(_RESOURCE_ID_PATTERN.fullmatch(str(value or "").strip()))


def normalize_resource_id(value):
    """Normalize a caller-supplied resource identifier without rewriting Engine IDs."""
    if is_resource_id(value):
        return str(value).strip()
    text = "".join(
        character.lower()
        if character.isalnum()
        else "_"
        if character == "_"
        else "-"
        for character in str(value or "").strip()
    )
    text = "-".join(part for part in text.split("-") if part)
    return text or "item"
