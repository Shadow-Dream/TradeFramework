#!/usr/bin/env python3
"""Lifecycle orchestration for isolated per-Workspace Jupyter servers."""

from __future__ import annotations

import atexit
from contextlib import contextmanager
from pathlib import Path
import shutil
import threading
import time

from engine.contracts import workspace as workspace_contract
from engine.repository import workspace_files
from engine.repository import workspace_paths
from engine.runtime import jupyter_workspace
from engine.runtime import process_session


_state_lock = threading.RLock()
_instances = {}
_blocked_slugs = set()
_lifecycle_lock = threading.Lock()
_lifecycle_locks = {}


def workspace_host_path(config, workspace_id, workspace_kind):
    kind = workspace_contract.normalize_workspace_kind(workspace_kind)
    candidate = workspace_paths.managed_workspace_path(config, kind, workspace_id)
    if not candidate.is_dir():
        raise ValueError(f"Jupyter Workspace does not exist: {workspace_id}")
    if kind == "sampler":
        for name, label, required in (
            (".sampler-workspace.json", "Sampler Workspace identity metadata", True),
            ("sampler.json", "Sampler Workspace sampler.json", True),
            ("sampler.py", "Python Script Sampler Workspace sampler.py", False),
            ("SAMPLER_VERSION.md", "Sampler Workspace instructions", True),
        ):
            workspace_paths.require_workspace_file(
                candidate,
                name,
                label=label,
                required=required,
            )
    return candidate


def normalize_read_only_roots(config, workspace_kind, read_only_roots):
    if not isinstance(read_only_roots, (list, tuple)):
        raise ValueError("Jupyter read-only roots must be an array.")
    if workspace_kind != "dataset" and read_only_roots:
        raise ValueError("Only Dataset Workspaces may mount source Dataset roots.")
    allowed_root = (Path(config["releaseRoot"]) / "_data").resolve()
    normalized = []
    seen = set()
    for value in read_only_roots:
        root = Path(value)
        if root.is_symlink():
            raise ValueError("Jupyter source roots may not be symbolic links.")
        root = root.resolve()
        try:
            root.relative_to(allowed_root)
        except ValueError as exc:
            raise ValueError(
                "Jupyter source root is outside immutable Dataset storage."
            ) from exc
        if not root.is_dir():
            raise ValueError(
                "Jupyter source root must be an immutable Dataset directory."
            )
        if root not in seen:
            seen.add(root)
            normalized.append(root)
    return tuple(normalized)


def ensure_workspace_writable(path):
    """Repair owned entries without following read-only Dataset symlinks."""
    root = Path(path)
    for item in [root, *root.rglob("*")]:
        if item.is_symlink():
            continue
        mode = item.stat().st_mode
        item.chmod(mode | (0o700 if item.is_dir() else 0o600))


def _workspace_identity(workspace_path, read_only_roots):
    details = workspace_path.stat()
    return (
        details.st_dev,
        details.st_ino,
        tuple(
            (str(root), root.stat().st_dev, root.stat().st_ino)
            for root in read_only_roots
        ),
    )


def _public_instance(record):
    return dict(record["instance"])


def _new_record(instance, workspace_identity):
    return {
        "authority": object(),
        "state": "starting",
        "instance": instance,
        "workspaceIdentity": workspace_identity,
        "session": None,
        "cancelled": False,
        "ready": threading.Event(),
        "spawnDone": threading.Event(),
        "stopLock": threading.Lock(),
        "error": None,
    }


def _is_current(slug, record):
    with _state_lock:
        return _instances.get(slug) is record


def _remove_record_after_proof(slug, record):
    key = f"jupyter:{slug}"
    if process_session.PROCESS_SESSIONS.get(key) is not None:
        return False
    with _state_lock:
        if _instances.get(slug) is record:
            _instances.pop(slug)
        record["ready"].set()
        record["spawnDone"].set()
    return True


