"""Supervise disposable Result projection and verification workers."""

from __future__ import annotations

import os
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path

from engine.contracts import result as result_contracts
from engine.contracts import strict_json
from engine.runtime import process_session
from engine.runtime import result_stream


RESULT_RUNTIME_POLL_SECONDS = 0.1
RESULT_RUNTIME_STDERR_TAIL_BYTES = 64 * 1024
_RESULT_SESSION_PREFIX = "result:"
_PENDING_RELEASE_LOCK = threading.Lock()
_PENDING_RELEASES = {}
_VERIFIER_OUTCOME_FIELDS = frozenset({
    "schemaVersion",
    "status",
    "shardIndex",
    "lineCount",
    "validatedCount",
    "firstCycleId",
    "lastCycleId",
    "errorLocalIndex",
    "errorType",
    "errorMessage",
})
_CYCLE_INDEX_TOKEN = "__ENGINE_RESULT_CYCLE_INDEX__"


def _cleanup_released_runtime(metadata):
    """Release output/scratch only after the outer authority is gone."""

    first_error = None
    if metadata.get("removeDestinationOnRelease"):
        try:
            Path(metadata["destination"]).unlink(missing_ok=True)
        except BaseException as exc:
            first_error = exc
    scratch_owner = metadata.get("scratchOwner")
    if scratch_owner is not None:
        try:
            scratch_owner.cleanup()
        except BaseException as exc:
            first_error = first_error or exc
    return first_error


def _retain_pending_release(key, metadata):
    with _PENDING_RELEASE_LOCK:
        _PENDING_RELEASES.setdefault(str(key), dict(metadata))


def _release_pending(key):
    key = str(key)
    with _PENDING_RELEASE_LOCK:
        metadata = _PENDING_RELEASES.get(key)
        if metadata is None:
            return None
        release_error = _cleanup_released_runtime(metadata)
        if release_error is None and _PENDING_RELEASES.get(key) is metadata:
            _PENDING_RELEASES.pop(key, None)
        return release_error


def _retry_pending_releases():
    with _PENDING_RELEASE_LOCK:
        keys = tuple(_PENDING_RELEASES)
    first_error = None
    for key in keys:
        release_error = _release_pending(key)
        first_error = first_error or release_error
    return first_error


def shutdown_result_runtimes():
    """Cancel every Result worker and retain any unproven authority."""

    registry = process_session.PROCESS_SESSIONS
    sessions = registry.snapshot(_RESULT_SESSION_PREFIX)
    first_error = None
    try:
        registry.shutdown(_RESULT_SESSION_PREFIX)
    except BaseException as exc:
        first_error = exc
    for key, session in sessions.items():
        if registry.is_current(key, session):
            continue
        _retain_pending_release(key, session.metadata)
    pending_error = _retry_pending_releases()
    first_error = first_error or pending_error
    if first_error is not None:
        raise first_error


def _start_result_session(session_key, command, runtime_root, release_metadata):
    """Keep the single Result process launcher behind the shared registry."""

    return process_session.PROCESS_SESSIONS.start(
        session_key,
        command,
        cwd=Path(__file__).resolve().parents[2],
        env=process_session.minimal_host_environment(home=runtime_root),
        max_output_bytes=RESULT_RUNTIME_STDERR_TAIL_BYTES,
        stderr_output_bytes=RESULT_RUNTIME_STDERR_TAIL_BYTES,
        metadata=release_metadata,
    )


def _strict_json_equal(left, right):
    return strict_json.exact_equal(left, right)


def _archive_identity(result_path):
    result_path = Path(result_path)
    directory = result_path.parent
    manifest_path = directory / "result-manifest.json"
    if (
        directory.is_symlink()
        or result_path.is_symlink()
        or manifest_path.is_symlink()
        or not directory.is_dir()
        or not result_path.is_file()
        or not manifest_path.is_file()
        or set(directory.iterdir()) != {result_path, manifest_path}
    ):
        raise ValueError("Result verification archive identity is invalid.")

    def identity(path):
        state = os.stat(path, follow_symlinks=False)
        return {
            "device": state.st_dev,
            "inode": state.st_ino,
            "mode": state.st_mode,
            "size": state.st_size,
            "modifiedNs": state.st_mtime_ns,
            "changedNs": state.st_ctime_ns,
        }

    archive_identity = {
        "directory": identity(directory),
        "result": identity(result_path),
        "manifest": identity(manifest_path),
    }
    if any(
        archive_identity[name]["mode"] & 0o222
        for name in ("directory", "result", "manifest")
    ):
        raise ValueError("Result verification archive must be sealed read-only.")
    return archive_identity


