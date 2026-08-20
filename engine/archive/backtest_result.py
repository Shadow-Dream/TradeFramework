"""Canonical immutable filesystem locations for Backtest Results."""

import os
from pathlib import Path

from engine.archive import version as version_archive
from engine.core import resource_ids


ARCHIVE_DIRECTORY_NAME = "_backtests"
RESULT_FILE_NAME = "result.json"
MANIFEST_FILE_NAME = "result-manifest.json"


def archive_root(release_root):
    """Return the one Engine-owned root containing immutable Result archives."""

    return Path(release_root) / ARCHIVE_DIRECTORY_NAME


def archive_directory(release_root, backtest_id, *, label):
    """Resolve one Result directory beneath the managed release root."""

    if (
        not isinstance(backtest_id, str)
        or not backtest_id.startswith("bt_")
        or not resource_ids.is_resource_id(backtest_id)
    ):
        raise ValueError(
            "Backtest Result ID must be an Engine-issued Backtest resource ID."
        )
    return version_archive.resolve_managed_path(
        release_root,
        archive_root(release_root) / backtest_id,
        label=label,
    )


def result_path(release_root, backtest_id, *, label):
    return archive_directory(
        release_root,
        backtest_id,
        label=label,
    ) / RESULT_FILE_NAME


def sealed_archive_identity(directory, *, label):
    """Prove one exact sealed Result directory and return stable inode evidence."""

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
    "result_path",
    "sealed_archive_identity",
)