def _start_record(config, slug, record, timeout):
    session = None
    primary_error = None
    primary_traceback = None
    cleanup_error = None
    try:
        instance = record["instance"]
        ensure_workspace_writable(instance["workspacePath"])
        storage = workspace_files.prepare_instance_storage(config, slug)
        workspace_files.install_ui_sync_labextension(storage)
        instance["token"] = workspace_files.load_or_create_token(config, slug)
        workspace_files.write_server_config(config, instance["token"], slug)
        log_path = workspace_files.jupyter_log_path(config, slug)
        workspace_files.rotate_jupyter_log(log_path)
        session = jupyter_workspace.start_workspace_process(
            instance,
            storage,
            log_path,
            max_log_bytes=workspace_files.MAX_JUPYTER_LOG_BYTES,
        )
        with _state_lock:
            if (
                _instances.get(slug) is not record
                or record["cancelled"]
                or slug in _blocked_slugs
            ):
                raise RuntimeError("Jupyter Workspace startup was cancelled.")
            record["session"] = session
            record["spawnDone"].set()

        deadline = time.monotonic() + timeout
        while True:
            with _state_lock:
                current = (
                    _instances.get(slug) is record
                    and record["state"] == "starting"
                    and not record["cancelled"]
                    and slug not in _blocked_slugs
                )
            if not current:
                raise RuntimeError("Jupyter Workspace startup was cancelled.")
            if session.poll() is not None:
                tail = workspace_files.read_log_tail(config, slug)
                raise RuntimeError(
                    f"Workspace JupyterLab exited during startup.\n{tail}".strip()
                )
            if jupyter_workspace.workspace_health(instance, session):
                with _state_lock:
                    if (
                        _instances.get(slug) is record
                        and record["state"] == "starting"
                        and not record["cancelled"]
                        and slug not in _blocked_slugs
                        and process_session.PROCESS_SESSIONS.is_current(
                            f"jupyter:{slug}", session
                        )
                    ):
                        record["state"] = "running"
                        record["ready"].set()
                        return _public_instance(record)
                raise RuntimeError("Jupyter Workspace startup lost its authority.")
            if time.monotonic() >= deadline:
                raise RuntimeError(
                    f"Workspace JupyterLab did not become ready within {timeout:g} "
                    f"seconds. See {log_path}."
                )
            time.sleep(0.2)
    except BaseException as exc:
        primary_error = exc
        primary_traceback = exc.__traceback__
    finally:
        record["spawnDone"].set()
        if primary_error is not None:
            session = session or process_session.PROCESS_SESSIONS.get(
                f"jupyter:{slug}"
            )
            if session is not None:
                try:
                    process_session.PROCESS_SESSIONS.stop(
                        f"jupyter:{slug}",
                        terminate_grace=1.0,
                        kill_grace=5.0,
                    )
                except BaseException as exc:
                    cleanup_error = exc
            with _state_lock:
                record["error"] = primary_error
                record["ready"].set()
                if process_session.PROCESS_SESSIONS.get(f"jupyter:{slug}") is None:
                    if _instances.get(slug) is record:
                        _instances.pop(slug)
                elif _instances.get(slug) is record:
                    record["state"] = "failed"
    if primary_error is not None:
        if cleanup_error is not None:
            raise primary_error.with_traceback(primary_traceback) from cleanup_error
        raise primary_error.with_traceback(primary_traceback)
    raise RuntimeError("Jupyter Workspace startup ended without a result.")


def ensure_workspace_running(
    config,
    workspace_id,
    workspace_kind,
    *,
    read_only_roots=(),
    timeout=20.0,
):
    kind = workspace_contract.normalize_workspace_kind(workspace_kind)
    slug = workspace_contract.workspace_slug(str(workspace_id), kind)
    workspace_path = workspace_host_path(config, str(workspace_id), kind)
    if workspace_path.is_symlink() or not workspace_path.is_dir():
        raise ValueError("Jupyter Workspace does not exist as a managed directory.")
    roots = normalize_read_only_roots(config, kind, read_only_roots)
    identity = _workspace_identity(workspace_path, roots)

    while True:
        candidate = None
        waiter = None
        stop_stale = False
        with _state_lock:
            if slug in _blocked_slugs:
                raise RuntimeError("Jupyter Workspace lifecycle is deleting this Workspace.")
            current = _instances.get(slug)
            if current is None:
                instance = {
                    "slug": slug,
                    "workspaceId": str(workspace_id),
                    "workspaceKind": kind,
                    "workspacePath": str(workspace_path),
                    "readOnlyRoots": tuple(str(root) for root in roots),
                    "host": workspace_contract.jupyter_host(config),
                    "port": jupyter_workspace.available_workspace_port(
                        workspace_contract.jupyter_host(config)
                    ),
                    "baseUrl": workspace_contract.workspace_base_url(
                        config, str(workspace_id), kind
                    ),
                }
                candidate = _new_record(instance, identity)
                _instances[slug] = candidate
            elif current["state"] == "running" and (
                current["workspaceIdentity"] == identity
                and current["instance"]["workspacePath"] == str(workspace_path)
            ):
                candidate = current
            elif current["state"] == "starting":
                waiter = current["ready"]
            else:
                stop_stale = True

        if waiter is not None:
            waiter.wait(timeout=max(0.1, timeout))
            continue
        if stop_stale:
            stop_workspace_server(str(workspace_id), kind)
            continue
        if candidate["state"] == "starting":
            return _start_record(config, slug, candidate, timeout)

        session = candidate["session"]
        healthy = jupyter_workspace.workspace_health(
            candidate["instance"], session
        )
        with _state_lock:
            if (
                healthy
                and _instances.get(slug) is candidate
                and candidate["state"] == "running"
                and not candidate["cancelled"]
            ):
                return _public_instance(candidate)
        stop_workspace_server(str(workspace_id), kind)


