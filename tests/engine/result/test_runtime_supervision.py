#!/usr/bin/env python3

import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from engine.contracts import strict_json
from engine.runtime import process_session
from engine.runtime import result_runtime
from engine.runtime import result_stream
from engine.worker import result_verifier


class PrimaryFailure(BaseException):
    pass


class CleanupFailure(BaseException):
    pass


class _FakeSession:
    def __init__(self, metadata, *, return_code=0, stderr=""):
        self.metadata = dict(metadata)
        self.return_code = return_code
        self.stderr = stderr
        self.terminate_errors = []
        self.close_errors = []
        self.terminate_calls = 0
        self.close_calls = 0
        self.wait_calls = 0

    def poll(self):
        return self.return_code

    def wait(self, timeout=None):
        del timeout
        self.wait_calls += 1
        return self.return_code

    def stderr_text(self):
        return self.stderr

    def terminate(self, **_options):
        self.terminate_calls += 1
        if self.terminate_errors:
            error = self.terminate_errors.pop(0)
            if error is not None:
                raise error

    def close(self):
        self.close_calls += 1
        if self.close_errors:
            error = self.close_errors.pop(0)
            if error is not None:
                raise error


class ResultRuntimeSupervisionTests(unittest.TestCase):
    def setUp(self):
        with result_runtime._PENDING_RELEASE_LOCK:
            result_runtime._PENDING_RELEASES.clear()
        self.registry = process_session.ProcessSessionRegistry()
        self.registry_patch = mock.patch.object(
            result_runtime.process_session,
            "PROCESS_SESSIONS",
            self.registry,
        )
        self.registry_patch.start()

    def tearDown(self):
        with result_runtime._PENDING_RELEASE_LOCK:
            result_runtime._PENDING_RELEASES.clear()
        self.registry_patch.stop()

    @staticmethod
    def _evidence(root):
        return {
            "path": Path(root) / "result.json",
            "manifest": {},
            "contentDigest": "sha256:" + ("0" * 64),
            "resultSize": 1,
            "request": {},
            "metrics": {},
            "dataKeys": {},
            "executionChain": {},
        }

    def _install_start(self, *, return_code=0, stderr="", write_output=True):
        captured = {}

        def start(key, command, **options):
            captured.update(key=key, command=command, options=options)
            captured["spec"] = result_runtime.strict_json.loads(
                Path(command[-1]).read_text(encoding="utf-8")
            )
            session = _FakeSession(
                options["metadata"],
                return_code=return_code,
                stderr=stderr,
            )
            captured["session"] = session
            self.registry._sessions[key] = session
            if write_output:
                Path(options["metadata"]["destination"]).write_text(
                    "{}", encoding="utf-8"
                )
            return session

        patcher = mock.patch.object(self.registry, "start", side_effect=start)
        patcher.start()
        self.addCleanup(patcher.stop)
        return captured

    @staticmethod
    def _verification_evidence(root, cycles):
        archive = Path(root) / "archive"
        archive.mkdir()
        metadata = {
            "schemaVersion": 8,
            "dataKeys": {},
            "metrics": {"cycleCount": len(cycles)},
            "executionChain": {},
            "sampleFrameContract": {},
        }
        encoded_cycles = ",\n".join(
            strict_json.dumps(
                cycle, sort_keys=True, separators=(",", ":")
            )
            for cycle in cycles
        )
        tail = "".join(
            "," + strict_json.dumps(key) + ":" + strict_json.dumps(
                value, sort_keys=True, separators=(",", ":")
            )
            for key, value in metadata.items()
        )
        result_path = archive / "result.json"
        result_path.write_text(
            '{"cycles":[\n'
            + encoded_cycles
            + ("\n]" if cycles else "]")
            + tail
            + "}",
            encoding="utf-8",
        )
        raw = result_path.read_bytes()
        digest = "sha256:" + hashlib.sha256(raw).hexdigest()
        manifest = {
            "schemaVersion": 4,
            "backtestId": "bt_01KZR8BEXCRN97RX3Q4YW1BHK8",
            "resultFile": "result.json",
            "contentDigest": digest,
            "size": len(raw),
            "catalog": {"metrics": metadata["metrics"]},
            "resultMetadata": metadata,
        }
        manifest_path = archive / "result-manifest.json"
        manifest_path.write_text(
            strict_json.dumps(manifest, sort_keys=True, separators=(",", ":")),
            encoding="utf-8",
        )
        result_path.chmod(result_path.stat().st_mode & ~0o222)
        manifest_path.chmod(manifest_path.stat().st_mode & ~0o222)
        archive.chmod(archive.stat().st_mode & ~0o222)
        return {
            "path": result_path,
            "manifest": manifest,
            "contentDigest": digest,
            "resultSize": len(raw),
            "request": {"executionSnapshot": {}},
            "metrics": metadata["metrics"],
            "dataKeys": {},
            "executionChain": {},
        }

    def _install_verifier_start(self, *, return_codes=(), stderrs=()):
        captured = []

        def start(key, command, **options):
            self.assertEqual(
                command[1:3], ["-m", "engine.worker.result_verifier"]
            )
            result_verifier.verify_specification(command[-1])
            shard_index = len(captured)
            session = _FakeSession(
                options["metadata"],
                return_code=(
                    return_codes[shard_index]
                    if shard_index < len(return_codes)
                    else 0
                ),
                stderr=(
                    stderrs[shard_index]
                    if shard_index < len(stderrs)
                    else ""
                ),
            )
            self.registry._sessions[key] = session
            captured.append((key, command, options, session))
            return session

        patcher = mock.patch.object(self.registry, "start", side_effect=start)
        patcher.start()
        self.addCleanup(patcher.stop)
        return captured

    def test_earlier_rejection_precedes_later_verifier_process_failure(self):
        cycles = [
            {
                "schemaVersion": 3,
                "cycleId": "",
                "decisionTime": "2026-01-01T00:00:00Z",
                "data": {},
            },
            {
                "schemaVersion": 3,
                "cycleId": "later",
                "decisionTime": "2026-01-01T00:00:00Z",
                "data": {},
            },
        ]
        with tempfile.TemporaryDirectory() as root, mock.patch.object(
            result_stream, "RESULT_VERIFICATION_TARGET_BYTES", 1
        ):
            evidence = self._verification_evidence(root, cycles)
            captured = self._install_verifier_start(
                return_codes=(0, 19),
                stderrs=("", "later verifier crashed"),
            )
            with self.assertRaisesRegex(
                ValueError, "cycleId values must be unique non-empty strings"
            ) as raised:
                result_runtime.verify_result_archive_in_runtimes(evidence)
        self.assertNotIn("later verifier crashed", str(raised.exception))
        self.assertEqual(len(captured), 2)
        self.assertTrue(all(item[3].wait_calls == 1 for item in captured))
        self.assertTrue(all(item[3].close_calls == 1 for item in captured))
        self.assertEqual(self.registry.snapshot("result:"), {})

    def test_earlier_verifier_process_failure_precedes_later_rejection(self):
        cycles = [
            {
                "schemaVersion": 3,
                "cycleId": "earlier",
                "decisionTime": "2026-01-01T00:00:00Z",
                "data": {},
            },
            {
                "schemaVersion": 3,
                "cycleId": "later-invalid",
                "decisionTime": "2026-01-01T00:00:00Z",
                "data": {},
                "unexpected": True,
            },
        ]
        with tempfile.TemporaryDirectory() as root, mock.patch.object(
            result_stream, "RESULT_VERIFICATION_TARGET_BYTES", 1
        ):
            evidence = self._verification_evidence(root, cycles)
            captured = self._install_verifier_start(
                return_codes=(23, 0),
                stderrs=("earlier verifier crashed", ""),
            )
            with self.assertRaisesRegex(
                RuntimeError, "earlier verifier crashed"
            ) as raised:
                result_runtime.verify_result_archive_in_runtimes(evidence)
        self.assertNotIn("unsupported field", str(raised.exception))
        self.assertEqual(len(captured), 2)
        self.assertTrue(all(item[3].wait_calls == 1 for item in captured))
        self.assertTrue(all(item[3].close_calls == 1 for item in captured))
        self.assertEqual(self.registry.snapshot("result:"), {})

    def test_worker_launch_uses_shared_result_session_and_no_parent_pid_spec(self):
        with tempfile.TemporaryDirectory() as root:
            destination = Path(root) / "projection.json"
            captured = self._install_start()
            returned = result_runtime.write_result_projection_in_runtime(
                self._evidence(root),
                ["metrics"],
                [{"instanceId": "temporary"}],
                {},
                destination,
            )
            self.assertEqual(returned, destination.resolve())
            self.assertTrue(captured["key"].startswith("result:"))
            self.assertEqual(
                captured["command"][1:3],
                ["-m", "engine.worker.result_runtime"],
            )
            self.assertNotIn("parentPid", captured["spec"])
            self.assertEqual(
                captured["options"]["env"]["HOME"],
                captured["options"]["metadata"]["executionRoot"],
            )
            self.assertEqual(self.registry.snapshot("result:"), {})
            self.assertTrue(destination.is_file())

    def test_parallel_verifiers_use_fresh_result_sessions_and_merge_all_shards(self):
        cycles = [
            {
                "schemaVersion": 3,
                "cycleId": f"cycle-{index}",
                "decisionTime": "2026-01-01T00:00:00Z",
                "data": {},
            }
            for index in range(3)
        ]
        with tempfile.TemporaryDirectory() as root, mock.patch.object(
            result_stream, "RESULT_VERIFICATION_TARGET_BYTES", 1
        ), mock.patch.object(
            result_runtime.result_contracts,
            "require_metadata",
        ):
            evidence = self._verification_evidence(root, cycles)
            captured = self._install_verifier_start()
            verified = result_runtime.verify_result_archive_in_runtimes(evidence)
            self.assertEqual(verified["cycleCount"], 3)
            self.assertEqual(verified["firstCycleId"], "cycle-0")
            self.assertEqual(verified["lastCycleId"], "cycle-2")
            self.assertGreater(len(captured), 1)
            self.assertTrue(all(key.startswith("result:verify:") for key, *_ in captured))
            self.assertEqual(self.registry.snapshot("result:"), {})
            self.assertTrue(all(
                not Path(options["metadata"]["executionRoot"]).exists()
                for _key, _command, options, _session in captured
            ))

    def test_parallel_verifiers_reject_a_cross_shard_duplicate(self):
        cycle = {
            "schemaVersion": 3,
            "cycleId": "duplicate",
            "decisionTime": "2026-01-01T00:00:00Z",
            "data": {},
        }
        with tempfile.TemporaryDirectory() as root, mock.patch.object(
            result_stream, "RESULT_VERIFICATION_TARGET_BYTES", 1
        ), mock.patch.object(
            result_runtime.result_contracts,
            "require_metadata",
        ):
            evidence = self._verification_evidence(root, [cycle, cycle])
            self._install_verifier_start()
            with self.assertRaisesRegex(ValueError, "cycleId values must be unique"):
                result_runtime.verify_result_archive_in_runtimes(evidence)
            self.assertEqual(self.registry.snapshot("result:"), {})

    def test_parallel_verifier_error_uses_the_absolute_cycle_index(self):
        cycles = [
            {
                "schemaVersion": 3,
                "cycleId": "valid",
                "decisionTime": "2026-01-01T00:00:00Z",
                "data": {},
            },
            {
                "schemaVersion": 3,
                "cycleId": "invalid",
                "decisionTime": "2026-01-01T00:00:00Z",
                "data": {},
                "unexpected": True,
            },
        ]
        with tempfile.TemporaryDirectory() as root, mock.patch.object(
            result_stream, "RESULT_VERIFICATION_TARGET_BYTES", 1
        ):
            evidence = self._verification_evidence(root, cycles)
            self._install_verifier_start()
            with self.assertRaisesRegex(
                ValueError, r"Result cycles\[1\].*unsupported field"
            ) as raised:
                result_runtime.verify_result_archive_in_runtimes(evidence)
            self.assertNotIn(
                "__ENGINE_RESULT_CYCLE_INDEX__", str(raised.exception)
            )

    def test_concurrent_metadata_failure_waits_for_cycle_error_priority(self):
        cycles = [
            {
                "schemaVersion": 3,
                "cycleId": "invalid",
                "decisionTime": "2026-01-01T00:00:00Z",
                "data": {},
                "unexpected": True,
            },
        ]
        metadata_started = []

        def reject_metadata(*_args, **_kwargs):
            metadata_started.append(True)
            raise ValueError("metadata must remain secondary")

        original_poll = _FakeSession.poll

        def poll_after_metadata(session):
            self.assertTrue(metadata_started)
            return original_poll(session)

        with tempfile.TemporaryDirectory() as root, mock.patch.object(
            result_runtime.result_contracts,
            "require_metadata",
            side_effect=reject_metadata,
        ), mock.patch.object(
            _FakeSession,
            "poll",
            poll_after_metadata,
        ):
            evidence = self._verification_evidence(root, cycles)
            self._install_verifier_start()
            with self.assertRaisesRegex(
                ValueError, r"Result cycles\[0\].*unsupported field"
            ) as raised:
                result_runtime.verify_result_archive_in_runtimes(evidence)
        self.assertNotIn("metadata must remain secondary", str(raised.exception))

    def test_cross_shard_duplicate_precedes_later_data_error_in_same_cycle(self):
        cycles = [
            {
                "schemaVersion": 3,
                "cycleId": "duplicate",
                "decisionTime": "2026-01-01T00:00:00Z",
                "data": {},
            },
            {
                "schemaVersion": 3,
                "cycleId": "duplicate",
                "decisionTime": "2026-01-01T00:00:00Z",
                "data": {"unknown": 1},
            },
        ]
        with tempfile.TemporaryDirectory() as root, mock.patch.object(
            result_stream, "RESULT_VERIFICATION_TARGET_BYTES", 1
        ):
            evidence = self._verification_evidence(root, cycles)
            self._install_verifier_start()
            with self.assertRaisesRegex(ValueError, "cycleId values must be unique"):
                result_runtime.verify_result_archive_in_runtimes(evidence)

    def test_parallel_verifier_start_failure_releases_every_started_session(self):
        cycles = [
            {
                "schemaVersion": 3,
                "cycleId": f"cycle-{index}",
                "decisionTime": "2026-01-01T00:00:00Z",
                "data": {},
            }
            for index in range(3)
        ]
        primary = PrimaryFailure("second verifier failed to start")
        captured = []

        def start(key, command, **options):
            if captured:
                raise primary
            result_verifier.verify_specification(command[-1])
            session = _FakeSession(options["metadata"])
            self.registry._sessions[key] = session
            captured.append(session)
            return session

        with tempfile.TemporaryDirectory() as root, mock.patch.object(
            result_stream, "RESULT_VERIFICATION_TARGET_BYTES", 1
        ), mock.patch.object(
            self.registry, "start", side_effect=start
        ), self.assertRaises(PrimaryFailure) as raised:
            evidence = self._verification_evidence(root, cycles)
            result_runtime.verify_result_archive_in_runtimes(evidence)
        self.assertIs(raised.exception, primary)
        self.assertEqual(self.registry.snapshot("result:"), {})
        self.assertEqual(len(captured), 1)
        self.assertFalse(
            Path(captured[0].metadata["executionRoot"]).exists()
        )

    def test_worker_error_stays_primary_when_cleanup_retry_reports_first_error(self):
        with tempfile.TemporaryDirectory() as root:
            destination = Path(root) / "projection.json"
            captured = self._install_start(
                return_code=1,
                stderr="worker failed first",
            )
            cleanup = CleanupFailure("cleanup interrupted")

            def add_cleanup_error(*_args, **_kwargs):
                session = captured.get("session")
                if session is not None and not session.terminate_errors:
                    session.terminate_errors.extend((cleanup, None))
                return original_finish(*_args, **_kwargs)

            original_finish = self.registry.finish
            with mock.patch.object(
                self.registry,
                "finish",
                side_effect=add_cleanup_error,
            ):
                with self.assertRaisesRegex(
                    RuntimeError, "worker failed first"
                ) as raised:
                    result_runtime.write_result_projection_in_runtime(
                        self._evidence(root),
                        ["metrics"],
                        [{"instanceId": "temporary"}],
                        {},
                        destination,
                    )
            self.assertIs(raised.exception.__context__, cleanup)
            self.assertEqual(self.registry.snapshot("result:"), {})
            self.assertFalse(destination.exists())

    def test_unproven_cleanup_retains_authority_destination_and_scratch_for_shutdown(self):
        with tempfile.TemporaryDirectory() as root:
            destination = Path(root) / "projection.json"
            captured = self._install_start(return_code=1, stderr="worker failed")
            failure = CleanupFailure("termination remains unproven")

            def block_finish(*_args, **_kwargs):
                session = captured["session"]
                session.terminate_errors.extend((failure, failure))
                return original_finish(*_args, **_kwargs)

            original_finish = self.registry.finish
            with mock.patch.object(
                self.registry,
                "finish",
                side_effect=block_finish,
            ):
                with self.assertRaisesRegex(RuntimeError, "worker failed"):
                    result_runtime.write_result_projection_in_runtime(
                        self._evidence(root),
                        ["metrics"],
                        [{"instanceId": "temporary"}],
                        {},
                        destination,
                    )
            session = captured["session"]
            self.assertTrue(destination.is_file())
            self.assertTrue(Path(session.metadata["executionRoot"]).is_dir())
            self.assertIs(
                self.registry.get(captured["key"]),
                session,
            )
            result_runtime.shutdown_result_runtimes()
            self.assertEqual(self.registry.snapshot("result:"), {})
            self.assertFalse(destination.exists())
            self.assertFalse(Path(session.metadata["executionRoot"]).exists())

    def test_initialization_baseexception_retains_shared_placeholder_until_retry(self):
        with tempfile.TemporaryDirectory() as root:
            primary = PrimaryFailure("tail initialization failed")
            cleanup = CleanupFailure("termination proof failed")
            with (
                mock.patch.object(
                    process_session.ProcessSession,
                    "initialize",
                    side_effect=primary,
                ),
                mock.patch.object(
                    process_session.ProcessSession,
                    "terminate",
                    side_effect=cleanup,
                ),
                self.assertRaises(PrimaryFailure) as raised,
            ):
                result_runtime.write_result_projection_in_runtime(
                    self._evidence(root),
                    ["metrics"],
                    [{"instanceId": "temporary"}],
                    {},
                    Path(root) / "projection.json",
                )
            self.assertIs(raised.exception, primary)
            self.assertIs(raised.exception.__cause__, cleanup)
            retained = self.registry.snapshot("result:")
            self.assertEqual(len(retained), 1)
            session = next(iter(retained.values()))
            self.assertTrue(Path(session.metadata["executionRoot"]).is_dir())
            result_runtime.shutdown_result_runtimes()
            self.assertEqual(self.registry.snapshot("result:"), {})

    def test_shutdown_attempts_all_sessions_and_releases_only_proven_resources(self):
        first = CleanupFailure("first remains unproven")
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            blocked_destination = root / "blocked.json"
            healthy_destination = root / "healthy.json"
            blocked_destination.write_text("x", encoding="utf-8")
            healthy_destination.write_text("x", encoding="utf-8")
            blocked_scratch = tempfile.TemporaryDirectory(dir=root)
            healthy_scratch = tempfile.TemporaryDirectory(dir=root)
            blocked = _FakeSession({
                "executionRoot": blocked_scratch.name,
                "destination": str(blocked_destination),
                "removeDestinationOnRelease": True,
                "scratchOwner": blocked_scratch,
            })
            healthy = _FakeSession({
                "executionRoot": healthy_scratch.name,
                "destination": str(healthy_destination),
                "removeDestinationOnRelease": True,
                "scratchOwner": healthy_scratch,
            })
            blocked.terminate_errors.extend((first, first))
            self.registry._sessions = {
                "result:blocked": blocked,
                "result:healthy": healthy,
            }
            with self.assertRaises(CleanupFailure) as raised:
                result_runtime.shutdown_result_runtimes()
            self.assertIs(raised.exception, first)
            self.assertIs(self.registry.get("result:blocked"), blocked)
            self.assertIsNone(self.registry.get("result:healthy"))
            self.assertTrue(blocked_destination.exists())
            self.assertFalse(healthy_destination.exists())
            self.assertTrue(Path(blocked_scratch.name).exists())
            self.assertFalse(Path(healthy_scratch.name).exists())
            result_runtime.shutdown_result_runtimes()
            self.assertEqual(self.registry.snapshot("result:"), {})
            self.assertFalse(blocked_destination.exists())
            self.assertFalse(Path(blocked_scratch.name).exists())

    def test_released_cleanup_failure_is_retried_by_later_shutdown(self):
        failure = CleanupFailure("release cleanup interrupted")
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            destination = root / "pending.json"
            destination.write_text("x", encoding="utf-8")
            scratch = tempfile.TemporaryDirectory(dir=root)
            session = _FakeSession({
                "executionRoot": scratch.name,
                "destination": str(destination),
                "removeDestinationOnRelease": True,
                "scratchOwner": scratch,
            })
            self.registry._sessions["result:pending-release"] = session
            real_cleanup = result_runtime._cleanup_released_runtime
            calls = 0

            def fail_once(metadata):
                nonlocal calls
                calls += 1
                if calls == 1:
                    return failure
                return real_cleanup(metadata)

            with mock.patch.object(
                result_runtime,
                "_cleanup_released_runtime",
                side_effect=fail_once,
            ):
                with self.assertRaises(CleanupFailure) as raised:
                    result_runtime.shutdown_result_runtimes()
            self.assertIs(raised.exception, failure)
            self.assertEqual(self.registry.snapshot("result:"), {})
            with result_runtime._PENDING_RELEASE_LOCK:
                self.assertIn(
                    "result:pending-release",
                    result_runtime._PENDING_RELEASES,
                )
            self.assertTrue(destination.exists())
            self.assertTrue(Path(scratch.name).exists())
            result_runtime.shutdown_result_runtimes()
            with result_runtime._PENDING_RELEASE_LOCK:
                self.assertEqual(result_runtime._PENDING_RELEASES, {})
            self.assertFalse(destination.exists())
            self.assertFalse(Path(scratch.name).exists())


if __name__ == "__main__":
    unittest.main()
