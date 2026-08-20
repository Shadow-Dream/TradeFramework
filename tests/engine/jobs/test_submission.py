#!/usr/bin/env python3
"""Submission-boundary regressions for persistent Backtest jobs."""

import tempfile
import threading
import time
import unittest
import json
from pathlib import Path
from unittest import mock

from engine.control import database as engine_database
from engine.contracts import backtest as backtest_contracts
from engine.jobs.manager import BacktestJobManager, BacktestJobServices


class BacktestJobSubmissionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.config = {
            "controlRoot": str(root / "control"),
            "releaseRoot": str(root / "releases"),
            "liveRoot": str(root / "live"),
        }
        engine_database.prepare_database(self.config)

    def tearDown(self):
        self.temporary.cleanup()

    def test_freeze_failure_cannot_enqueue_or_launch_a_job(self):
        freeze_request = mock.Mock(side_effect=ValueError("invalid composition"))
        runtime_launcher = mock.Mock()
        services = BacktestJobServices(
            freeze_request=freeze_request,
            reconcile_result_staging=lambda _config: None,
            recover_result_catalog=lambda *_args: None,
            validate_result_archive=lambda *_args: None,
        )
        manager = BacktestJobManager(
            self.config,
            services,
            max_workers=1,
            runtime_launcher=runtime_launcher,
        )
        request = {"pipeline": {}, "datasetId": "dataset"}
        try:
            with self.assertRaisesRegex(ValueError, "invalid composition"):
                manager.submit(request)

            freeze_request.assert_called_once_with(self.config, request)
            runtime_launcher.assert_not_called()
            self.assertEqual(manager.list(), [])
        finally:
            manager.shutdown()

    def test_prepared_submission_consumes_once_and_skips_full_freeze(self):
        with engine_database.connect_database(self.config) as connection:
            connection.execute(
                "INSERT INTO datasets "
                "(dataset_id, name, source_json, created_at, metadata_json) "
                "VALUES (?, ?, ?, ?, ?)",
                ("dataset", "Dataset", json.dumps({"type": "test"}), "now", "{}"),
            )
            connection.commit()
        raw = {
            "pipeline": {"pipelineId": "pipeline", "version": "1"},
            "datasetId": "dataset",
            "datasetVersionId": "dataset@version",
            "sampler": {"samplerId": "sampler", "version": "1", "parameters": {}},
            "environment": {"environmentId": "environment", "version": "1"},
            "analysis": {"analysisId": "analysis", "version": "1"},
        }
        snapshot = {
            "executionInputs": backtest_contracts.backtest_execution_inputs(raw)
        }
        snapshot["snapshotHash"] = backtest_contracts.backtest_evidence_digest(snapshot)
        frozen = {**raw, "executionSnapshot": snapshot}
        freeze_request = mock.Mock(
            side_effect=AssertionError("prepared submit repeated full freeze")
        )
        consume = mock.Mock(return_value=frozen)
        admission = mock.Mock(return_value=frozen)
        runtime_launcher = mock.Mock()
        services = BacktestJobServices(
            freeze_request=freeze_request,
            reconcile_result_staging=lambda _config: None,
            recover_result_catalog=lambda *_args: None,
            validate_result_archive=lambda *_args: None,
            consume_prepared_request=consume,
            validate_frozen_admission=admission,
        )
        manager = BacktestJobManager(
            self.config,
            services,
            max_workers=1,
            runtime_launcher=runtime_launcher,
        )
        try:
            submitted = manager.submit(
                raw,
                prepared_submission_token="opaque",
                session_identity="session",
            )
            consume.assert_called_once_with(
                "opaque", raw, session_identity="session"
            )
            admission.assert_called_once_with(self.config, frozen)
            freeze_request.assert_not_called()
            self.assertEqual(submitted["snapshotHash"], snapshot["snapshotHash"])
        finally:
            manager.shutdown()

    def test_ordinary_submission_without_token_still_uses_full_freeze(self):
        with engine_database.connect_database(self.config) as connection:
            connection.execute(
                "INSERT INTO datasets "
                "(dataset_id, name, source_json, created_at, metadata_json) "
                "VALUES (?, ?, ?, ?, ?)",
                ("dataset", "Dataset", json.dumps({"type": "test"}), "now", "{}"),
            )
            connection.commit()
        raw = {
            "pipeline": {"pipelineId": "pipeline", "version": "1"},
            "datasetId": "dataset",
            "datasetVersionId": "dataset@version",
            "sampler": {"samplerId": "sampler", "version": "1", "parameters": {}},
            "environment": {"environmentId": "environment", "version": "1"},
            "analysis": {"analysisId": "analysis", "version": "1"},
        }
        snapshot = {
            "executionInputs": backtest_contracts.backtest_execution_inputs(raw)
        }
        snapshot["snapshotHash"] = backtest_contracts.backtest_evidence_digest(snapshot)
        frozen = {**raw, "executionSnapshot": snapshot}
        freeze_request = mock.Mock(return_value=frozen)
        consume = mock.Mock()
        manager = BacktestJobManager(
            self.config,
            BacktestJobServices(
                freeze_request=freeze_request,
                reconcile_result_staging=lambda _config: None,
                recover_result_catalog=lambda *_args: None,
                validate_result_archive=lambda *_args: None,
                consume_prepared_request=consume,
                validate_frozen_admission=mock.Mock(),
            ),
            max_workers=1,
            runtime_launcher=mock.Mock(),
        )
        try:
            manager.submit(raw)
            freeze_request.assert_called_once_with(self.config, raw)
            consume.assert_not_called()
        finally:
            manager.shutdown()

    def test_prepared_queue_failure_is_terminal_and_never_refreezes(self):
        frozen = {
            "pipeline": {"pipelineId": "pipeline", "version": "1"},
            "datasetId": "dataset",
            "executionSnapshot": {"snapshotHash": "sha256:" + "3" * 64},
        }
        freeze = mock.Mock()
        consume = mock.Mock(return_value=frozen)
        repository = mock.Mock()
        repository.prepare.return_value = None
        repository.active_references.return_value = []
        repository.insert_queued.side_effect = OSError("disk full")
        manager = BacktestJobManager(
            self.config,
            BacktestJobServices(
                freeze_request=freeze,
                reconcile_result_staging=lambda _config: None,
                recover_result_catalog=lambda *_args: None,
                validate_result_archive=lambda *_args: None,
                consume_prepared_request=consume,
                validate_frozen_admission=lambda _config, value: value,
            ),
            max_workers=1,
            runtime_launcher=mock.Mock(),
            repository=repository,
        )
        try:
            with self.assertRaisesRegex(RuntimeError, "consumed.*durably queued"):
                manager.submit(
                    {"raw": True},
                    prepared_submission_token="opaque",
                    session_identity="session",
                )
            freeze.assert_not_called()
            consume.assert_called_once()
        finally:
            manager.shutdown()

    def test_explicit_null_or_empty_prepared_token_never_falls_back_to_freeze(self):
        freeze = mock.Mock()
        manager = BacktestJobManager(
            self.config,
            BacktestJobServices(
                freeze_request=freeze,
                reconcile_result_staging=lambda _config: None,
                recover_result_catalog=lambda *_args: None,
                validate_result_archive=lambda *_args: None,
                consume_prepared_request=mock.Mock(),
                validate_frozen_admission=mock.Mock(),
            ),
            max_workers=1,
            runtime_launcher=mock.Mock(),
        )
        try:
            for token in (None, ""):
                with self.subTest(token=token), self.assertRaisesRegex(
                    ValueError, "non-empty string"
                ):
                    manager.submit(
                        {"raw": True},
                        prepared_submission_token=token,
                        session_identity="session",
                    )
            freeze.assert_not_called()
        finally:
            manager.shutdown()

    def test_progress_coalesces_for_one_second_but_forces_phase_and_terminal(self):
        calls = []
        runtime_finished = threading.Event()
        monotonic_values = iter((10.0, 10.2, 10.4, 10.5, 11.5))

        def runtime(
            _config, _request, *, backtest_id, progress_callback,
            execution_root, should_stop,
        ):
            del backtest_id, execution_root, should_stop
            progress_callback(1, 10, "running")
            progress_callback(2, 10, "running")
            progress_callback(3, 10, "verifying")
            progress_callback(10, 10, "verifying")
            runtime_finished.set()
            return {}

        repository = mock.Mock()
        repository.prepare.return_value = None
        repository.active_references.return_value = []
        repository.insert_queued.return_value = None
        repository.get.return_value = {
            "jobId": "job",
            "backtestId": "backtest",
            "status": "queued",
        }
        repository.record_progress.side_effect = (
            lambda _job_id, **evidence: calls.append(evidence)
        )
        services = BacktestJobServices(
            freeze_request=lambda _config, value: value,
            reconcile_result_staging=lambda _config: None,
            recover_result_catalog=lambda *_args: None,
            validate_result_archive=lambda *_args: None,
        )
        manager = BacktestJobManager(
            self.config,
            services,
            max_workers=1,
            runtime_launcher=runtime,
            repository=repository,
        )
        manager._completed_evidence = mock.Mock(return_value=None)
        request = {
            "pipeline": {"pipelineId": "pipeline", "version": "1"},
            "datasetId": "dataset",
            "executionSnapshot": {"snapshotHash": "sha256:" + "7" * 64},
        }
        try:
            with mock.patch.object(time, "monotonic", side_effect=monotonic_values):
                manager._execute("job", "backtest", request)
            self.assertTrue(runtime_finished.is_set())
        finally:
            manager.shutdown()

        self.assertEqual(
            [(item["phase"], item["completed_cycles"]) for item in calls],
            [("running", 1), ("verifying", 3), ("verifying", 10)],
        )


if __name__ == "__main__":
    unittest.main()
