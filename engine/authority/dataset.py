#!/usr/bin/env python3
"""Verified immutable Dataset storage authority."""

from __future__ import annotations

import copy
from collections.abc import Mapping
from pathlib import Path

from engine.archive import dataset as dataset_archive

_VERIFIED_DATASET_STORAGE_TOKEN = object()


class _VerifiedDatasetStorage:
    """Nominal proof for one sealed Dataset container and its exact metadata."""

    __slots__ = (
        "_dataset_id",
        "_dataset_version_id",
        "_storage",
        "_content_hash",
        "_capabilities",
        "_semantic_validated_capabilities",
        "_root",
    )

    def __init__(
        self,
        *,
        dataset_id,
        dataset_version_id,
        storage,
        content_hash,
        capabilities,
        semantic_validated_capabilities,
        root,
        _token,
    ):
        if _token is not _VERIFIED_DATASET_STORAGE_TOKEN:
            raise TypeError("Verified Dataset storage authority is Engine-owned.")
        self._dataset_id = dataset_id
        self._dataset_version_id = dataset_version_id
        self._storage = copy.deepcopy(storage)
        self._content_hash = content_hash
        self._capabilities = copy.deepcopy(capabilities)
        self._semantic_validated_capabilities = frozenset(
            semantic_validated_capabilities
        )
        self._root = Path(root)

    def _material(self):
        return (
            self._dataset_id,
            self._dataset_version_id,
            copy.deepcopy(self._storage),
            self._content_hash,
            copy.deepcopy(self._capabilities),
            self._semantic_validated_capabilities,
            self._root,
        )


def verify_dataset_storage_authority(
    *,
    dataset_id,
    dataset_version_id,
    storage,
    content_hash,
    capabilities,
    manifest,
    semantic_capabilities=None,
):
    """Verify and bind the exact immutable Dataset storage used by a Runtime."""

    if not isinstance(dataset_id, str) or not dataset_id:
        raise ValueError("Dataset ID must be a non-empty string.")
    if not isinstance(dataset_version_id, str) or not dataset_version_id:
        raise ValueError("Dataset Version ID must be a non-empty string.")
    if not isinstance(storage, Mapping) or set(storage) != {"type", "uri"}:
        raise ValueError("Dataset storage descriptor is invalid.")
    if storage["type"] != "directory" or not isinstance(storage["uri"], str):
        raise ValueError("Dataset storage must identify an immutable directory.")
    if not isinstance(content_hash, str) or not content_hash:
        raise ValueError("Dataset content hash must be a non-empty string.")
    if not isinstance(capabilities, Mapping) or not isinstance(manifest, Mapping):
        raise ValueError("Dataset capabilities and manifest must be objects.")
    if (
        manifest.get("datasetId") != dataset_id
        or manifest.get("datasetVersionId") != dataset_version_id
        or manifest.get("storage") != storage
        or manifest.get("contentHash") != content_hash
        or manifest.get("capabilities") != capabilities
    ):
        raise ValueError("Dataset execution metadata does not match its sealed manifest.")
    raw_root = Path(storage["uri"]).expanduser()
    if raw_root.is_symlink():
        raise ValueError("Dataset archive root may not be a symbolic link.")
    root = raw_root.resolve()
    dataset_archive.verify_sealed_container(
        root,
        manifest,
        content_hash,
        semantic_capabilities=semantic_capabilities,
    )
    semantic_capabilities = dataset_archive.semantic_capabilities_to_verify(
        manifest,
        semantic_capabilities,
    )
    return _VerifiedDatasetStorage(
        dataset_id=dataset_id,
        dataset_version_id=dataset_version_id,
        storage=storage,
        content_hash=content_hash,
        capabilities=capabilities,
        semantic_validated_capabilities=semantic_capabilities,
        root=root,
        _token=_VERIFIED_DATASET_STORAGE_TOKEN,
    )


def verify_dataset_version_storage_authority(
    release_root,
    version,
    *,
    semantic_capabilities=None,
):
    """Bind a complete frozen Version only from its managed sealed archive."""
    version = dataset_archive.validate_sealed_version_descriptor(version)
    dataset_archive.resolve_version_storage_root(release_root, version)
    authority = verify_dataset_storage_authority(
        dataset_id=version["datasetId"],
        dataset_version_id=version["datasetVersionId"],
        storage=version["storage"],
        content_hash=version["contentHash"],
        capabilities=version["capabilities"],
        manifest=version["manifest"],
        semantic_capabilities=semantic_capabilities,
    )
    return version, authority


def verified_dataset_storage_material(authority):
    if type(authority) is not _VerifiedDatasetStorage:
        raise TypeError("Verified Dataset storage authority is Engine-owned.")
    return authority._material()
