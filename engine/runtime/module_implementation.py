"""Materialize verified Module implementations into isolated execution roots."""

from __future__ import annotations

import copy
import os
from pathlib import Path
import shutil
import stat
import tempfile
import threading
import weakref

from engine.authority.module_definition import (
    require_verified_module_definition_authority,
    verified_module_definition_material,
)
import engine.authority.module_material as module_material


__all__ = (
    "materialized_module_definition_material",
    "materialize_verified_module_definition",
)


_MATERIALIZED_MODULE_DEFINITION_TOKEN = object()


class _MaterializedModuleDefinition:
    """Nominal proof that a verified Module was isolated into an execution root."""

    __slots__ = ("_definition_authority_ref", "_definition", "_materialized_root")

    def __init__(self, definition_authority, definition, materialized_root, *, _token):
        if _token is not _MATERIALIZED_MODULE_DEFINITION_TOKEN:
            raise TypeError("Materialized Module Definition is Engine-owned.")
        self._definition_authority_ref = weakref.ref(definition_authority)
        self._definition = definition
        self._materialized_root = str(materialized_root)

    def _material(self, definition_authority):
        if self._definition_authority_ref() is not definition_authority:
            raise TypeError(
                "Materialized Module Definition does not match its authority."
            )
        return copy.deepcopy(self._definition)

    def _execution_root(self):
        return self._materialized_root


def materialized_module_definition_material(material, definition_authority):
    """Return a detached Definition only from its matching materialization proof."""

    require_verified_module_definition_authority(definition_authority)
    if type(material) is not _MaterializedModuleDefinition:
        raise TypeError("Materialized Module Definition is Engine-owned.")
    return material._material(definition_authority)


class _AuthorityMaterializationCache:
    __slots__ = ("lock", "source_material", "isolated_materials")

    def __init__(self):
        self.lock = threading.Lock()
        self.source_material = None
        self.isolated_materials = {}


_AUTHORITY_CACHES = weakref.WeakKeyDictionary()
_AUTHORITY_CACHES_LOCK = threading.Lock()


def _authority_cache(authority):
    require_verified_module_definition_authority(authority)
    with _AUTHORITY_CACHES_LOCK:
        cached = _AUTHORITY_CACHES.get(authority)
    if cached is not None:
        return cached, None

    definition = verified_module_definition_material(authority)
    candidate = _AuthorityMaterializationCache()
    with _AUTHORITY_CACHES_LOCK:
        cached = _AUTHORITY_CACHES.get(authority)
        if cached is None:
            _AUTHORITY_CACHES[authority] = candidate
            cached = candidate
            return cached, definition
    return cached, None


def materialize_verified_module_definition(
    authority,
    execution_root,
    namespace="modules",
):
    """Materialize one verified Definition under its isolated execution root."""

    cache, definition = _authority_cache(authority)
    if not execution_root:
        raise ValueError("Module execution requires an isolated execution root.")
    if not isinstance(namespace, str) or not namespace:
        raise ValueError(
            "Module execution namespace must be a non-empty relative path."
        )
    namespace_path = Path(namespace)
    if (
        namespace_path.is_absolute()
        or not namespace_path.parts
        or ".." in namespace_path.parts
    ):
        raise ValueError(
            "Module execution namespace must stay within its execution root."
        )
    raw_execution_root = Path(execution_root)
    raw_execution_root.mkdir(parents=True, exist_ok=True)
    if raw_execution_root.is_symlink() or not raw_execution_root.is_dir():
        raise ValueError("Module execution root must be an owned directory.")
    resolved_execution_root = raw_execution_root.resolve(strict=True)
    root_stat = resolved_execution_root.stat()
    namespace_root = resolved_execution_root.joinpath(namespace_path)
    current = resolved_execution_root
    for part in namespace_path.parts:
        current = current / part
        if current.is_symlink():
            raise ValueError(
                "Module execution namespace may not traverse a symbolic link."
            )
    try:
        namespace_root.resolve(strict=False).relative_to(resolved_execution_root)
    except ValueError as exc:
        raise ValueError(
            "Module execution namespace must stay within its execution root."
        ) from exc
    cache_key = (
        str(resolved_execution_root),
        root_stat.st_dev,
        root_stat.st_ino,
        str(namespace_path),
    )
    with cache.lock:
        cached = cache.isolated_materials.get(cache_key)
        if cached is not None:
            cached_root = Path(cached._execution_root())
            if cache.source_material is None:
                raise RuntimeError(
                    "Cached Module material is missing its verified source proof."
                )
            _source, source_fingerprint, _source_digest = (
                module_material.module_source_material(cache.source_material)
            )
            if (
                cached_root.is_dir()
                and not cached_root.is_symlink()
                and module_material.directory_tree_fingerprint(cached_root)
                == source_fingerprint
            ):
                return cached
            raise ValueError(
                "Cached Module execution bundle does not match its archive."
            )

        if cache.source_material is None:
            if definition is None:
                definition = verified_module_definition_material(authority)
            cache.source_material = module_material.verify_module_source_material(
                definition
            )
        if definition is None:
            definition = verified_module_definition_material(authority)
        source, source_fingerprint, source_digest = (
            module_material.module_source_material(cache.source_material)
        )
        isolated, materialized_root = _materialize_definition(
            definition,
            source,
            source_fingerprint,
            source_digest,
            resolved_execution_root,
            namespace_path,
        )
        materialized = _MaterializedModuleDefinition(
            authority,
            isolated,
            materialized_root,
            _token=_MATERIALIZED_MODULE_DEFINITION_TOKEN,
        )
        cache.isolated_materials[cache_key] = materialized
        return materialized


