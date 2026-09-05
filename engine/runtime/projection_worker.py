"""Engine-owned client for the resident isolated Result Projection Worker."""

from __future__ import annotations

import json
import hashlib
import os
import socket
import stat
import struct
import tempfile
import threading
import time
import uuid
from pathlib import Path

from engine.contracts import strict_json
from engine.contracts.module import require_exact_fields
from engine.runtime import process_session


PROJECTION_WORKER_SESSION_KEY = "result:projection-worker"
PROJECTION_WORKER_POOL_SIZE = 2
PROJECTION_WORKER_START_SECONDS = 10.0
PROJECTION_WORKER_CONNECT_SECONDS = 5.0
PROJECTION_MESSAGE_MAX_BYTES = 32 * 1024 * 1024
PROJECTION_CACHE_ENTRY_SOURCE_BYTES = 16 * 1024 * 1024
PROJECTION_CACHE_TOTAL_SOURCE_BYTES = 32 * 1024 * 1024
PROJECTION_CACHE_MAX_ENTRIES = 8
_START_LOCK = threading.Lock()


def _receive_message(connection):
    header = _receive_exact(connection, 8)
    size = struct.unpack("!Q", header)[0]
    if size < 2 or size > PROJECTION_MESSAGE_MAX_BYTES:
        raise RuntimeError("Projection Worker response size is invalid.")
    return strict_json.loads(_receive_exact(connection, size))


