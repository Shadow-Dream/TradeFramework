"""Canonical immutable locations for Sampler-only Sample Results."""

from __future__ import annotations

import os
from pathlib import Path

from engine.archive import version as version_archive
from engine.contracts import sample_result as sample_result_contracts


ARCHIVE_DIRECTORY_NAME = "_sample_results"
RESULT_FILE_NAME = "sample-result.json"
MANIFEST_FILE_NAME = "sample-result-manifest.json"


def archive_root(release_root):
    return Path(release_root) / ARCHIVE_DIRECTORY_NAME


def archive_directory(release_root, sample_result_id, *, label):
    sample_result_contracts.require_sample_result_id(sample_result_id)
    digest = sample_result_id.removeprefix("sha256:")
    return version_archive.resolve_managed_path(
        release_root,
        archive_root(release_root) / digest,
        label=label,
    )


def sealed_archive_identity(directory, *, label):
    directory = Path(directory)
    result = directory / RESULT_FILE_NAME
    manifest = directory / MANIFEST_FILE_NAME
    paths = {"directory": directory, "result": result, "manifest": manifest}
    if (
        any(path.is_symlink() for path in paths.values())
        or not directory.is_dir()
        or not result.is_file()
        or not manifest.is_file()
        or set(directory.iterdir()) != {result, manifest}
        or any(path.stat().st_mode & 0o222 for path in paths.values())
    ):
        raise ValueError(f"{label} is invalid.")
    evidence = {}
    for name, path in paths.items():
        state = os.stat(path, follow_symlinks=False)
        evidence[name] = {
            "device": state.st_dev,
            "inode": state.st_ino,
            "mode": state.st_mode,
            "size": state.st_size,
            "modifiedNs": state.st_mtime_ns,
            "changedNs": state.st_ctime_ns,
        }
    return evidence


__all__ = (
    "ARCHIVE_DIRECTORY_NAME",
    "MANIFEST_FILE_NAME",
    "RESULT_FILE_NAME",
    "archive_directory",
    "archive_root",
    "sealed_archive_identity",
)
