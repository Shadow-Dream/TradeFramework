#!/usr/bin/env python3

import copy
import os
import sqlite3
import tempfile
import threading
import time
import unittest
import json
import subprocess
import sys
from pathlib import Path
from unittest import mock

from builtin_implementations import analysis_presets
from builtin_implementations import environment_presets
from engine.contracts import strict_json
from engine.core import clock as engine_clock
from engine.control import database as engine_database
from engine.jobs.manager import BacktestJobManager, BacktestJobServices
from engine.jobs.repository import BacktestJobRepository
from engine.repository import backtest_results as result_repository
from engine.runtime import process_session
from engine.runtime.process_supervision import BoundedStreamTail
from engine.worker.backtest_supervisor import (
    read_runtime_status,
    run_backtest_runtime,
)
from engine.worker import backtest_supervisor
from engine.service import backtests as backtest_service
from engine.service import backtest_results as backtest_result_service
from engine.service import result_projection as result_projection_service
from engine.core import resource_ids
from engine.service import control_api as control
from engine.control.owner import (
    claim_control_owner,
)
from tests.support.backtest_runtime import BacktestRuntimeFixture


class BacktestJobManagerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.config = {
            "controlRoot": str(root / "control"),
            "releaseRoot": str(root / "releases"),
            "liveRoot": str(root / "live"),
        }
        engine_database.prepare_database(self.config)
        with engine_database.connect_database(self.config) as connection:
            connection.execute(
                """
                INSERT INTO datasets
                (dataset_id, name, source_json, created_at, metadata_json,
                 status, archived_at, archive_reason)
                VALUES ('dataset', 'Dataset', '{"type":"test","details":{}}',
                        '2026-01-01T00:00:00Z', '{}', 'active', '', '')
                """
            )
            connection.commit()

    def tearDown(self):
        self.temp.cleanup()

    @staticmethod
    def request(sequence):
        request = {
            "pipeline": {"pipelineId": f"pipeline-{sequence}", "version": "1"},
            "datasetId": "dataset",
            "datasetVersionId": "dataset@sha256:" + "0" * 64,
            "sampler": {"samplerId": "sampler", "version": "1", "parameters": {}},
            "environment": {"environmentId": "environment", "version": "1"},
            "analysis": {"analysisId": "analysis", "version": "1"},
        }
        execution_inputs = {**request, "limit": None}
        snapshot = {"executionInputs": execution_inputs}
        snapshot["snapshotHash"] = "sha256:" + control.json_digest(snapshot)
        request["executionSnapshot"] = snapshot
        return request

    @staticmethod
    def wait_for(manager, job_id, statuses, timeout=5):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            job = manager.get(job_id)
            if job["status"] in statuses:
                return job
            time.sleep(0.01)
        raise AssertionError(f"Job {job_id} did not reach {statuses}")

    @staticmethod
    def job_services(freeze_request=lambda _config, request: request):
        return BacktestJobServices(
            freeze_request=freeze_request,
            reconcile_result_staging=lambda _config: None,
            recover_result_catalog=lambda _config, _backtest_id, _request: None,
            validate_result_archive=lambda _config, _backtest_id: None,
        )

    def insert_queued(self, repository, request, suffix):
        repository.insert_queued(
            job_id=f"job-{suffix}",
            backtest_id=f"backtest-{suffix}",
            pipeline_id=request["pipeline"]["pipelineId"],
            dataset_id=request["datasetId"],
            request=request,
            submitted_at="2026-08-10T00:00:00Z",
            snapshot_hash=request["executionSnapshot"]["snapshotHash"],
        )

    def test_repository_rejects_non_exact_execution_inputs_before_insert(self):
        repository = BacktestJobRepository(self.config)
        for suffix, replacement in (("boolean", True), ("float", 1.0)):
            with self.subTest(replacement=replacement):
                request = self.request(suffix)
                request["sampler"]["parameters"]["value"] = 1
                snapshot_inputs = copy.deepcopy({
                    key: value
                    for key, value in request.items()
                    if key != "executionSnapshot"
                })
                snapshot_inputs["limit"] = None
                snapshot_inputs["sampler"]["parameters"]["value"] = replacement
                snapshot = {"executionInputs": snapshot_inputs}
                snapshot["snapshotHash"] = (
                    "sha256:" + control.json_digest(snapshot)
                )
                request["executionSnapshot"] = snapshot
                with self.assertRaisesRegex(
                    ValueError,
                    "execution inputs do not match",
                ):
                    self.insert_queued(repository, request, suffix)
                with self.assertRaisesRegex(ValueError, "Unknown Backtest job"):
                    repository.get(f"job-{suffix}")

    def test_public_job_reads_do_not_decode_hidden_execution_request(self):
        repository = BacktestJobRepository(self.config)
        request = self.request("public-read-model")
        self.insert_queued(repository, request, "public-read-model")
        with mock.patch.object(
            strict_json,
            "loads",
            side_effect=AssertionError("public reads decoded hidden evidence"),
        ):
            self.assertEqual(
                repository.get("job-public-read-model")["status"],
                "queued",
            )
            self.assertEqual(
                repository.list()[0]["jobId"],
                "job-public-read-model",
            )
        self.assertEqual(
            repository.active_request_for_job(
                "job-public-read-model",
                "backtest-public-read-model",
            ),
            request,
        )

    def test_completion_requires_running_and_queued_job_stays_readable(self):
        repository = BacktestJobRepository(self.config)
        request = self.request("completion-state")
        self.insert_queued(repository, request, "completion-state")
        with self.assertRaisesRegex(
            ValueError,
            "cannot complete from status 'queued'",
        ):
            repository.mark_completed(
                "job-completion-state",
                "2026-08-10T00:01:00Z",
                2,
            )
        self.assertEqual(
            repository.get("job-completion-state")["status"],
            "queued",
        )

    def test_repository_rejects_duplicate_backtest_identity(self):
        repository = BacktestJobRepository(self.config)
        first_request = self.request("first-binding")
        second_request = self.request("second-binding")
        repository.insert_queued(
            job_id="job-first-binding",
            backtest_id="backtest-shared-binding",
            pipeline_id=first_request["pipeline"]["pipelineId"],
            dataset_id=first_request["datasetId"],
            request=first_request,
            submitted_at="2026-08-10T00:00:00Z",
            snapshot_hash=first_request["executionSnapshot"]["snapshotHash"],
        )

        with self.assertRaises(sqlite3.IntegrityError):
            repository.insert_queued(
                job_id="job-second-binding",
                backtest_id="backtest-shared-binding",
                pipeline_id=second_request["pipeline"]["pipelineId"],
                dataset_id=second_request["datasetId"],
                request=second_request,
                submitted_at="2026-08-10T00:00:01Z",
                snapshot_hash=(
                    second_request["executionSnapshot"]["snapshotHash"]
                ),
            )

        self.assertEqual(
            repository.get("job-first-binding")["backtestId"],
            "backtest-shared-binding",
        )
        with self.assertRaisesRegex(ValueError, "Unknown Backtest job"):
            repository.get("job-second-binding")

    def test_active_request_requires_the_exact_bound_job(self):
        repository = BacktestJobRepository(self.config)
        request = self.request("exact-active-binding")
        repository.insert_queued(
            job_id="job-exact-active-binding",
            backtest_id="backtest-exact-active-binding",
            pipeline_id=request["pipeline"]["pipelineId"],
            dataset_id=request["datasetId"],
            request=request,
            submitted_at="2026-08-10T00:00:00Z",
            snapshot_hash=request["executionSnapshot"]["snapshotHash"],
        )

        self.assertEqual(
            repository.active_request_for_job(
                "job-exact-active-binding",
                "backtest-exact-active-binding",
            ),
            request,
        )
        self.assertIsNone(
            repository.active_request_for_job(
                "job-wrong-active-binding",
                "backtest-exact-active-binding",
            )
        )

    def test_repository_rejects_an_empty_backtest_identity(self):
        repository = BacktestJobRepository(self.config)
        request = self.request("empty-active-binding")
        for backtest_id in ("", "   "):
            with self.subTest(backtest_id=backtest_id), self.assertRaisesRegex(
                ValueError,
                "backtest_id must be a non-empty string",
            ):
                repository.insert_queued(
                    job_id=f"job-empty-active-binding-{len(backtest_id)}",
                    backtest_id=backtest_id,
                    pipeline_id=request["pipeline"]["pipelineId"],
                    dataset_id=request["datasetId"],
                    request=request,
                    submitted_at="2026-08-10T00:00:00Z",
                    snapshot_hash=request["executionSnapshot"]["snapshotHash"],
                )

    def test_startup_interrupts_queued_job_without_result_recovery(self):
        repository = BacktestJobRepository(self.config)
        request = self.request("queued-recovery")
        self.insert_queued(repository, request, "queued-recovery")
        recovery_calls = []
        services = BacktestJobServices(
            freeze_request=lambda _config, value: value,
            reconcile_result_staging=lambda _config: None,
            recover_result_catalog=lambda *_args: recovery_calls.append(_args),
            validate_result_archive=lambda _config, _backtest_id: None,
        )
        manager = BacktestJobManager(
            self.config,
            services,
            repository=repository,
        )
        try:
            job = manager.get("job-queued-recovery")
            self.assertEqual(job["status"], "failed")
            self.assertEqual(job["phase"], "interrupted")
            self.assertEqual(recovery_calls, [])
        finally:
            manager.shutdown()

    def test_multiple_jobs_run_concurrently_and_excess_jobs_queue(self):
        lock = threading.Lock()
        release = threading.Event()
        two_started = threading.Event()
        active = 0
        peak = 0
        roots = set()
        evidence = {}

        def fake_run(
            _config, _request, *, backtest_id, progress_callback,
            execution_root, should_stop,
        ):
            nonlocal active, peak
            with lock:
                active += 1
                peak = max(peak, active)
                roots.add(str(execution_root))
                if active == 2:
                    two_started.set()
            progress_callback(0, 4, "running")
            self.assertTrue(release.wait(5))
            for completed in range(1, 5):
                progress_callback(completed, 4, "running")
            with lock:
                active -= 1
                evidence[backtest_id] = (engine_clock.utc_now(), 4)
            return {"backtestId": backtest_id, "metrics": {"cycleCount": 4}}

        manager = BacktestJobManager(
            self.config,
            self.job_services(),
            max_workers=2,
            runtime_launcher=fake_run,
        )
        manager._completed_evidence = (
            lambda _job_id, backtest_id: evidence.get(backtest_id)
        )
        try:
            first = manager.submit(self.request(1))
            second = manager.submit(self.request(2))
            third = manager.submit(self.request(3))
            self.assertTrue(two_started.wait(5))
            queued = next(job for job in manager.list() if job["jobId"] == third["jobId"])
            self.assertEqual(queued["status"], "queued")
            self.assertGreaterEqual(queued["queuePosition"], 0)
            release.set()
            for job in (first, second, third):
                completed = self.wait_for(manager, job["jobId"], {"completed"})
                self.assertEqual(completed["completedCycles"], 4)
                self.assertEqual(completed["progress"], 1.0)
            self.assertEqual(peak, 2)
            self.assertEqual(len(roots), 3)
        finally:
            release.set()
            manager.shutdown()

    def test_failure_is_recorded_without_blocking_later_jobs(self):
        calls = 0
        evidence = {}

        def fake_run(
            _config, _request, *, backtest_id, progress_callback,
            execution_root, should_stop,
        ):
            nonlocal calls
            calls += 1
            progress_callback(1, 2, "running")
            if calls == 1:
                raise ValueError("invalid strategy output")
            progress_callback(2, 2, "running")
            evidence[backtest_id] = (engine_clock.utc_now(), 2)
            return {"backtestId": backtest_id, "metrics": {"cycleCount": 2}}

        manager = BacktestJobManager(
            self.config,
            self.job_services(),
            max_workers=1,
            runtime_launcher=fake_run,
        )
        manager._completed_evidence = (
            lambda _job_id, backtest_id: evidence.get(backtest_id)
        )
        try:
            failed = manager.submit(self.request("failed"))
            succeeded = manager.submit(self.request("succeeded"))
            failed_job = self.wait_for(manager, failed["jobId"], {"failed"})
            completed_job = self.wait_for(manager, succeeded["jobId"], {"completed"})
            self.assertEqual(failed_job["phase"], "failed")
            self.assertIn("invalid strategy output", failed_job["error"])
            self.assertEqual(completed_job["completedCycles"], 2)
        finally:
            manager.shutdown()

    def test_runtime_failure_preserves_completion_verification_error(self):
        verification_error = ValueError(
            "sealed Result request does not match the active Job"
        )
        services = BacktestJobServices(
            freeze_request=lambda _config, request: request,
            reconcile_result_staging=lambda _config: None,
            recover_result_catalog=mock.Mock(
                side_effect=verification_error
            ),
            validate_result_archive=lambda _config, _backtest_id: None,
        )

        def fail_runtime(*_args, **_kwargs):
            raise RuntimeError("runtime process failed first")

        manager = BacktestJobManager(
            self.config,
            services,
            max_workers=1,
            runtime_launcher=fail_runtime,
        )
        try:
            submitted = manager.submit(self.request("double-failure"))
            failed = self.wait_for(manager, submitted["jobId"], {"failed"})
            self.assertIn(
                "Backtest Result completion verification failed: sealed "
                "Result request does not match the active Job",
                failed["error"],
            )
            self.assertIn(
                "Runtime failure: runtime process failed first",
                failed["error"],
            )
        finally:
            manager.shutdown()

    def test_unknown_total_progress_remains_readable_while_running_and_failed(self):
        progress_written = threading.Event()
        release = threading.Event()

        def fake_run(
            _config, _request, *, backtest_id, progress_callback,
            execution_root, should_stop,
        ):
            progress_callback(1, 0, "running")
            progress_written.set()
            self.assertTrue(release.wait(5))
            raise ValueError("unknown-length failure")

        manager = BacktestJobManager(
            self.config,
            self.job_services(),
            max_workers=1,
            runtime_launcher=fake_run,
        )
        try:
            submitted = manager.submit(self.request("unknown-total"))
            self.assertTrue(progress_written.wait(5))
            running = manager.get(submitted["jobId"])
            self.assertEqual(running["status"], "running")
            self.assertEqual(running["completedCycles"], 1)
            self.assertEqual(running["totalCycles"], 0)
            self.assertEqual(running["progress"], 0.0)
            release.set()
            failed = self.wait_for(manager, submitted["jobId"], {"failed"})
            self.assertEqual(failed["completedCycles"], 1)
            self.assertEqual(failed["totalCycles"], 0)
        finally:
            release.set()
            manager.shutdown()

    def test_default_runtime_launcher_uses_a_disposable_worker_process(self):
        execution_root = Path(self.temp.name) / "runtime-failure"
        execution_root.mkdir()
        with self.assertRaisesRegex(RuntimeError, "explicitly frozen executionSnapshot"):
            run_backtest_runtime(
                self.config,
                {},
                backtest_id=resource_ids.new_resource_id("backtest"),
                progress_callback=lambda *_args: None,
                execution_root=execution_root,
                should_stop=lambda: False,
            )
        status = json.loads(
            (execution_root / "runtime-status.json").read_text(encoding="utf-8")
        )
        self.assertEqual(status["status"], "failed")
        self.assertTrue((execution_root / "runtime-stderr.log").is_file())

    def test_worker_preserves_release_runtime_dependency_authority(self):
        runtime_root = Path(self.temp.name) / "worker-home"
        release_site = "/opt/trade-engine/release/runtime/site-packages"
        with mock.patch.dict(
            os.environ,
            {"TRADE_ENGINE_RUNTIME_SITE_PACKAGES": release_site},
            clear=False,
        ):
            environment = backtest_supervisor._worker_environment(runtime_root)
        self.assertEqual(
            environment["TRADE_ENGINE_RUNTIME_SITE_PACKAGES"], release_site
        )
        self.assertEqual(environment["HOME"], str(runtime_root))
        self.assertNotIn("PYTHONPATH", environment)

    def test_shared_runtime_start_failure_retains_authority_until_retry(self):
        class InitializationFailure(BaseException):
            pass

        class CleanupFailure(BaseException):
            pass

        registry = process_session.ProcessSessionRegistry()
        execution_root = Path(self.temp.name) / "shared-start-failure"
        execution_root.mkdir()
        primary = InitializationFailure("session initialization failed")
        cleanup = CleanupFailure("termination proof failed")
        with (
            mock.patch.object(
                backtest_supervisor.process_session,
                "PROCESS_SESSIONS",
                registry,
            ),
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
            self.assertRaises(InitializationFailure) as raised,
        ):
            run_backtest_runtime(
                self.config,
                {},
                backtest_id=resource_ids.new_resource_id("backtest"),
                progress_callback=lambda *_args: None,
                execution_root=execution_root,
                should_stop=lambda: False,
            )
        self.assertIs(raised.exception, primary)
        self.assertIs(raised.exception.__cause__, cleanup)
        self.assertTrue(
            any(
                session.metadata["executionRoot"] == str(execution_root.resolve())
                for session in registry.snapshot("backtest:").values()
            )
        )
        with mock.patch.object(
            backtest_supervisor.process_session,
            "PROCESS_SESSIONS",
            registry,
        ):
            shutdown_backtest_runtimes = (
                backtest_supervisor.shutdown_backtest_runtimes
            )
            shutdown_backtest_runtimes(execution_root.parent)
        self.assertEqual(registry.snapshot("backtest:"), {})

    def test_shared_runtime_shutdown_retries_and_retains_only_unproven_session(self):
        registry = process_session.ProcessSessionRegistry()
        execution_root = Path(self.temp.name) / "shared-retained-runtime"
        execution_root.mkdir()
        first_error = KeyboardInterrupt("shutdown interrupted")
        retry_error = RuntimeError("shutdown still unproven")

        class RetainedSession:
            def __init__(self):
                self.metadata = {"executionRoot": str(execution_root.resolve())}
                self.errors = [first_error, retry_error]
                self.terminate_calls = 0
                self.closed = False

            def terminate(self, **_options):
                self.terminate_calls += 1
                if self.errors:
                    raise self.errors.pop(0)

            def close(self):
                self.closed = True

        session = RetainedSession()
        key = "backtest:retained"
        registry._sessions[key] = session
        with mock.patch.object(
            backtest_supervisor.process_session,
            "PROCESS_SESSIONS",
            registry,
        ):
            with self.assertRaises(KeyboardInterrupt) as raised:
                backtest_supervisor.shutdown_backtest_runtimes(
                    execution_root.parent
                )
            self.assertIs(raised.exception, first_error)
            self.assertIs(registry.get(key), session)
            self.assertFalse(session.closed)
            backtest_supervisor.shutdown_backtest_runtimes(
                execution_root.parent
            )
        self.assertEqual(session.terminate_calls, 3)
        self.assertTrue(session.closed)
        self.assertIsNone(registry.get(key))

    def test_manager_keeps_running_job_and_scratch_until_shared_authority_is_proven(self):
        registry = process_session.ProcessSessionRegistry()
        launched = threading.Event()

        class RetainedSession:
            def __init__(self, execution_root):
                self.metadata = {
                    "executionRoot": str(Path(execution_root).resolve())
                }
                self.blocked = RuntimeError("termination remains unproven")

            def terminate(self, **_options):
                if self.blocked is not None:
                    raise self.blocked

            def close(self):
                return None

        retained = []

        def fail_with_retained_runtime(
            _config,
            _request,
            *,
            backtest_id,
            progress_callback,
            execution_root,
            should_stop,
        ):
            del backtest_id, progress_callback, should_stop
            session = RetainedSession(execution_root)
            retained.append(session)
            registry._sessions["backtest:manager-retained"] = session
            (Path(execution_root) / "writer-owned.txt").write_text(
                "retained", encoding="utf-8"
            )
            launched.set()
            raise RuntimeError("worker request failed")

        manager = None
        with mock.patch.object(
            backtest_supervisor.process_session,
            "PROCESS_SESSIONS",
            registry,
        ):
            manager = BacktestJobManager(
                self.config,
                self.job_services(),
                max_workers=1,
                runtime_launcher=fail_with_retained_runtime,
            )
            submitted = manager.submit(self.request("shared-retained"))
            self.assertTrue(launched.wait(5))
            execution_root = (
                Path(self.config["controlRoot"])
                / "backtest-runs"
                / submitted["jobId"]
            )
            with self.assertRaisesRegex(
                RuntimeError, "termination remains unproven"
            ):
                manager.shutdown()
            self.assertEqual(manager.get(submitted["jobId"])["status"], "running")
            self.assertTrue((execution_root / "writer-owned.txt").is_file())
            self.assertTrue(
                backtest_supervisor.runtime_process_authority(execution_root)
            )
            retained[0].blocked = None
            manager.shutdown()
            self.assertEqual(
                BacktestJobRepository(self.config).get(submitted["jobId"])["status"],
                "failed",
            )
            self.assertFalse(execution_root.exists())
            self.assertFalse(
                backtest_supervisor.runtime_process_authority(execution_root)
            )

    def test_runtime_stderr_capture_is_bounded_in_memory_and_on_disk(self):
        log_path = Path(self.temp.name) / "bounded-runtime-stderr.log"
        process = subprocess.Popen(
            [
                sys.executable,
                "-c",
                "import sys;sys.stderr.write('x'*8192)",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        tail = BoundedStreamTail(
            process.stderr,
            max_bytes=1024,
            persist_path=log_path,
            persist_interval=0,
        )
        self.assertEqual(process.wait(timeout=10), 0)
        tail.close()
        self.assertEqual(tail.bytes(), b"x" * 1024)
        self.assertEqual(log_path.read_bytes(), b"x" * 1024)

    def test_default_runtime_needs_no_control_owner_delegation(self):
        execution_root = Path(self.temp.name) / "nondelegated-runtime-failure"
        execution_root.mkdir()
        lease = claim_control_owner(self.config)
        try:
            with self.assertRaisesRegex(RuntimeError, "explicitly frozen executionSnapshot"):
                run_backtest_runtime(
                    self.config,
                    {},
                    backtest_id=resource_ids.new_resource_id("backtest"),
                    progress_callback=lambda *_args: None,
                    execution_root=execution_root,
                    should_stop=lambda: False,
                )
        finally:
            lease.close()

    def test_owner_lock_does_not_require_worker_control_delegation(self):
        fixture = BacktestRuntimeFixture().open()
        lease = None
        try:
            lease = claim_control_owner(fixture.config)
            pipeline = fixture.empty_pipeline("owner-delegated-runtime")
            environment_version = fixture.graph_version(
                "environments.json",
                "environmentId",
                environment_presets.NEUTRAL_ENVIRONMENT_ID,
            )
            analysis_version = fixture.graph_version(
                "analyses.json",
                "analysisId",
                analysis_presets.NEUTRAL_ANALYSIS_ID,
            )
            request = backtest_service.freeze_backtest_request(
                fixture.config,
                fixture.request(
                    pipeline["pipelineId"],
                    fixture.row_sampler,
                    environment_version,
                    analysis_version,
                ),
            )
            execution_root = fixture.root / "delegated-complete-runtime"
            execution_root.mkdir()
            result = run_backtest_runtime(
                fixture.config,
                request,
                backtest_id=resource_ids.new_resource_id("backtest"),
                progress_callback=lambda *_args: None,
                execution_root=execution_root,
                should_stop=lambda: False,
            )
            self.assertEqual(result["status"], "completed")
            self.assertEqual(result["completedCycles"], 3)
        finally:
            if lease is not None:
                lease.close()
            fixture.close()

    def test_manager_completes_a_real_package_runtime_and_parent_recovery(self):
        fixture = BacktestRuntimeFixture().open()
        lease = None
        manager = None
        try:
            lease = claim_control_owner(fixture.config)
            pipeline = fixture.empty_pipeline("manager-package-runtime")
            environment = fixture.graph_version(
                "environments.json",
                "environmentId",
                environment_presets.NEUTRAL_ENVIRONMENT_ID,
            )
            analysis = fixture.graph_version(
                "analyses.json",
                "analysisId",
                analysis_presets.NEUTRAL_ANALYSIS_ID,
            )
            manager = BacktestJobManager(
                fixture.config,
                BacktestJobServices(
                    freeze_request=backtest_service.freeze_backtest_request,
                    reconcile_result_staging=(
                        result_repository.reconcile_result_staging
                    ),
                    recover_result_catalog=(
                        backtest_result_service.recover_backtest_result_catalog
                    ),
                    validate_result_archive=(
                        result_projection_service.validate_backtest_result_archive
                    ),
                ),
                max_workers=1,
            )
            submitted = manager.submit(
                fixture.request(
                    pipeline["pipelineId"],
                    fixture.row_sampler,
                    environment,
                    analysis,
                )
            )
            completed = self.wait_for(
                manager,
                submitted["jobId"],
                {"completed", "failed"},
                timeout=30,
            )
            self.assertEqual(completed["status"], "completed")
            self.assertEqual(completed["completedCycles"], 3)
            self.assertEqual(
                result_repository.get_backtest_meta(
                    fixture.config,
                    completed["backtestId"],
                )["metrics"]["cycleCount"],
                3,
            )
        finally:
            if manager is not None:
                manager.shutdown()
            if lease is not None:
                lease.close()
            fixture.close()

    def test_runtime_status_protocol_rejects_extra_and_state_incompatible_fields(self):
        path = Path(self.temp.name) / "runtime-status.json"
        valid = {
            "schemaVersion": 1,
            "status": "running",
            "sequence": 1,
            "phase": "running",
            "completedCycles": 1,
            "totalCycles": 2,
        }
        path.write_text(json.dumps(valid), encoding="utf-8")
        self.assertEqual(read_runtime_status(path), valid)

        for invalid in (
            {**valid, "fallback": True},
            {**valid, "status": "failed", "error": "failure"},
            {**valid, "sequence": 0},
            {
                **valid,
                "status": "completed",
                "phase": "running",
                "completedCycles": 2,
            },
            {
                "schemaVersion": 1,
                "status": "failed",
                "sequence": 2,
                "phase": "running",
                "error": "failure",
            },
        ):
            path.write_text(json.dumps(invalid), encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "invalid status"):
                read_runtime_status(path)

    def test_worker_count_rejects_coercion_and_clamping(self):
        for value in (0, -1, True, "2", 1.5):
            with self.subTest(value=value), self.assertRaisesRegex(
                ValueError, "positive integer"
            ):
                BacktestJobManager(
                    self.config,
                    self.job_services(),
                    max_workers=value,
                )

    def test_job_list_zero_limit_matches_public_query_contract(self):
        self.assertEqual(BacktestJobRepository(self.config).list(0), [])


if __name__ == "__main__":
    unittest.main()
