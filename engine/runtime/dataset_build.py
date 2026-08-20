#!/usr/bin/env python3
"""Sandbox execution and process authority for Dataset Builds."""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

from engine.contracts.dataset_workspace import (
    ENGINE_SCRIPT_NAME,
    ENGINE_WORKSPACE_DIRECTORY,
    MAX_EXECUTION_WORKSPACE_BYTES,
    MAX_EXECUTION_WORKSPACE_ENTRIES,
    MAX_LOG_CHARS,
    MAX_LOG_TAIL_BYTES,
    MIN_HOST_FREE_BYTES,
    PROCESS_TERMINATION_GRACE_SECONDS,
    WORKSPACE_SCAN_INTERVAL_SECONDS,
)
from engine.runtime import process_session


_BUILD_STOPPING = threading.Event()


def shutdown_build_processes():
    """Cancel active Dataset sandboxes during Engine service shutdown."""
    _BUILD_STOPPING.set()
    process_session.PROCESS_SESSIONS.shutdown("dataset:")


def _allocated_bytes(stat_result):
    """Conservatively account for logical and physically allocated file size."""
    allocated = getattr(stat_result, "st_blocks", 0) * 512
    if stat.S_ISREG(stat_result.st_mode):
        return max(stat_result.st_size, allocated)
    return allocated


def workspace_usage(execution_workspace):
    """Measure a Workspace without following user-controlled symbolic links."""
    root = Path(execution_workspace)
    try:
        root_stat = root.lstat()
    except OSError as exc:
        raise RuntimeError(f"Dataset execution Workspace cannot be inspected: {exc}") from exc
    if stat.S_ISLNK(root_stat.st_mode) or not stat.S_ISDIR(root_stat.st_mode):
        raise RuntimeError("Dataset execution Workspace must be a real directory.")

    entry_count = 0
    total_bytes = _allocated_bytes(root_stat)
    pending = [root]
    while pending:
        directory = pending.pop()
        try:
            with os.scandir(directory) as entries:
                for entry in entries:
                    try:
                        entry_stat = entry.stat(follow_symlinks=False)
                    except FileNotFoundError:
                        # A running Script may atomically replace a temporary
                        # file.  The next bounded scan observes its replacement.
                        continue
                    except OSError as exc:
                        raise RuntimeError(
                            f"Dataset execution Workspace entry cannot be inspected: {entry.path}: {exc}"
                        ) from exc
                    entry_count += 1
                    if entry_count > MAX_EXECUTION_WORKSPACE_ENTRIES:
                        raise RuntimeError(
                            "Dataset execution Workspace exceeded the Engine safety limit of "
                            f"{MAX_EXECUTION_WORKSPACE_ENTRIES} filesystem entries."
                        )
                    total_bytes += _allocated_bytes(entry_stat)
                    if total_bytes > MAX_EXECUTION_WORKSPACE_BYTES:
                        raise RuntimeError(
                            "Dataset execution Workspace exceeded the Engine safety limit of "
                            f"{MAX_EXECUTION_WORKSPACE_BYTES} bytes."
                        )
                    if stat.S_ISDIR(entry_stat.st_mode):
                        pending.append(Path(entry.path))
        except OSError as exc:
            raise RuntimeError(
                f"Dataset execution Workspace directory cannot be inspected: {directory}: {exc}"
            ) from exc
    return {"bytes": total_bytes, "entries": entry_count}


def enforce_workspace_safety(execution_workspace):
    usage = workspace_usage(execution_workspace)
    try:
        free_bytes = shutil.disk_usage(execution_workspace).free
    except OSError as exc:
        raise RuntimeError(f"Dataset execution Workspace capacity cannot be inspected: {exc}") from exc
    if free_bytes < MIN_HOST_FREE_BYTES:
        raise RuntimeError(
            "Dataset execution Workspace stopped because the host filesystem has less than "
            f"{MIN_HOST_FREE_BYTES} bytes free."
        )
    return usage