def _materialize_definition(
    definition,
    source,
    source_fingerprint,
    source_digest,
    execution_root,
    namespace,
):
    isolated = definition
    parameters = isolated["parameters"]
    mode = isolated["activationMode"]
    digest = source_digest.removeprefix("sha256:")
    target = Path(execution_root) / namespace / digest
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.is_symlink():
        raise ValueError(f"Module execution bundle may not be a symbolic link: {target}")
    if target.exists():
        if (
            not target.is_dir()
            or module_material.directory_tree_fingerprint(target)
            != source_fingerprint
        ):
            raise ValueError(
                "Existing Module execution bundle does not match its archive."
            )
    else:
        staging = Path(
            tempfile.mkdtemp(prefix=".module-bundle-", dir=target.parent)
        )
        try:
            shutil.copytree(source, staging, dirs_exist_ok=True)
            if (
                module_material.directory_tree_fingerprint(staging)
                != source_fingerprint
            ):
                raise ValueError(
                    "Copied Module execution bundle does not match its archive."
                )
            try:
                os.replace(staging, target)
            except OSError:
                if (
                    not target.is_dir()
                    or target.is_symlink()
                    or module_material.directory_tree_fingerprint(target)
                    != source_fingerprint
                ):
                    raise
        except BaseException:
            cleanup_errors = []
            if staging.exists():
                try:
                    _discard_execution_staging(staging)
                except BaseException as cleanup_error:
                    cleanup_errors.append(cleanup_error)
            raise
        if staging.exists():
            _discard_execution_staging(staging)

    def rebase_archived_path(value):
        if not isinstance(value, str):
            return value
        path = Path(value)
        if not path.is_absolute():
            return value
        try:
            relative = path.resolve().relative_to(source)
        except ValueError:
            return value
        rebased_path = target / relative
        try:
            rebased_path.resolve(strict=True).relative_to(
                target.resolve(strict=True)
            )
        except (OSError, ValueError) as exc:
            raise ValueError(
                "Materialized ProcessRunner path escapes its isolated "
                "Module bundle."
            ) from exc
        return str(rebased_path)

    def rebase_argument(value):
        if not isinstance(value, str):
            return value
        prefix, separator, candidate = value.partition("=")
        if separator and candidate:
            rebased_candidate = rebase_archived_path(candidate)
            if rebased_candidate != candidate:
                return f"{prefix}={rebased_candidate}"
        return rebase_archived_path(value)

    isolated["archive"] = {
        **copy.deepcopy(isolated["archive"]),
        "root": str(target),
    }
    if mode == "ProcessRunner":
        isolated["parameters"] = copy.deepcopy(parameters)
        isolated["parameters"]["command"] = rebase_archived_path(
            parameters["command"]
        )
        isolated["parameters"]["arguments"] = [
            rebase_argument(argument)
            for argument in parameters["arguments"]
        ]
        if "workingDirectory" in parameters:
            isolated["parameters"]["workingDirectory"] = rebase_archived_path(
                parameters["workingDirectory"]
            )
        isolated["parameters"].setdefault("workingDirectory", str(target))
    return isolated, target


def _discard_execution_staging(root):
    """Remove only a temporary execution bundle owned by this materializer."""

    root = Path(root)
    if root.is_symlink():
        raise ValueError(f"Module execution staging may not be a symbolic link: {root}")
    if not root.exists():
        return
    if not root.is_dir():
        raise ValueError(f"Module execution staging must be a directory: {root}")

    def make_writable_and_retry(function, path, _error):
        candidate = Path(path)
        candidate.chmod(candidate.stat().st_mode | stat.S_IWUSR | stat.S_IXUSR)
        function(path)

    parent = root.parent
    shutil.rmtree(root, onerror=make_writable_and_retry)
    descriptor = os.open(
        parent,
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
