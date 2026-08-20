"""Durable JSON state and history owned by the Engine control repository."""

from __future__ import annotations

import fcntl
import hashlib
import os
import tempfile
import threading
from contextlib import contextmanager
from pathlib import Path

from engine.contracts import strict_json
from engine.contracts.archive import require_resource_path_segment
from engine.control.owner import assert_control_access
from engine.core import clock as engine_clock


_CONTROL_STATE_THREAD_LOCK = threading.RLock()
_CONTROL_STATE_LOCAL = threading.local()


def atomic_write_json(path, payload):
    """Replace one JSON file only after its bytes and directory are durable."""

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{target.name}.",
        suffix=".tmp",
        dir=str(target.parent),
    )
    primary_error = None
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(strict_json.dumps(payload, indent=2))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, target)
        directory_fd = os.open(target.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except BaseException as exc:
        primary_error = exc
        raise
    finally:
        if os.path.exists(tmp_name):
            try:
                os.unlink(tmp_name)
            except BaseException:
                if primary_error is None:
                    raise


def state_path(config, name):
    """Resolve one control-state path after checking Engine ownership."""

    assert_control_access(config)
    name = require_resource_path_segment(name, label="Control state file name")
    root = Path(config["controlRoot"]).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root / name


@contextmanager
def control_state_lock(config):
    """Serialize a re-entrant control transaction within one Engine root."""

    lock_path = state_path(config, ".control.lock")
    with _CONTROL_STATE_THREAD_LOCK:
        depth = getattr(_CONTROL_STATE_LOCAL, "depth", 0)
        active_path = getattr(_CONTROL_STATE_LOCAL, "path", None)
        if depth:
            if active_path != str(lock_path):
                raise RuntimeError(
                    "One control transaction may not span different Engine roots."
                )
            _CONTROL_STATE_LOCAL.depth = depth + 1
            try:
                yield
            finally:
                _CONTROL_STATE_LOCAL.depth -= 1
            return
        with lock_path.open("a+", encoding="utf-8") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                # Close the check/acquire race with an Engine owner claim that
                # happened after state_path() but before this transaction won
                # the advisory lock.
                assert_control_access(config)
                _CONTROL_STATE_LOCAL.depth = 1
                _CONTROL_STATE_LOCAL.path = str(lock_path)
                try:
                    yield
                finally:
                    _CONTROL_STATE_LOCAL.depth = 0
                    _CONTROL_STATE_LOCAL.path = None
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def load_json_file(path, default):
    path = Path(path)
    if not path.exists():
        return default
    with path.open(encoding="utf-8") as handle:
        return strict_json.load(handle)


def load_state(config, name, default):
    return load_json_file(state_path(config, name), default)


def save_state(config, name, payload):
    atomic_write_json(state_path(config, name), payload)


def _json_digest(payload):
    data = strict_json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def _append_history_event_locked(config, event_type, payload):
    event = {
        "schemaVersion": 1,
        "timestamp": engine_clock.utc_now(),
        "type": event_type,
        "payload": payload,
    }
    event["id"] = (
        f"{event['timestamp'].replace(':', '').replace('.', '')}-"
        f"{_json_digest(event)[:12]}"
    )

    path = state_path(config, "events.jsonl")
    encoded = (
        strict_json.dumps(event, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        original_size = os.fstat(descriptor).st_size
        written = os.write(descriptor, encoded)
        if written != len(encoded):
            incomplete_error = OSError(
                f"Control history append was incomplete: {written}/{len(encoded)} bytes."
            )
            try:
                os.ftruncate(descriptor, original_size)
                os.fsync(descriptor)
            except BaseException as rollback_error:
                incomplete_error.__context__ = rollback_error
            raise incomplete_error
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)
    return event


def append_history_event(config, event_type, payload):
    """Durably append one complete event in control-transaction order."""

    with control_state_lock(config):
        return _append_history_event_locked(config, event_type, payload)


def load_history_events(config, limit=100):
    with control_state_lock(config):
        path = state_path(config, "events.jsonl")
        if not path.exists():
            return []
        events = []
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                events.append(strict_json.loads(line))
    if limit and limit > 0:
        return events[-limit:]
    return events


def sanitize_history_event(event):
    result = dict(event or {})
    payload = result.get("payload")
    if isinstance(payload, dict):
        compact = {}
        keep_keys = [
            "accepted",
            "status",
            "pipelineId",
            "version",
            "name",
            "moduleId",
            "instanceId",
            "backtestId",
            "datasetId",
            "datasetVersionId",
            "contentHash",
            "source",
            "manifestHash",
        ]
        for key in keep_keys:
            if key in payload:
                compact[key] = payload[key]
        if isinstance(payload.get("dataset"), dict):
            dataset = payload["dataset"]
            compact.update({
                "datasetId": dataset.get("datasetId"),
                "datasetVersionId": dataset.get("latestVersionId"),
                "name": dataset.get("name"),
                "source": dataset.get("source"),
                "status": dataset.get("status"),
            })
        if isinstance(payload.get("backtest"), dict):
            backtest = payload["backtest"]
            compact.update({
                "backtestId": backtest.get("backtestId"),
                "pipelineId": backtest.get("pipelineId"),
                "datasetId": backtest.get("datasetId"),
                "status": backtest.get("status"),
                "runner": backtest.get("runner"),
                "metrics": backtest.get("metrics"),
            })
        result["payload"] = {
            key: value
            for key, value in compact.items()
            if value not in (None, "", [], {})
        }
    return result


def load_sanitized_history_events(config, limit=100):
    return [
        sanitize_history_event(event)
        for event in load_history_events(config, limit)
    ]


__all__ = (
    "append_history_event",
    "atomic_write_json",
    "control_state_lock",
    "load_history_events",
    "load_json_file",
    "load_sanitized_history_events",
    "load_state",
    "sanitize_history_event",
    "save_state",
    "state_path",
)