def _receive_exact(connection, size):
    chunks = []
    remaining = size
    while remaining:
        chunk = connection.recv(remaining)
        if not chunk:
            raise RuntimeError("Projection Worker closed its response channel.")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _send_message(connection, value):
    payload = strict_json.dumps(
        value, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    if len(payload) > PROJECTION_MESSAGE_MAX_BYTES:
        raise ValueError("Projection Worker request is too large.")
    connection.sendall(struct.pack("!Q", len(payload)) + payload)


def _session_key(slot):
    if slot == 0:
        return PROJECTION_WORKER_SESSION_KEY
    return f"{PROJECTION_WORKER_SESSION_KEY}:{slot}"


def _cleanup_session(registry, session, *, terminate):
    cleanup_error = None
    try:
        registry.finish(
            session.metadata["sessionKey"],
            session,
            terminate=terminate,
        )
    except BaseException as exc:
        cleanup_error = exc
    if not registry.is_current(session.metadata["sessionKey"], session):
        owner = session.metadata.get("scratchOwner")
        if owner is not None:
            try:
                owner.cleanup()
            except BaseException as exc:
                cleanup_error = cleanup_error or exc
    if cleanup_error is not None:
        raise cleanup_error


def _ready_document(session):
    ready_path = Path(session.metadata["readyPath"])
    socket_path = Path(session.metadata["socketPath"])
    if ready_path.is_symlink() or socket_path.is_symlink():
        raise RuntimeError("Projection Worker readiness paths are invalid.")
    ready = strict_json.loads(ready_path.read_bytes())
    require_exact_fields(
        ready,
        allowed={"schemaVersion", "pid"},
        required={"schemaVersion", "pid"},
        label="Projection Worker readiness",
    )
    if (
        ready["schemaVersion"] != 1
        or isinstance(ready["pid"], bool)
        or not isinstance(ready["pid"], int)
        or ready["pid"] < 1
    ):
        raise RuntimeError("Projection Worker readiness is invalid.")
    state = os.stat(socket_path, follow_symlinks=False)
    if not stat.S_ISSOCK(state.st_mode) or state.st_mode & 0o077:
        raise RuntimeError("Projection Worker socket authority is invalid.")
    return ready


def _start_worker(slot):
    # Import lazily so the generic Result Runtime remains the single owner of
    # Result process launch/shutdown policy without introducing an import cycle.
    from engine.runtime.result_runtime import start_result_session

    registry = process_session.PROCESS_SESSIONS
    session_key = _session_key(slot)
    owner = tempfile.TemporaryDirectory(
        prefix=f"trade-projection-worker-{slot}-"
    )
    root = Path(owner.name)
    socket_path = root / "worker.sock"
    ready_path = root / "ready.json"
    status_path = root / "status.json"
    spec_path = root / "spec.json"
    spec_path.write_text(
        strict_json.dumps({
            "schemaVersion": 1,
            "socketPath": str(socket_path),
            "readyPath": str(ready_path),
            "statusPath": str(status_path),
            "cacheEntrySourceBytes": PROJECTION_CACHE_ENTRY_SOURCE_BYTES,
            "cacheTotalSourceBytes": PROJECTION_CACHE_TOTAL_SOURCE_BYTES,
            "cacheMaxEntries": PROJECTION_CACHE_MAX_ENTRIES,
            "messageMaxBytes": PROJECTION_MESSAGE_MAX_BYTES,
        }, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
    metadata = {
        "executionRoot": str(root),
        "socketPath": str(socket_path),
        "readyPath": str(ready_path),
        "statusPath": str(status_path),
        "scratchOwner": owner,
        "sessionKey": session_key,
        "slot": slot,
    }
    session = None
    try:
        session = start_result_session(
            session_key,
            [
                os.sys.executable,
                "-m",
                "engine.worker.projection_worker",
                str(spec_path),
            ],
            root,
            metadata,
        )
        deadline = time.monotonic() + PROJECTION_WORKER_START_SECONDS
        while time.monotonic() < deadline:
            if session.poll() is not None:
                detail = session.stderr_text()[-4000:].strip()
                raise RuntimeError(
                    detail or "Projection Worker exited during startup."
                )
            if ready_path.is_file() and socket_path.exists():
                _ready_document(session)
                return session
            time.sleep(0.02)
        raise RuntimeError("Projection Worker did not become ready in time.")
    except BaseException as primary_error:
        primary_traceback = primary_error.__traceback__
        cleanup_error = None
        if session is not None:
            try:
                _cleanup_session(registry, session, terminate=True)
            except BaseException as exc:
                cleanup_error = exc
        else:
            try:
                owner.cleanup()
            except BaseException as exc:
                cleanup_error = exc
        if cleanup_error is not None:
            primary_error.__context__ = cleanup_error
        raise primary_error.with_traceback(primary_traceback)


def _worker_slot(kind, evidence):
    digest = hashlib.sha256(
        (kind + "\0" + str(evidence.get("contentDigest") or "")).encode("utf-8")
    ).digest()
    return int.from_bytes(digest[:8], "big") % PROJECTION_WORKER_POOL_SIZE


def _worker_session(kind, evidence):
    registry = process_session.PROCESS_SESSIONS
    if registry.is_stopping("result:"):
        raise RuntimeError("Engine is stopping and cannot start a Projection Worker.")
    slot = _worker_slot(kind, evidence)
    session_key = _session_key(slot)
    with _START_LOCK:
        session = registry.get(session_key)
        if session is not None and session.poll() is None:
            _ready_document(session)
            return session
        if session is not None:
            _cleanup_session(registry, session, terminate=False)
        return _start_worker(slot)


def _projection_request(
    kind,
    evidence,
    paths,
    temporary_modules,
    module_definitions,
    destination_path,
    *,
    projection_format="rows",
    window=None,
    priority="interactive",
):
    if kind not in {"backtest", "sample"}:
        raise ValueError("Projection Worker kind is invalid.")
    destination = Path(destination_path).resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    serialized_evidence = {
        key: str(value) if key == "path" else value
        for key, value in evidence.items()
        if key != "archiveIdentity"
    }
    request_id = uuid.uuid4().hex
    request = {
        "schemaVersion": 2,
        "requestId": request_id,
        "kind": kind,
        "evidence": serialized_evidence,
        "paths": paths,
        "temporaryModules": temporary_modules,
        "moduleDefinitions": module_definitions,
        "outputPath": str(destination),
        "projectionFormat": projection_format,
        "window": window,
        "priority": priority,
    }
    session = _worker_session(kind, evidence)
    transport_error = None
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(PROJECTION_WORKER_CONNECT_SECONDS)
            connection.connect(session.metadata["socketPath"])
            connection.settimeout(None)
            _send_message(connection, request)
            response = _receive_message(connection)
    except BaseException as exc:
        transport_error = exc
    if transport_error is not None:
        cleanup_error = None
        try:
            _cleanup_session(
                process_session.PROCESS_SESSIONS,
                session,
                terminate=True,
            )
        except BaseException as exc:
            cleanup_error = exc
        if cleanup_error is not None:
            transport_error.__context__ = cleanup_error
        raise RuntimeError("Projection Worker request channel failed.") from transport_error
    require_exact_fields(
        response,
        allowed={
            "schemaVersion", "requestId", "status", "errorType",
            "errorMessage", "cacheStatus", "workerPid", "durationMs",
        },
        required={
            "schemaVersion", "requestId", "status", "errorType",
            "errorMessage", "cacheStatus", "workerPid", "durationMs",
        },
        label="Projection Worker response",
    )
    if response["schemaVersion"] != 1 or response["requestId"] != request_id:
        raise RuntimeError("Projection Worker response identity is invalid.")
    if response["status"] == "error":
        if response["errorType"] == "ValueError":
            raise ValueError(response["errorMessage"])
        raise RuntimeError(response["errorMessage"])
    if (
        response["status"] != "ok"
        or response["errorType"] is not None
        or response["errorMessage"] is not None
        or not destination.is_file()
        or destination.is_symlink()
    ):
        destination.unlink(missing_ok=True)
        raise RuntimeError("Projection Worker response is invalid.")
    return destination


def write_result_projection_in_worker(
    evidence,
    paths,
    temporary_modules,
    module_definitions,
    destination_path,
    *,
    projection_format="rows",
    window=None,
    priority="interactive",
):
    return _projection_request(
        "backtest", evidence, paths, temporary_modules,
        module_definitions, destination_path,
        projection_format=projection_format,
        window=window,
        priority=priority,
    )


def write_sample_result_projection_in_worker(
    evidence,
    paths,
    temporary_modules,
    module_definitions,
    destination_path,
    *,
    projection_format="rows",
    window=None,
    priority="interactive",
):
    return _projection_request(
        "sample", evidence, paths, temporary_modules,
        module_definitions, destination_path,
        projection_format=projection_format,
        window=window,
        priority=priority,
    )


def projection_worker_status():
    """Return bounded Engine diagnostics without starting the Worker."""

    workers = []
    for slot in range(PROJECTION_WORKER_POOL_SIZE):
        session = process_session.PROCESS_SESSIONS.get(_session_key(slot))
        if session is None or session.poll() is not None:
            continue
        status_path = Path(session.metadata["statusPath"])
        status = {}
        if status_path.is_file() and not status_path.is_symlink():
            try:
                status = json.loads(status_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                status = {}
        workers.append({
            "slot": slot,
            "supervisorPid": session.process.pid,
            **status,
        })
    if not workers:
        return {"running": False}
    primary = workers[0]
    return {
        "running": True,
        "poolSize": PROJECTION_WORKER_POOL_SIZE,
        "runningWorkers": len(workers),
        "requests": sum(item.get("requests", 0) for item in workers),
        "cacheHits": sum(item.get("cacheHits", 0) for item in workers),
        "cacheMisses": sum(item.get("cacheMisses", 0) for item in workers),
        "planCacheHits": sum(item.get("planCacheHits", 0) for item in workers),
        "planCacheMisses": sum(item.get("planCacheMisses", 0) for item in workers),
        "supervisorPid": primary["supervisorPid"],
        "workerPid": primary.get("workerPid"),
        "workers": workers,
    }


def projection_source_generation(kind, evidence):
    """Return the current sticky Worker generation without starting it."""

    if kind not in {"backtest", "sample"} or not isinstance(evidence, dict):
        raise ValueError("Projection Worker source identity is invalid.")
    slot = _worker_slot(kind, evidence)
    session = process_session.PROCESS_SESSIONS.get(_session_key(slot))
    if session is None or session.poll() is not None:
        return None
    try:
        ready = _ready_document(session)
    except (OSError, ValueError, RuntimeError):
        return None
    return f"{slot}:{ready['pid']}"


def shutdown_projection_worker():
    """Stop only the resident worker without closing Result Runtime authority."""

    with _START_LOCK:
        registry = process_session.PROCESS_SESSIONS
        stopped = False
        for slot in range(PROJECTION_WORKER_POOL_SIZE):
            session = registry.get(_session_key(slot))
            if session is None:
                continue
            _cleanup_session(registry, session, terminate=True)
            stopped = True
        return stopped


__all__ = (
    "PROJECTION_WORKER_SESSION_KEY",
    "PROJECTION_WORKER_POOL_SIZE",
    "projection_worker_status",
    "projection_source_generation",
    "shutdown_projection_worker",
    "write_result_projection_in_worker",
    "write_sample_result_projection_in_worker",
)
