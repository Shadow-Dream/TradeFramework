"""Engine-owned exact persistent cache and single-flight for Result projections."""

from __future__ import annotations

import fcntl
import hashlib
import os
import shutil
import stat
import tempfile
import threading
from contextlib import contextmanager
from pathlib import Path

from engine.archive import version as version_archive
from engine.contracts import strict_json
from engine.contracts.module import require_exact_fields
from engine.core import clock
from engine.core import runtime_identity
from engine.repository import control_state


PROJECTION_CACHE_SCHEMA_VERSION = 1
PROJECTION_CACHE_CONTRACT_VERSION = 2
PROJECTION_CACHE_MAX_ENTRIES = 64
PROJECTION_CACHE_MAX_TOTAL_BYTES = 256 * 1024 * 1024
PROJECTION_CACHE_MAX_ENTRY_BYTES = 64 * 1024 * 1024
_LOCKS_GUARD = threading.Lock()
_KEY_LOCKS = {}


def _cache_root(config):
    control_root = Path(config["controlRoot"]).expanduser().absolute()
    control_root.mkdir(parents=True, exist_ok=True)
    if control_root.is_symlink() or not control_root.is_dir():
        raise ValueError("Engine control root is invalid for Projection cache.")
    root = control_root / "result-projections"
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    if root.is_symlink() or not root.is_dir() or root.resolve().parent != control_root.resolve():
        raise ValueError("Engine Projection cache root is invalid.")
    return root


def _module_identities(definitions):
    identities = []
    for key, definition in sorted((definitions or {}).items()):
        if not isinstance(definition, dict):
            raise ValueError("Projection Module Definition cache identity is invalid.")
        values = {
            "key": key,
            "kind": definition.get("kind"),
            "moduleId": definition.get("moduleId"),
            "version": definition.get("version"),
            "contentDigest": definition.get("contentDigest"),
        }
        if any(not isinstance(value, str) or not value for value in values.values()):
            raise ValueError("Projection Module Definition cache identity is incomplete.")
        identities.append(values)
    return identities


def projection_identity(
    *,
    kind,
    result_id,
    result_content_digest,
    paths,
    temporary_modules,
    module_definitions,
    projection_format,
    window,
):
    if kind not in {"backtest", "sample"}:
        raise ValueError("Projection cache Result kind is invalid.")
    if not isinstance(result_id, str) or not result_id:
        raise ValueError("Projection cache Result identity is invalid.")
    if not isinstance(result_content_digest, str) or not result_content_digest:
        raise ValueError("Projection cache Result digest is invalid.")
    return {
        "cacheContractVersion": PROJECTION_CACHE_CONTRACT_VERSION,
        "engineRuntime": runtime_identity.engine_runtime_identity(),
        "kind": kind,
        "resultId": result_id,
        "resultContentDigest": result_content_digest,
        "paths": sorted(paths),
        "temporaryModules": temporary_modules,
        "moduleDefinitions": _module_identities(module_definitions),
        "projectionFormat": projection_format,
        "window": window,
    }


def _key(identity):
    return version_archive.content_digest(identity).removeprefix("sha256:")


def _paths(root, key):
    if len(key) != 64 or any(character not in "0123456789abcdef" for character in key):
        raise ValueError("Projection cache key is invalid.")
    return (
        root / f"{key}.json",
        root / f"{key}.meta.json",
        root / f"{key}.lock",
    )


@contextmanager
def _single_flight(root, key):
    token = (str(root), key)
    with _LOCKS_GUARD:
        entry = _KEY_LOCKS.get(token)
        if entry is None:
            entry = {"lock": threading.Lock(), "users": 0}
            _KEY_LOCKS[token] = entry
        entry["users"] += 1
        local_lock = entry["lock"]
    local_lock.acquire()
    lock_fd = None
    try:
        _payload, _metadata, lock_path = _paths(root, key)
        flags = os.O_CREAT | os.O_RDWR
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        lock_fd = os.open(lock_path, flags, 0o600)
        state = os.fstat(lock_fd)
        if not stat.S_ISREG(state.st_mode) or state.st_mode & 0o077:
            raise ValueError("Projection cache single-flight authority is invalid.")
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        yield
    finally:
        if lock_fd is not None:
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
            finally:
                os.close(lock_fd)
        local_lock.release()
        with _LOCKS_GUARD:
            entry["users"] -= 1
            if entry["users"] == 0:
                _KEY_LOCKS.pop(token, None)


def _file_digest(path):
    digest = hashlib.sha256()
    size = 0
    with Path(path).open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            size += len(chunk)
            digest.update(chunk)
    return "sha256:" + digest.hexdigest(), size


def _metadata_record(identity, key, payload_path):
    payload_digest, payload_size = _file_digest(payload_path)
    if payload_size < 2 or payload_size > PROJECTION_CACHE_MAX_ENTRY_BYTES:
        raise ValueError("Projection cache payload size is outside its bound.")
    return {
        "schemaVersion": PROJECTION_CACHE_SCHEMA_VERSION,
        "cacheKey": "sha256:" + key,
        "identity": identity,
        "createdAt": clock.utc_now(),
        "payloadDigest": payload_digest,
        "payloadSize": payload_size,
    }