def _require_verifier_outcome(entry):
    outcome_path = entry["outcomePath"]
    ledger_path = entry["ledgerPath"]
    if (
        outcome_path.is_symlink()
        or not outcome_path.is_file()
        or ledger_path.is_symlink()
        or not ledger_path.is_file()
    ):
        raise RuntimeError("Result verifier completed without exact evidence files.")
    outcome = strict_json.loads(outcome_path.read_bytes())
    if not isinstance(outcome, dict) or set(outcome) != _VERIFIER_OUTCOME_FIELDS:
        raise RuntimeError("Result verifier emitted an invalid outcome.")
    line_count = outcome["lineCount"]
    validated_count = outcome["validatedCount"]
    if (
        outcome["schemaVersion"] != 1
        or outcome["shardIndex"] != entry["shardIndex"]
        or isinstance(line_count, bool)
        or not isinstance(line_count, int)
        or line_count < 1
        or isinstance(validated_count, bool)
        or not isinstance(validated_count, int)
        or validated_count < 0
        or validated_count > line_count
    ):
        raise RuntimeError("Result verifier emitted invalid cycle counts.")
    for field in ("firstCycleId", "lastCycleId"):
        if outcome[field] is not None and (
            not isinstance(outcome[field], str) or not outcome[field]
        ):
            raise RuntimeError("Result verifier emitted an invalid cycle boundary.")
    if outcome["status"] == "verified":
        if (
            validated_count != line_count
            or any(
                outcome[field] is not None
                for field in ("errorLocalIndex", "errorType", "errorMessage")
            )
            or (validated_count == 0)
            or outcome["firstCycleId"] is None
            or outcome["lastCycleId"] is None
        ):
            raise RuntimeError("Result verifier success evidence is incomplete.")
    elif outcome["status"] == "rejected":
        error_index = outcome["errorLocalIndex"]
        if (
            isinstance(error_index, bool)
            or not isinstance(error_index, int)
            or error_index < 0
            or error_index >= line_count
            or outcome["errorType"] != "ValueError"
            or not isinstance(outcome["errorMessage"], str)
            or not outcome["errorMessage"]
        ):
            raise RuntimeError("Result verifier rejection evidence is invalid.")
    else:
        raise RuntimeError("Result verifier outcome status is invalid.")
    return outcome


def _cleanup_verifier_entries(entries, *, terminate):
    registry = process_session.PROCESS_SESSIONS
    first_error = None
    for entry in entries:
        session = entry.get("session") or registry.get(entry["key"])
        if session is not None:
            entry["session"] = session
            try:
                registry.finish(entry["key"], session, terminate=terminate)
            except BaseException as exc:
                first_error = first_error or exc
        retained = session is not None and registry.is_current(entry["key"], session)
        if not retained:
            metadata = session.metadata if session is not None else entry["releaseMetadata"]
            _retain_pending_release(entry["key"], metadata)
            release_error = _release_pending(entry["key"])
            first_error = first_error or release_error
    return first_error


def _raise_earliest_cycle_domain_error(ledger_entries, rejection):
    """Raise only a cycle-domain error proven before a later shard failure."""

    with tempfile.TemporaryDirectory(
        prefix="trade-result-identity-merge-"
    ) as merge_root:
        duplicate_index = result_stream.merge_cycle_identity_ledgers(
            ledger_entries,
            Path(merge_root) / "identities.sqlite3",
        ) if ledger_entries else None
    if duplicate_index is not None and (
        rejection is None or duplicate_index <= rejection[0]
    ):
        raise ValueError(
            "Result cycleId values must be unique non-empty strings."
        )
    if rejection is not None:
        raise ValueError(rejection[1])


