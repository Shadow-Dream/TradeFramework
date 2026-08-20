#!/usr/bin/env python3
"""Private runtime files for managed Workspace services."""

from __future__ import annotations

import json
import os
from pathlib import Path
import secrets
import shutil
import stat
import sys

from engine.archive import version as version_archive
from engine.contracts.archive import require_resource_path_segment
from engine.contracts.workspace import workspace_slug


MAX_JUPYTER_LOG_BYTES = 10 * 1024 * 1024
JUPYTER_LOG_BACKUPS = 3
JUPYTER_UI_SYNC_ASSET = (
    Path(__file__).resolve().parents[1]
    / "assets"
    / "jupyter_labextensions"
    / "@trade-engine"
    / "jupyter-ui-sync"
)


def _require_directory_chain(path, *, label):
    path = Path(path).expanduser().absolute()
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current = current / part
        if current.is_symlink():
            raise ValueError(f"{label} may not traverse a symbolic link: {current}")
        if current.exists() and not current.is_dir():
            raise ValueError(f"{label} may traverse only directories: {current}")


def runtime_root(config):
    control_root = Path(config["controlRoot"]).expanduser().absolute()
    _require_directory_chain(control_root, label="Jupyter control root")
    control_root.mkdir(parents=True, exist_ok=True)
    root = control_root / "jupyter"
    _require_directory_chain(root, label="Jupyter runtime root")
    root.mkdir(exist_ok=True)
    resolved = root.resolve()
    try:
        resolved.relative_to(control_root.resolve())
    except ValueError as exc:
        raise ValueError("Jupyter runtime root is outside the control repository.") from exc
    return resolved


def _instances_root(config):
    runtime = runtime_root(config)
    instances = runtime / "instances"
    _require_directory_chain(instances, label="Jupyter instance repository")
    _mkdir_relative_nofollow(
        runtime, ("instances",), label="Jupyter instance repository"
    )
    resolved = instances.resolve()
    if resolved.parent != runtime:
        raise ValueError("Jupyter instance repository is outside its runtime root.")
    return resolved


def instance_runtime_root(config, instance_slug):
    slug = require_resource_path_segment(instance_slug, label="Jupyter instance slug")
    instances = _instances_root(config)
    root = instances / slug
    _require_directory_chain(root, label="Jupyter instance runtime")
    _mkdir_relative_nofollow(
        instances, (slug,), label="Jupyter instance runtime"
    )
    resolved = root.resolve()
    if resolved.parent != instances.resolve():
        raise ValueError("Jupyter instance runtime is outside its managed repository.")
    return resolved


def _mkdir_relative_nofollow(root, parts, *, label):
    """Create a relative directory chain without a symlink race."""
    flags = (
        os.O_RDONLY
        | os.O_DIRECTORY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    descriptor = os.open(root, flags)
    try:
        for part in parts:
            try:
                os.mkdir(part, mode=0o700, dir_fd=descriptor)
            except FileExistsError:
                pass
            try:
                child = os.open(part, flags, dir_fd=descriptor)
            except OSError as exc:
                raise ValueError(
                    f"{label} may contain only real directories."
                ) from exc
            os.close(descriptor)
            descriptor = child
    finally:
        os.close(descriptor)


def prepare_instance_storage(config, instance_slug):
    root = instance_runtime_root(config, instance_slug)
    private = root / "private"
    _mkdir_relative_nofollow(root, ("private",), label="Jupyter private runtime")
    private.chmod(0o700, follow_symlinks=False)
    directories = {}
    for name in ("home", "data", "runtime", "cache", "ipython", "python-user"):
        path = root / name
        _mkdir_relative_nofollow(
            root, (name,), label="Jupyter instance storage"
        )
        directories[name] = path
    user_site = (
        directories["python-user"]
        / "lib"
        / f"python{sys.version_info.major}.{sys.version_info.minor}"
        / "site-packages"
    )
    _mkdir_relative_nofollow(
        directories["python-user"],
        ("lib", f"python{sys.version_info.major}.{sys.version_info.minor}", "site-packages"),
        label="Jupyter Python user site",
    )
    _mkdir_relative_nofollow(
        directories["python-user"],
        ("bin",),
        label="Jupyter Python user bin",
    )
    return {
        "root": root,
        "private": private.resolve(),
        "serverConfig": (private / "jupyter_server_config.py").resolve(),
        "userSite": user_site.resolve(),
        **directories,
    }


def install_ui_sync_labextension(storage):
    """Install the release-owned prebuilt extension into one private Jupyter data dir."""
    source = JUPYTER_UI_SYNC_ASSET
    if source.is_symlink() or not source.is_dir():
        raise RuntimeError("TradeEngine Jupyter UI sync extension is unavailable.")
    for item in source.rglob("*"):
        if item.is_symlink():
            raise RuntimeError("TradeEngine Jupyter UI sync extension may not contain symbolic links.")
    data_root = Path(storage["data"])
    if data_root.is_symlink() or not data_root.is_dir():
        raise RuntimeError("Jupyter data directory must be a real directory.")
    _mkdir_relative_nofollow(
        data_root,
        ("labextensions", "@trade-engine"),
        label="Jupyter Lab extension repository",
    )
    parent = data_root / "labextensions" / "@trade-engine"
    target = parent / "jupyter-ui-sync"
    staging = parent / f".jupyter-ui-sync.{secrets.token_hex(8)}.tmp"
    try:
        shutil.copytree(source, staging, symlinks=False)
        if target.exists():
            if target.is_symlink() or not target.is_dir():
                raise RuntimeError("Jupyter UI sync extension target is unsafe.")
            shutil.rmtree(target)
        os.replace(staging, target)
    finally:
        if staging.exists():
            shutil.rmtree(staging)
    return target


def token_path(config, instance_slug):
    return prepare_instance_storage(config, instance_slug)["private"] / "token"


def _read_private_token(path):
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        details = os.fstat(descriptor)
        if not stat.S_ISREG(details.st_mode) or details.st_mode & 0o077:
            raise RuntimeError("Jupyter token evidence must be a private regular file.")
        raw = b""
        while len(raw) <= 4096:
            chunk = os.read(descriptor, 4097 - len(raw))
            if not chunk:
                break
            raw += chunk
    finally:
        os.close(descriptor)
    if len(raw) > 4096:
        raise RuntimeError("Jupyter token evidence is too large.")
    try:
        token = raw.decode("ascii").strip()
    except UnicodeDecodeError as exc:
        raise RuntimeError("Jupyter token evidence is not ASCII.") from exc
    if not token or any(character.isspace() for character in token):
        raise RuntimeError("Jupyter token evidence is invalid.")
    return token


def load_or_create_token(config, instance_slug):
    """Atomically create one private token; concurrent callers read the winner."""
    path = token_path(config, instance_slug)
    if path.is_symlink():
        raise RuntimeError("Jupyter token evidence may not be a symbolic link.")
    try:
        return _read_private_token(path)
    except FileNotFoundError:
        pass
    token = secrets.token_urlsafe(32)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{secrets.token_hex(8)}.tmp")
    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    descriptor = os.open(temporary, flags, 0o600)
    try:
        payload = (token + "\n").encode("ascii")
        offset = 0
        while offset < len(payload):
            offset += os.write(descriptor, payload[offset:])
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    won = False
    try:
        try:
            os.link(temporary, path, follow_symlinks=False)
            won = True
            directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        except FileExistsError:
            pass
    finally:
        temporary.unlink(missing_ok=True)
    return token if won else _read_private_token(path)


def write_server_config(config, token, instance_slug):
    storage = prepare_instance_storage(config, instance_slug)
    path = storage["serverConfig"]
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(8)}.tmp")
    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    descriptor = os.open(temporary, flags, 0o600)
    try:
        payload = (
            "c = get_config()\n"
            f"c.IdentityProvider.token = {json.dumps(token)}\n"
            "c.MappingKernelManager.default_kernel_name = 'python3'\n"
            "c.KernelSpecManager.allowed_kernelspecs = {'python3'}\n"
            "c.FileContentsManager.delete_to_trash = False\n"
        ).encode("utf-8")
        offset = 0
        while offset < len(payload):
            offset += os.write(descriptor, payload[offset:])
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    try:
        os.replace(temporary, path)
        path.chmod(0o600, follow_symlinks=False)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        temporary.unlink(missing_ok=True)
    return path