def stop_workspace_server(workspace_id, workspace_kind):
    slug = workspace_contract.workspace_slug(str(workspace_id), workspace_kind)
    key = f"jupyter:{slug}"
    with _state_lock:
        record = _instances.get(slug)
        if record is not None:
            record["cancelled"] = True
            record["state"] = "stopping"
            spawn_done = record["spawnDone"]
            stop_lock = record["stopLock"]
        else:
            spawn_done = None
            stop_lock = threading.Lock()
    if spawn_done is not None:
        spawn_done.wait(timeout=30.0)
        if not spawn_done.is_set():
            raise RuntimeError("Jupyter Workspace spawn did not yield termination authority.")
    with stop_lock:
        first_error = None
        try:
            process_session.PROCESS_SESSIONS.stop(
                key,
                terminate_grace=1.0,
                kill_grace=5.0,
            )
        except BaseException as exc:
            first_error = exc
        proven = process_session.PROCESS_SESSIONS.get(key) is None
        if record is not None and proven:
            _remove_record_after_proof(slug, record)
        elif record is not None:
            with _state_lock:
                if _instances.get(slug) is record:
                    record["state"] = "failed"
                    record["ready"].set()
        if not proven and first_error is None:
            first_error = RuntimeError(
                "Jupyter Workspace process termination is not proven."
            )
        if first_error is not None:
            raise first_error
    return proven


def stop_managed_process():
    with _state_lock:
        records = tuple(_instances.items())
        for _slug, record in records:
            record["cancelled"] = True
            record["state"] = "stopping"
    first_error = None
    for slug, record in records:
        try:
            stop_workspace_server(
                record["instance"]["workspaceId"],
                record["instance"]["workspaceKind"],
            )
        except BaseException as exc:
            first_error = first_error or exc
    for key in tuple(process_session.PROCESS_SESSIONS.snapshot("jupyter:")):
        try:
            process_session.PROCESS_SESSIONS.stop(key)
        except BaseException as exc:
            first_error = first_error or exc
    if first_error is not None:
        raise first_error


def shutdown_managed_process():
    """Permanently close the Jupyter namespace during Engine shutdown."""
    first_error = None
    try:
        stop_managed_process()
    except BaseException as exc:
        first_error = exc
    try:
        process_session.PROCESS_SESSIONS.shutdown("jupyter:")
    except BaseException as exc:
        first_error = first_error or exc
    if first_error is not None:
        raise first_error


def status(config):
    with _state_lock:
        records = tuple(
            record
            for record in _instances.values()
            if record["state"] == "running"
        )
    active = [
        record
        for record in records
        if jupyter_workspace.workspace_health(record["instance"], record["session"])
    ]
    return {
        "installed": jupyter_workspace.is_installed(),
        "sandboxAvailable": bool(shutil.which("bwrap")),
        "running": bool(active),
        "baseUrl": workspace_contract.jupyter_base_url(config),
        "authentication": "jupyter-token",
        "activeWorkspaceServers": len(active),
    }


def resolve_proxy_instance(path):
    request_path = str(path or "").split("?", 1)[0]
    with _state_lock:
        records = tuple(
            record
            for record in _instances.values()
            if record["state"] == "running"
        )
    for record in records:
        instance = record["instance"]
        if (
            request_path.startswith(instance["baseUrl"])
            and process_session.PROCESS_SESSIONS.is_current(
                f"jupyter:{instance['slug']}", record["session"]
            )
            and record["session"].poll() is None
        ):
            return _public_instance(record)
    raise ValueError("Unknown or expired Workspace Jupyter session.")


def workspace_url(
    config,
    public_url,
    workspace_id,
    workspace_kind,
    *,
    read_only_roots=(),
):
    instance = ensure_workspace_running(
        config,
        workspace_id,
        workspace_kind,
        read_only_roots=read_only_roots,
    )
    return str(public_url or "").rstrip("/") + instance["baseUrl"] + "lab?reset"


@contextmanager
def workspace_lifecycle_guard(workspace_id, workspace_kind):
    slug = workspace_contract.workspace_slug(str(workspace_id), workspace_kind)
    with _lifecycle_lock:
        lifecycle = _lifecycle_locks.setdefault(slug, threading.RLock())
    with lifecycle:
        with _state_lock:
            _blocked_slugs.add(slug)
        try:
            yield
        finally:
            with _state_lock:
                _blocked_slugs.discard(slug)


atexit.register(shutdown_managed_process)


__all__ = (
    "ensure_workspace_running",
    "ensure_workspace_writable",
    "normalize_read_only_roots",
    "resolve_proxy_instance",
    "shutdown_managed_process",
    "status",
    "stop_managed_process",
    "stop_workspace_server",
    "workspace_host_path",
    "workspace_lifecycle_guard",
    "workspace_url",
)