def verify_result_archive_in_runtimes(evidence):
    """Strictly verify every Result cycle in fresh Engine-owned processes."""

    registry = process_session.PROCESS_SESSIONS
    if registry.is_stopping(_RESULT_SESSION_PREFIX):
        raise RuntimeError("Engine is stopping and cannot verify a Result archive.")
    result_path = Path(evidence["path"])
    if result_path.is_symlink() or not result_path.is_file():
        raise ValueError("Result verification archive path is invalid.")
    result_path = result_path.resolve()
    initial_identity = _archive_identity(result_path)
    actual_manifest = strict_json.loads(
        (result_path.parent / "result-manifest.json").read_bytes()
    )
    if not _strict_json_equal(actual_manifest, evidence["manifest"]):
        raise ValueError(
            "Result verification manifest changed before strict validation."
        )
    plan = result_stream.plan_framed_cycle_ranges(
        result_path,
        expected_size=evidence["resultSize"],
    )
    entries = []
    primary_error = None
    primary_traceback = None
    cleanup_error = None
    result = None
    try:
        batch_id = uuid.uuid4().hex
        for shard_index, byte_range in enumerate(plan["ranges"]):
            runtime_owner = tempfile.TemporaryDirectory(
                prefix="trade-result-verifier-"
            )
            runtime_root = Path(runtime_owner.name)
            spec_path = runtime_root / "spec.json"
            outcome_path = runtime_root / "outcome.json"
            ledger_path = runtime_root / "identities.sqlite3"
            session_key = (
                f"{_RESULT_SESSION_PREFIX}verify:{batch_id}:{shard_index}"
            )
            release_metadata = {
                "executionRoot": str(runtime_root),
                "scratchOwner": runtime_owner,
            }
            entry = {
                "key": session_key,
                "session": None,
                "releaseMetadata": release_metadata,
                "shardIndex": shard_index,
                "outcomePath": outcome_path,
                "ledgerPath": ledger_path,
            }
            entries.append(entry)
            spec_path.write_text(
                strict_json.dumps({
                    "schemaVersion": 1,
                    "shardIndex": shard_index,
                    "resultPath": str(result_path),
                    "rangeStart": byte_range["start"],
                    "rangeEnd": byte_range["end"],
                    "finalRange": byte_range["final"],
                    "dataKeys": evidence["dataKeys"],
                    "ledgerPath": str(ledger_path),
                    "outcomePath": str(outcome_path),
                }, sort_keys=True, separators=(",", ":")),
                encoding="utf-8",
            )
            entry["session"] = _start_result_session(
                session_key,
                [
                    sys.executable,
                    "-m",
                    "engine.worker.result_verifier",
                    str(spec_path),
                ],
                runtime_root,
                release_metadata,
            )
        # Compile the same immutable declaration in the parent while the
        # fresh verifiers compile and scan their independent byte ranges.
        verified_cycle_validator = result_contracts.compile_cycle_validator(
            evidence["dataKeys"]
        )
        # The archive is sealed and its writer is already proven quiescent.
        # Perform the independent whole-file digest and metadata validation
        # while the fresh children scan cycles, but defer every observation
        # until after cycle outcomes.  This preserves the established error
        # priority (cycles, digest, indexed metadata) without serialising two
        # independent read-only proofs.
        actual_digest = None
        digest_error = None
        digest_traceback = None
        try:
            actual_digest = result_stream.hash_result_archive(
                result_path, expected_size=evidence["resultSize"]
            )
        except BaseException as exc:
            digest_error = exc
            digest_traceback = exc.__traceback__

        metadata = None
        metadata_read_error = None
        metadata_read_traceback = None
        metadata_validation_error = None
        metadata_validation_traceback = None
        if digest_error is None:
            try:
                metadata = result_stream.read_framed_result_metadata(
                    result_path,
                    metadata_start=plan["metadataStart"],
                    expected_size=evidence["resultSize"],
                )
                metrics = metadata.get("metrics") if isinstance(metadata, dict) else None
                claimed_cycle_count = (
                    metrics.get("cycleCount")
                    if isinstance(metrics, dict)
                    else 0
                )
                sample_contract = (
                    metadata.get("sampleFrameContract")
                    if isinstance(metadata, dict)
                    else None
                )
                claimed_first_cycle_id = (
                    sample_contract.get("firstCycleId")
                    if isinstance(sample_contract, dict)
                    else None
                )
                claimed_last_cycle_id = (
                    sample_contract.get("lastCycleId")
                    if isinstance(sample_contract, dict)
                    else None
                )
                try:
                    result_contracts.require_metadata(
                        metadata,
                        cycle_count=claimed_cycle_count,
                        first_cycle_id=claimed_first_cycle_id,
                        last_cycle_id=claimed_last_cycle_id,
                        execution_snapshot=evidence["request"]["executionSnapshot"],
                        verified_cycle_validator=verified_cycle_validator,
                    )
                except BaseException as exc:
                    metadata_validation_error = exc
                    metadata_validation_traceback = exc.__traceback__
            except BaseException as exc:
                metadata_read_error = exc
                metadata_read_traceback = exc.__traceback__
        while any(entry["session"].poll() is None for entry in entries):
            if registry.is_stopping(_RESULT_SESSION_PREFIX):
                raise RuntimeError("Engine stopped during Result verification.")
            time.sleep(RESULT_RUNTIME_POLL_SECONDS)
        # Reap every completed session before interpreting any shard.  Outcome
        # priority is nevertheless sequential by shard: a domain rejection or
        # duplicate proven in an earlier range wins over a later process
        # failure, while an earlier process failure prevents a later outcome
        # from being observed as primary.
        for entry in entries:
            entry["returnCode"] = entry["session"].wait()
        base_index = 0
        ledger_entries = []
        first_cycle_id = None
        last_cycle_id = None
        for entry in entries:
            if entry["returnCode"] != 0:
                _raise_earliest_cycle_domain_error(ledger_entries, None)
                detail = entry["session"].stderr_text()[-4000:].strip()
                raise RuntimeError(
                    detail
                    or "Result verifier exited without complete validation evidence."
                )
            outcome_error = None
            outcome_traceback = None
            try:
                outcome = _require_verifier_outcome(entry)
            except BaseException as exc:
                outcome_error = exc
                outcome_traceback = exc.__traceback__
            if outcome_error is not None:
                _raise_earliest_cycle_domain_error(ledger_entries, None)
                raise outcome_error.with_traceback(outcome_traceback)
            entry["baseIndex"] = base_index
            ledger_entries.append({
                "path": entry["ledgerPath"],
                "baseIndex": base_index,
            })
            if outcome["status"] == "rejected":
                absolute_index = base_index + outcome["errorLocalIndex"]
                message = outcome["errorMessage"].replace(
                    _CYCLE_INDEX_TOKEN, str(absolute_index)
                )
                _raise_earliest_cycle_domain_error(
                    ledger_entries,
                    (absolute_index, message),
                )
            if outcome["validatedCount"]:
                if first_cycle_id is None:
                    first_cycle_id = outcome["firstCycleId"]
                last_cycle_id = outcome["lastCycleId"]
            base_index += outcome["lineCount"]
        _raise_earliest_cycle_domain_error(ledger_entries, None)
        if digest_error is not None:
            raise digest_error.with_traceback(digest_traceback)
        if actual_digest != evidence["contentDigest"]:
            raise ValueError(
                "Result archive digest does not match its immutable index."
            )
        if metadata_read_error is not None:
            raise metadata_read_error.with_traceback(metadata_read_traceback)
        if (
            not _strict_json_equal(metadata["dataKeys"], evidence["dataKeys"])
            or not _strict_json_equal(
                metadata["executionChain"], evidence["executionChain"]
            )
            or not _strict_json_equal(metadata["metrics"], evidence["metrics"])
        ):
            raise ValueError(
                "Result archive content does not match its immutable metadata index."
            )
        metrics = metadata.get("metrics") if isinstance(metadata, dict) else None
        metrics_cycle_count = (
            metrics.get("cycleCount") if isinstance(metrics, dict) else None
        )
        valid_metrics_cycle_count = (
            type(metrics_cycle_count) is int and metrics_cycle_count >= 0
        )
        # require_metadata ordinarily reports this mismatch before inspecting
        # execution metadata.  Its concurrent call used the claimed count so
        # that it could validate the remaining immutable material early; keep
        # the original mismatch priority explicit here.
        if valid_metrics_cycle_count and metrics_cycle_count != base_index:
            raise ValueError("Result metrics.cycleCount does not match cycles.")
        if metadata_validation_error is not None:
            raise metadata_validation_error.with_traceback(
                metadata_validation_traceback
            )
        sample_contract = metadata["sampleFrameContract"]
        if {"firstCycleId", "lastCycleId"} <= set(sample_contract) and (
            sample_contract["firstCycleId"] != first_cycle_id
            or sample_contract["lastCycleId"] != last_cycle_id
        ):
            raise ValueError(
                "Result sampleFrameContract cycle boundaries do not match cycles."
            )
        if not _strict_json_equal(
            metadata, evidence["manifest"]["resultMetadata"]
        ):
            raise ValueError(
                "Result archive metadata does not exactly match its sealed manifest."
            )
        if not _strict_json_equal(
            evidence["manifest"]["catalog"]["metrics"], metadata["metrics"]
        ):
            raise ValueError(
                "Result archive catalog metrics do not match its content."
            )
        final_identity = _archive_identity(result_path)
        if final_identity != initial_identity:
            raise ValueError("Result archive changed during strict verification.")
        result = {
            "metadata": metadata,
            "cycleCount": base_index,
            "firstCycleId": first_cycle_id,
            "lastCycleId": last_cycle_id,
            "contentDigest": actual_digest,
            "archiveIdentity": final_identity,
        }
    except BaseException as exc:
        primary_error = exc
        primary_traceback = exc.__traceback__
    cleanup_error = _cleanup_verifier_entries(
        entries, terminate=primary_error is not None
    )
    if primary_error is None and cleanup_error is not None:
        primary_error = cleanup_error
        primary_traceback = cleanup_error.__traceback__
    if primary_error is not None:
        if cleanup_error is not None and cleanup_error is not primary_error:
            primary_error.__context__ = cleanup_error
        raise primary_error.with_traceback(primary_traceback)
    return result
