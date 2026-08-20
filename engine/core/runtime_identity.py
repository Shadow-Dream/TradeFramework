"""Complete immutable identity of the Python Backtest runtime."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import site
import stat
import sys
import sysconfig
from functools import lru_cache
from pathlib import Path
from zoneinfo import TZPATH

from engine.core.build_identity import ENGINE_BUILD_ID


_MAX_EXTERNAL_INSTALLATION_NODES = 100_000
_MAX_EXTERNAL_INSTALLATION_BYTES = 512 * 1024 * 1024
_MAX_INSTALLATION_METADATA_BYTES = 1024 * 1024


def _python_distribution_paths():
    """Return the installation roots available to isolated Engine workers.

    Backtest workers deliberately receive neither the control process HOME nor
    its ambient PYTHONPATH.  Enumerating the ambient ``sys.path`` here would
    therefore make a frozen request depend on packages that the worker cannot
    import (most notably the control user's site-packages directory).
    """

    release_site = os.environ.get("TRADE_ENGINE_RUNTIME_SITE_PACKAGES")
    if release_site is not None:
        if not os.path.isabs(release_site):
            raise RuntimeError("Engine runtime site-packages authority is not absolute.")
        release_path = Path(release_site).resolve(strict=True)
        available = {
            Path(value).resolve(strict=True)
            for value in sys.path
            if value and Path(value).exists()
        }
        if release_path not in available:
            raise RuntimeError(
                "Engine runtime site-packages authority is not importable."
            )
        return (str(release_path),)

    candidates = []
    get_site_packages = getattr(site, "getsitepackages", None)
    if get_site_packages is not None:
        candidates.extend(get_site_packages())
    install_paths = sysconfig.get_paths()
    candidates.extend(
        install_paths.get(name) for name in ("purelib", "platlib")
    )
    result = []
    seen = set()
    for value in candidates:
        if not value:
            continue
        path = os.path.abspath(os.fspath(value))
        if path not in seen:
            seen.add(path)
            result.append(path)
    return tuple(result)


def _path_is_within(path: Path, roots: tuple[Path, ...]) -> bool:
    return any(path == root or root in path.parents for root in roots)


def _read_bounded_installation_text(path: Path) -> str:
    try:
        with path.open("rb") as handle:
            initial = os.fstat(handle.fileno())
            if not stat.S_ISREG(initial.st_mode):
                raise RuntimeError(
                    f"Backtest Python metadata is not a regular file: {path}"
                )
            if initial.st_size > _MAX_INSTALLATION_METADATA_BYTES:
                raise RuntimeError(
                    f"Backtest Python metadata exceeds its size limit: {path}"
                )
            payload = handle.read(_MAX_INSTALLATION_METADATA_BYTES + 1)
            completed = os.fstat(handle.fileno())
    except FileNotFoundError as exc:
        raise RuntimeError(
            f"Backtest Python metadata changed while reading: {path}"
        ) from exc
    if len(payload) > _MAX_INSTALLATION_METADATA_BYTES:
        raise RuntimeError(
            f"Backtest Python metadata exceeds its size limit: {path}"
        )
    if (
        completed.st_dev != initial.st_dev
        or completed.st_ino != initial.st_ino
        or completed.st_size != initial.st_size
        or completed.st_mtime_ns != initial.st_mtime_ns
        or completed.st_ctime_ns != initial.st_ctime_ns
    ):
        raise RuntimeError(
            f"Backtest Python metadata changed while reading: {path}"
        )
    try:
        return payload.decode("utf-8")
    except UnicodeError as exc:
        raise RuntimeError(
            f"Backtest Python metadata is not UTF-8: {path}"
        ) from exc


def _validate_installation_indirections(
    roots: tuple[Path, ...],
    authorized_roots: tuple[Path, ...],
) -> None:
    """Reject installation metadata that grants an unhashed external import root."""

    for root in roots:
        if not root.is_dir():
            continue
        egg_links = sorted(root.glob("*.egg-link"), key=lambda item: item.name)
        if egg_links:
            raise RuntimeError(
                "Backtest Python installation contains an unsupported editable "
                f"egg-link: {egg_links[0]}"
            )
        for direct_url_path in sorted(
            root.glob("*.dist-info/direct_url.json"),
            key=lambda item: item.as_posix(),
        ):
            try:
                direct_url = json.loads(
                    _read_bounded_installation_text(direct_url_path)
                )
            except (OSError, ValueError) as exc:
                raise RuntimeError(
                    "Backtest Python installation has unreadable direct-url "
                    f"metadata: {direct_url_path}"
                ) from exc
            if (
                isinstance(direct_url, dict)
                and isinstance(direct_url.get("dir_info"), dict)
                and direct_url["dir_info"].get("editable") is True
            ):
                raise RuntimeError(
                    "Backtest Python installation contains an unsupported editable "
                    f"distribution: {direct_url_path.parent.name}"
                )
        for pth_path in sorted(root.glob("*.pth"), key=lambda item: item.name):
            try:
                lines = _read_bounded_installation_text(pth_path).splitlines()
            except OSError as exc:
                raise RuntimeError(
                    f"Backtest Python path metadata is unreadable: {pth_path}"
                ) from exc
            for raw_line in lines:
                line = raw_line.strip()
                if not line or line.startswith("#"):
                    continue
                if line.startswith(("import ", "import\t")):
                    # A non-editable executable .pth hook is trusted installed
                    # code: both this line and every file under its site root are
                    # hashed below. Supported deployments may not use such a hook
                    # to expose another, undeclared import root.
                    if "__editable__" in line or pth_path.name.startswith(
                        "__editable__"
                    ):
                        raise RuntimeError(
                            "Backtest Python installation contains an unsupported "
                            f"editable path hook: {pth_path}"
                        )
                    continue
                target = (pth_path.parent / line).resolve()
                if target.exists() and not _path_is_within(
                    target,
                    authorized_roots,
                ):
                    raise RuntimeError(
                        "Backtest Python path metadata references an external import "
                        f"root: {pth_path} -> {target}"
                    )


def _update_path_token(digest, value) -> None:
    digest.update(os.fsencode(str(value).replace(os.sep, "/")))
    digest.update(b"\0")


def _consume_external_budget(budget, *, nodes=0, byte_count=0) -> None:
    budget["nodes"] += nodes
    budget["bytes"] += byte_count
    if (
        budget["nodes"] > _MAX_EXTERNAL_INSTALLATION_NODES
        or budget["bytes"] > _MAX_EXTERNAL_INSTALLATION_BYTES
    ):
        raise RuntimeError(
            "Backtest Python installation has an unbounded external symbolic-link "
            "target."
        )


def _hash_installation_file(digest, path, expected, *, external, budget) -> None:
    if external:
        _consume_external_budget(budget, nodes=1)
    try:
        with Path(path).open("rb") as handle:
            opened = os.fstat(handle.fileno())
            if (
                opened.st_dev != expected.st_dev
                or opened.st_ino != expected.st_ino
                or not stat.S_ISREG(opened.st_mode)
                or opened.st_size != expected.st_size
                or opened.st_mtime_ns != expected.st_mtime_ns
                or opened.st_ctime_ns != expected.st_ctime_ns
            ):
                raise RuntimeError(
                    f"Backtest Python installation changed while hashing: {path}"
                )
            while chunk := handle.read(1024 * 1024):
                if external:
                    _consume_external_budget(budget, byte_count=len(chunk))
                digest.update(chunk)
            completed = os.fstat(handle.fileno())
    except FileNotFoundError as exc:
        raise RuntimeError(
            f"Backtest Python installation changed while hashing: {path}"
        ) from exc
    if (
        completed.st_size != opened.st_size
        or completed.st_mtime_ns != opened.st_mtime_ns
        or completed.st_ctime_ns != opened.st_ctime_ns
    ):
        raise RuntimeError(
            f"Backtest Python installation changed while hashing: {path}"
        )
    digest.update(b"\0")


def _hash_installation_directory(
    digest,
    directory: Path,
    virtual_parts: tuple[str, ...],
    *,
    roots: tuple[Path, ...],
    ancestors: frozenset[tuple[int, int]],
    external_scope: bool,
    budget,
) -> None:
    try:
        initial = directory.stat()
    except FileNotFoundError as exc:
        raise RuntimeError(
            f"Backtest Python installation changed while hashing: {directory}"
        ) from exc
    if not stat.S_ISDIR(initial.st_mode):
        raise RuntimeError(
            f"Backtest Python installation root is not a directory: {directory}"
        )
    resolved_directory = directory.resolve(strict=True)
    current_external_scope = external_scope or not _path_is_within(
        resolved_directory,
        roots,
    )
    if current_external_scope:
        _consume_external_budget(budget, nodes=1)
    identity = (initial.st_dev, initial.st_ino)
    if identity in ancestors:
        digest.update(b"cycle\0")
        return
    try:
        with os.scandir(directory) as scan:
            entries = sorted(scan, key=lambda item: os.fsencode(item.name))
    except FileNotFoundError as exc:
        raise RuntimeError(
            f"Backtest Python installation changed while hashing: {directory}"
        ) from exc
    next_ancestors = ancestors | {identity}
    for entry in entries:
        if entry.name == "__pycache__":
            continue
        relative_parts = (*virtual_parts, entry.name)
        relative_text = "/".join(relative_parts)
        _update_path_token(digest, relative_text)
        try:
            is_link = entry.is_symlink()
            raw_target = os.readlink(entry.path) if is_link else None
            followed = entry.stat(follow_symlinks=True)
        except FileNotFoundError as exc:
            raise RuntimeError(
                f"Backtest Python installation contains a broken entry: {entry.path}"
            ) from exc
        if is_link:
            digest.update(b"symlink\0")
            _update_path_token(digest, raw_target)
        # A non-symlink child of an authorized directory cannot cross an
        # installation-root boundary.  Resolving every ordinary entry walks
        # its complete ancestor chain and used to dominate the runtime
        # identity check on installations containing tens of thousands of
        # files.  Only a symbolic link can redirect this traversal, so resolve
        # exactly that case; descendants retain the monotonic external scope.
        child_external_scope = current_external_scope
        if is_link:
            resolved = Path(entry.path).resolve(strict=True)
            child_external_scope = child_external_scope or not _path_is_within(
                resolved,
                roots,
            )
        if stat.S_ISDIR(followed.st_mode):
            digest.update(b"directory\0")
            _hash_installation_directory(
                digest,
                Path(entry.path),
                relative_parts,
                roots=roots,
                ancestors=next_ancestors,
                external_scope=child_external_scope,
                budget=budget,
            )
        elif stat.S_ISREG(followed.st_mode):
            digest.update(b"file\0")
            _hash_installation_file(
                digest,
                entry.path,
                followed,
                external=child_external_scope,
                budget=budget,
            )
        else:
            raise RuntimeError(
                "Backtest Python installation contains an unsupported special "
                f"filesystem entry: {entry.path}"
            )
        if is_link:
            try:
                unchanged_target = os.readlink(entry.path)
            except FileNotFoundError as exc:
                raise RuntimeError(
                    f"Backtest Python installation changed while hashing: {entry.path}"
                ) from exc
            if unchanged_target != raw_target:
                raise RuntimeError(
                    f"Backtest Python installation changed while hashing: {entry.path}"
                )
    completed = directory.stat()
    if (
        completed.st_dev != initial.st_dev
        or completed.st_ino != initial.st_ino
        or completed.st_size != initial.st_size
        or completed.st_mtime_ns != initial.st_mtime_ns
        or completed.st_ctime_ns != initial.st_ctime_ns
    ):
        raise RuntimeError(
            f"Backtest Python installation changed while hashing: {directory}"
        )


@lru_cache(maxsize=1)
def python_environment_digest():
    """Hash complete authorized roots of the isolated Backtest installation."""

    digest = hashlib.sha256()
    roots = tuple(Path(value) for value in _python_distribution_paths())
    authorized_roots = tuple(
        dict.fromkeys(root.resolve() for root in roots)
    )
    _validate_installation_indirections(roots, authorized_roots)
    budget = {"nodes": 0, "bytes": 0}
    for root in roots:
        digest.update(b"root\0")
        _update_path_token(digest, root)
        root_link_target = os.readlink(root) if root.is_symlink() else None
        if root_link_target is not None:
            digest.update(b"symlink\0")
            _update_path_token(digest, root_link_target)
        if not root.exists():
            digest.update(b"missing\0")
            continue
        digest.update(b"directory\0")
        _hash_installation_directory(
            digest,
            root,
            (),
            roots=authorized_roots,
            ancestors=frozenset(),
            external_scope=False,
            budget=budget,
        )
        if root_link_target is not None and (
            not root.is_symlink() or os.readlink(root) != root_link_target
        ):
            raise RuntimeError(
                f"Backtest Python installation changed while hashing: {root}"
            )
    return "sha256:" + digest.hexdigest()


def time_zone_database_digest():
    digest = hashlib.sha256()
    for root_text in sorted(str(item) for item in TZPATH):
        root = Path(root_text)
        digest.update(root_text.encode("utf-8"))
        digest.update(b"\0")
        if not root.is_dir():
            digest.update(b"missing\0")
            continue
        for path in sorted(
            root.rglob("*"),
            key=lambda item: item.relative_to(root).as_posix(),
        ):
            if not path.is_file():
                continue
            relative = path.relative_to(root).as_posix()
            digest.update(relative.encode("utf-8"))
            digest.update(b"\0")
            if path.is_symlink():
                digest.update(os.readlink(path).encode("utf-8"))
                digest.update(b"\0")
            with path.open("rb") as handle:
                while chunk := handle.read(1024 * 1024):
                    digest.update(chunk)
            digest.update(b"\0")
    return "sha256:" + digest.hexdigest()


def file_digest(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


PYTHON_ENVIRONMENT_DIGEST = python_environment_digest()
TIME_ZONE_DATABASE_DIGEST = time_zone_database_digest()
PYTHON_EXECUTABLE_DIGEST = file_digest(sys.executable)


def engine_runtime_identity():
    return {
        "buildId": ENGINE_BUILD_ID,
        "pythonImplementation": sys.implementation.name,
        "pythonVersion": platform.python_version(),
        "pythonCacheTag": str(sys.implementation.cache_tag or ""),
        "pythonEnvironmentDigest": PYTHON_ENVIRONMENT_DIGEST,
        "pythonExecutableDigest": PYTHON_EXECUTABLE_DIGEST,
        "platformSystem": platform.system(),
        "platformRelease": platform.release(),
        "platformMachine": platform.machine(),
        "platformLibc": list(platform.libc_ver()),
        "timeZoneDatabaseDigest": TIME_ZONE_DATABASE_DIGEST,
    }


__all__ = (
    "PYTHON_ENVIRONMENT_DIGEST",
    "PYTHON_EXECUTABLE_DIGEST",
    "TIME_ZONE_DATABASE_DIGEST",
    "engine_runtime_identity",
    "file_digest",
    "python_environment_digest",
    "time_zone_database_digest",
)
