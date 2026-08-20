import copy
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from engine.archive import dataset as dataset_archive
from engine.archive import version as version_archive
from engine.control import database as engine_database
from engine.core import clock as engine_clock
from engine.repository import dataset_publication
from engine.repository import dataset_staging
from engine.repository import datasets


class DatasetPublicationAuthorityTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.config = {
            "controlRoot": str(root / "control"),
            "releaseRoot": str(root / "releases"),
        }
        engine_database.prepare_database(self.config)

    def tearDown(self):
        try:
            dataset_publication.retry_pending_dataset_publication_cleanup()
        except BaseException:
            pass
        self.temporary.cleanup()

    @staticmethod
    def descriptor(dataset_id, name="Dataset"):
        return {
            "datasetId": dataset_id,
            "name": name,
            "source": {"type": "test", "details": {}},
            "metadata": {},
        }

    def staging_with_value(self, config, dataset_id, value="value"):
        staging = dataset_staging.create_dataset_staging(config, dataset_id)
        (staging.path / "value.txt").write_text(value, encoding="utf-8")
        return staging

    def publish(self, config, staging, dataset_id, **overrides):
        arguments = {
            "dataset": self.descriptor(dataset_id),
            "staging": staging,
            "capabilities": {},
            "version_source": {"type": "test", "details": {}},
        }
        arguments.update(overrides)
        return dataset_publication.publish_dataset_version(config, **arguments)

    def test_foreign_raw_staging_is_rejected_without_side_effects(self):
        versions_root = (
            dataset_archive.repository_root(self.config["releaseRoot"])
            / "foreign"
            / "versions"
        )
        foreign = versions_root / "caller-created"
        foreign.mkdir(parents=True)
        payload = foreign / "value.txt"
        payload.write_text("unchanged", encoding="utf-8")

        with self.assertRaisesRegex(TypeError, "Engine-owned staging authority"):
            self.publish(self.config, foreign, "foreign")

        self.assertEqual(payload.read_text(encoding="utf-8"), "unchanged")
        self.assertEqual(list(versions_root.iterdir()), [foreign])
        with self.assertRaisesRegex(ValueError, "Unknown dataset"):
            datasets.get_dataset(self.config, "foreign")

    def test_post_commit_close_error_is_reported_and_exact_retry_is_idempotent(self):
        staging = self.staging_with_value(self.config, "close-retry")
        original_connect = engine_database.connect_database
        close_errors = []

        class ConnectionProxy:
            def __init__(self, connection):
                self.connection = connection
                self.publication = False

            def __enter__(self):
                return self

            def __exit__(self, kind, value, traceback):
                return self.connection.__exit__(kind, value, traceback)

            def __getattr__(self, name):
                return getattr(self.connection, name)

            def execute(self, sql, parameters=()):
                if sql.strip().upper() == "BEGIN IMMEDIATE":
                    self.publication = True
                return self.connection.execute(sql, parameters)

            def close(self):
                self.connection.close()
                if self.publication and not close_errors:
                    close_errors.append(True)
                    raise OSError("post-commit close failed")

        with mock.patch.object(
            engine_database,
            "connect_database",
            side_effect=lambda config: ConnectionProxy(original_connect(config)),
        ):
            with self.assertRaisesRegex(OSError, "post-commit close failed"):
                self.publish(self.config, staging, "close-retry")

        self.assertTrue(close_errors)
        first_version = datasets.get_dataset(
            self.config,
            "close-retry",
        )["latestVersionId"]
        retry = self.staging_with_value(self.config, "close-retry")
        repeated = self.publish(self.config, retry, "close-retry")
        self.assertEqual(repeated["latestVersionId"], first_version)
        self.assertEqual(len(datasets.list_dataset_versions(self.config, "close-retry")), 1)

    def test_post_commit_staging_cleanup_error_is_reported(self):
        staging = self.staging_with_value(self.config, "staging-cleanup")
        original_discard = dataset_staging.discard_dataset_staging
        failed = []

        def fail_once(authority):
            if not failed:
                failed.append(True)
                raise OSError("staging cleanup failed")
            return original_discard(authority)

        with mock.patch.object(
            dataset_staging,
            "discard_dataset_staging",
            side_effect=fail_once,
        ):
            with self.assertRaisesRegex(OSError, "staging cleanup failed"):
                self.publish(self.config, staging, "staging-cleanup")

        self.assertEqual(
            datasets.get_dataset(self.config, "staging-cleanup")["datasetId"],
            "staging-cleanup",
        )
        dataset_publication.retry_pending_dataset_publication_cleanup()
        self.assertFalse(staging.path.parent.exists())

    def test_evidence_failure_preserves_commit_acknowledgement_as_primary(self):
        staging = self.staging_with_value(self.config, "evidence-failure")
        original_connect = engine_database.connect_database
        state = {"commit_failed": False}

        class ConnectionProxy:
            def __init__(self, connection):
                self.connection = connection
                self.publication = False

            def __enter__(self):
                return self

            def __exit__(self, kind, value, traceback):
                return self.connection.__exit__(kind, value, traceback)

            def __getattr__(self, name):
                return getattr(self.connection, name)

            def execute(self, sql, parameters=()):
                if sql.strip().upper() == "BEGIN IMMEDIATE":
                    self.publication = True
                return self.connection.execute(sql, parameters)

            def commit(self):
                self.connection.commit()
                if self.publication:
                    state["commit_failed"] = True
                    raise OSError("commit acknowledgement lost")

        def connect(config):
            if state["commit_failed"]:
                raise RuntimeError("evidence probe failed")
            return ConnectionProxy(original_connect(config))

        with mock.patch.object(
            engine_database, "connect_database", side_effect=connect
        ):
            with self.assertRaisesRegex(
                OSError, "commit acknowledgement lost"
            ) as raised:
                self.publish(self.config, staging, "evidence-failure")

        self.assertIsInstance(raised.exception.__context__, RuntimeError)
        retry = self.staging_with_value(self.config, "evidence-failure")
        recovered = self.publish(self.config, retry, "evidence-failure")
        self.assertEqual(
            len(datasets.list_dataset_versions(self.config, "evidence-failure")), 1
        )
        datasets.verify_dataset_version_id(
            self.config, recovered["latestVersionId"]
        )

    def test_precommit_failure_on_exact_retry_cannot_use_old_version_as_receipt(self):
        original = self.publish(
            self.config,
            self.staging_with_value(self.config, "retry-rollback"),
            "retry-rollback",
        )
        original_connect = engine_database.connect_database

        class ConnectionProxy:
            def __init__(self, connection):
                self.connection = connection
                self.publication = False

            def __enter__(self):
                return self

            def __exit__(self, kind, value, traceback):
                return self.connection.__exit__(kind, value, traceback)

            def __getattr__(self, name):
                return getattr(self.connection, name)

            def execute(self, sql, parameters=()):
                if sql.strip().upper() == "BEGIN IMMEDIATE":
                    self.publication = True
                return self.connection.execute(sql, parameters)

            def commit(self):
                if self.publication:
                    raise OSError("commit failed before durability")
                return self.connection.commit()

        retry = self.staging_with_value(self.config, "retry-rollback")
        with mock.patch.object(
            engine_database,
            "connect_database",
            side_effect=lambda config: ConnectionProxy(original_connect(config)),
        ):
            with self.assertRaisesRegex(OSError, "before durability"):
                with dataset_publication.dataset_publication_transaction(
                    self.config,
                    dataset=self.descriptor("retry-rollback"),
                    staging=retry,
                    capabilities={},
                    version_source={"type": "test", "details": {}},
                ) as publication:
                    publication["connection"].execute(
                        "UPDATE datasets SET name = ? WHERE dataset_id = ?",
                        ("must roll back", "retry-rollback"),
                    )

        stored = datasets.get_dataset(self.config, "retry-rollback")
        self.assertEqual(stored["name"], "Dataset")
        self.assertEqual(stored["latestVersionId"], original["latestVersionId"])

    def test_publication_order_is_monotonic_across_equal_and_rolled_back_clocks(self):
        versions = []
        for value, instant, append in (
            ("A", "2042-01-02T00:00:00.000000Z", False),
            ("B", "2042-01-02T00:00:00.000000Z", True),
            ("C", "2041-01-02T00:00:00.000000Z", True),
        ):
            with mock.patch.object(engine_clock, "utc_now", return_value=instant):
                published = self.publish(
                    self.config,
                    self.staging_with_value(self.config, "ordered", value),
                    "ordered",
                    append=append,
                )
            versions.append(published["latestVersionId"])

        self.assertEqual(datasets.get_dataset(self.config, "ordered")["latestVersionId"], versions[2])
        self.assertEqual(
            [item["datasetVersionId"] for item in datasets.list_dataset_versions(self.config, "ordered")],
            list(reversed(versions)),
        )
        self.assertEqual(
            datasets.ensure_dataset_version(self.config, "ordered")["datasetVersionId"],
            versions[2],
        )
        self.assertEqual(
            [item["datasetVersionId"] for item in datasets.list_dataset_version_summaries(
                self.config, ["ordered"]
            )],
            list(reversed(versions)),
        )

    def test_historical_content_address_cannot_masquerade_as_exact_retry(self):
        first = self.publish(
            self.config,
            self.staging_with_value(self.config, "historical", "A"),
            "historical",
        )
        second = self.publish(
            self.config,
            self.staging_with_value(self.config, "historical", "B"),
            "historical",
            append=True,
        )
        retry = self.staging_with_value(self.config, "historical", "A")

        with self.assertRaisesRegex(ValueError, "Historical Dataset Version"):
            self.publish(self.config, retry, "historical")

        self.assertNotEqual(first["latestVersionId"], second["latestVersionId"])
        self.assertEqual(
            datasets.get_dataset(self.config, "historical")["latestVersionId"],
            second["latestVersionId"],
        )
        self.assertEqual(len(datasets.list_dataset_versions(self.config, "historical")), 2)

    def test_retry_rechecks_latest_after_concurrent_append_at_transaction_boundary(self):
        first = self.publish(
            self.config,
            self.staging_with_value(self.config, "latest-race", "A"),
            "latest-race",
        )
        retry = self.staging_with_value(self.config, "latest-race", "A")
        append = self.staging_with_value(self.config, "latest-race", "B")
        original_connect = engine_database.connect_database
        retry_waiting = threading.Event()
        append_committed = threading.Event()
        errors = []

        class ConnectionProxy:
            def __init__(self, connection):
                self.connection = connection

            def __enter__(self):
                return self

            def __exit__(self, kind, value, traceback):
                return self.connection.__exit__(kind, value, traceback)

            def __getattr__(self, name):
                return getattr(self.connection, name)

            def execute(self, sql, parameters=()):
                if (
                    sql.strip().upper() == "BEGIN IMMEDIATE"
                    and threading.current_thread().name == "retry-A"
                    and not retry_waiting.is_set()
                ):
                    retry_waiting.set()
                    if not append_committed.wait(timeout=10):
                        raise RuntimeError("concurrent append did not finish")
                return self.connection.execute(sql, parameters)

        def retry_a():
            try:
                self.publish(self.config, retry, "latest-race")
            except BaseException as error:
                errors.append(error)

        with mock.patch.object(
            engine_database,
            "connect_database",
            side_effect=lambda config: ConnectionProxy(original_connect(config)),
        ):
            thread = threading.Thread(target=retry_a, name="retry-A")
            thread.start()
            self.assertTrue(retry_waiting.wait(timeout=10))
            try:
                second = self.publish(
                    self.config, append, "latest-race", append=True
                )
            finally:
                append_committed.set()
            thread.join(timeout=10)

        self.assertFalse(thread.is_alive())
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], ValueError)
        self.assertIn("Historical Dataset Version", str(errors[0]))
        self.assertNotEqual(first["latestVersionId"], second["latestVersionId"])
        self.assertEqual(
            datasets.get_dataset(self.config, "latest-race")["latestVersionId"],
            second["latestVersionId"],
        )

    def test_orphan_job_authority_cannot_be_claimed_under_another_job(self):
        staging = self.staging_with_value(self.config, "job-owner", "same")
        original = self.publish(
            self.config,
            staging,
            "job-owner",
            version_source={"type": "build", "details": {"jobId": "job-A"}},
            build={"jobId": "job-A"},
            build_job_id="job-A",
        )
        recovery_config = {
            **self.config,
            "controlRoot": str(Path(self.temporary.name) / "recovery-control"),
        }
        engine_database.prepare_database(recovery_config)
        replacement = self.staging_with_value(recovery_config, "job-owner", "same")

        with self.assertRaisesRegex(ValueError, "jobId authority"):
            self.publish(
                recovery_config,
                replacement,
                "job-owner",
                version_source={
                    "type": "build",
                    "details": {"jobId": "job-A"},
                },
                build={"jobId": "job-A"},
                build_job_id="job-B",
            )

        self.assertFalse(replacement.path.exists())
        with self.assertRaisesRegex(ValueError, "Unknown dataset"):
            datasets.get_dataset(recovery_config, "job-owner")
        datasets.verify_dataset_version_id(
            self.config, original["latestVersionId"]
        )

    def test_self_consistent_archive_with_noncanonical_dataset_id_is_rejected(self):
        container = Path(self.temporary.name) / "canonical-container"
        container.mkdir()
        (container / "value.txt").write_text("value", encoding="utf-8")
        manifest = dataset_archive.build_manifest(
            container,
            dataset_id="a-b",
            dataset=self.descriptor("a-b"),
            source={"type": "test", "details": {}},
            created_at=engine_clock.utc_now(),
            storage_uri=container,
            capabilities={},
        )
        manifest["datasetId"] = "a/b"
        manifest["dataset"]["datasetId"] = "a/b"
        manifest["datasetVersionId"] = f"a/b@{manifest['contentHash']}"
        manifest["manifestDigest"] = version_archive.content_digest(
            {
                key: value
                for key, value in manifest.items()
                if key != "manifestDigest"
            }
        )

        with self.assertRaisesRegex(ValueError, "already be normalized"):
            dataset_archive.validate_manifest(manifest)

    def test_self_consistent_storage_alias_is_not_a_canonical_location(self):
        published = self.publish(
            self.config,
            self.staging_with_value(self.config, "storage-alias"),
            "storage-alias",
        )
        version = datasets.verify_dataset_version_id(
            self.config, published["latestVersionId"]
        )
        aliased = copy.deepcopy(version)
        canonical = aliased["storage"]["uri"]
        alias = f"{canonical}/../container"
        aliased["storage"]["uri"] = alias
        aliased["manifest"]["storage"]["uri"] = alias
        aliased["manifest"]["manifestDigest"] = version_archive.content_digest(
            {
                key: value
                for key, value in aliased["manifest"].items()
                if key != "manifestDigest"
            }
        )
        aliased["manifestDigest"] = aliased["manifest"]["manifestDigest"]

        dataset_archive.validate_sealed_version_descriptor(aliased)
        with self.assertRaisesRegex(ValueError, "Engine-managed archive root"):
            dataset_archive.resolve_version_storage_root(
                self.config["releaseRoot"], aliased
            )
