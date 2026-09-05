"""Resident isolated worker for Backtest and Sample Result projections."""

from __future__ import annotations

import hashlib
import os
import signal
import socket
import struct
import sys
import time
import traceback
from collections import OrderedDict
from pathlib import Path

from engine.archive import backtest_result as backtest_archive
from engine.archive import sample_result as sample_archive
from engine.composition.result_projection import (
    compile_result_projection_plan,
    project_result,
    project_result_frames,
)
from engine.composition.sample_result_projection import (
    project_sample_result,
    project_sample_result_frames,
)
from engine.contracts import strict_json
from engine.contracts.module import require_exact_fields
from engine.runtime import process_session
from engine.runtime import process_supervision
from engine.runtime.result_projection import (
    write_verified_result_projection,
    write_verified_result_projection_from_frames,
)
from engine.runtime.sample_result_projection import (
    write_verified_sample_result_projection,
    write_verified_sample_result_projection_from_frames,
)


def _receive_exact(connection, size):
    chunks = []
    remaining = size
    while remaining:
        chunk = connection.recv(remaining)
        if not chunk:
            raise ValueError("Projection Worker request ended early.")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _receive_message(connection, maximum):
    size = struct.unpack("!Q", _receive_exact(connection, 8))[0]
    if size < 2 or size > maximum:
        raise ValueError("Projection Worker request size is invalid.")
    return strict_json.loads(_receive_exact(connection, size))


