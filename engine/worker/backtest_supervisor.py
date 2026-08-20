"""Disposable Backtest workers supervised by the Engine process registry."""

from __future__ import annotations

import copy
import os
import sys
import time
import uuid
from pathlib import Path

from engine.contracts import strict_json
from engine.runtime import process_session


RUNTIME_STATUS_POLL_SECONDS = 0.1
RUNTIME_STDERR_TAIL_BYTES = 256 * 1024
_BACKTEST_SESSION_PREFIX = "backtest:"
_RUNTIME_SITE_PACKAGES_ENV = "TRADE_ENGINE_RUNTIME_SITE_PACKAGES"


def _worker_environment(runtime_root):
    extra = {}
    runtime_site_packages = os.environ.get(_RUNTIME_SITE_PACKAGES_ENV)
    if runtime_site_packages is not None:
        # The immutable-release launcher has already proved this absolute,
        # importable directory before Engine code runs.  Preserve that same
        # authority in the disposable worker so both processes compute the
        # runtime identity from the release-owned dependency tree.
        extra[_RUNTIME_SITE_PACKAGES_ENV] = runtime_site_packages
    return process_session.minimal_host_environment(
        home=runtime_root,
        extra=extra,
    )


def _write_json(path, payload):
    path = Path(path)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        strict_json.dumps(payload, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
    os.replace(temporary, path)


def read_runtime_status(path):
    try:
        value = strict_json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    if not isinstance(value, dict) or value.get("schemaVersion") != 1:
        raise RuntimeError("Backtest Runtime emitted an invalid status document.")
    status = value.get("status")
    fields = {
        "running": {
            "schemaVersion",
            "status",
            "sequence",
            "phase",
            "completedCycles",
            "totalCycles",
        },
        "completed": {
            "schemaVersion",
            "status",
            "sequence",
            "phase",
            "completedCycles",
            "totalCycles",
        },
        "failed": {
            "schemaVersion",
            "status",
            "sequence",
            "phase",
            "error",
        },
    }.get(status)
    if fields is None or set(value) != fields:
        raise RuntimeError("Backtest Runtime emitted an invalid status document.")
    if (
        isinstance(value["sequence"], bool)
        or not isinstance(value["sequence"], int)
        or value["sequence"] < 1
        or not isinstance(value["phase"], str)
        or not value["phase"]
    ):
        raise RuntimeError("Backtest Runtime emitted an invalid status document.")
    if status in {"running", "completed"}:
        for field in ("completedCycles", "totalCycles"):
            if (
                isinstance(value[field], bool)
                or not isinstance(value[field], int)
                or value[field] < 0
            ):
                raise RuntimeError(
                    "Backtest Runtime emitted an invalid status document."
                )
        if (
            value["completedCycles"] > value["totalCycles"]
            and value["totalCycles"] > 0
        ):
            raise RuntimeError("Backtest Runtime emitted an invalid status document.")
        if status == "completed" and (
            value["phase"] != "completed"
            or value["completedCycles"] != value["totalCycles"]
        ):
            raise RuntimeError("Backtest Runtime emitted an invalid status document.")
    elif (
        value["phase"] != "failed"
        or not isinstance(value["error"], str)
        or not value["error"]
    ):
        raise RuntimeError("Backtest Runtime emitted an invalid status document.")
    return value


def _execution_root(session):
    value = session.metadata.get("executionRoot")
    return None if value is None else Path(value)


def runtime_process_authority(execution_root):
    """Return whether an exact Backtest process still needs shutdown proof."""

    expected = Path(execution_root).resolve()
    return any(
        _execution_root(session) == expected
        for session in process_session.PROCESS_SESSIONS.snapshot(
            _BACKTEST_SESSION_PREFIX
        ).values()
    )


def shutdown_backtest_runtimes(execution_parent=None):
    """Retry matching Backtest sessions without releasing unproven authority."""

    parent = None if execution_parent is None else Path(execution_parent).resolve()
    registry = process_session.PROCESS_SESSIONS
    sessions = tuple(registry.snapshot(_BACKTEST_SESSION_PREFIX).items())
    first_error = None
    for key, session in sessions:
        execution_root = _execution_root(session)
        if execution_root is None or (
            parent is not None
            and execution_root != parent
            and parent not in execution_root.parents
        ):
            continue
        try:
            registry.stop(key, terminate_grace=1.0, kill_grace=1.0)
        except BaseException as exc:
            first_error = first_error or exc
    if first_error is not None:
        raise first_error


def run_backtest_runtime(
    config,
    request,
    *,
    backtest_id,
    progress_callback,
    execution_root,
    should_stop,
):
    """Run one frozen Backtest under the shared outer subreaper."""

    execution_root = Path(execution_root).resolve()
    runtime_root = execution_root / "runtime"
    runtime_root.mkdir(parents=True, exist_ok=True)
    status_path = execution_root / "runtime-status.json"
    spec_path = execution_root / "runtime-spec.json"
    stderr_path = execution_root / "runtime-stderr.log"
    _write_json(spec_path, {
        "schemaVersion": 1,
        "config": copy.deepcopy(config),
        "request": copy.deepcopy(request),
        "backtestId": backtest_id,
        "runtimeRoot": str(runtime_root),
        "statusPath": str(status_path),
    })
    worker_root = Path(__file__).resolve().parents[2]
    session_key = (
        f"{_BACKTEST_SESSION_PREFIX}{backtest_id}:{uuid.uuid4().hex}"
    )
    registry = process_session.PROCESS_SESSIONS
    session = None
    last_sequence = -1
    return_code = None
    primary_error = None
    primary_traceback = None
    cleanup_error = None
    try:
        session = process_session.PROCESS_SESSIONS.start(
            session_key,
            [
                sys.executable,
                "-m",
                "engine.worker.backtest_runtime",
                str(spec_path),
            ],
            cwd=worker_root,
            env=_worker_environment(runtime_root),
            max_output_bytes=RUNTIME_STDERR_TAIL_BYTES,
            stderr_output_bytes=RUNTIME_STDERR_TAIL_BYTES,
            stderr_path=stderr_path,
            metadata={"executionRoot": str(execution_root)},
        )
        while session.poll() is None:
            if should_stop():
                raise RuntimeError(
                    "Engine stopped while this Backtest job was running."
                )
            status = read_runtime_status(status_path)
            if status and status["sequence"] > last_sequence:
                last_sequence = status["sequence"]
                if status["status"] == "running":
                    progress_callback(
                        status["completedCycles"],
                        status["totalCycles"],
                        status["phase"],
                    )
            should_stop_event = getattr(should_stop, "event", None)
            if should_stop_event is not None:
                should_stop_event.wait(RUNTIME_STATUS_POLL_SECONDS)
            else:
                time.sleep(RUNTIME_STATUS_POLL_SECONDS)
        return_code = session.wait()
    except BaseException as exc:
        primary_error = exc
        primary_traceback = exc.__traceback__
        if session is None:
            session = registry.get(session_key)
    if session is not None:
        try:
            registry.finish(
                session_key,
                session,
                terminate=primary_error is not None,
            )
        except BaseException as exc:
            cleanup_error = exc
    if primary_error is not None:
        if cleanup_error is not None and cleanup_error is not primary_error:
            raise primary_error.with_traceback(primary_traceback) from cleanup_error
        raise primary_error.with_traceback(primary_traceback)
    if cleanup_error is not None:
        raise cleanup_error
    if session is None:
        raise RuntimeError("Backtest Runtime process authority is unavailable.")
    status = read_runtime_status(status_path)
    if status is None:
        detail = session.stderr_text()[-4000:].strip()
        raise RuntimeError(
            detail
            or f"Backtest Runtime exited with code {return_code} without status."
        )
    if return_code != 0:
        if status["status"] != "failed":
            raise RuntimeError(
                f"Backtest Runtime exited with code {return_code} "
                "and invalid terminal status."
            )
        raise RuntimeError(status["error"])
    if status["status"] != "completed":
        raise RuntimeError(
            "Backtest Runtime exited successfully without completed status."
        )
    progress_callback(
        status["completedCycles"],
        status["totalCycles"],
        "completed",
    )
    return copy.deepcopy(status)


__all__ = (
    "read_runtime_status",
    "run_backtest_runtime",
    "runtime_process_authority",
    "shutdown_backtest_runtimes",
)
