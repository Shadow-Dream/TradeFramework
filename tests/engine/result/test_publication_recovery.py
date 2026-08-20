#!/usr/bin/env python3

import copy
import shutil
import stat
import unittest
from unittest import mock

from engine.archive import backtest_result as backtest_result_archive
from engine.contracts import strict_json
from engine.core import resource_ids
from engine.repository import backtest_results as result_repository
from engine.service import backtest_execution as backtest_execution_service
from tests.support.backtest_runtime import BacktestRuntimeFixture


class BacktestPublicationRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.fixture = BacktestRuntimeFixture().open()
        self.config = self.fixture.config

    def tearDown(self):
        self.fixture.close()

    @staticmethod
    def _rewrite_sealed_manifest(directory, manifest):
        path = directory / "result-manifest.json"
        directory.chmod(directory.stat().st_mode | stat.S_IWUSR | stat.S_IXUSR)
        path.chmod(path.stat().st_mode | stat.S_IWUSR)
        path.write_text(
            strict_json.dumps(manifest, sort_keys=True, separators=(",", ":")),
            encoding="utf-8",
        )
        path.chmod(path.stat().st_mode & ~0o222)
        directory.chmod(directory.stat().st_mode & ~0o222)

    def _copy_unindexed_result(self, completed):
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
        manifest = strict_json.loads(
            (destination / "result-manifest.json").read_text(encoding="utf-8")
        )
        manifest["backtestId"] = backtest_id
        manifest["catalog"]["backtestId"] = backtest_id
        self._rewrite_sealed_manifest(destination, manifest)
        return backtest_id, destination

    def test_preexisting_sealed_result_cannot_reconcile_worker_failure(self):
        completed = self.fixture.run_minimal_backtest("preexisting-result-failure")
        backtest_id, _destination = self._copy_unindexed_result(completed)
        primary = (
            backtest_execution_service.backtest_worker
            .BacktestResultPublicationUncertain(OSError("acknowledgement loss"))
        )
        with (
            mock.patch.object(
                backtest_execution_service.backtest_worker,
                "execute_backtest",
                side_effect=primary,
            ),
            mock.patch.object(
                backtest_execution_service, "_recover_published_result"
            ) as recover,
            self.assertRaises(
                backtest_execution_service.backtest_worker
                .BacktestResultPublicationUncertain
            ) as raised,
        ):
            backtest_execution_service.run_backtest(
                self.config, completed["request"], backtest_id=backtest_id
            )
        self.assertIs(raised.exception, primary)
        recover.assert_not_called()

    def test_publication_recovery_does_not_swallow_process_cancellation(self):
        frozen = self.fixture.frozen_minimal_request("recovery-cancellation")
        uncertain = (
            backtest_execution_service.backtest_worker
            .BacktestResultPublicationUncertain(OSError("fsync failed"))
        )
        cancellation = KeyboardInterrupt("cancel recovery")
        backtest_id = resource_ids.new_resource_id("backtest")
        destination = backtest_result_archive.archive_directory(
            self.config["releaseRoot"],
            backtest_id,
            label="Backtest Result directory",
        )

        def publish_then_report_uncertain(*_args, **_kwargs):
            destination.mkdir(parents=True)
            raise uncertain

        with (
            mock.patch.object(
                backtest_execution_service.backtest_worker,
                "execute_backtest",
                side_effect=publish_then_report_uncertain,
            ),
            mock.patch.object(
                backtest_execution_service,
                "_recover_published_result",
                side_effect=cancellation,
            ),
            self.assertRaises(KeyboardInterrupt) as raised,
        ):
            backtest_execution_service.run_backtest(
                self.config, frozen, backtest_id=backtest_id
            )
        self.assertIs(raised.exception, cancellation)

    def test_publication_uncertainty_requires_exact_request_binding(self):
        completed = self.fixture.run_minimal_backtest("request-binding")
        source = (
            backtest_result_archive.archive_root(self.config["releaseRoot"])
            / completed["backtestId"]
        )
        backtest_id = resource_ids.new_resource_id("backtest")
        destination = backtest_result_archive.archive_directory(
            self.config["releaseRoot"],
            backtest_id,
            label="Backtest Result directory",
        )
        mismatched = copy.deepcopy(completed["request"])
        mismatched["name"] = "different active request"
        uncertain = (
            backtest_execution_service.backtest_worker
            .BacktestResultPublicationUncertain(OSError("fsync failed"))
        )

        def publish_then_report_uncertain(*_args, **_kwargs):
            shutil.copytree(source, destination)
            manifest = strict_json.loads(
                (destination / "result-manifest.json").read_text(encoding="utf-8")
            )
            manifest["backtestId"] = backtest_id
            manifest["catalog"]["backtestId"] = backtest_id
            self._rewrite_sealed_manifest(destination, manifest)
            raise uncertain

        with (
            mock.patch.object(
                backtest_execution_service.backtest_worker,
                "execute_backtest",
                side_effect=publish_then_report_uncertain,
            ),
            self.assertRaises(
                backtest_execution_service.backtest_worker
                .BacktestResultPublicationUncertain
            ) as raised,
        ):
            backtest_execution_service.run_backtest(
                self.config, mismatched, backtest_id=backtest_id
            )
        self.assertIs(raised.exception, uncertain)
        self.assertIsInstance(raised.exception.__cause__, ValueError)
        self.assertRegex(str(raised.exception.__cause__), "request does not match")
        self.assertEqual(result_repository.count_backtests(self.config), 1)


if __name__ == "__main__":
    unittest.main()
