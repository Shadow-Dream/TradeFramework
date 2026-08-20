import io
import json
import stat
import sqlite3
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

import dataset_transfer
from engine.archive import dataset as dataset_archive
from engine.contracts import dataset as dataset_contracts
from engine.core import clock as engine_clock
from engine.control import database as engine_database
from engine.archive import version as version_archive
from engine.repository import dataset_publication
from engine.repository import dataset_staging
from engine.repository import datasets
from engine.service import datasets as dataset_service


def zip_bytes(files):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return output.getvalue()


class DatasetTransferTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.config = {
            "controlRoot": str(root / "control"),
            "releaseRoot": str(root / "releases"),
        }
        engine_database.prepare_database(self.config)

    def tearDown(self):
        self.temp.cleanup()

    def test_zip_upload_extracts_and_registers_an_immutable_container(self):
        archive = zip_bytes({"tables/prices.parquet": b"parquet", "notes.txt": b"source notes"})
        dataset = dataset_transfer.import_dataset_zip_stream(
            self.config,
            "Uploaded Features",
            io.BytesIO(archive),
            len(archive),
            filename="features.zip",
        )

        self.assertEqual(dataset["datasetId"], "uploaded-features")
        self.assertEqual(dataset["source"], {"type": "zip-upload", "details": {}})
        self.assertFalse(
            set(dataset) & {"symbol", "interval", "startDate", "endDate", "rowCount", "csvPath"}
        )
        version = datasets.list_dataset_versions(self.config, dataset["datasetId"])[0]
        root = Path(version["storage"]["uri"])
        self.assertEqual((root / "tables" / "prices.parquet").read_bytes(), b"parquet")
        self.assertEqual(json.loads((root / "_dataset.json").read_text())["datasetId"], "uploaded-features")
        self.assertFalse(stat.S_IMODE((root / "notes.txt").stat().st_mode) & stat.S_IWUSR)
        self.assertEqual(version["storage"]["type"], "directory")
        self.assertEqual(version["capabilities"], {})
        self.assertEqual(version["status"], "sealed")

    def test_dataset_publication_uses_the_common_durable_directory_publish(self):
        archive = zip_bytes({"value.txt": b"durable"})
        with mock.patch.object(
            version_archive,
            "publish_staging_directory",
            wraps=version_archive.publish_staging_directory,
        ) as publish:
            dataset = dataset_transfer.import_dataset_zip(
                self.config,
                "durable-publication",
                archive,
            )

        self.assertEqual(publish.call_count, 1)
        staging, destination = publish.call_args.args
        self.assertTrue(
            Path(staging).parent.name.startswith(version_archive.STAGING_PREFIX)
        )
        self.assertEqual(Path(staging).name, "content")
        self.assertEqual(
            Path(destination),
            Path(datasets.ensure_dataset_version(
                self.config, dataset["datasetId"]
            )["storage"]["uri"]),
        )
        self.assertEqual(
            Path(publish.call_args.kwargs["managed_root"]),
            dataset_archive.repository_root(self.config["releaseRoot"])
            / dataset["datasetId"]
            / "versions",
        )

    def test_dataset_identity_versions_and_deletion_are_database_immutable(self):
        archive = zip_bytes({"value.txt": b"immutable"})
        dataset = dataset_transfer.import_dataset_zip(
            self.config, "immutable-dataset", archive
        )
        version = datasets.ensure_dataset_version(self.config, dataset["datasetId"])
        with engine_database.connect_database(self.config) as conn:
            for statement, parameters in (
                (
                    "UPDATE datasets SET source_json = '{}' WHERE dataset_id = ?",
                    (dataset["datasetId"],),
                ),
                (
                    "DELETE FROM datasets WHERE dataset_id = ?",
                    (dataset["datasetId"],),
                ),
                (
                    "UPDATE dataset_versions SET content_hash = ? WHERE version_id = ?",
                    ("sha256:" + "0" * 64, version["datasetVersionId"]),
                ),
                (
                    "DELETE FROM dataset_versions WHERE version_id = ?",
                    (version["datasetVersionId"],),
                ),
            ):
                with self.subTest(statement=statement), self.assertRaises(sqlite3.IntegrityError):
                    conn.execute(statement, parameters)
        renamed = datasets.rename_dataset(
            self.config, dataset["datasetId"], "Renamed immutable Dataset"
        )
        self.assertEqual(renamed["name"], "Renamed immutable Dataset")

    def test_zip_upload_can_generate_an_opaque_dataset_id(self):
        archive = zip_bytes({"value.txt": b"generated"})
        dataset = dataset_transfer.import_dataset_zip_stream(
            self.config, None, io.BytesIO(archive), len(archive), name="Readable Dataset",
        )
        self.assertRegex(dataset["datasetId"], r"^ds_[0-9A-HJKMNP-TV-Z]{26}$")
        self.assertEqual(dataset["name"], "Readable Dataset")

    def test_zip_upload_rejects_path_traversal_and_symbolic_links(self):
        with self.assertRaisesRegex(ValueError, "unsafe path"):
            dataset_transfer.import_dataset_zip(
                self.config,
                "escape",
                zip_bytes({"../outside.txt": b"no"}),
            )

        output = io.BytesIO()
        with zipfile.ZipFile(output, "w") as archive:
            link = zipfile.ZipInfo("linked-file")
            link.create_system = 3
            link.external_attr = (stat.S_IFLNK | 0o777) << 16
            archive.writestr(link, "target")
        with self.assertRaisesRegex(ValueError, "regular files and directories"):
            dataset_transfer.import_dataset_zip(self.config, "links", output.getvalue())

    def test_replace_adds_a_new_sealed_version_without_mutating_the_old_one(self):
        original = dataset_transfer.import_dataset_zip(
            self.config, "replace-me", zip_bytes({"value.txt": b"old"}), name="Original"
        )
        old_version = datasets.ensure_dataset_version(self.config, original["datasetId"])
        old_root = Path(old_version["storage"]["uri"])
        archive = zip_bytes({"value.txt": b"new", "extra.txt": b"added"})

        replaced = dataset_transfer.replace_dataset_zip_stream(
            self.config, original["datasetId"], io.BytesIO(archive), len(archive), filename="new.zip"
        )

        versions = datasets.list_dataset_versions(self.config, replaced["datasetId"])
        self.assertEqual(len(versions), 2)
        self.assertEqual((old_root / "value.txt").read_bytes(), b"old")
        self.assertEqual((Path(versions[0]["storage"]["uri"]) / "value.txt").read_bytes(), b"new")
        self.assertTrue(all(version["status"] == "sealed" for version in versions))

    def test_renamed_dataset_can_reuse_the_same_content_address(self):
        archive = zip_bytes({"value.txt": b"same"})
        original = dataset_transfer.import_dataset_zip(
            self.config,
            "renamed-reuse",
            archive,
            name="Original name",
            filename="same.zip",
        )
        datasets.rename_dataset(
            self.config, original["datasetId"], "Renamed presentation"
        )

        reused = dataset_transfer.replace_dataset_zip_stream(
            self.config,
            original["datasetId"],
            io.BytesIO(archive),
            len(archive),
            filename="same.zip",
        )

        self.assertEqual(reused["name"], "Renamed presentation")
        self.assertEqual(len(datasets.list_dataset_versions(
            self.config, original["datasetId"]
        )), 1)

    def test_single_and_batch_download_preserve_dataset_files(self):
        for dataset_id, filename in (("first", "a.txt"), ("second", "nested/b.txt")):
            dataset_transfer.import_dataset_zip(
                self.config,
                dataset_id,
                zip_bytes({filename: dataset_id.encode("utf-8")}),
            )

        single_path, single_name = dataset_transfer.build_dataset_archive(self.config, ["first"])
        try:
            self.assertEqual(single_name, "first.zip")
            with zipfile.ZipFile(single_path) as archive:
                self.assertEqual(archive.read("a.txt"), b"first")
                self.assertEqual(json.loads(archive.read("_dataset.json"))["datasetId"], "first")
        finally:
            single_path.unlink(missing_ok=True)

        batch_path, batch_name = dataset_transfer.build_dataset_archive(self.config, ["first", "second"])
        try:
            self.assertEqual(batch_name, "trade-datasets.zip")
            with zipfile.ZipFile(batch_path) as archive:
                self.assertEqual(archive.read("first/a.txt"), b"first")
                self.assertEqual(archive.read("second/nested/b.txt"), b"second")
                exported = json.loads(archive.read("_trade_dataset_export.json"))
                self.assertEqual([item["datasetId"] for item in exported["datasets"]], ["first", "second"])
        finally:
            batch_path.unlink(missing_ok=True)

    def test_export_rejects_an_empty_selection(self):
        with self.assertRaisesRegex(ValueError, "at least one"):
            dataset_transfer.build_dataset_archive(self.config, [])

        with self.assertRaisesRegex(ValueError, "duplicate"):
            dataset_transfer.build_dataset_archive(self.config, ["same", "same"])

    def test_download_rejects_dataset_without_a_sealed_version(self):
        root = dataset_archive.repository_root(self.config["releaseRoot"]) / "unsealed"
        root.mkdir(parents=True)
        with engine_database.connect_database(self.config) as conn:
            conn.execute(
                """
                INSERT INTO datasets
                (dataset_id, name, source_json, created_at, metadata_json,
                 status, archived_at, archive_reason)
                VALUES ('unsealed', 'unsealed', ?, ?, '{}', 'active', '', '')
                """,
                (json.dumps({"type": "test", "details": {}}), engine_clock.utc_now()),
            )
            conn.commit()

        with self.assertRaisesRegex(ValueError, "no sealed Dataset Version"):
            dataset_transfer.build_dataset_archive(self.config, ["unsealed"])

    def test_capability_semantics_are_part_of_dataset_content_hash(self):
        root = Path(self.temp.name) / "capability-hash"
        root.mkdir()
        (root / "records.jsonl").write_text(
            '{"sequence":0,"eventTime":"2026-01-01","availableAt":"2026-01-02","values":{"v":1}}\n',
            encoding="utf-8",
        )
        files = dataset_archive.container_files(root)
        base = {
            "protocol": dataset_contracts.RECORDS_PROTOCOL,
            "descriptor": {
                "path": "records.jsonl",
                "recordCount": 1,
                "eventTimeField": "eventTime",
                "availableTimeField": "availableAt",
                "valueSchema": {
                    "fields": {"v": {"type": "integer"}},
                    "entityKeys": [],
                    "sortKeys": [],
                },
            },
        }
        first = dataset_archive.content_hash(files, {"records": base})
        changed = json.loads(json.dumps(base))
        changed["descriptor"]["availableTimeField"] = "publishedAt"
        second = dataset_archive.content_hash(files, {"records": changed})
        self.assertNotEqual(first, second)

    def test_content_hash_frames_file_boundaries_without_structural_collisions(self):
        first_root = Path(self.temp.name) / "hash-first"
        second_root = Path(self.temp.name) / "hash-second"
        first_root.mkdir()
        second_root.mkdir()
        (first_root / "a").write_bytes(b"x\0b\0y")
        (second_root / "a").write_bytes(b"x")
        (second_root / "b").write_bytes(b"y")
        self.assertNotEqual(
            dataset_archive.content_hash(dataset_archive.container_files(first_root), {}),
            dataset_archive.content_hash(dataset_archive.container_files(second_root), {}),
        )

    def test_export_import_preserves_generic_records_capability(self):
        staging = dataset_staging.create_dataset_staging(self.config, "records-source")
        (staging.path / "records.jsonl").write_text(
            '{"sequence":0,"observedAt":"2026-01-02","publishedAt":"2026-01-01T12:00:00Z","values":{"v":1}}\n',
            encoding="utf-8",
        )
        dataset_publication.publish_dataset_version(
            self.config,
            dataset={
                "datasetId": "records-source",
                "name": "Records source",
                "source": {"type": "test", "details": {}},
                "metadata": {},
            },
            staging=staging,
            capabilities=self.records_capability(1),
            version_source={"type": "test", "details": {}},
        )
        archive_path, _filename = dataset_transfer.build_dataset_archive(
            self.config, ["records-source"]
        )
        try:
            imported = dataset_transfer.import_dataset_zip_path(
                self.config, "records-copy", archive_path
            )
        finally:
            archive_path.unlink(missing_ok=True)
        version = datasets.ensure_dataset_version(self.config, imported["datasetId"])
        self.assertEqual(version["capabilities"], self.records_capability(1))
        self.assertEqual(
            [record.values for record in dataset_service.get_dataset_records(
                self.config, version["datasetVersionId"]
            )],
            [{"v": 1}],
        )

    def test_runtime_verification_never_accepts_a_nonexistent_storage_uri(self):
        root = Path(self.temp.name) / "wrong-uri"
        root.mkdir()
        (root / "value.txt").write_text("value", encoding="utf-8")
        destination = Path(self.temp.name) / "does-not-exist"
        manifest = dataset_archive.build_manifest(
            root,
            dataset_id="wrong-uri",
            dataset={
                "datasetId": "wrong-uri",
                "name": "Wrong URI",
                "source": {"type": "test", "details": {}},
                "metadata": {},
            },
            source={"type": "test", "details": {}},
            created_at=engine_clock.utc_now(),
            storage_uri=destination,
            capabilities={},
        )
        dataset_archive.write_manifest(root, manifest)
        dataset_archive.make_tree_read_only(root)
        dataset_archive.verify_staging_container(root, manifest, destination)
        with self.assertRaisesRegex(ValueError, "storage URI"):
            dataset_archive.verify_sealed_container(root, manifest)

    @staticmethod
    def records_capability(record_count):
        return {
            "records": {
                "protocol": dataset_contracts.RECORDS_PROTOCOL,
                "descriptor": {
                    "path": "records.jsonl",
                    "recordCount": record_count,
                    "eventTimeField": "observedAt",
                    "availableTimeField": "publishedAt",
                    "valueSchema": {
                        "fields": {"v": {"type": "integer"}},
                        "entityKeys": [],
                        "sortKeys": [],
                    },
                },
            },
        }

    def test_records_capability_is_verified_before_publication(self):
        staging = dataset_staging.create_dataset_staging(self.config, "invalid-records")
        (staging.path / "records.jsonl").write_text(
            '{"sequence":1,"observedAt":"2026-01-01","publishedAt":"2026-01-02","values":{"v":1}}\n',
            encoding="utf-8",
        )
        with self.assertRaisesRegex(ValueError, "sequence"):
            dataset_publication.publish_dataset_version(
                self.config,
                dataset={
                    "datasetId": "invalid-records",
                    "name": "Invalid records",
                    "source": {"type": "test", "details": {}},
                    "metadata": {},
                },
                staging=staging,
                capabilities=self.records_capability(1),
                version_source={"type": "test", "details": {}},
            )
        with self.assertRaisesRegex(ValueError, "Unknown dataset"):
            datasets.get_dataset(self.config, "invalid-records")

    def test_scoped_semantic_verification_still_hashes_every_container_byte(self):
        root = Path(self.temp.name) / "scoped-semantics"
        root.mkdir()
        records_path = root / "records.jsonl"
        invalid_records = (
            '{"sequence":1,"observedAt":"2026-01-01",'
            '"publishedAt":"2026-01-02","values":{"v":1}}\n'
        )
        records_path.write_text(invalid_records, encoding="utf-8")
        manifest = dataset_archive.build_manifest(
            root,
            dataset_id="scoped-semantics",
            dataset={
                "datasetId": "scoped-semantics",
                "name": "Scoped semantics",
                "source": {"type": "test", "details": {}},
                "metadata": {},
            },
            source={"type": "test", "details": {}},
            created_at=engine_clock.utc_now(),
            storage_uri=root,
            capabilities=self.records_capability(1),
        )
        dataset_archive.write_manifest(root, manifest)
        dataset_archive.make_tree_read_only(root)

        with self.assertRaisesRegex(ValueError, "sequence"):
            dataset_archive.verify_sealed_container(root, manifest)
        dataset_archive.verify_sealed_container(
            root,
            manifest,
            semantic_capabilities=frozenset(),
        )

        records_path.chmod(0o600)
        records_path.write_text(invalid_records + " ", encoding="utf-8")
        dataset_archive.make_tree_read_only(root)
        with self.assertRaisesRegex(ValueError, "file verification|content hash"):
            dataset_archive.verify_sealed_container(
                root,
                manifest,
                semantic_capabilities=frozenset(),
            )

    def test_explicit_unregistered_semantic_capability_fails_closed(self):
        manifest = {
            "capabilities": {
                "opaque": {
                    "protocol": "test/opaque-v1",
                    "descriptor": {},
                },
            },
        }
        self.assertEqual(
            dataset_archive.semantic_capabilities_to_verify(manifest, None),
            frozenset(),
        )
        with self.assertRaisesRegex(ValueError, "no registered verifier"):
            dataset_archive.semantic_capabilities_to_verify(
                manifest,
                frozenset({"opaque"}),
            )
        with self.assertRaisesRegex(ValueError, "undeclared capability"):
            dataset_archive.semantic_capabilities_to_verify(
                manifest,
                frozenset({"missing"}),
            )

    def test_publication_rejects_unconsumed_reserved_capability_file(self):
        staging = dataset_staging.create_dataset_staging(self.config, "reserved-file")
        (staging.path / "value.txt").write_text("value", encoding="utf-8")
        (staging.path / dataset_archive.CAPABILITIES_DECLARATION_NAME).write_text(
            '{"schemaVersion":1,"capabilities":{}}', encoding="utf-8"
        )
        with self.assertRaisesRegex(ValueError, "reserved root file"):
            dataset_publication.publish_dataset_version(
                self.config,
                dataset={
                    "datasetId": "reserved-file",
                    "name": "Reserved file",
                    "source": {"type": "test", "details": {}},
                    "metadata": {},
                },
                staging=staging,
                capabilities={},
                version_source={"type": "test", "details": {}},
            )

    def test_empty_records_are_a_valid_generic_dataset_capability(self):
        staging = dataset_staging.create_dataset_staging(self.config, "empty-records")
        (staging.path / "records.jsonl").write_text("", encoding="utf-8")
        dataset = dataset_publication.publish_dataset_version(
            self.config,
            dataset={
                "datasetId": "empty-records",
                "name": "Empty records",
                "source": {"type": "test", "details": {}},
                "metadata": {},
            },
            staging=staging,
            capabilities=self.records_capability(0),
            version_source={"type": "test", "details": {}},
        )
        self.assertEqual(
            dataset_service.get_dataset_records(self.config, dataset["latestVersionId"]), []
        )

    def test_publication_transaction_rolls_back_index_and_container_together(self):
        staging = dataset_staging.create_dataset_staging(self.config, "rolled-back")
        (staging.path / "value.txt").write_text("value", encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "abort publication"):
            with dataset_publication.dataset_publication_transaction(
                self.config,
                dataset={
                    "datasetId": "rolled-back",
                    "name": "Rolled back",
                    "source": {"type": "test", "details": {}},
                    "metadata": {},
                },
                staging=staging,
                capabilities={},
                version_source={"type": "test", "details": {}},
            ):
                raise RuntimeError("abort publication")
        with self.assertRaisesRegex(ValueError, "Unknown dataset"):
            datasets.get_dataset(self.config, "rolled-back")
        versions = dataset_archive.repository_root(self.config["releaseRoot"]) / "rolled-back" / "versions"
        self.assertEqual(list(versions.iterdir()), [])

    def test_publication_recovers_when_commit_succeeds_before_driver_error(self):
        staging = dataset_staging.create_dataset_staging(self.config, "uncertain-commit")
        (staging.path / "value.txt").write_text("value", encoding="utf-8")
        original_connect = engine_database.connect_database
        raised = []

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
                if self.publication and not raised:
                    raised.append(True)
                    raise RuntimeError("driver reported commit late")

        def connect(config):
            return ConnectionProxy(original_connect(config))

        with mock.patch.object(
            engine_database,
            "connect_database",
            side_effect=connect,
        ):
            dataset = dataset_publication.publish_dataset_version(
                self.config,
                dataset={
                    "datasetId": "uncertain-commit",
                    "name": "Uncertain commit",
                    "source": {"type": "test", "details": {}},
                    "metadata": {},
                },
                staging=staging,
                capabilities={},
                version_source={"type": "test", "details": {}},
            )
        self.assertTrue(raised)
        self.assertEqual(dataset["datasetId"], "uncertain-commit")
        datasets.verify_dataset_version_id(
            self.config, dataset["latestVersionId"]
        )

    def test_orphan_archive_cannot_be_claimed_by_another_dataset_descriptor(self):
        staging = dataset_staging.create_dataset_staging(self.config, "orphan-owner")
        (staging.path / "value.txt").write_text("same", encoding="utf-8")
        dataset_publication.publish_dataset_version(
            self.config,
            dataset={
                "datasetId": "orphan-owner",
                "name": "Original",
                "source": {"type": "original", "details": {}},
                "metadata": {"owner": "original"},
            },
            staging=staging,
            capabilities={},
            version_source={"type": "test", "details": {}},
        )
        recovery_config = {
            **self.config,
            "controlRoot": str(Path(self.temp.name) / "recovery-control"),
        }
        engine_database.prepare_database(recovery_config)
        replacement = dataset_staging.create_dataset_staging(
            recovery_config, "orphan-owner"
        )
        (replacement.path / "value.txt").write_text("same", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "different provenance"):
            dataset_publication.publish_dataset_version(
                recovery_config,
                dataset={
                    "datasetId": "orphan-owner",
                    "name": "Different",
                    "source": {"type": "different", "details": {}},
                    "metadata": {"owner": "different"},
                },
                staging=replacement,
                capabilities={},
                version_source={"type": "test", "details": {}},
            )


if __name__ == "__main__":
    unittest.main()
