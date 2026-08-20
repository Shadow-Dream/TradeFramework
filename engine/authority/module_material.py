"""Verified source material for an immutable Module implementation archive."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path


__all__ = (
    "directory_tree_fingerprint",
    "module_source_material",
    "verify_module_source_material",
)


_VERIFIED_MODULE_SOURCE_TOKEN = object()


class ModuleSourceMaterial:
    """Nominal proof of one immutable Module source tree and its fingerprint."""

    __slots__ = ("_source", "_fingerprint", "_digest")

    def __init__(self, source, fingerprint, *, _token):
        if _token is not _VERIFIED_MODULE_SOURCE_TOKEN:
            raise TypeError("Verified Module source material is Engine-owned.")
        self._source = Path(source)
        self._fingerprint = tuple(fingerprint)
        self._digest = _directory_fingerprint_hash(self._fingerprint)

    def _material(self):
        return self._source, self._fingerprint, self._digest


def verify_module_source_material(definition):
    """Verify the executable paths and fingerprint one Module archive tree."""

    source = _module_archive_source(definition)
    fingerprint = directory_tree_fingerprint(source)
    return ModuleSourceMaterial(
        source,
        fingerprint,
        _token=_VERIFIED_MODULE_SOURCE_TOKEN,
    )


def module_source_material(material):
    """Return the immutable source tuple captured by a nominal material proof."""

    if type(material) is not ModuleSourceMaterial:
        raise TypeError("Verified Module source material is Engine-owned.")
    return material._material()


def _module_archive_source(definition):
    parameters = definition["parameters"]
    mode = definition["activationMode"]
    source_text = definition["archive"]["root"]
    raw_source = Path(source_text)
    if raw_source.is_symlink():
        raise ValueError(f"Module archive root may not be a symbolic link: {raw_source}")
    source = raw_source.resolve()
    if not source.is_dir():
        raise ValueError(f"Module archive root does not exist: {source}")
    if mode == "ProcessRunner":
        _validate_process_runner_archive_paths(parameters, source)
    return source


def _validate_process_runner_archive_paths(parameters, archive_root):
    """Require every declared ProcessRunner execution path to belong to its Version."""

    archive_root = Path(archive_root).resolve()

    def require_archived_path(value, field, *, directory=False, executable=False):
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"ProcessRunner {field} must be a non-empty archived path.")
        raw_path = Path(value)
        if not raw_path.is_absolute():
            raise ValueError(f"ProcessRunner {field} must be rooted in its Module archive.")
        path = raw_path.resolve()
        try:
            path.relative_to(archive_root)
        except ValueError as exc:
            raise ValueError(
                f"ProcessRunner {field} is outside its immutable Module archive."
            ) from exc
        if directory:
            if not path.is_dir():
                raise ValueError(f"ProcessRunner {field} is not an archived directory.")
        elif not path.is_file():
            raise ValueError(f"ProcessRunner {field} is not an archived file.")
        if executable and not os.access(path, os.X_OK):
            raise ValueError(f"ProcessRunner {field} is not executable.")
        return path

    require_archived_path(parameters["command"], "command", executable=True)
    working_directory = parameters.get("workingDirectory")
    if working_directory is not None:
        require_archived_path(working_directory, "workingDirectory", directory=True)
    for index, argument in enumerate(parameters["arguments"]):
        if not isinstance(argument, str):
            continue
        candidate = argument.split("=", 1)[-1] if "=" in argument else argument
        path = Path(candidate)
        if path.is_absolute():
            require_archived_path(candidate, f"arguments[{index}]")
        elif ".." in path.parts:
            raise ValueError(
                f"ProcessRunner arguments[{index}] may not traverse outside its Module archive."
            )


def directory_tree_fingerprint(root):
    """Return the exact file, directory, mode, size, and digest tree projection."""

    root = Path(root)
    if root.is_symlink() or not root.is_dir():
        raise ValueError(f"Module bundle root must be a real directory: {root}")
    result = [("directory", ".", root.stat().st_mode & 0o777, 0, "")]
    for path in sorted(
        root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()
    ):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            raise ValueError(f"Module bundles may not contain symbolic links: {relative}")
        mode = path.stat().st_mode & 0o777
        if path.is_dir():
            result.append(("directory", relative, mode, 0, ""))
        elif path.is_file():
            result.append(
                ("file", relative, mode, path.stat().st_size, _sha256_file(path))
            )
        else:
            raise ValueError(
                f"Module bundles may contain only files and directories: {relative}"
            )
    return tuple(result)


def _sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _directory_fingerprint_hash(fingerprint):
    digest = hashlib.sha256()
    for entry in fingerprint:
        digest.update(repr(entry).encode("utf-8"))
        digest.update(b"\0")
    return "sha256:" + digest.hexdigest()