def _read_record(metadata_path, payload_path, identity, key):
    if metadata_path.is_symlink() or payload_path.is_symlink():
        raise ValueError("Projection cache entry cannot be a symbolic link.")
    if not metadata_path.is_file() or not payload_path.is_file():
        return None
    record = strict_json.loads(metadata_path.read_bytes())
    fields = {
        "schemaVersion", "cacheKey", "identity", "createdAt",
        "payloadDigest", "payloadSize",
    }
    require_exact_fields(
        record,
        allowed=fields,
        required=fields,
        label="Engine Projection cache record",
    )
    if (
        record["schemaVersion"] != PROJECTION_CACHE_SCHEMA_VERSION
        or record["cacheKey"] != "sha256:" + key
        or not strict_json.exact_equal(record["identity"], identity)
        or isinstance(record["payloadSize"], bool)
        or not isinstance(record["payloadSize"], int)
        or record["payloadSize"] < 2
        or record["payloadSize"] > PROJECTION_CACHE_MAX_ENTRY_BYTES
        or payload_path.stat().st_size != record["payloadSize"]
    ):
        raise ValueError("Engine Projection cache identity is incompatible.")
    payload_digest, payload_size = _file_digest(payload_path)
    if payload_size != record["payloadSize"] or payload_digest != record["payloadDigest"]:
        raise ValueError("Engine Projection cache payload digest is invalid.")
    return record


def _copy_atomic(source, destination):
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.name + ".tmp")
    temporary.unlink(missing_ok=True)
    try:
        with Path(source).open("rb") as reader, temporary.open("xb") as writer:
            shutil.copyfileobj(reader, writer, length=1024 * 1024)
            writer.flush()
            os.fsync(writer.fileno())
        os.replace(temporary, destination)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def _evict(root, keep_key):
    records = []
    for metadata_path in root.glob("*.meta.json"):
        if metadata_path.is_symlink() or not metadata_path.is_file():
            continue
        key = metadata_path.name.removesuffix(".meta.json")
        payload_path = root / f"{key}.json"
        try:
            size = payload_path.stat().st_size
            modified = metadata_path.stat().st_mtime_ns
        except OSError:
            continue
        records.append((modified, key, size, payload_path, metadata_path))
    records.sort()
    total = sum(item[2] for item in records)
    while (
        len(records) > PROJECTION_CACHE_MAX_ENTRIES
        or total > PROJECTION_CACHE_MAX_TOTAL_BYTES
    ):
        index = next(
            (position for position, item in enumerate(records) if item[1] != keep_key),
            None,
        )
        if index is None:
            break
        _modified, _key_value, size, payload_path, metadata_path = records.pop(index)
        payload_path.unlink(missing_ok=True)
        metadata_path.unlink(missing_ok=True)
        total -= size


def write_cached_projection(config, identity, destination_path, build):
    """Write an exact cached projection and return cache evidence.

    ``build`` must create the supplied new path through the authoritative
    Engine projection route.  Cache misses never select another execution
    implementation.
    """

    if not callable(build):
        raise TypeError("Projection cache build operation must be callable.")
    root = _cache_root(config)
    key = _key(identity)
    payload_path, metadata_path, _lock_path = _paths(root, key)
    with _single_flight(root, key):
        record = None
        try:
            record = _read_record(metadata_path, payload_path, identity, key)
        except (OSError, ValueError):
            payload_path.unlink(missing_ok=True)
            metadata_path.unlink(missing_ok=True)
        if record is not None:
            _copy_atomic(payload_path, destination_path)
            return {
                "hit": True,
                "cacheKey": "sha256:" + key,
                "payloadDigest": record["payloadDigest"],
                "payloadSize": record["payloadSize"],
            }
        with tempfile.TemporaryDirectory(
            prefix="trade-projection-cache-", dir=root
        ) as temporary_root:
            staged_payload = Path(temporary_root) / "projection.json"
            build(staged_payload)
            if staged_payload.is_symlink() or not staged_payload.is_file():
                raise RuntimeError("Projection cache build omitted its exact payload.")
            # Decode once at the trust boundary.  The cached bytes remain the
            # worker's canonical JSON rather than a host reserialization.
            value = strict_json.loads(staged_payload.read_bytes())
            if not isinstance(value, dict):
                raise ValueError("Projection cache payload must be a JSON object.")
            record = _metadata_record(identity, key, staged_payload)
            os.replace(staged_payload, payload_path)
            control_state.atomic_write_json(metadata_path, record)
        _copy_atomic(payload_path, destination_path)
        _evict(root, key)
        return {
            "hit": False,
            "cacheKey": "sha256:" + key,
            "payloadDigest": record["payloadDigest"],
            "payloadSize": record["payloadSize"],
        }


__all__ = (
    "PROJECTION_CACHE_CONTRACT_VERSION",
    "projection_identity",
    "write_cached_projection",
)