def _send_message(connection, value, maximum):
    payload = strict_json.dumps(
        value, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    if len(payload) > maximum:
        raise RuntimeError("Projection Worker response is too large.")
    connection.sendall(struct.pack("!Q", len(payload)) + payload)


def _write_atomic(path, value):
    path = Path(path)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        strict_json.dumps(value, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
    os.replace(temporary, path)


class ProjectionCache:
    def __init__(self, *, entry_bytes, total_bytes, max_entries):
        self.entry_bytes = entry_bytes
        self.total_bytes = total_bytes
        self.max_entries = max_entries
        self.entries = OrderedDict()
        self.source_bytes = 0

    @staticmethod
    def key(kind, evidence):
        payload = strict_json.dumps(
            {"kind": kind, "evidence": evidence},
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()

    def get(self, key, identity):
        entry = self.entries.get(key)
        if entry is None:
            return None
        if entry["identity"] != identity:
            self.remove(key)
            return None
        self.entries.move_to_end(key)
        return entry["frames"]

    def remove(self, key):
        entry = self.entries.pop(key, None)
        if entry is not None:
            self.source_bytes -= entry["sourceBytes"]

    def put(self, key, identity, frames, source_bytes):
        if source_bytes > self.entry_bytes:
            return False
        self.remove(key)
        self.entries[key] = {
            "identity": identity,
            "frames": frames,
            "sourceBytes": source_bytes,
        }
        self.source_bytes += source_bytes
        while (
            len(self.entries) > self.max_entries
            or self.source_bytes > self.total_bytes
        ):
            _old_key, old = self.entries.popitem(last=False)
            self.source_bytes -= old["sourceBytes"]
        return key in self.entries


class ProjectionPlanCache:
    """Bounded cache of pure temporary Graph compilation proofs."""

    def __init__(self, max_entries=64):
        self.max_entries = max_entries
        self.entries = OrderedDict()

    @staticmethod
    def key(evidence, temporary_modules, module_definitions):
        payload = strict_json.dumps(
            {
                "dataKeys": evidence["dataKeys"],
                "temporaryModules": temporary_modules,
                "moduleDefinitions": module_definitions,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()

    def get_or_compile(self, evidence, temporary_modules, module_definitions):
        if not temporary_modules:
            return None, "none"
        key = self.key(evidence, temporary_modules, module_definitions)
        plan = self.entries.get(key)
        if plan is not None:
            self.entries.move_to_end(key)
            return plan, "hit"
        plan = compile_result_projection_plan(
            {"dataKeys": evidence["dataKeys"]},
            temporary_modules,
            module_definitions,
        )
        self.entries[key] = plan
        self.entries.move_to_end(key)
        while len(self.entries) > self.max_entries:
            self.entries.popitem(last=False)
        return plan, "miss"


def _archive_identity(kind, path):
    directory = Path(path).parent
    if kind == "backtest":
        return backtest_archive.sealed_archive_identity(
            directory, label="Projection Worker Backtest Result archive"
        )
    return sample_archive.sealed_archive_identity(
        directory, label="Projection Worker Sample Result archive"
    )


def _require_request(request):
    fields = {
        "schemaVersion", "requestId", "kind", "evidence", "paths",
        "temporaryModules", "moduleDefinitions", "outputPath",
        "projectionFormat", "window", "priority",
    }
    require_exact_fields(
        request,
        allowed=fields,
        required=fields,
        label="Projection Worker request",
    )
    if request["schemaVersion"] != 2:
        raise ValueError("Projection Worker request schemaVersion is invalid.")
    if (
        not isinstance(request["requestId"], str)
        or not request["requestId"]
        or request["kind"] not in {"backtest", "sample"}
        or not isinstance(request["paths"], list)
        or not isinstance(request["temporaryModules"], list)
        or not isinstance(request["moduleDefinitions"], dict)
        or not isinstance(request["evidence"], dict)
        or request["projectionFormat"] not in {"rows", "columns-v2"}
        or request["priority"] not in {"interactive", "background"}
    ):
        raise ValueError("Projection Worker request is invalid.")
    kind = request["kind"]
    evidence_fields = (
        {
            "path", "manifest", "contentDigest", "resultSize", "request",
            "metrics", "dataKeys", "executionChain",
        }
        if kind == "backtest"
        else {
            "path", "manifest", "contentDigest", "resultSize", "dataKeys",
            "execution", "sampleFrameContract",
        }
    )
    require_exact_fields(
        request["evidence"],
        allowed=evidence_fields,
        required=evidence_fields,
        label="Projection Worker archive evidence",
    )
    result_path = Path(request["evidence"]["path"]).resolve()
    output_path = Path(request["outputPath"]).resolve()
    if (
        not result_path.is_file()
        or result_path.is_symlink()
        or output_path == result_path
        or output_path.is_symlink()
    ):
        raise ValueError("Projection Worker paths are invalid.")
    request["evidence"]["path"] = result_path
    request["outputPath"] = output_path
    return request


def _execute_projection(request, frames, temporary_plan=None):
    kind = request["kind"]
    if temporary_plan is not None:
        if kind == "backtest":
            projector = (
                write_verified_result_projection_from_frames
                if frames is not None
                else write_verified_result_projection
            )
        else:
            projector = (
                write_verified_sample_result_projection_from_frames
                if frames is not None
                else write_verified_sample_result_projection
            )
        arguments = (
            (request["evidence"], frames, request["paths"], request["outputPath"])
            if frames is not None
            else (request["evidence"], request["paths"], request["outputPath"])
        )
        projector(
            *arguments,
            temporary_plan=temporary_plan,
            projection_format=request["projectionFormat"],
            window=request["window"],
        )
        return
    if frames is not None:
        projector = (
            project_result_frames
            if kind == "backtest"
            else project_sample_result_frames
        )
        projector(
            request["evidence"],
            frames,
            request["paths"],
            request["temporaryModules"],
            request["moduleDefinitions"],
            request["outputPath"],
            projection_format=request["projectionFormat"],
            window=request["window"],
        )
        return
    projector = project_result if kind == "backtest" else project_sample_result
    projector(
        request["evidence"],
        request["paths"],
        request["temporaryModules"],
        request["moduleDefinitions"],
        request["outputPath"],
        projection_format=request["projectionFormat"],
        window=request["window"],
    )


def _clean_executor_descendants():
    forced = False
    cleanup_error = None
    while True:
        descendants, active = process_session.active_descendants()
        if not active:
            no_children = process_session.reap_children()
            _remaining, active = process_session.active_descendants()
            if (
                not active
                and no_children
                and process_session.reap_children()
            ):
                break
        else:
            forced = True
        try:
            process_supervision.terminate_descendants(
                os.getpid(),
                descendants,
                terminate_grace=0.1,
                kill_grace=1.0,
            )
        except BaseException as exc:
            cleanup_error = cleanup_error or exc
        process_session.reap_children()
        time.sleep(0.01)
    if cleanup_error is not None:
        raise cleanup_error
    return forced


def _project_in_executor(
    request,
    frames,
    temporary_plan,
    runtime_root,
    server_fd,
    connection_fd,
):
    outcome_token = hashlib.sha256(
        request["requestId"].encode("utf-8")
    ).hexdigest()
    outcome_path = runtime_root / f"outcome-{outcome_token}.json"
    outcome_path.unlink(missing_ok=True)
    executor_pid = os.fork()
    if executor_pid == 0:
        try:
            os.close(server_fd)
            os.close(connection_fd)
            parent_pid = os.getppid()
            process_session.become_child_subreaper()
            process_session.set_parent_death_signal(signal.SIGTERM)
            if os.getppid() != parent_pid:
                raise RuntimeError("Projection executor lost its Worker authority.")
            try:
                _execute_projection(request, frames, temporary_plan)
                outcome = {
                    "schemaVersion": 1,
                    "status": "ok",
                    "errorType": None,
                    "errorMessage": None,
                }
            except BaseException as exc:
                request["outputPath"].unlink(missing_ok=True)
                outcome = {
                    "schemaVersion": 1,
                    "status": "error",
                    "errorType": type(exc).__name__,
                    "errorMessage": str(exc) or type(exc).__name__,
                }
            try:
                forced = _clean_executor_descendants()
            except BaseException as exc:
                request["outputPath"].unlink(missing_ok=True)
                outcome = {
                    "schemaVersion": 1,
                    "status": "error",
                    "errorType": type(exc).__name__,
                    "errorMessage": str(exc) or type(exc).__name__,
                }
            else:
                if forced and outcome["status"] == "ok":
                    request["outputPath"].unlink(missing_ok=True)
                    outcome = {
                        "schemaVersion": 1,
                        "status": "error",
                        "errorType": "RuntimeError",
                        "errorMessage": (
                            "Projection executor required forced descendant cleanup."
                        ),
                    }
            _write_atomic(outcome_path, outcome)
            os._exit(0)
        except BaseException:
            os._exit(1)
    _pid, wait_status = os.waitpid(executor_pid, 0)
    try:
        if not os.WIFEXITED(wait_status) or os.WEXITSTATUS(wait_status) != 0:
            request["outputPath"].unlink(missing_ok=True)
            raise RuntimeError("Projection executor exited without an outcome.")
        if outcome_path.is_symlink() or not outcome_path.is_file():
            request["outputPath"].unlink(missing_ok=True)
            raise RuntimeError("Projection executor omitted its exact outcome.")
        outcome = strict_json.loads(outcome_path.read_bytes())
        fields = {"schemaVersion", "status", "errorType", "errorMessage"}
        require_exact_fields(
            outcome,
            allowed=fields,
            required=fields,
            label="Projection executor outcome",
        )
        if outcome["schemaVersion"] != 1:
            raise RuntimeError("Projection executor outcome is invalid.")
        if outcome["status"] == "error":
            if outcome["errorType"] == "ValueError":
                raise ValueError(outcome["errorMessage"])
            raise RuntimeError(outcome["errorMessage"])
        if (
            outcome["status"] != "ok"
            or outcome["errorType"] is not None
            or outcome["errorMessage"] is not None
        ):
            raise RuntimeError("Projection executor outcome is invalid.")
    finally:
        outcome_path.unlink(missing_ok=True)


def _project(
    request,
    cache,
    plan_cache,
    runtime_root,
    server_fd,
    connection_fd,
):
    kind = request["kind"]
    evidence = request["evidence"]
    identity = _archive_identity(kind, evidence["path"])
    key_evidence = {
        name: str(value) if name == "path" else value
        for name, value in evidence.items()
    }
    key = cache.key(kind, key_evidence)
    temporary_plan, plan_status = plan_cache.get_or_compile(
        evidence,
        request["temporaryModules"],
        request["moduleDefinitions"],
    )
    frames = cache.get(key, identity)
    if frames is not None:
        if request["temporaryModules"]:
            _project_in_executor(
                request,
                frames,
                temporary_plan,
                runtime_root,
                server_fd,
                connection_fd,
            )
        else:
            _execute_projection(request, frames)
        return "hit", plan_status
    source_size = evidence["resultSize"]
    capture = source_size <= cache.entry_bytes
    if not capture:
        if request["temporaryModules"]:
            _project_in_executor(
                request,
                None,
                temporary_plan,
                runtime_root,
                server_fd,
                connection_fd,
            )
        else:
            _execute_projection(request, None)
        return "streamed", plan_status
    captured_cycles = []
    captured_metadata = []
    projector = project_result if kind == "backtest" else project_sample_result
    capture_path = runtime_root / f"capture-{key}.json"
    capture_path.unlink(missing_ok=True)
    if request["temporaryModules"]:
        projector(
            evidence,
            ["dataKeys"],
            [],
            {},
            capture_path,
            capture_cycle=captured_cycles.append,
            capture_metadata=captured_metadata.append,
            projection_format="rows",
            window=None,
        )
        capture_path.unlink(missing_ok=True)
    else:
        projector(
            evidence,
            request["paths"],
            [],
            {},
            request["outputPath"],
            capture_cycle=captured_cycles.append,
            capture_metadata=captured_metadata.append,
            projection_format=request["projectionFormat"],
            window=request["window"],
        )
    final_identity = _archive_identity(kind, evidence["path"])
    if final_identity != identity:
        request["outputPath"].unlink(missing_ok=True)
        raise ValueError("Projection Worker archive changed during projection.")
    if len(captured_metadata) != 1:
        request["outputPath"].unlink(missing_ok=True)
        raise RuntimeError("Projection Worker did not capture exact metadata.")
    frames = {"cycles": captured_cycles, "metadata": captured_metadata[0]}
    cache.put(key, final_identity, frames, source_size)
    if request["temporaryModules"]:
        _project_in_executor(
            request,
            frames,
            temporary_plan,
            runtime_root,
            server_fd,
            connection_fd,
        )
    return "miss", plan_status


def _serve(spec):
    cache = ProjectionCache(
        entry_bytes=spec["cacheEntrySourceBytes"],
        total_bytes=spec["cacheTotalSourceBytes"],
        max_entries=spec["cacheMaxEntries"],
    )
    plan_cache = ProjectionPlanCache()
    socket_path = Path(spec["socketPath"])
    ready_path = Path(spec["readyPath"])
    status_path = Path(spec["statusPath"])
    runtime_root = status_path.parent
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        server.bind(str(socket_path))
        os.chmod(socket_path, 0o600)
        server.listen(32)
        counters = {
            "requests": 0,
            "cacheHits": 0,
            "cacheMisses": 0,
            "planCacheHits": 0,
            "planCacheMisses": 0,
        }
        _write_atomic(status_path, {"workerPid": os.getpid(), **counters})
        _write_atomic(ready_path, {"schemaVersion": 1, "pid": os.getpid()})
        while True:
            connection, _address = server.accept()
            with connection:
                started = time.perf_counter()
                request_id = None
                cache_status = "none"
                plan_status = "none"
                try:
                    request = _require_request(
                        _receive_message(connection, spec["messageMaxBytes"])
                    )
                    request_id = request["requestId"]
                    cache_status, plan_status = _project(
                        request,
                        cache,
                        plan_cache,
                        runtime_root,
                        server.fileno(),
                        connection.fileno(),
                    )
                    response = {
                        "schemaVersion": 1,
                        "requestId": request_id,
                        "status": "ok",
                        "errorType": None,
                        "errorMessage": None,
                        "cacheStatus": cache_status,
                        "workerPid": os.getpid(),
                        "durationMs": round(
                            (time.perf_counter() - started) * 1000, 3
                        ),
                    }
                except BaseException as exc:
                    response = {
                        "schemaVersion": 1,
                        "requestId": request_id,
                        "status": "error",
                        "errorType": type(exc).__name__,
                        "errorMessage": str(exc) or type(exc).__name__,
                        "cacheStatus": cache_status,
                        "workerPid": os.getpid(),
                        "durationMs": round(
                            (time.perf_counter() - started) * 1000, 3
                        ),
                    }
                counters["requests"] += 1
                if cache_status == "hit":
                    counters["cacheHits"] += 1
                elif cache_status in {"miss", "streamed"}:
                    counters["cacheMisses"] += 1
                if plan_status == "hit":
                    counters["planCacheHits"] += 1
                elif plan_status == "miss":
                    counters["planCacheMisses"] += 1
                _write_atomic(status_path, {"workerPid": os.getpid(), **counters})
                _send_message(connection, response, spec["messageMaxBytes"])
    finally:
        server.close()
        socket_path.unlink(missing_ok=True)


def main():
    if len(sys.argv) != 2:
        raise SystemExit(
            "usage: python -m engine.worker.projection_worker SPEC_PATH"
        )
    spec_path = Path(sys.argv[1]).resolve()
    if not spec_path.is_file() or spec_path.is_symlink():
        raise ValueError("Projection Worker specification path is invalid.")
    spec = strict_json.loads(spec_path.read_bytes())
    fields = {
        "schemaVersion", "socketPath", "readyPath", "statusPath",
        "cacheEntrySourceBytes", "cacheTotalSourceBytes", "cacheMaxEntries",
        "messageMaxBytes",
    }
    require_exact_fields(
        spec,
        allowed=fields,
        required=fields,
        label="Projection Worker specification",
    )
    numeric = (
        "cacheEntrySourceBytes", "cacheTotalSourceBytes", "cacheMaxEntries",
        "messageMaxBytes",
    )
    if (
        spec["schemaVersion"] != 1
        or any(
            isinstance(spec[field], bool)
            or not isinstance(spec[field], int)
            or spec[field] < 1
            for field in numeric
        )
        or spec["cacheEntrySourceBytes"] > spec["cacheTotalSourceBytes"]
    ):
        raise ValueError("Projection Worker specification is invalid.")
    roots = {Path(spec[field]).resolve().parent for field in (
        "socketPath", "readyPath", "statusPath"
    )}
    if roots != {spec_path.parent}:
        raise ValueError("Projection Worker authority paths are invalid.")
    _serve(spec)


if __name__ == "__main__":
    try:
        main()
    except BaseException as error:
        traceback.print_exception(error, file=sys.stderr)
        raise SystemExit(1)