def _run_bounded_process(command, execution_workspace, timeout_seconds, *, limits=None):
    """Execute only until the shared supervisor proves complete quiescence."""
    enforce_workspace_safety(execution_workspace)
    if _BUILD_STOPPING.is_set():
        raise RuntimeError("Engine is stopping and cannot start a Dataset Build.")
    session_key = f"dataset:{uuid.uuid4().hex}"
    try:
        session = process_session.PROCESS_SESSIONS.start(
            session_key,
            command,
            cwd=execution_workspace,
            env=process_session.minimal_host_environment(home=execution_workspace),
            max_output_bytes=MAX_LOG_TAIL_BYTES,
            stderr_output_bytes=MAX_LOG_TAIL_BYTES,
            limits=limits,
            metadata={"executionWorkspace": str(Path(execution_workspace).resolve())},
        )
    except BaseException as primary_error:
        primary_traceback = primary_error.__traceback__
        if process_session.PROCESS_SESSIONS.get(session_key) is not None:
            try:
                process_session.PROCESS_SESSIONS.stop(
                    session_key,
                    terminate_grace=PROCESS_TERMINATION_GRACE_SECONDS,
                    kill_grace=1.0,
                )
            except BaseException as cleanup_error:
                raise primary_error.with_traceback(primary_traceback) from cleanup_error
        raise primary_error.with_traceback(primary_traceback)
    deadline = time.monotonic() + timeout_seconds
    next_workspace_scan = time.monotonic()
    primary_error = None
    primary_traceback = None
    return_code = None
    try:
        if _BUILD_STOPPING.is_set():
            raise RuntimeError("Engine stopped before Dataset Build execution.")
        while session.poll() is None:
            now = time.monotonic()
            if _BUILD_STOPPING.is_set():
                raise RuntimeError("Engine stopped during Dataset Build execution.")
            if now >= deadline:
                raise subprocess.TimeoutExpired(command, timeout_seconds)
            if now >= next_workspace_scan:
                enforce_workspace_safety(execution_workspace)
                next_workspace_scan = time.monotonic() + WORKSPACE_SCAN_INTERVAL_SECONDS
            time.sleep(max(0.0, min(
                0.02,
                max(0.0, deadline - time.monotonic()),
                max(0.0, next_workspace_scan - time.monotonic()),
            )))
        return_code = session.wait()
        enforce_workspace_safety(execution_workspace)
    except BaseException as exc:
        primary_error = exc
        primary_traceback = exc.__traceback__
    finally:
        try:
            process_session.PROCESS_SESSIONS.finish(
                session_key,
                session,
                terminate=primary_error is not None,
            )
        except BaseException as cleanup_error:
            if primary_error is not None:
                raise primary_error.with_traceback(primary_traceback) from cleanup_error
            raise
    if primary_error is not None:
        raise primary_error.with_traceback(primary_traceback)
    return (
        return_code,
        session.stdout_text()[-MAX_LOG_CHARS:],
        session.stderr_text()[-MAX_LOG_CHARS:],
    )


def execution_process_authority(execution_workspace):
    """Return whether a build writer still has retained supervision authority."""
    expected = str(Path(execution_workspace).resolve())
    return any(
        session.metadata.get("executionWorkspace") == expected
        for session in process_session.PROCESS_SESSIONS.snapshot("dataset:").values()
    )


def run_limits(timeout_seconds):
    return {
        "cpu_seconds": max(1, int(timeout_seconds)),
        "file_bytes": MAX_EXECUTION_WORKSPACE_BYTES,
        "open_files": 512,
    }


def execute_in_sandbox(execution_workspace, script_path, arguments, bindings, timeout_seconds):
    bwrap = shutil.which("bwrap")
    if not bwrap:
        raise RuntimeError("Dataset build sandbox is unavailable: bubblewrap is required; execution refused.")
    execution_workspace = execution_workspace.resolve()
    script_path = script_path.resolve()
    sandbox_script = f"/workspace/{ENGINE_WORKSPACE_DIRECTORY}/{ENGINE_SCRIPT_NAME}"
    command = [
        bwrap,
        "--die-with-parent",
        "--new-session",
        "--unshare-all",
    ]
    for system_path in ("/usr", "/bin", "/lib", "/lib64"):
        if Path(system_path).exists():
            command.extend(["--ro-bind", system_path, system_path])
    local_site = Path.home() / ".local" / "lib" / f"python{sys.version_info.major}.{sys.version_info.minor}" / "site-packages"
    if local_site.is_dir():
        command.extend(["--ro-bind", str(local_site), str(local_site)])
    sandbox_python = sys.executable
    if sys.prefix != sys.base_prefix:
        # The Engine commonly runs from a virtualenv so JupyterLab and its
        # Workspace packages are available.  A Dataset Script must use that
        # same runtime, but the sandbox previously mounted only /usr and then
        # attempted to execute the host-only virtualenv path.  Mount the
        # complete environment at a stable private path instead of exposing
        # any surrounding deployment directories.
        virtualenv_root = Path(sys.prefix).resolve()
        sandbox_virtualenv = Path("/opt/trade-python")
        command.extend([
            "--dir", "/opt",
            "--ro-bind", str(virtualenv_root), str(sandbox_virtualenv),
        ])
        sandbox_python = str(sandbox_virtualenv / "bin" / Path(sys.executable).name)
    for source_root in sorted({str(Path(item["storageRoot"]).resolve()) for item in bindings}):
        command.extend(["--ro-bind", source_root, source_root])
    command.extend([
        "--bind", str(execution_workspace), "/workspace",
        "--ro-bind", str(script_path.parent), f"/workspace/{ENGINE_WORKSPACE_DIRECTORY}",
        "--proc", "/proc",
        "--dev", "/dev",
        "--remount-ro", "/",
        "--chdir", "/workspace",
        "--clearenv",
        "--setenv", "PATH", "/usr/local/bin:/usr/bin:/bin",
        "--setenv", "HOME", "/workspace",
        "--setenv", "TMPDIR", "/workspace/.tmp",
        "--setenv", "PYTHONDONTWRITEBYTECODE", "1",
    ])
    if local_site.is_dir():
        command.extend(["--setenv", "PYTHONPATH", str(local_site)])
    command.extend([
        sandbox_python,
        "-B",
        sandbox_script,
        *arguments,
    ])
    (execution_workspace / ".tmp").mkdir(exist_ok=True)
    return_code, stdout, stderr = _run_bounded_process(
        command,
        execution_workspace,
        timeout_seconds,
        limits=run_limits(timeout_seconds),
    )
    if return_code != 0:
        raise RuntimeError(
            f"Dataset script exited with code {return_code}: " + (stderr.strip() or stdout.strip())[-4000:]
        )
    return stdout, stderr
