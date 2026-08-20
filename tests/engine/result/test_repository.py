#!/usr/bin/env python3

import copy
import hashlib
import json
import shutil
import stat
import unittest
from pathlib import Path
from unittest import mock

from engine.archive import backtest_result as backtest_result_archive
from engine.archive import version as version_archive
from engine.contracts import strict_json
from engine.control import database as engine_database
from engine.core import resource_ids
from engine.repository import backtest_results as result_repository
from engine.service import backtest_results as backtest_result_service
from engine.service import backtest_execution as backtest_execution_service
from engine.service import result_projection as result_projection_service
from engine.runtime import result_runtime
from tests.support.backtest_runtime import BacktestRuntimeFixture


class BacktestResultRepositoryTests(unittest.TestCase):
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
            strict_json.dumps(manifest, sort_keys=True, separators=(",", ":")),
            encoding="utf-8",
        )
        manifest_path.chmod(manifest_path.stat().st_mode & ~0o222)
        directory.chmod(directory.stat().st_mode & ~0o222)

    @staticmethod
    def _encode_framed_result(result):
        encoded = [b'{"cycles":[\n']
        for index, cycle in enumerate(result["cycles"]):
            if index:
                encoded.append(b",\n")
            encoded.append(
                strict_json.dumps(
                    cycle, sort_keys=True, separators=(",", ":")
                ).encode("utf-8")
            )
        encoded.append(b"\n]" if result["cycles"] else b"]")
        for key, value in result.items():
            if key == "cycles":
                continue
            encoded.extend((
                b",",
                strict_json.dumps(key).encode("utf-8"),
                b":",
                strict_json.dumps(
                    value, sort_keys=True, separators=(",", ":")
                ).encode("utf-8"),
            ))
        encoded.append(b"}")
        return b"".join(encoded)

    def _copy_unindexed_result(self, completed):
        source_directory = (
            backtest_result_archive.archive_root(self.config["releaseRoot"])
            / completed["backtestId"]
        )
        backtest_id = resource_ids.new_resource_id("backtest")
        result_directory = (
            backtest_result_archive.archive_root(self.config["releaseRoot"])
            / backtest_id
        )
        shutil.copytree(source_directory, result_directory)
        manifest = strict_json.loads(
            (result_directory / "result-manifest.json").read_text(
                encoding="utf-8"
            )
        )
        manifest["backtestId"] = backtest_id
        manifest["catalog"]["backtestId"] = backtest_id
        self._rewrite_sealed_manifest(result_directory, manifest)
        return backtest_id, result_directory, manifest

    def test_summary_archive_and_immutable_evidence_use_the_result_index(self):
        result = self.fixture.run_minimal_backtest("archive-pipeline")
        backtest_id = result["backtestId"]
        self.assertNotIn("result", result)
        with engine_database.connect_database(self.config) as conn:
            columns = {
                row["name"] for row in conn.execute("PRAGMA table_info(backtests)")
            }
        self.assertNotIn("result_json", columns)

        result_directory = (
            backtest_result_archive.archive_root(self.config["releaseRoot"])
            / backtest_id
        )
        result_path = result_directory / "result.json"
        manifest_path = result_directory / "result-manifest.json"
        self.assertTrue(result_path.is_file())
        self.assertTrue(manifest_path.is_file())
        self.assertFalse(result_directory.stat().st_mode & 0o222)
        self.assertFalse(result_path.stat().st_mode & 0o222)
        self.assertFalse(manifest_path.stat().st_mode & 0o222)

        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        with engine_database.connect_database(self.config) as conn:
            digest_row = conn.execute(
                "SELECT content_digest, result_size FROM backtest_result_metadata "
                "WHERE backtest_id = ?",
                (backtest_id,),
            ).fetchone()
        self.assertEqual(manifest["contentDigest"], digest_row["content_digest"])
        self.assertEqual(manifest["size"], digest_row["result_size"])

        self.assertEqual(result_repository.count_backtests(self.config), 1)
        listed = result_repository.list_backtests(self.config)
        self.assertEqual([item["backtestId"] for item in listed], [backtest_id])
        self.assertNotIn("request", listed[0])
        self.assertTrue(listed[0]["resultContentDigest"].startswith("sha256:"))
        self.assertGreater(listed[0]["resultSize"], 0)
        summary = result_repository.get_backtest_meta(self.config, backtest_id)
        self.assertIn("request", summary)
        self.assertTrue(summary["visualizable"])
        self.assertEqual(summary["resultSchemaVersion"], 8)
        self.assertIsInstance(summary["dataKeys"], dict)
        view = result_repository.get_backtest_result_view(self.config, backtest_id)
        self.assertNotIn("request", view)
        self.assertEqual(view["backtestId"], backtest_id)
        self.assertEqual(view["metrics"], summary["metrics"])
        self.assertEqual(view["visualization"], summary["visualization"])
        self.assertEqual(view["dataKeys"], summary["dataKeys"])
        self.assertEqual(
            view["executionSummary"]["dataset"]["datasetId"],
            summary["executionChain"]["dataset"]["datasetId"],
        )
        self.assertEqual(
            view["executionSummary"]["sampler"]["samplerId"],
            summary["executionChain"]["sampler"]["samplerId"],
        )
        self.assertNotIn("executionChain", view)
        renamed = result_repository.rename_backtest(
            self.config, backtest_id, "Renamed Result"
        )
        self.assertEqual(renamed["name"], "Renamed Result")
        archived = result_repository.archive_backtest(
            self.config, backtest_id, "test archive"
        )
        self.assertEqual(archived["status"], "archived")
        self.assertEqual(archived["archiveReason"], "test archive")
        self.assertEqual(result_repository.count_backtests(self.config), 0)
        self.assertEqual(
            result_repository.count_backtests(self.config, include_archived=True),
            1,
        )

        result_directory.chmod(result_directory.stat().st_mode | stat.S_IWUSR)
        result_path.chmod(result_path.stat().st_mode | stat.S_IWUSR)
        result_path.write_text(
            result_path.read_text(encoding="utf-8") + " ", encoding="utf-8"
        )
        with self.assertRaisesRegex(ValueError, "writable|digest mismatch"):
            result_projection_service.validate_backtest_result_archive(
                self.config, backtest_id
            )

    def test_manifest_metadata_is_exact_and_recovery_rejects_bad_envelopes_early(self):
        completed = self.fixture.run_minimal_backtest("manifest-metadata-pipeline")
        backtest_id = completed["backtestId"]
        result_directory = (
            backtest_result_archive.archive_root(self.config["releaseRoot"])
            / backtest_id
        )
        manifest_path = result_directory / "result-manifest.json"
        manifest = strict_json.loads(manifest_path.read_text(encoding="utf-8"))

        indexed_envelope_cases = (
            ("schemaVersion", 4.0),
            ("size", float(manifest["size"])),
        )
        for field, value in indexed_envelope_cases:
            with self.subTest(indexed_envelope=field):
                corrupted = copy.deepcopy(manifest)
                corrupted[field] = value
                self._rewrite_sealed_manifest(result_directory, corrupted)
                with self.assertRaisesRegex(ValueError, "manifest does not match"):
                    result_repository.load_result_archive_evidence(
                        self.config, backtest_id, verify_digest=False
                    )
        self._rewrite_sealed_manifest(result_directory, manifest)

        request_type_mismatch = copy.deepcopy(manifest)
        snapshot_schema_version = request_type_mismatch["catalog"]["request"][
            "executionSnapshot"
        ]["schemaVersion"]
        self.assertIs(type(snapshot_schema_version), int)
        request_type_mismatch["catalog"]["request"]["executionSnapshot"][
            "schemaVersion"
        ] = float(snapshot_schema_version)
        self._rewrite_sealed_manifest(result_directory, request_type_mismatch)
        with self.assertRaisesRegex(ValueError, "catalog evidence"):
            result_repository.load_result_archive_evidence(
                self.config, backtest_id, verify_digest=False
            )
        self._rewrite_sealed_manifest(result_directory, manifest)

        wrong_schema = copy.deepcopy(manifest)
        wrong_schema["resultMetadata"]["schemaVersion"] = 8.0
        self._rewrite_sealed_manifest(result_directory, wrong_schema)
        with self.assertRaisesRegex(ValueError, "metadata schemaVersion"):
            result_repository.load_result_archive_evidence(
                self.config, backtest_id, verify_digest=False
            )

        type_mismatch = copy.deepcopy(manifest)
        frame_count = type_mismatch["resultMetadata"]["sampleFrameContract"][
            "frameCount"
        ]
        self.assertIs(type(frame_count), int)
        type_mismatch["resultMetadata"]["sampleFrameContract"]["frameCount"] = float(
            frame_count
        )
        self._rewrite_sealed_manifest(result_directory, type_mismatch)
        result_repository.load_result_archive_evidence(
            self.config, backtest_id, verify_digest=False
        )
        with self.assertRaisesRegex(ValueError, "exactly match its sealed manifest"):
            result_projection_service.validate_backtest_result_archive(
                self.config, backtest_id
            )

        self._rewrite_sealed_manifest(result_directory, manifest)
        recovery_cases = (
            (
                "shape",
                lambda item: item["resultMetadata"].pop("sampleFrameContract"),
                "missing required field",
            ),
            (
                "schema",
                lambda item: item["resultMetadata"].__setitem__(
                    "schemaVersion", 8.0
                ),
                "metadata schemaVersion",
            ),
            (
                "request-type",
                lambda item: item["catalog"]["request"][
                    "executionSnapshot"
                ].__setitem__(
                    "schemaVersion",
                    float(
                        item["catalog"]["request"]["executionSnapshot"][
                            "schemaVersion"
                        ]
                    ),
                ),
                "request does not match",
            ),
        )
        for label, corrupt, error in recovery_cases:
            with self.subTest(label=label):
                orphan_id = resource_ids.new_resource_id("backtest")
                orphan_directory = (
                    backtest_result_archive.archive_root(
                        self.config["releaseRoot"]
                    )
                    / orphan_id
                )
                shutil.copytree(result_directory, orphan_directory)
                orphan_manifest = copy.deepcopy(manifest)
                orphan_manifest["backtestId"] = orphan_id
                orphan_manifest["catalog"]["backtestId"] = orphan_id
                corrupt(orphan_manifest)
                self._rewrite_sealed_manifest(orphan_directory, orphan_manifest)
                with mock.patch.object(
                    result_runtime, "verify_result_archive_in_runtimes"
                ) as scan:
                    with self.assertRaisesRegex(ValueError, error):
                        backtest_result_service.recover_backtest_result_catalog(
                            self.config,
                            orphan_id,
                            completed["request"],
                        )
                    scan.assert_not_called()

    def test_atomic_publish_error_recovers_the_result_catalog(self):
        frozen = self.fixture.frozen_minimal_request("publish-recovery-pipeline")
        result_root = backtest_result_archive.archive_root(
            self.config["releaseRoot"]
        ).resolve()
        real_publish = version_archive.publish_staging_directory
        raised_after_result_publish = False
        catalog_absent_when_sealed = False

        def publish_then_raise(staging, destination, *, managed_root):
            nonlocal raised_after_result_publish, catalog_absent_when_sealed
            published = real_publish(
                staging,
                destination,
                managed_root=managed_root,
            )
            if (
                Path(destination).parent.resolve() == result_root
                and not raised_after_result_publish
            ):
                with engine_database.connect_database(self.config) as conn:
                    catalog_absent_when_sealed = conn.execute(
                        "SELECT 1 FROM backtests WHERE backtest_id = ?",
                        (Path(destination).name,),
                    ).fetchone() is None
                raised_after_result_publish = True
                raise OSError("destination parent fsync failed")
            return published

        with mock.patch.object(
            version_archive,
            "publish_staging_directory",
            side_effect=publish_then_raise,
        ):
            result = backtest_execution_service.run_backtest(self.config, frozen)

        self.assertTrue(raised_after_result_publish)
        self.assertTrue(catalog_absent_when_sealed)
        evidence = result_repository.load_result_archive_evidence(
            self.config,
            result["backtestId"],
            verify_digest=True,
        )
        self.assertEqual(evidence["metrics"]["cycleCount"], 3)
        self.assertEqual(result_repository.count_backtests(self.config), 1)
        self.assertEqual(
            [path.name for path in result_root.iterdir()],
            [result["backtestId"]],
        )

    def test_publish_error_keeps_priority_when_parent_recovery_fails(self):
        frozen = self.fixture.frozen_minimal_request(
            "publish-recovery-priority-pipeline"
        )
        result_root = backtest_result_archive.archive_root(
            self.config["releaseRoot"]
        ).resolve()
        real_publish = version_archive.publish_staging_directory
        primary = OSError("destination parent fsync failed")
        recovery = RuntimeError("parent recovery failed")

        def publish_then_raise(staging, destination, *, managed_root):
            published = real_publish(
                staging,
                destination,
                managed_root=managed_root,
            )
            if Path(destination).parent.resolve() == result_root:
                raise primary
            return published

        with (
            mock.patch.object(
                version_archive,
                "publish_staging_directory",
                side_effect=publish_then_raise,
            ),
            mock.patch.object(
                backtest_result_service,
                "recover_backtest_result_catalog",
                side_effect=recovery,
            ),
            self.assertRaises(
                backtest_execution_service.backtest_worker.BacktestResultPublicationUncertain
            ) as raised,
        ):
            backtest_execution_service.run_backtest(self.config, frozen)

        self.assertIs(raised.exception.__cause__, recovery)
        self.assertIs(raised.exception.publication_error, primary)
        self.assertEqual(result_repository.count_backtests(self.config), 0)

    def test_untyped_worker_failure_cannot_be_reconciled_as_success(self):
        frozen = self.fixture.frozen_minimal_request(
            "untyped-worker-failure-pipeline"
        )
        backtest_id = resource_ids.new_resource_id("backtest")
        result_directory = backtest_result_archive.archive_directory(
            self.config["releaseRoot"],
            backtest_id,
            label="Backtest Result directory",
        )
        primary = RuntimeError("worker failed outside publication")

        def create_destination_then_fail(*args, **kwargs):
            result_directory.mkdir(parents=True)
            raise primary

        with (
            mock.patch.object(
                backtest_execution_service.backtest_worker,
                "execute_backtest",
                side_effect=create_destination_then_fail,
            ),
            mock.patch.object(
                backtest_execution_service,
                "_recover_published_result",
            ) as recover,
            self.assertRaises(RuntimeError) as raised,
        ):
            backtest_execution_service.run_backtest(
                self.config,
                frozen,
                backtest_id=backtest_id,
            )

        self.assertIs(raised.exception, primary)
        recover.assert_not_called()

    def test_process_control_failures_cannot_be_reconciled_as_success(self):
        frozen = self.fixture.frozen_minimal_request(
            "process-control-failure-pipeline"
        )
        for primary in (
            KeyboardInterrupt("worker interrupted"),
            SystemExit("worker terminated"),
        ):
            with self.subTest(error_type=type(primary).__name__):
                backtest_id = resource_ids.new_resource_id("backtest")
                result_directory = backtest_result_archive.archive_directory(
                    self.config["releaseRoot"],
                    backtest_id,
                    label="Backtest Result directory",
                )

                def create_destination_then_fail(*_args, **_kwargs):
                    result_directory.mkdir(parents=True)
                    raise primary

                with (
                    mock.patch.object(
                        backtest_execution_service.backtest_worker,
                        "execute_backtest",
                        side_effect=create_destination_then_fail,
                    ),
                    mock.patch.object(
                        backtest_execution_service,
                        "_recover_published_result",
                    ) as recover,
                    self.assertRaises(type(primary)) as raised,
                ):
                    backtest_execution_service.run_backtest(
                        self.config,
                        frozen,
                        backtest_id=backtest_id,
                    )

                self.assertIs(raised.exception, primary)
                recover.assert_not_called()

    def test_new_exact_sealed_result_cannot_turn_untyped_failure_into_success(self):
        completed = self.fixture.run_minimal_backtest(
            "new-sealed-untyped-failure-pipeline"
        )
        source_directory = (
            backtest_result_archive.archive_root(self.config["releaseRoot"])
            / completed["backtestId"]
        )
        for primary in (
            RuntimeError("ordinary worker failure"),
            KeyboardInterrupt("worker interrupted"),
            SystemExit("worker terminated"),
        ):
            with self.subTest(error_type=type(primary).__name__):
                backtest_id = resource_ids.new_resource_id("backtest")
                result_directory = backtest_result_archive.archive_directory(
                    self.config["releaseRoot"],
                    backtest_id,
                    label="Backtest Result directory",
                )

                def publish_exact_result_then_fail(*_args, **_kwargs):
                    shutil.copytree(source_directory, result_directory)
                    manifest_path = result_directory / "result-manifest.json"
                    manifest = strict_json.loads(
                        manifest_path.read_text(encoding="utf-8")
                    )
                    manifest["backtestId"] = backtest_id
                    manifest["catalog"]["backtestId"] = backtest_id
                    self._rewrite_sealed_manifest(result_directory, manifest)
                    raise primary

                with (
                    mock.patch.object(
                        backtest_execution_service.backtest_worker,
                        "execute_backtest",
                        side_effect=publish_exact_result_then_fail,
                    ),
                    mock.patch.object(
                        backtest_execution_service,
                        "_recover_published_result",
                    ) as recover,
                    self.assertRaises(type(primary)) as raised,
                ):
                    backtest_execution_service.run_backtest(
                        self.config,
                        completed["request"],
                        backtest_id=backtest_id,
                    )

                self.assertIs(raised.exception, primary)
                recover.assert_not_called()

    def test_unknown_result_is_rejected_before_loading_temporary_definitions(self):
        definition_loader = mock.Mock(
            side_effect=RuntimeError("definition repository must not be read")
        )
        with self.assertRaisesRegex(ValueError, "Unknown backtest"):
            result_projection_service.write_backtest_result_slice(
                self.config,
                resource_ids.new_resource_id("backtest"),
                ["metrics"],
                [{"instanceId": "temporary"}],
                self.fixture.root / "projection.json",
                module_definitions_loader=definition_loader,
            )
        definition_loader.assert_not_called()

    def test_recovery_reconciles_a_commit_acknowledgement_failure(self):
        completed = self.fixture.run_minimal_backtest("commit-recovery-pipeline")
        source_id = completed["backtestId"]
        source_directory = (
            backtest_result_archive.archive_root(self.config["releaseRoot"])
            / source_id
        )
        backtest_id = resource_ids.new_resource_id("backtest")
        result_directory = (
            backtest_result_archive.archive_root(self.config["releaseRoot"])
            / backtest_id
        )
        shutil.copytree(source_directory, result_directory)
        manifest_path = result_directory / "result-manifest.json"
        manifest = strict_json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["backtestId"] = backtest_id
        manifest["catalog"]["backtestId"] = backtest_id
        self._rewrite_sealed_manifest(
            result_directory,
            manifest,
        )

        real_connect = engine_database.connect_database
        connection_count = 0

        class CommitAcknowledgementLost:
            def __init__(self, connection):
                self._connection = connection

            def __getattr__(self, name):
                return getattr(self._connection, name)

            def __enter__(self):
                self._connection.__enter__()
                return self

            def __exit__(self, kind, value, traceback):
                return self._connection.__exit__(kind, value, traceback)

            def commit(self):
                self._connection.commit()
                raise OSError("commit acknowledgement lost")

        def connect(config):
            nonlocal connection_count
            connection_count += 1
            connection = real_connect(config)
            if connection_count == 2:
                return CommitAcknowledgementLost(connection)
            return connection

        real_verification = result_runtime.verify_result_archive_in_runtimes
        with (
            mock.patch.object(
                engine_database,
                "connect_database",
                side_effect=connect,
            ),
            mock.patch.object(
                result_runtime,
                "verify_result_archive_in_runtimes",
                wraps=real_verification,
            ) as verification,
        ):
            recovered = backtest_result_service.recover_backtest_result_catalog(
                self.config,
                backtest_id,
                completed["request"],
            )

        self.assertEqual(recovered["backtestId"], backtest_id)
        self.assertEqual(recovered["status"], "completed")
        self.assertEqual(verification.call_count, 1)
        result_projection_service.validate_backtest_result_archive(
            self.config,
            backtest_id,
        )

    def test_recovery_preserves_commit_error_when_reconcile_cannot_prove_it(self):
        completed = self.fixture.run_minimal_backtest(
            "commit-error-priority-pipeline"
        )
        backtest_id, _directory, _manifest = self._copy_unindexed_result(
            completed
        )
        real_connect = engine_database.connect_database
        connection_count = 0
        commit_error = OSError("catalog commit failed first")

        class CommitFails:
            def __init__(self, connection):
                self._connection = connection

            def __getattr__(self, name):
                return getattr(self._connection, name)

            def __enter__(self):
                self._connection.__enter__()
                return self

            def __exit__(self, kind, value, traceback):
                return self._connection.__exit__(kind, value, traceback)

            def commit(self):
                raise commit_error

        def connect(config):
            nonlocal connection_count
            connection_count += 1
            if connection_count == 3:
                raise RuntimeError("catalog reconciliation also failed")
            connection = real_connect(config)
            if connection_count == 2:
                return CommitFails(connection)
            return connection

        with mock.patch.object(
            engine_database,
            "connect_database",
            side_effect=connect,
        ):
            with self.assertRaises(OSError) as raised:
                backtest_result_service.recover_backtest_result_catalog(
                    self.config,
                    backtest_id,
                    completed["request"],
                )
        self.assertIs(raised.exception, commit_error)
        with real_connect(self.config) as conn:
            self.assertIsNone(
                conn.execute(
                    "SELECT 1 FROM backtests WHERE backtest_id = ?",
                    (backtest_id,),
                ).fetchone()
            )

    def test_sealed_unindexed_result_recovers_with_the_public_catalog_shape(self):
        completed = self.fixture.run_minimal_backtest(
            "sealed-unindexed-recovery-pipeline"
        )
        backtest_id, _directory, manifest = self._copy_unindexed_result(
            completed
        )

        real_verification = result_runtime.verify_result_archive_in_runtimes
        with mock.patch.object(
            result_runtime,
            "verify_result_archive_in_runtimes",
            wraps=real_verification,
        ) as verification:
            recovered = backtest_result_service.recover_backtest_result_catalog(
                self.config,
                backtest_id,
                completed["request"],
            )

        self.assertEqual(
            recovered,
            result_repository.get_backtest_meta(self.config, backtest_id),
        )
        self.assertEqual(verification.call_count, 1)
        self.assertEqual(recovered["backtestId"], backtest_id)
        self.assertEqual(recovered["status"], "completed")
        self.assertEqual(recovered["metrics"]["cycleCount"], 3)
        for field, source in (
            ("request", manifest["catalog"]["request"]),
            ("metrics", manifest["catalog"]["metrics"]),
            ("visualization", manifest["catalog"]["visualization"]),
            ("dataKeys", manifest["resultMetadata"]["dataKeys"]),
            (
                "executionChain",
                manifest["resultMetadata"]["executionChain"],
            ),
        ):
            with self.subTest(field=field):
                self.assertEqual(recovered[field], source)
                self.assertIsNot(recovered[field], source)

    def test_recovery_rejects_path_catalog_and_snapshot_tampering(self):
        completed = self.fixture.run_minimal_backtest(
            "recovery-tamper-pipeline"
        )

        path_id, path_directory, _manifest = self._copy_unindexed_result(
            completed
        )
        result_path = path_directory / "result.json"
        source_path = (
            backtest_result_archive.archive_root(self.config["releaseRoot"])
            / completed["backtestId"]
            / "result.json"
        )
        path_directory.chmod(
            path_directory.stat().st_mode | stat.S_IWUSR | stat.S_IXUSR
        )
        result_path.unlink()
        result_path.symlink_to(source_path)
        path_directory.chmod(path_directory.stat().st_mode & ~0o222)
        with self.assertRaisesRegex(ValueError, "may not contain symlinks"):
            backtest_result_service.recover_backtest_result_catalog(
                self.config,
                path_id,
                completed["request"],
            )

        catalog_id, catalog_directory, catalog_manifest = (
            self._copy_unindexed_result(completed)
        )
        catalog_manifest["catalog"]["datasetId"] = "different-dataset"
        self._rewrite_sealed_manifest(catalog_directory, catalog_manifest)
        with self.assertRaisesRegex(ValueError, "catalog request identity"):
            backtest_result_service.recover_backtest_result_catalog(
                self.config,
                catalog_id,
                completed["request"],
            )

        snapshot_id, snapshot_directory, snapshot_manifest = (
            self._copy_unindexed_result(completed)
        )
        snapshot_path = snapshot_directory / "result.json"
        snapshot_directory.chmod(
            snapshot_directory.stat().st_mode | stat.S_IWUSR | stat.S_IXUSR
        )
        snapshot_path.chmod(snapshot_path.stat().st_mode | stat.S_IWUSR)
        result = strict_json.loads(snapshot_path.read_text(encoding="utf-8"))
        result["executionChain"]["snapshotHash"] = "sha256:" + ("f" * 64)
        encoded = self._encode_framed_result(result)
        snapshot_path.write_bytes(encoded)
        snapshot_path.chmod(snapshot_path.stat().st_mode & ~0o222)
        snapshot_manifest["resultMetadata"]["executionChain"] = copy.deepcopy(
            result["executionChain"]
        )
        snapshot_manifest["contentDigest"] = (
            "sha256:" + hashlib.sha256(encoded).hexdigest()
        )
        snapshot_manifest["size"] = len(encoded)
        self._rewrite_sealed_manifest(snapshot_directory, snapshot_manifest)
        with self.assertRaisesRegex(
            ValueError,
            "does not match its stored execution snapshot",
        ):
            backtest_result_service.recover_backtest_result_catalog(
                self.config,
                snapshot_id,
                completed["request"],
            )


if __name__ == "__main__":
    unittest.main()
