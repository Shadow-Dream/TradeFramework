#!/usr/bin/env python3
"""One explicit schema boundary for all active Engine control resources."""

from __future__ import annotations

import os
import shutil
import stat
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from engine.core import clock as engine_clock
from engine.archive import version as version_archive
from engine.contracts import strict_json


CONTROL_SCHEMA_VERSION = 9
MARKER_NAME = "control-schema.json"
ARCHIVE_DIRECTORY = "_incompatible_archives"
PRESERVED_CONTROL_NAMES = {
    "auth",
    ARCHIVE_DIRECTORY,
    MARKER_NAME,
    ".engine-owner.json",
    ".engine-owner.lock",
    ".engine-database.lock",
    ".control.lock",
}

__all__ = (
    "ARCHIVE_DIRECTORY",
    "CONTROL_SCHEMA_VERSION",
    "MARKER_NAME",
    "prepare",
)


def _load_json_file(path, default):
    path = Path(path)
    if not path.exists():
        return default
    with path.open(encoding="utf-8") as handle:
        return strict_json.load(handle)


def _atomic_write_json(path, payload):
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{target.name}.", suffix=".tmp", dir=str(target.parent)
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(strict_json.dumps(payload, indent=2))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, target)
        directory_descriptor = os.open(
            target.parent, os.O_RDONLY | os.O_DIRECTORY
        )
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    finally:
        if os.path.exists(temporary_name):
            os.unlink(temporary_name)


def _is_current_marker(marker):
    if not isinstance(marker, dict) or set(marker) != {
        "schemaVersion", "activatedAt", "previousArchive",
    }:
        return False
    if marker["schemaVersion"] != CONTROL_SCHEMA_VERSION:
        return False
    if not isinstance(marker["activatedAt"], str) or not marker["activatedAt"]:
        return False
    try:
        activated_at = datetime.fromisoformat(marker["activatedAt"].replace("Z", "+00:00"))
    except ValueError:
        return False
    if activated_at.tzinfo is None or activated_at.utcoffset() != timezone.utc.utcoffset(
        activated_at
    ):
        return False
    return marker["previousArchive"] is None or (
        isinstance(marker["previousArchive"], str) and bool(marker["previousArchive"])
    )


def _is_within(path, parent):
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _copy(source, destination, exclusions=()):
    if source in exclusions:
        return
    metadata = source.lstat()
    if stat.S_ISLNK(metadata.st_mode):
        raise ValueError(f"Control resources may not contain symbolic links: {source}")
    if stat.S_ISDIR(metadata.st_mode):
        destination.mkdir(parents=True, exist_ok=False)
        for child in source.iterdir():
            _copy(child, destination / child.name, exclusions)
        shutil.copystat(source, destination, follow_symlinks=False)
    elif stat.S_ISREG(metadata.st_mode):
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination, follow_symlinks=False)
    else:
        raise ValueError(
            f"Control resources may contain only regular files and directories: {source}"
        )


def _remove(source, exclusions=()):
    if source in exclusions:
        return
    if not source.exists():
        return
    if source.is_symlink():
        raise ValueError(f"Control resources may not contain symbolic links: {source}")
    if source.is_dir() and exclusions:
        for child in tuple(source.iterdir()):
            _remove(child, exclusions)
        if any(source.iterdir()):
            return
        source.rmdir()
        return
    if source.is_dir():
        version_archive.discard_archive(source)
        return
    source.chmod(source.stat().st_mode | stat.S_IWUSR)
    source.unlink()


def _configured_roots(config):
    roots = {}
    for area in ("control", "release", "live"):
        raw = Path(config[f"{area}Root"]).expanduser().absolute()
        if raw.is_symlink():
            raise ValueError(f"Configured {area} root may not be a symbolic link: {raw}")
        roots[area] = raw.resolve()
    if len(set(roots.values())) != len(roots):
        raise ValueError("Control, Release, and Live roots must resolve to distinct directories.")
    return roots


def _active_entries(config, roots=None):
    roots = roots or _configured_roots(config)
    entries = []
    configured_roots = set(roots.values())
    for area, root in roots.items():
        if not root.exists():
            continue
        if root.is_symlink() or not root.is_dir():
            raise ValueError(f"Configured {area} root must be a real directory: {root}")
        for path in root.iterdir():
            if area == "control" and path.name in PRESERVED_CONTROL_NAMES:
                continue
            # A nested configured root owns its own contents.  It must never be
            # copied through its parent area (the default Control root is
            # Release/_control).
            if path in configured_roots:
                continue
            exclusions = tuple(
                nested_root
                for nested_root in configured_roots
                if nested_root != root and _is_within(nested_root, path)
            )
            entries.append((area, path, exclusions))
    return entries


def prepare(config):
    """Archive the previous active schema wholesale; never interpret its records."""
    roots = _configured_roots(config)
    control_root = roots["control"]
    control_root.mkdir(parents=True, exist_ok=True)
    marker_path = control_root / MARKER_NAME
    try:
        marker = _load_json_file(marker_path, {})
    except ValueError:
        marker = {}
    if _is_current_marker(marker):
        return {"changed": False, "schemaVersion": CONTROL_SCHEMA_VERSION}

    entries = _active_entries(config, roots)
    archive_root = control_root / ARCHIVE_DIRECTORY / "control-schema"
    archive_root.mkdir(parents=True, exist_ok=True)
    archived_to = None
    if entries:
        archived_at = engine_clock.utc_now()
        stamp = datetime.fromisoformat(
            archived_at.removesuffix("Z") + "+00:00"
        ).strftime("%Y%m%dT%H%M%S%fZ")
        destination = archive_root / stamp
        staging = version_archive.staging_directory(archive_root)
        try:
            for area, source, exclusions in entries:
                _copy(source, staging / area / source.name, exclusions)
            (staging / "archive.json").write_text(strict_json.dumps({
                "resourceType": "incompatible-control-schema",
                "foundSchemaVersion": marker.get("schemaVersion") if isinstance(marker, dict) else None,
                "requiredSchemaVersion": CONTROL_SCHEMA_VERSION,
                "archivedAt": archived_at,
            }, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            digest = version_archive.content_digest(version_archive.file_manifest(staging))
            version_archive.seal_directory(
                staging,
                destination,
                managed_root=control_root,
                resource_type="incompatible-control-schema",
                resource_id="trade-engine-control",
                version=stamp,
                digest=digest,
            )
            archived_to = str(destination)
        except BaseException:
            if staging.exists():
                version_archive.discard_archive(staging)
            raise
        for _area, source, exclusions in entries:
            _remove(source, exclusions)

    _atomic_write_json(marker_path, {
        "schemaVersion": CONTROL_SCHEMA_VERSION,
        "activatedAt": engine_clock.utc_now(),
        "previousArchive": archived_to,
    })
    return {
        "changed": True,
        "schemaVersion": CONTROL_SCHEMA_VERSION,
        "previousArchive": archived_to,
    }