def write_result_projection_in_runtime(
    evidence,
    paths,
    temporary_modules,
    module_definitions,
    destination_path,
):
    """Project temporary Modules in a supervised disposable Python process."""

    registry = process_session.PROCESS_SESSIONS
    if registry.is_stopping(_RESULT_SESSION_PREFIX):
        raise RuntimeError("Engine is stopping and cannot start a Result Runtime.")
    destination = Path(destination_path).resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    runtime_owner = tempfile.TemporaryDirectory(prefix="trade-result-runtime-")
    runtime_root = Path(runtime_owner.name)
    session_key = f"{_RESULT_SESSION_PREFIX}{uuid.uuid4().hex}"
    release_metadata = {
        "executionRoot": str(runtime_root),
        "destination": str(destination),
        "removeDestinationOnRelease": True,
        # A retained session owns its specification until a later shutdown
        # retry proves the outer supervisor is gone.
        "scratchOwner": runtime_owner,
    }
    session = None
    primary_error = None
    primary_traceback = None
    cleanup_error = None
    process_started = False
    try:
        spec_path = runtime_root / "spec.json"
        spec_path.write_text(
            strict_json.dumps({
                "schemaVersion": 2,
                "resultEvidence": {
                    "path": str(Path(evidence["path"]).resolve()),
                    "manifest": evidence["manifest"],
                    "contentDigest": evidence["contentDigest"],
                    "resultSize": evidence["resultSize"],
                    "request": evidence["request"],
                    "metrics": evidence["metrics"],
                    "dataKeys": evidence["dataKeys"],
                    "executionChain": evidence["executionChain"],
                },
                "paths": paths,
                "temporaryModules": temporary_modules,
                "moduleDefinitions": module_definitions,
                "outputPath": str(destination),
            }, sort_keys=True, separators=(",", ":")),
            encoding="utf-8",
        )
        process_started = True
        session = _start_result_session(
            session_key,
            [
                sys.executable,
                "-m",
                "engine.worker.result_runtime",
                str(spec_path),
            ],
            runtime_root,
            release_metadata,
        )
        if registry.is_stopping(_RESULT_SESSION_PREFIX):
            raise RuntimeError("Engine stopped before Result Runtime execution.")
        while session.poll() is None:
            if registry.is_stopping(_RESULT_SESSION_PREFIX):
                raise RuntimeError("Engine stopped during Result Runtime execution.")
            time.sleep(RESULT_RUNTIME_POLL_SECONDS)
        return_code = session.wait()
        if registry.is_stopping(_RESULT_SESSION_PREFIX):
            raise RuntimeError("Engine stopped during Result Runtime execution.")
        if return_code != 0:
            detail = session.stderr_text()[-4000:].strip()
            raise RuntimeError(
                detail or f"Result Runtime exited with code {return_code}."
            )
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
    retained = session is not None and registry.is_current(session_key, session)
    if primary_error is None and cleanup_error is None:
        try:
            valid_destination = destination.is_file() and not destination.is_symlink()
        except BaseException as exc:
            primary_error = exc
            primary_traceback = exc.__traceback__
        else:
            if not valid_destination:
                primary_error = RuntimeError(
                    "Result Runtime completed without its projected Result document."
                )
                primary_traceback = primary_error.__traceback__
            elif session is not None:
                session.metadata["removeDestinationOnRelease"] = False
            else:
                release_metadata["removeDestinationOnRelease"] = False
    if not retained:
        metadata = session.metadata if session is not None else release_metadata
        _retain_pending_release(session_key, metadata)
        scratch_error = _release_pending(session_key)
        cleanup_error = cleanup_error or scratch_error
    if primary_error is None and cleanup_error is not None:
        primary_error = cleanup_error
        primary_traceback = cleanup_error.__traceback__
    if primary_error is not None:
        if process_started and not retained:
            try:
                destination.unlink(missing_ok=True)
            except BaseException as exc:
                cleanup_error = cleanup_error or exc
        if cleanup_error is not None and cleanup_error is not primary_error:
            primary_error.__context__ = cleanup_error
        raise primary_error.with_traceback(primary_traceback)
    return destination


__all__ = (
    "shutdown_result_runtimes",
    "verify_result_archive_in_runtimes",
    "write_result_projection_in_runtime",
)
