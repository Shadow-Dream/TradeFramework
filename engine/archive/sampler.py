#!/usr/bin/env python3
"""Immutable Sampler runtime asset manifests."""

from __future__ import annotations

from pathlib import Path

from engine.contracts import digest as digest_contracts
from engine.contracts.sampler import sampler_type_spec


_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_SAMPLER_RUNTIME_ASSET_SOURCES = {
    "row_map_sampler_runtime.py": (
        _PROJECT_ROOT / "engine/runtime/sampler_assets/row_map_sampler_runtime.py"
    ),
    "sampler_worker.py": (
        _PROJECT_ROOT / "engine/runtime/sampler_assets/sampler_worker.py"
    ),
    "sampler_sdk.py": _PROJECT_ROOT / "strategy_devkit/sampler_sdk.py",
}


def asset_digest(path):
    return digest_contracts.sha256_file_digest(path)


def sampler_runtime_bundle(sampler_type):
    """Return immutable runtime metadata plus source assets for one Draft."""
    sampler_type = str(sampler_type or "").strip()
    spec = sampler_type_spec(sampler_type)
    sources = {
        name: _SAMPLER_RUNTIME_ASSET_SOURCES[name] for name in spec["assets"]
    }
    missing = sorted(name for name, path in sources.items() if not path.is_file())
    if missing:
        raise RuntimeError(
            "Sampler runtime source asset(s) are missing: " + ", ".join(missing)
        )
    runtime = {
        "schemaVersion": 1,
        "samplerType": sampler_type,
        "protocol": spec["protocol"],
        "entryAsset": spec["entryAsset"],
        "entryPoint": spec["entryPoint"],
        "assets": [
            {
                "path": f"runtime/{name}",
                "sha256": asset_digest(path),
            }
            for name, path in sources.items()
        ],
    }
    return runtime, sources
