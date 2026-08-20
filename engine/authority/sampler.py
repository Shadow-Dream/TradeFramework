#!/usr/bin/env python3
"""Verification and nominal authority for archived Sampler runtimes."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from engine.authority import execution_records
from engine.contracts import digest as digest_contracts
from engine.contracts import strict_json
from engine.contracts.sampler import (
    canonical_sampler_parameters,
    require_exact_sampler_fields,
    resolve_sampler_output_contracts,
    sampler_protocol_spec,
)
from engine.contracts.data_model import validate_json_value


def verify_sampler_runtime_bundle(definition):
    if not isinstance(definition, Mapping):
        raise ValueError("Sampler Version must be an object.")
    try:
        runtime = definition["runtime"]
        sampler_type = definition["type"]
        archive = definition["archive"]
    except KeyError as exc:
        raise ValueError(
            "Sampler Version is missing its immutable runtime authority."
        ) from exc
    require_exact_sampler_fields(
        runtime,
        allowed={
            "schemaVersion",
            "samplerType",
            "protocol",
            "entryAsset",
            "entryPoint",
            "assets",
        },
        required={
            "schemaVersion",
            "samplerType",
            "protocol",
            "entryAsset",
            "entryPoint",
            "assets",
        },
        label="Sampler runtime bundle",
    )
    if runtime["schemaVersion"] != 1:
        raise ValueError("Sampler runtime bundle schemaVersion 1 is required.")
    if runtime["samplerType"] != sampler_type:
        raise ValueError(
            "Sampler runtime bundle type does not match its Sampler Version."
        )
    spec = sampler_protocol_spec(runtime["protocol"])
    if (
        runtime["entryAsset"] != spec["entryAsset"]
        or runtime["entryPoint"] != spec["entryPoint"]
    ):
        raise ValueError(
            "Sampler runtime bundle entry point does not match its protocol."
        )
    assets = runtime["assets"]
    if not isinstance(assets, list) or not assets:
        raise ValueError("Sampler runtime bundle requires immutable assets.")
    if not isinstance(archive, Mapping) or not isinstance(archive.get("root"), str):
        raise ValueError("Sampler Version archive root is invalid.")
    raw_archive_root = Path(archive["root"])
    if raw_archive_root.is_symlink():
        raise ValueError("Sampler Version archive root may not be a symbolic link.")
    archive_root = raw_archive_root.resolve()
    resolved = {}
    for item in assets:
        if not isinstance(item, Mapping) or set(item) != {"path", "sha256"}:
            raise ValueError(
                "Sampler runtime bundle contains an invalid asset manifest."
            )
        if not isinstance(item["path"], str) or not item["path"]:
            raise ValueError(
                "Sampler runtime asset path must be a non-empty string."
            )
        if not isinstance(item["sha256"], str):
            raise ValueError("Sampler runtime asset digest must be a string.")
        relative = Path(item["path"])
        if (
            relative.is_absolute()
            or ".." in relative.parts
            or relative.parent != Path("runtime")
        ):
            raise ValueError(f"Sampler runtime asset path is invalid: {relative}")
        path = (archive_root / relative).resolve()
        if path.parent != (archive_root / "runtime").resolve() or not path.is_file():
            raise ValueError(f"Sampler runtime asset is missing: {relative}")
        if digest_contracts.sha256_file_digest(path) != item["sha256"]:
            raise ValueError(f"Sampler runtime asset digest mismatch: {relative}")
        if path.name in resolved:
            raise ValueError(
                f"Sampler runtime bundle contains duplicate asset: {path.name}"
            )
        resolved[path.name] = path
    if set(resolved) != set(spec["assets"]):
        raise ValueError(
            "Sampler runtime bundle assets do not match its protocol."
        )
    return runtime, resolved, spec


_VERIFIED_SAMPLER_RUNTIME_BUNDLE_TOKEN = object()


class _VerifiedSamplerRuntimeBundle:
    """Nominal proof for one immutable Sampler runtime bundle."""

    __slots__ = (
        "_definition_json",
        "_runtime_json",
        "_assets",
        "_spec_items",
        "_sealed",
    )

    def __setattr__(self, name, value):
        if getattr(self, "_sealed", False):
            raise AttributeError("Verified Sampler runtime bundle is immutable.")
        object.__setattr__(self, name, value)

    def __init__(self, definition, runtime, assets, spec, *, token):
        if token is not _VERIFIED_SAMPLER_RUNTIME_BUNDLE_TOKEN:
            raise TypeError("Verified Sampler runtime bundle is Engine-owned.")
        self._definition_json = strict_json.dumps(
            definition,
            sort_keys=True,
            separators=(",", ":"),
        )
        self._runtime_json = strict_json.dumps(
            runtime,
            sort_keys=True,
            separators=(",", ":"),
        )
        self._assets = tuple(sorted(assets.items()))
        self._spec_items = tuple(
            (
                key,
                tuple(value) if isinstance(value, (list, tuple)) else value,
            )
            for key, value in sorted(spec.items())
        )
        object.__setattr__(self, "_sealed", True)

    def material(self):
        return (
            strict_json.loads(self._definition_json),
            strict_json.loads(self._runtime_json),
            dict(self._assets),
            {
                key: tuple(value) if isinstance(value, tuple) else value
                for key, value in self._spec_items
            },
        )


def verify_sampler_runtime_bundle_authority(definition):
    runtime, assets, spec = verify_sampler_runtime_bundle(definition)
    return _VerifiedSamplerRuntimeBundle(
        definition,
        runtime,
        assets,
        spec,
        token=_VERIFIED_SAMPLER_RUNTIME_BUNDLE_TOKEN,
    )


def verify_managed_sampler_runtime_bundle_authority(
    release_root,
    definition,
    *,
    expected_identity=None,
    expected_version=None,
):
    """Verify exact archive ownership before reading frozen Sampler assets."""

    execution_records.verify_sampler_record(
        release_root,
        definition,
        expected_identity=expected_identity,
        expected_version=expected_version,
    )
    runtime, assets, spec = verify_sampler_runtime_bundle(definition)
    return _VerifiedSamplerRuntimeBundle(
        definition,
        runtime,
        assets,
        spec,
        token=_VERIFIED_SAMPLER_RUNTIME_BUNDLE_TOKEN,
    )


def sampler_runtime_bundle_material(authority):
    if type(authority) is not _VerifiedSamplerRuntimeBundle:
        raise TypeError("Verified Sampler runtime bundle is Engine-owned.")
    return authority.material()


def resolve_verified_sampler_output_contracts(
    authority, parameters, source_schema=None
):
    definition, runtime, _assets, _spec = sampler_runtime_bundle_material(authority)
    config = definition["config"]
    if not isinstance(config, Mapping) or not isinstance(parameters, Mapping):
        raise ValueError("Sampler config and parameters must be objects.")
    effective_parameters = {**dict(config), **dict(parameters)}
    validate_json_value(
        effective_parameters,
        definition["parameterSchema"],
        path="sampler.parameters",
    )
    effective_parameters = canonical_sampler_parameters(effective_parameters)
    return resolve_sampler_output_contracts(
        definition,
        effective_parameters,
        source_schema,
        runtime["protocol"],
    )


def verified_sampler_required_capabilities(authority):
    _definition, _runtime, _assets, spec = sampler_runtime_bundle_material(authority)
    return tuple(spec["requiredCapabilities"])


__all__ = (
    "resolve_verified_sampler_output_contracts",
    "sampler_runtime_bundle_material",
    "verified_sampler_required_capabilities",
    "verify_managed_sampler_runtime_bundle_authority",
    "verify_sampler_runtime_bundle_authority",
)
