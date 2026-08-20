#!/usr/bin/env python3
"""Engine-owned filesystem staging authority for Dataset publication."""

from __future__ import annotations

import os
import secrets
import stat

from engine.archive import dataset as dataset_archive
from engine.archive import version as version_archive
from engine.contracts import strict_json
from engine.core import resource_ids


_AUTHORITY_TOKEN = object()
_MARKER_NAME = ".trade-dataset-staging-authority.json"


class _DatasetStagingAuthority:
    __slots__ = (
        "_dataset_id",
        "_versions_root",
        "_authority_root",
        "_content_root",
        "_marker_path",
        "_nonce",
        "_authority_identity",
        "_content_identity",
        "_marker_identity",
    )

    def __init__(self, *, token, **material):
        if token is not _AUTHORITY_TOKEN:
            raise TypeError("Dataset staging authority is Engine-owned.")
        for name, value in material.items():
            setattr(self, f"_{name}", value)

    @property
    def path(self):
        return self._content_root

    def _material(self):
        return (
            self._dataset_id,
            self._versions_root,
            self._authority_root,
            self._content_root,
            self._marker_path,
            self._nonce,
            self._authority_identity,
            self._content_identity,
            self._marker_identity,
        )


def _filesystem_identity(path):
    metadata = os.lstat(path)
    return metadata.st_dev, metadata.st_ino, metadata.st_mode


def _material(staging):
    if type(staging) is not _DatasetStagingAuthority:
        raise TypeError("Dataset publication requires Engine-owned staging authority.")
    return staging._material()


def require_dataset_staging_authority(staging):
    """Reject anything except an unforgeable Engine staging proof."""
    _material(staging)
    return staging


def require_dataset_staging(config, dataset_id, staging):
    (
        staged_dataset_id,
        versions_root,
        authority_root,
        content_root,
        marker_path,
        nonce,
        authority_identity,
        content_identity,
        marker_identity,
    ) = _material(staging)
    expected_versions_root = version_archive.resolve_managed_path(
        config["releaseRoot"],
        dataset_archive.repository_root(config["releaseRoot"])
        / dataset_id
        / "versions",
        label="Dataset versions root",
    )
    if staged_dataset_id != dataset_id or versions_root != expected_versions_root:
        raise ValueError("Dataset staging authority belongs to a different Dataset.")
    if (
        authority_root.parent != expected_versions_root
        or content_root.parent != authority_root
        or marker_path.parent != authority_root
        or marker_path.name != _MARKER_NAME
    ):
        raise ValueError("Dataset staging authority has an invalid managed location.")
    current_authority = _filesystem_identity(authority_root)
    current_content = _filesystem_identity(content_root)
    current_marker = _filesystem_identity(marker_path)
    if (
        current_authority != authority_identity
        or current_content != content_identity
        or current_marker != marker_identity
        or not stat.S_ISDIR(current_authority[2])
        or not stat.S_ISDIR(current_content[2])
        or not stat.S_ISREG(current_marker[2])
    ):
        raise ValueError("Dataset staging authority filesystem identity changed.")
    if set(authority_root.iterdir()) != {content_root, marker_path}:
        raise ValueError("Dataset staging authority root contains unexpected entries.")
    try:
        marker = strict_json.loads(marker_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ValueError("Dataset staging authority marker is invalid.") from error
    if marker != {"schemaVersion": 1, "datasetId": dataset_id, "nonce": nonce}:
        raise ValueError("Dataset staging authority marker does not match its proof.")
    return content_root


def create_dataset_staging(config, dataset_id):
    if not isinstance(dataset_id, str) or not dataset_id:
        raise ValueError("Dataset staging ID must be a non-empty string.")
    if resource_ids.normalize_resource_id(dataset_id) != dataset_id:
        raise ValueError("Dataset staging ID must already be normalized.")
    versions_root = version_archive.resolve_managed_path(
        config["releaseRoot"],
        dataset_archive.repository_root(config["releaseRoot"])
        / dataset_id
        / "versions",
        label="Dataset versions root",
    )
    authority_root = version_archive.staging_directory(versions_root)
    content_root = authority_root / "content"
    marker_path = authority_root / _MARKER_NAME
    nonce = secrets.token_hex(32)
    try:
        content_root.mkdir(exist_ok=False)
        marker_path.write_text(
            strict_json.dumps(
                {"schemaVersion": 1, "datasetId": dataset_id, "nonce": nonce},
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        return _DatasetStagingAuthority(
            token=_AUTHORITY_TOKEN,
            dataset_id=dataset_id,
            versions_root=versions_root,
            authority_root=authority_root,
            content_root=content_root,
            marker_path=marker_path,
            nonce=nonce,
            authority_identity=_filesystem_identity(authority_root),
            content_identity=_filesystem_identity(content_root),
            marker_identity=_filesystem_identity(marker_path),
        )
    except BaseException as error:
        traceback = error.__traceback__
        try:
            version_archive.discard_archive(authority_root)
        except BaseException as cleanup_error:
            if error.__context__ is None:
                error.__context__ = cleanup_error
        raise error.with_traceback(traceback)


def discard_dataset_staging(staging):
    (
        _dataset_id,
        _versions_root,
        authority_root,
        _content_root,
        _marker_path,
        _nonce,
        authority_identity,
        _content_identity,
        _marker_identity,
    ) = _material(staging)
    if not authority_root.exists():
        return
    if _filesystem_identity(authority_root) != authority_identity:
        raise ValueError("Dataset staging authority root identity changed before cleanup.")
    version_archive.discard_archive(authority_root)