def python_user_base(config, instance_slug):
    return prepare_instance_storage(config, instance_slug)["python-user"]


def python_user_site(config, instance_slug):
    return prepare_instance_storage(config, instance_slug)["userSite"]


def jupyter_log_path(config, instance_slug):
    slug = require_resource_path_segment(instance_slug, label="Jupyter instance slug")
    return runtime_root(config) / f"jupyter-{slug}.log"


def rotate_jupyter_log(path, *, force=False):
    path = Path(path)
    if path.is_symlink():
        raise ValueError("Jupyter log may not be a symbolic link.")
    try:
        if not force and path.stat().st_size <= MAX_JUPYTER_LOG_BYTES:
            return
    except FileNotFoundError:
        return
    oldest = path.with_name(f"{path.name}.{JUPYTER_LOG_BACKUPS}")
    oldest.unlink(missing_ok=True)
    for index in range(JUPYTER_LOG_BACKUPS - 1, 0, -1):
        source = path.with_name(f"{path.name}.{index}")
        if source.exists():
            source.replace(path.with_name(f"{path.name}.{index + 1}"))
    path.replace(path.with_name(f"{path.name}.1"))


def read_log_tail(config, instance_slug, max_chars=4000):
    path = jupyter_log_path(config, instance_slug)
    if path.is_symlink():
        raise ValueError("Jupyter log may not be a symbolic link.")
    try:
        return path.read_text(encoding="utf-8", errors="replace")[-max_chars:]
    except FileNotFoundError:
        return ""


def discard_workspace_runtime(config, workspace_id, workspace_kind):
    slug = workspace_slug(str(workspace_id), workspace_kind)
    instances = _instances_root(config)
    target = instances / slug
    if target.parent != instances or target.is_symlink():
        if target.is_symlink():
            target.unlink()
        elif target.parent != instances:
            raise ValueError("Jupyter instance runtime is outside its managed root.")
    elif target.exists():
        shutil.rmtree(target)
    log = jupyter_log_path(config, slug)
    paths = (
        log,
        *(log.with_name(f"{log.name}.{index}") for index in range(1, JUPYTER_LOG_BACKUPS + 1)),
    )
    for path in paths:
        if path.is_symlink() or path.is_file():
            path.unlink()
        elif path.is_dir():
            version_archive.discard_archive(path)


__all__ = (
    "JUPYTER_LOG_BACKUPS",
    "MAX_JUPYTER_LOG_BYTES",
    "discard_workspace_runtime",
    "instance_runtime_root",
    "install_ui_sync_labextension",
    "jupyter_log_path",
    "load_or_create_token",
    "prepare_instance_storage",
    "python_user_base",
    "python_user_site",
    "read_log_tail",
    "rotate_jupyter_log",
    "runtime_root",
    "token_path",
    "write_server_config",
)
