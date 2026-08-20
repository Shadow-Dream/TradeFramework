#!/usr/bin/env python3

import shutil
import sqlite3
import stat
import unittest
from pathlib import Path
from unittest import mock

from engine.archive import backtest_result as backtest_result_archive
from engine.contracts import strict_json
from engine.core import resource_ids
from engine.jobs.manager import BacktestJobManager, BacktestJobServices
from engine.jobs.repository import BacktestJobRepository
from engine.repository import backtest_results as result_repository
from engine.service import backtest_results as backtest_result_service
from engine.service import result_projection as result_projection_service
from tests.support.backtest_runtime import BacktestRuntimeFixture


class BacktestJobResultRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.fixture = BacktestRuntimeFixture().open()
        self.config = self.fixture.config

    def tearDown(self):
        self.fixture.close()

    @staticmethod
    def _rewrite_sealed_manifest(directory, manifest):
        manifest_path = directory / "result-manifest.json"
        directory.chmod(
            directory.stat().st_mode | stat.S_IWUSR | stat.S_IXUSR
        )
        manifest_path.chmod(manifest_path.stat().st_mode | stat.S_IWUSR)
        manifest_path.write_text(
            strict_json.dumps(
                manifest,
                sort_keys=True,
                separators=(",", ":"),
            ),
            encoding="utf-8",
        )
        manifest_path.chmod(manifest_path.stat().st_mode & ~0o222)
        directory.chmod(directory.stat().st_mode & ~0o222)

    def test_running_job_restart_recovers_sealed_result_as_completed(self):
        completed = self.fixture.run_minimal_backtest(
            "running-job-recovery-pipeline"
        )
        request = completed["request"]
        source = (
            backtest_result_archive.archive_root(self.config["releaseRoot"])
            / completed["backtestId"]
        )
        backtest_id = resource_ids.new_resource_id("backtest")
        destination = (
            backtest_result_archive.archive_root(self.config["releaseRoot"])
            / backtest_id
        )
        shutil.copytree(source, destination)
        manifest_path = destination / "result-manifest.json"
        manifest = strict_json.loads(
            manifest_path.read_text(encoding="utf-8")
        )
        manifest["backtestId"] = backtest_id
        manifest["catalog"]["backtestId"] = backtest_id
        self._rewrite_sealed_manifest(destination, manifest)

        job_id = resource_ids.new_resource_id("job")
        repository = BacktestJobRepository(self.config)
        repository.insert_queued(
            job_id=job_id,
            backtest_id=backtest_id,
            pipeline_id=request["pipeline"]["pipelineId"],
            dataset_id=request["datasetId"],
            request=request,
            submitted_at="2026-08-11T00:00:00Z",
            snapshot_hash=request["executionSnapshot"]["snapshotHash"],
        )
        repository.mark_running(job_id, "2026-08-11T00:00:01Z")
        launcher_calls = []
        recover = mock.Mock(
            wraps=backtest_result_service.recover_backtest_result_catalog
        )
        validate = mock.Mock(
            wraps=result_projection_service.validate_backtest_result_archive
        )
        manager = BacktestJobManager(
            self.config,
            BacktestJobServices(
                freeze_request=lambda _config, item: item,
                reconcile_result_staging=(
                    result_repository.reconcile_result_staging
                ),
                recover_result_catalog=recover,
                validate_result_archive=validate,
            ),
            max_workers=1,
            runtime_launcher=lambda *_args, **_kwargs: launcher_calls.append(
                (_args, _kwargs)
            ),
        )
        try:
            recovered = manager.get(job_id)
            self.assertEqual(recovered["status"], "completed")
            self.assertEqual(recovered["phase"], "completed")
            self.assertEqual(recovered["completedCycles"], 3)
            self.assertEqual(recovered["totalCycles"], 3)
            self.assertEqual(launcher_calls, [])
            self.assertEqual(
                result_repository.get_backtest_meta(
                    self.config,
                    backtest_id,
                )["status"],
                "completed",
            )
            recover.assert_called_once_with(
                self.config,
                backtest_id,
                request,
            )
            validate.assert_not_called()
        finally:
            manager.shutdown()

    def test_running_job_restart_validates_an_existing_catalog_once(self):
        completed = self.fixture.run_minimal_backtest(
            "running-indexed-result-pipeline"
        )
        request = completed["request"]
        backtest_id = completed["backtestId"]
        job_id = resource_ids.new_resource_id("job")
        repository = BacktestJobRepository(self.config)
        repository.insert_queued(
            job_id=job_id,
            backtest_id=backtest_id,
            pipeline_id=request["pipeline"]["pipelineId"],
            dataset_id=request["datasetId"],
            request=request,
            submitted_at="2026-08-11T00:00:00Z",
            snapshot_hash=request["executionSnapshot"]["snapshotHash"],
        )
        repository.mark_running(job_id, "2026-08-11T00:00:01Z")
        recover = mock.Mock(
            wraps=backtest_result_service.recover_backtest_result_catalog
        )
        validate = mock.Mock(
            wraps=result_projection_service.validate_backtest_result_archive
        )
        manager = BacktestJobManager(
            self.config,
            BacktestJobServices(
                freeze_request=lambda _config, item: item,
                reconcile_result_staging=(
                    result_repository.reconcile_result_staging
                ),
                recover_result_catalog=recover,
                validate_result_archive=validate,
            ),
            max_workers=1,
            runtime_launcher=lambda *_args, **_kwargs: self.fail(
                "An indexed Result must not launch a new worker."
            ),
        )
        try:
            recovered = manager.get(job_id)
            self.assertEqual(recovered["status"], "completed")
            self.assertEqual(recovered["completedCycles"], 3)
            recover.assert_called_once_with(
                self.config,
                backtest_id,
                request,
            )
            validate.assert_called_once_with(self.config, backtest_id)
        finally:
            manager.shutdown()

    def test_running_job_restart_rejects_catalog_for_another_request(self):
        completed = self.fixture.run_minimal_backtest(
            "running-wrong-indexed-result-pipeline"
        )
        backtest_id = completed["backtestId"]
        request = self.fixture.frozen_minimal_request(
            "different-active-job-pipeline"
        )
        job_id = resource_ids.new_resource_id("job")
        repository = BacktestJobRepository(self.config)
        repository.insert_queued(
            job_id=job_id,
            backtest_id=backtest_id,
            pipeline_id=request["pipeline"]["pipelineId"],
            dataset_id=request["datasetId"],
            request=request,
            submitted_at="2026-08-11T00:00:00Z",
            snapshot_hash=request["executionSnapshot"]["snapshotHash"],
        )
        repository.mark_running(job_id, "2026-08-11T00:00:01Z")

        with self.assertRaisesRegex(
            ValueError,
            "request does not match its Job",
        ):
            BacktestJobManager(
                self.config,
                BacktestJobServices(
                    freeze_request=lambda _config, item: item,
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
                runtime_launcher=lambda *_args, **_kwargs: self.fail(
                    "A mismatched indexed Result must not launch a worker."
                ),
            )

        self.assertEqual(repository.get(job_id)["status"], "running")

    def test_restart_fails_closed_before_duplicate_job_uses_shared_result(self):
        completed = self.fixture.run_minimal_backtest(
            "legacy-duplicate-result-pipeline"
        )
        backtest_id = completed["backtestId"]
        matching_request = completed["request"]
        wrong_request = self.fixture.frozen_minimal_request(
            "legacy-duplicate-wrong-job-pipeline"
        )
        repository = BacktestJobRepository(self.config)
        matching_job_id = resource_ids.new_resource_id("job")
        wrong_job_id = resource_ids.new_resource_id("job")
        repository.insert_queued(
            job_id=matching_job_id,
            backtest_id=backtest_id,
            pipeline_id=matching_request["pipeline"]["pipelineId"],
            dataset_id=matching_request["datasetId"],
            request=matching_request,
            submitted_at="2026-08-11T00:00:00Z",
            snapshot_hash=(
                matching_request["executionSnapshot"]["snapshotHash"]
            ),
        )
        repository.mark_running(matching_job_id, "2026-08-11T00:00:01Z")
        database_path = Path(self.config["controlRoot"]) / "engine-data.db"
        with sqlite3.connect(database_path) as connection:
            connection.execute("DROP INDEX backtest_jobs_backtest_identity")
            connection.execute(
                "DROP TRIGGER backtest_job_backtest_identity_required"
            )
            connection.execute("PRAGMA user_version = 18")
            connection.execute(
                """
                INSERT INTO backtest_jobs
                (job_id, status, phase, pipeline_id, dataset_id, request_json,
                 submitted_at, started_at, completed_at, total_cycles,
                 completed_cycles, backtest_id, error_text, snapshot_hash)
                VALUES (?, 'running', 'preparing', ?, ?, ?, ?, ?, '', 0, 0,
                        ?, '', ?)
                """,
                (
                    wrong_job_id,
                    wrong_request["pipeline"]["pipelineId"],
                    wrong_request["datasetId"],
                    strict_json.dumps(wrong_request, sort_keys=True),
                    "2026-08-11T00:00:02Z",
                    "2026-08-11T00:00:03Z",
                    backtest_id,
                    wrong_request["executionSnapshot"]["snapshotHash"],
                ),
            )
            connection.commit()

        reconcile = mock.Mock()
        recover = mock.Mock()
        with self.assertRaisesRegex(
            RuntimeError,
            "duplicate Backtest Job identity bindings",
        ):
            BacktestJobManager(
                self.config,
                BacktestJobServices(
                    freeze_request=lambda _config, item: item,
                    reconcile_result_staging=reconcile,
                    recover_result_catalog=recover,
                    validate_result_archive=lambda *_args: None,
                ),
                max_workers=1,
                runtime_launcher=lambda *_args, **_kwargs: self.fail(
                    "Duplicate legacy bindings must fail before worker launch."
                ),
            )

        reconcile.assert_not_called()
        recover.assert_not_called()
        with sqlite3.connect(database_path) as connection:
            statuses = dict(
                connection.execute(
                    "SELECT job_id, status FROM backtest_jobs "
                    "WHERE backtest_id = ?",
                    (backtest_id,),
                ).fetchall()
            )
        self.assertEqual(
            statuses,
            {
                matching_job_id: "running",
                wrong_job_id: "running",
            },
        )

    def test_existing_catalog_recovery_requires_exact_expected_request(self):
        completed = self.fixture.run_minimal_backtest(
            "existing-catalog-request-binding-pipeline"
        )
        different_request = self.fixture.frozen_minimal_request(
            "existing-catalog-different-request-pipeline"
        )

        with self.assertRaisesRegex(
            ValueError,
            "request does not match its Job",
        ):
            backtest_result_service.recover_backtest_result_catalog(
                self.config,
                completed["backtestId"],
                different_request,
            )


if __name__ == "__main__":
    unittest.main()
