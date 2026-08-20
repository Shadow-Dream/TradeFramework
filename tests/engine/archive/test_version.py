#!/usr/bin/env python3
"""Version archive transaction and authority tests."""

import json
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from engine.archive import version as version_archive
from engine.archive import version_evidence
from engine.archive import version_transaction
from builtin_implementations import resources as builtin_resources
from engine.control import database as engine_database
from engine.repository import control_state
from engine.repository import module_definitions
from engine.service import control_api as control


class VersionArchiveTransactionTests(unittest.TestCase):
    def test_discard_archive_removes_a_fully_read_only_tree(self):
        with tempfile.TemporaryDirectory() as temporary:
            archive = Path(temporary) / "sealed"
            nested = archive / "nested"
            nested.mkdir(parents=True)
            payload = nested / "payload.txt"
            payload.write_text("content", encoding="utf-8")
            payload.chmod(0o444)
            nested.chmod(0o555)
            archive.chmod(0o555)

            version_archive.discard_archive(archive)

            self.assertFalse(archive.exists())

    def test_control_json_write_syncs_file_before_replace_and_parent_after(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "control" / "state.json"
            events = []
            real_fsync = os.fsync
            real_replace = os.replace

            def fsync(descriptor):
                mode = os.fstat(descriptor).st_mode
                events.append("directory-fsync" if stat.S_ISDIR(mode) else "file-fsync")
                return real_fsync(descriptor)

            def replace(source, destination):
                events.append("replace")
                return real_replace(source, destination)

            with (
                mock.patch.object(control_state.os, "fsync", side_effect=fsync),
                mock.patch.object(control_state.os, "replace", side_effect=replace),
            ):
                control_state.atomic_write_json(target, {"state": "durable"})

            self.assertEqual(events, ["file-fsync", "replace", "directory-fsync"])
            self.assertEqual(
                json.loads(target.read_text(encoding="utf-8")),
                {"state": "durable"},
            )
            self.assertEqual(list(target.parent.glob(".state.json.*.tmp")), [])

    def test_directory_publication_syncs_tree_and_rename_parents_in_order(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source_parent = root / "source"
            destination_parent = root / "destination"
            source_parent.mkdir()
            destination_parent.mkdir()
            staging = source_parent / f"{version_archive.STAGING_PREFIX}durability"
            staging.mkdir()
            (staging / "payload.txt").write_text("content", encoding="utf-8")
            destination = destination_parent / "1"
            events = []
            real_replace = os.replace

            def replace(source, target):
                events.append(("replace", Path(source), Path(target)))
                return real_replace(source, target)

            with (
                mock.patch.object(
                    version_archive,
                    "fsync_tree",
                    side_effect=lambda path: events.append(("tree", Path(path))),
                ),
                mock.patch.object(
                    version_archive,
                    "_fsync_directory",
                    side_effect=lambda path: events.append(("directory", Path(path))),
                ),
                mock.patch.object(version_archive.os, "replace", side_effect=replace),
            ):
                published = version_archive.publish_staging_directory(
                    staging,
                    destination,
                    managed_root=root,
                )

            replace_index = next(
                index for index, event in enumerate(events) if event[0] == "replace"
            )
            self.assertEqual(events[0], ("tree", staging.resolve(strict=False)))
            self.assertIn(("directory", source_parent), events[:replace_index])
            self.assertIn(("directory", destination_parent), events[:replace_index])
            self.assertEqual(
                events[replace_index + 1 :],
                [
                    ("directory", destination_parent),
                    ("directory", source_parent),
                ],
            )
            self.assertEqual(published, destination)
            self.assertTrue((destination / "payload.txt").is_file())

    def test_fsync_tree_flushes_each_file_and_directory(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "tree"
            nested = root / "nested"
            nested.mkdir(parents=True)
            (root / "first").write_bytes(b"first")
            (nested / "second").write_bytes(b"second")
            with mock.patch.object(
                version_archive.os,
                "fsync",
                wraps=version_archive.os.fsync,
            ) as fsync:
                version_archive.fsync_tree(root)
            self.assertEqual(fsync.call_count, 4)

    def test_startup_recovery_removes_only_uncommitted_archive_staging(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            stale = root / "repository" / f"{version_archive.STAGING_PREFIX}stale"
            stale.mkdir(parents=True)
            (stale / "payload").write_text("stale", encoding="utf-8")

            committed = root / "repository" / "versions" / "1"
            protected_version_staging = (
                committed / "payload" / f"{version_archive.STAGING_PREFIX}data"
            )
            protected_version_staging.mkdir(parents=True)
            (committed / version_archive.MANIFEST_NAME).write_text(
                "committed", encoding="utf-8"
            )

            dataset = root / "data" / "versions" / "hash" / "container"
            protected_dataset_staging = (
                dataset / "payload" / f"{version_archive.STAGING_PREFIX}data"
            )
            protected_dataset_staging.mkdir(parents=True)
            (dataset / "_dataset.json").write_text("committed", encoding="utf-8")

            unrelated = root / "repository" / ".result-staging-unrelated"
            unrelated.mkdir()
            incomplete_name = root / "repository" / version_archive.STAGING_PREFIX
            incomplete_name.mkdir()

            removed = version_archive.reconcile_staging_directories(
                root,
                committed_markers=(version_archive.MANIFEST_NAME, "_dataset.json"),
            )

            self.assertEqual(removed, [str(stale)])
            self.assertFalse(stale.exists())
            self.assertTrue(protected_version_staging.is_dir())
            self.assertTrue(protected_dataset_staging.is_dir())
            self.assertTrue(unrelated.is_dir())
            self.assertTrue(incomplete_name.is_dir())

    def test_startup_recovery_never_follows_a_staging_symlink(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "target"
            target.mkdir()
            (target / "payload").write_text("keep", encoding="utf-8")
            staging_link = root / f"{version_archive.STAGING_PREFIX}link"
            staging_link.symlink_to(target, target_is_directory=True)

            with self.assertRaisesRegex(ValueError, "may not be a symbolic link"):
                version_archive.reconcile_staging_directories(
                    root,
                    committed_markers=(version_archive.MANIFEST_NAME, "_dataset.json"),
                )
            self.assertTrue(staging_link.is_symlink())
            self.assertEqual((target / "payload").read_text(encoding="utf-8"), "keep")

    def test_content_digest_rejects_non_json_and_non_finite_values(self):
        for value in ({1: "coerced"}, {"value": float("nan")}, {"value": float("inf")}):
            with self.subTest(value=value), self.assertRaises(ValueError):
                version_archive.content_digest(value)

    @staticmethod
    def archive_resource(root, *, nested_manifest=False, create_record=None):
        destination = Path(root) / "resource" / "1"
        state = {}

        def prepare(staging, _version, _destination):
            (staging / "payload.txt").write_text("content", encoding="utf-8")
            if nested_manifest:
                nested = staging / "nested"
                nested.mkdir()
                (nested / version_archive.MANIFEST_NAME).write_text(
                    "nested content", encoding="utf-8"
                )
            return {"value": "content", "nestedManifest": nested_manifest}, None

        result = version_transaction.archive_if_changed(
            records=[],
            identity_key="resourceId",
            identity="resource",
            resource_type="test-resource",
            resource_id="resource",
            managed_root=root,
            destination_for_version=lambda _version: destination,
            prepare_staging=prepare,
            create_record=create_record or (
                lambda _version, _context: {"resourceId": "resource"}
            ),
            record_fields={"resourceId"},
            write_record=lambda _staging, _record, _context: None,
            commit_record=lambda record, _context: state.update(record),
            read_committed_record=lambda _record, _context: dict(state) if state else None,
            immutable_fields=(),
        )
        return destination, result["record"]

    @staticmethod
    def rewrite_json(path, payload):
        path.chmod(0o644)
        path.write_text(json.dumps(payload), encoding="utf-8")
        path.chmod(0o444)

    @classmethod
    def rewrite_record_root(cls, destination, record, root_alias):
        forged = json.loads(json.dumps(record))
        forged["archive"]["root"] = root_alias
        archived_record = json.loads(json.dumps(forged))
        archived_record["archive"].pop("manifestDigest")
        cls.rewrite_json(
            destination / version_archive.RECORD_NAME,
            archived_record,
        )
        manifest_path = destination / version_archive.MANIFEST_NAME
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["files"] = version_archive.file_manifest(destination)
        manifest["manifestDigest"] = version_archive.content_digest({
            key: value for key, value in manifest.items() if key != "manifestDigest"
        })
        cls.rewrite_json(manifest_path, manifest)
        forged["archive"]["manifestDigest"] = manifest["manifestDigest"]
        return forged

    @staticmethod
    def verified_resource_index(root, destination, record):
        return version_evidence.verify_record_index_location_evidence(
            {"resource/1": record},
            ("resourceId",),
            managed_root=root,
            expected_root_for=lambda _record: destination,
        )[1]

    @staticmethod
    def publish_with_evidence(
        root,
        records,
        evidence,
        prepared,
        *,
        resource_id="resource",
        destination_directory="resource",
        immutable_fields=(),
    ):
        def prepare(staging, _version, _destination):
            prepared.append(True)
            (staging / "payload.txt").write_text("content", encoding="utf-8")
            return {"value": "content", "nestedManifest": False}, None

        return version_transaction.archive_if_changed(
            records=records,
            identity_key="resourceId",
            identity="resource",
            resource_type="test-resource",
            resource_id=resource_id,
            managed_root=root,
            destination_for_version=lambda version: (
                Path(root) / destination_directory / version
            ),
            prepare_staging=prepare,
            create_record=lambda _version, _context: {"resourceId": "resource"},
            record_fields={"resourceId"},
            write_record=lambda _staging, _record, _context: None,
            commit_record=lambda _record, _context: None,
            read_committed_record=lambda _record, _context: None,
            immutable_fields=immutable_fields,
            verified_records_evidence=evidence,
        )

    def test_verified_index_evidence_is_immutable_and_rejects_forged_unchanged(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            destination, record = self.archive_resource(root)
            evidence = self.verified_resource_index(root, destination, record)
            forged = json.loads(json.dumps(record))
            forged["archive"]["root"] = str(root / "outside-index" / "1")

            with self.assertRaisesRegex(AttributeError, "immutable"):
                evidence._records_json = json.dumps({"resource/1": forged})

            prepared = []
            with self.assertRaisesRegex(ValueError, "not covered"):
                self.publish_with_evidence(root, [forged], evidence, prepared)
            self.assertEqual(prepared, [])
            self.assertFalse((root / "outside-index").exists())

    def test_verified_index_evidence_cannot_cross_transaction_managed_roots(self):
        with (
            tempfile.TemporaryDirectory() as first_temporary,
            tempfile.TemporaryDirectory() as second_temporary,
        ):
            first_root = Path(first_temporary)
            second_root = Path(second_temporary)
            destination, record = self.archive_resource(first_root)
            evidence = self.verified_resource_index(
                first_root, destination, record
            )
            prepared = []

            with self.assertRaisesRegex(ValueError, "different managed root"):
                self.publish_with_evidence(
                    second_root,
                    [record],
                    evidence,
                    prepared,
                )
            self.assertEqual(prepared, [])
            self.assertEqual(list(second_root.iterdir()), [])

    def test_verified_index_evidence_binds_archive_resource_identity(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            destination, record = self.archive_resource(root)
            evidence = self.verified_resource_index(root, destination, record)
            prepared = []

            with self.assertRaisesRegex(ValueError, "different resource"):
                self.publish_with_evidence(
                    root,
                    [record],
                    evidence,
                    prepared,
                    resource_id="other-resource",
                )
            self.assertEqual(prepared, [])

    def test_verified_index_evidence_binds_exact_version_destination(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            destination, record = self.archive_resource(root)
            evidence = self.verified_resource_index(root, destination, record)
            prepared = []

            with self.assertRaisesRegex(ValueError, "different destination"):
                self.publish_with_evidence(
                    root,
                    [record],
                    evidence,
                    prepared,
                    destination_directory="other-repository",
                )
            self.assertEqual(prepared, [])
            self.assertFalse((root / "other-repository").exists())

    def test_verified_index_evidence_binds_identity_and_immutable_fields(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            destination, record = self.archive_resource(root)
            evidence = self.verified_resource_index(root, destination, record)
            prepared = []

            with self.assertRaisesRegex(ValueError, "different record domain"):
                self.publish_with_evidence(
                    root,
                    [record],
                    evidence,
                    prepared,
                    immutable_fields=("owner",),
                )
            self.assertEqual(prepared, [])

    def test_transaction_is_not_reexported_by_archive_primitives(self):
        self.assertFalse(hasattr(version_archive, "archive_if_changed"))

    def test_nominal_evidence_is_not_reexported_by_archive_primitives(self):
        for name in (
            "verified_record_collection_from_index_evidence",
            "verified_record_index_material",
            "verified_record_location_material",
            "verify_record_index_location_evidence",
            "verify_record_index_locations",
            "verify_record_location_evidence",
        ):
            with self.subTest(name=name):
                self.assertFalse(hasattr(version_archive, name))

    def test_record_rejects_noncanonical_numeric_versions_before_path_io(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _destination, record = self.archive_resource(root)
            for index, version in enumerate(("", "0", "-1", "+1", "01", "\u0661")):
                with self.subTest(version=version):
                    unavailable = root / f"must-not-be-created-{index}"
                    forged = json.loads(json.dumps(record))
                    forged["version"] = version
                    forged["archive"]["root"] = str(unavailable)
                    with self.assertRaisesRegex(
                        ValueError,
                        "positive canonical decimal integer",
                    ):
                        version_archive.verify_record(forged)
                    self.assertFalse(unavailable.exists())

    def test_record_location_rejects_self_consistent_dot_alias_without_side_effects(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            destination, record = self.archive_resource(root)
            alias_hop = destination.parent / "alias-hop"
            alias_hop.mkdir()
            alias = f"{alias_hop}/../{destination.name}"
            forged = self.rewrite_record_root(destination, record, alias)

            version_archive.verify_record(forged)
            with (
                mock.patch.object(version_archive, "verify_record") as verify_record,
                mock.patch.object(Path, "open") as path_open,
                self.assertRaisesRegex(ValueError, "canonical absolute path"),
            ):
                version_archive.verify_record_location(
                    forged,
                    managed_root=root,
                    expected_root=destination,
                )
            verify_record.assert_not_called()
            path_open.assert_not_called()
            self.assertEqual(list(alias_hop.iterdir()), [])

    def test_record_location_rejects_self_consistent_symlink_alias(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            destination, record = self.archive_resource(root)
            alias_parent = root / "resource-alias"
            alias_parent.symlink_to(destination.parent, target_is_directory=True)
            alias = str(alias_parent / destination.name)
            forged = self.rewrite_record_root(destination, record, alias)

            version_archive.verify_record(forged)
            with (
                mock.patch.object(version_archive, "verify_record") as verify_record,
                mock.patch.object(Path, "open") as path_open,
                self.assertRaisesRegex(ValueError, "may not traverse a symbolic link"),
            ):
                version_archive.verify_record_location(
                    forged,
                    managed_root=root,
                    expected_root=destination,
                )
            verify_record.assert_not_called()
            path_open.assert_not_called()

    def test_record_location_rejects_external_root_before_archive_read(self):
        with tempfile.TemporaryDirectory() as temporary, tempfile.TemporaryDirectory() as outside:
            root = Path(temporary)
            destination, record = self.archive_resource(root)
            forged = json.loads(json.dumps(record))
            forged["archive"]["root"] = str(Path(outside) / destination.name)

            with (
                mock.patch.object(version_archive, "verify_record") as verify_record,
                mock.patch.object(Path, "open") as path_open,
                self.assertRaisesRegex(ValueError, "outside its managed root"),
            ):
                version_archive.verify_record_location(
                    forged,
                    managed_root=root,
                    expected_root=destination,
                )
            verify_record.assert_not_called()
            path_open.assert_not_called()

    def test_builtin_module_archives_contain_only_their_own_implementation(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = {
                "controlRoot": str(root / "control"),
                "releaseRoot": str(root / "releases"),
                "liveRoot": str(root / "live"),
            }
            engine_database.prepare_database(config)
            builtin_resources.install(config)
            repositories = (
                (module_definitions.load_pipeline_definitions(config), "pipeline"),
                (module_definitions.load_analysis_definitions(config), "analysis"),
                (
                    module_definitions.load_environment_definitions(config),
                    "environment",
                ),
            )
            for records, repository in repositories:
                latest = {}
                for definition in records.values():
                    if not definition.get("builtin"):
                        continue
                    current = latest.get(definition["moduleId"])
                    if current is None or int(definition["version"]) > int(current["version"]):
                        latest[definition["moduleId"]] = definition
                for module_id, definition in latest.items():
                    archive = Path(definition["archive"]["root"])
                    paths = {
                        path.relative_to(archive).as_posix()
                        for path in archive.rglob("*.py")
                    }
                    own = (
                        f"builtin_implementations/{repository}/"
                        f"{module_id.replace('-', '_')}.py"
                    )
                    self.assertIn(own, paths)
                    for other_id in set(latest) - {module_id}:
                        other = (
                            f"builtin_implementations/{repository}/"
                            f"{other_id.replace('-', '_')}.py"
                        )
                        self.assertNotIn(other, paths)

    def test_state_commit_failure_removes_the_sealed_directory(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            destination = root / "resource" / "1"

            def prepare(staging, _version, _destination):
                (staging / "payload.txt").write_text("content", encoding="utf-8")
                return {"value": "content"}, None

            with self.assertRaisesRegex(RuntimeError, "state commit failed"):
                version_transaction.archive_if_changed(
                    records=[],
                    identity_key="resourceId",
                    identity="resource",
                    resource_type="test-resource",
                    resource_id="resource",
                    managed_root=root,
                    destination_for_version=lambda _version: destination,
                    prepare_staging=prepare,
                    create_record=lambda _version, _context: {"resourceId": "resource"},
                    record_fields={"resourceId"},
                    write_record=lambda _staging, _record, _context: None,
                    commit_record=lambda _record, _context: (_ for _ in ()).throw(
                        RuntimeError("state commit failed")
                    ),
                    read_committed_record=lambda _record, _context: None,
                    immutable_fields=(),
                )
            self.assertFalse(destination.exists())

    def test_validation_error_survives_staging_discard_failure(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            primary = RuntimeError("draft validation failed")
            cleanup = OSError("staging discard failed")

            with (
                mock.patch.object(
                    version_transaction,
                    "discard_archive",
                    side_effect=cleanup,
                ),
                self.assertRaises(RuntimeError) as raised,
            ):
                version_transaction.archive_if_changed(
                    records=[],
                    identity_key="resourceId",
                    identity="resource",
                    resource_type="test-resource",
                    resource_id="resource",
                    managed_root=root,
                    destination_for_version=lambda version: (
                        root / "resource" / version
                    ),
                    prepare_staging=lambda *_args: (_ for _ in ()).throw(primary),
                    create_record=lambda _version, _context: {
                        "resourceId": "resource"
                    },
                    record_fields={"resourceId"},
                    write_record=lambda *_args: None,
                    commit_record=lambda *_args: None,
                    read_committed_record=lambda *_args: None,
                    immutable_fields=(),
                )

            self.assertIs(raised.exception, primary)
            self.assertIs(raised.exception.__context__, cleanup)

    def test_commit_error_survives_destination_discard_failure(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            primary = RuntimeError("index commit failed")
            cleanup = OSError("destination discard failed")

            def prepare(staging, _version, _destination):
                (staging / "payload.txt").write_text(
                    "content", encoding="utf-8"
                )
                return {"value": "content"}, None

            with (
                mock.patch.object(
                    version_transaction,
                    "discard_archive",
                    side_effect=cleanup,
                ),
                self.assertRaises(RuntimeError) as raised,
            ):
                version_transaction.archive_if_changed(
                    records=[],
                    identity_key="resourceId",
                    identity="resource",
                    resource_type="test-resource",
                    resource_id="resource",
                    managed_root=root,
                    destination_for_version=lambda version: (
                        root / "resource" / version
                    ),
                    prepare_staging=prepare,
                    create_record=lambda _version, _context: {
                        "resourceId": "resource"
                    },
                    record_fields={"resourceId"},
                    write_record=lambda *_args: None,
                    commit_record=lambda *_args: (_ for _ in ()).throw(primary),
                    read_committed_record=lambda *_args: None,
                    immutable_fields=(),
                )

            self.assertIs(raised.exception, primary)
            self.assertIs(raised.exception.__context__, cleanup)
            self.assertTrue((root / "resource" / "1").is_dir())

    def test_base_exception_commit_failure_also_removes_the_sealed_directory(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            destination = root / "resource" / "1"

            def prepare(staging, _version, _destination):
                (staging / "payload.txt").write_text("content", encoding="utf-8")
                return {"value": "content"}, None

            with self.assertRaises(SystemExit):
                version_transaction.archive_if_changed(
                    records=[],
                    identity_key="resourceId",
                    identity="resource",
                    resource_type="test-resource",
                    resource_id="resource",
                    managed_root=root,
                    destination_for_version=lambda _version: destination,
                    prepare_staging=prepare,
                    create_record=lambda _version, _context: {"resourceId": "resource"},
                    record_fields={"resourceId"},
                    write_record=lambda _staging, _record, _context: None,
                    commit_record=lambda _record, _context: (_ for _ in ()).throw(SystemExit()),
                    read_committed_record=lambda _record, _context: None,
                    immutable_fields=(),
                )
            self.assertFalse(destination.exists())

    def test_exception_after_authoritative_commit_keeps_the_archive_and_succeeds(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            destination = root / "resource" / "1"
            state = {}

            def prepare(staging, _version, _destination):
                (staging / "payload.txt").write_text("content", encoding="utf-8")
                return {"value": "content"}, None

            def commit_then_raise(record, _context):
                state["record"] = json.loads(json.dumps(record))
                raise RuntimeError("exception after commit")

            result = version_transaction.archive_if_changed(
                records=[],
                identity_key="resourceId",
                identity="resource",
                resource_type="test-resource",
                resource_id="resource",
                managed_root=root,
                destination_for_version=lambda _version: destination,
                prepare_staging=prepare,
                create_record=lambda _version, _context: {"resourceId": "resource"},
                record_fields={"resourceId"},
                write_record=lambda _staging, _record, _context: None,
                commit_record=commit_then_raise,
                read_committed_record=lambda _record, _context: state.get("record"),
                immutable_fields=(),
            )

            self.assertTrue(destination.is_dir())
            self.assertEqual(result["record"], state["record"])
            version_archive.verify_record(result["record"])

    def test_retry_recovers_a_sealed_unindexed_version_before_publishing_a_new_draft(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            state = {}

            def publish(value, *, builtin=True):
                records = list(state.values())

                def prepare(staging, _version, _destination):
                    (staging / "payload.txt").write_text(value, encoding="utf-8")
                    return {"value": value, "builtin": builtin}, None

                def commit(record, _context):
                    state[record["version"]] = json.loads(json.dumps(record))

                return version_transaction.archive_if_changed(
                    records=records,
                    identity_key="resourceId",
                    identity="resource",
                    resource_type="test-resource",
                    resource_id="resource",
                    managed_root=root,
                    destination_for_version=lambda version: root / "resource" / version,
                    prepare_staging=prepare,
                    create_record=lambda _version, _context: {
                        "resourceId": "resource",
                        "value": value,
                        "builtin": builtin,
                    },
                    record_fields={"resourceId", "value", "builtin"},
                    write_record=lambda _staging, _record, _context: None,
                    commit_record=commit,
                    read_committed_record=lambda record, _context: state.get(
                        record["version"]
                    ),
                    immutable_fields={"builtin"},
                )

            first = publish("first")
            first_record = json.loads(json.dumps(first["record"]))
            state.clear()  # Models process loss after rename and before index commit.

            second = publish("second")

            self.assertEqual(second["record"]["version"], "2")
            self.assertEqual(set(state), {"1", "2"})
            self.assertEqual(state["1"], first_record)
            self.assertEqual(state["2"], second["record"])
            version_archive.verify_record(state["1"])
            version_archive.verify_record(state["2"])

            state.clear()
            with self.assertRaisesRegex(ValueError, "changes immutable field 'builtin'"):
                publish("third", builtin=False)
            self.assertEqual(set(state), {"1", "2"})
            self.assertFalse((root / "resource" / "3").exists())

    def test_retry_preserves_and_rejects_a_corrupt_unindexed_archive(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            state = {}

            def publish(value):
                def prepare(staging, _version, _destination):
                    (staging / "payload.txt").write_text(value, encoding="utf-8")
                    return {"value": value}, None

                return version_transaction.archive_if_changed(
                    records=list(state.values()),
                    identity_key="resourceId",
                    identity="resource",
                    resource_type="test-resource",
                    resource_id="resource",
                    managed_root=root,
                    destination_for_version=lambda version: root / "resource" / version,
                    prepare_staging=prepare,
                    create_record=lambda _version, _context: {"resourceId": "resource"},
                    record_fields={"resourceId"},
                    write_record=lambda _staging, _record, _context: None,
                    commit_record=lambda record, _context: state.update({
                        record["version"]: json.loads(json.dumps(record))
                    }),
                    read_committed_record=lambda record, _context: state.get(
                        record["version"]
                    ),
                    immutable_fields=(),
                )

            publish("first")
            state.clear()
            payload = root / "resource" / "1" / "payload.txt"
            payload.chmod(0o644)
            payload.write_text("corrupt", encoding="utf-8")
            payload.chmod(0o444)

            with self.assertRaisesRegex(ValueError, "verification failed"):
                publish("second")
            self.assertEqual(state, {})
            self.assertTrue((root / "resource" / "1").is_dir())
            self.assertFalse((root / "resource" / "2").exists())

    def test_archive_rejects_special_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            destination = root / "resource" / "1"

            def prepare(staging, _version, _destination):
                os.mkfifo(staging / "pipe")
                return {"value": "content"}, None

            with self.assertRaisesRegex(ValueError, "only files and directories"):
                version_transaction.archive_if_changed(
                    records=[],
                    identity_key="resourceId",
                    identity="resource",
                    resource_type="test-resource",
                    resource_id="resource",
                    managed_root=root,
                    destination_for_version=lambda _version: destination,
                    prepare_staging=prepare,
                    create_record=lambda _version, _context: {"resourceId": "resource"},
                    record_fields={"resourceId"},
                    write_record=lambda _staging, _record, _context: None,
                    commit_record=lambda _record, _context: None,
                    read_committed_record=lambda _record, _context: None,
                    immutable_fields=(),
                )
            self.assertFalse(destination.exists())

    def test_archive_rejects_a_symbolic_link_in_its_managed_subtree(self):
        with tempfile.TemporaryDirectory() as temporary, tempfile.TemporaryDirectory() as outside:
            root = Path(temporary)
            (root / "resource").symlink_to(Path(outside), target_is_directory=True)
            with self.assertRaisesRegex(ValueError, "may not traverse a symbolic link"):
                self.archive_resource(root)

    def test_manifest_rewrite_cannot_replace_the_trusted_manifest_digest(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            destination = root / "resource" / "1"
            state = {}

            def prepare(staging, _version, _destination):
                (staging / "payload.txt").write_text("content", encoding="utf-8")
                return {"value": "content"}, None

            result = version_transaction.archive_if_changed(
                records=[],
                identity_key="resourceId",
                identity="resource",
                resource_type="test-resource",
                resource_id="resource",
                managed_root=root,
                destination_for_version=lambda _version: destination,
                prepare_staging=prepare,
                create_record=lambda _version, _context: {"resourceId": "resource"},
                record_fields={"resourceId"},
                write_record=lambda _staging, _record, _context: None,
                commit_record=lambda record, _context: state.update(record),
                read_committed_record=lambda _record, _context: dict(state) if state else None,
                immutable_fields=(),
            )
            record = result["record"]
            payload = destination / "payload.txt"
            payload.chmod(0o644)
            payload.write_text("forged", encoding="utf-8")
            manifest_path = destination / version_archive.MANIFEST_NAME
            manifest_path.chmod(0o644)
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["files"] = version_archive.file_manifest(destination)
            manifest["manifestDigest"] = version_archive.content_digest({
                key: value for key, value in manifest.items() if key != "manifestDigest"
            })
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            payload.chmod(0o444)
            manifest_path.chmod(0o444)
            with self.assertRaisesRegex(ValueError, "manifestDigest mismatch"):
                version_archive.verify_record(record)

    def test_unknown_manifest_field_is_rejected_even_with_a_recomputed_digest(self):
        with tempfile.TemporaryDirectory() as temporary:
            destination, record = self.archive_resource(Path(temporary))
            manifest_path = destination / version_archive.MANIFEST_NAME
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["unexpectedField"] = "forged"
            manifest["manifestDigest"] = version_archive.content_digest({
                key: value for key, value in manifest.items() if key != "manifestDigest"
            })
            self.rewrite_json(manifest_path, manifest)
            record["archive"]["manifestDigest"] = manifest["manifestDigest"]

            with self.assertRaisesRegex(ValueError, "manifest has an invalid schema"):
                version_archive.verify_record(record)

    def test_duplicate_manifest_key_is_rejected_by_the_archive_boundary(self):
        with tempfile.TemporaryDirectory() as temporary:
            destination, record = self.archive_resource(Path(temporary))
            manifest_path = destination / version_archive.MANIFEST_NAME
            manifest_path.chmod(0o644)
            raw = manifest_path.read_text(encoding="utf-8")
            raw = raw.replace(
                '"schemaVersion": 1,',
                '"schemaVersion": 1, "schemaVersion": 1,',
                1,
            )
            manifest_path.write_text(raw, encoding="utf-8")
            manifest_path.chmod(0o444)
            with self.assertRaisesRegex(ValueError, "Duplicate JSON object key"):
                version_archive.verify_record(record)

    def test_nested_file_named_like_the_root_manifest_is_integrity_checked(self):
        with tempfile.TemporaryDirectory() as temporary:
            destination, record = self.archive_resource(
                Path(temporary), nested_manifest=True
            )
            root_manifest = json.loads(
                (destination / version_archive.MANIFEST_NAME).read_text(encoding="utf-8")
            )
            self.assertIn(
                f"nested/{version_archive.MANIFEST_NAME}",
                {item["path"] for item in root_manifest["files"]},
            )

            nested_manifest = destination / "nested" / version_archive.MANIFEST_NAME
            nested_manifest.chmod(0o644)
            nested_manifest.write_text("forged nested content", encoding="utf-8")
            nested_manifest.chmod(0o444)
            with self.assertRaisesRegex(ValueError, "verification failed"):
                version_archive.verify_record(record)

    def test_archive_transaction_rejects_missing_or_wrong_record_identity(self):
        cases = (
            (
                "missing record",
                lambda _version, _context: None,
                "create_record must return an object",
            ),
            (
                "wrong identity",
                lambda _version, _context: {"resourceId": "different"},
                "resourceId does not match identity",
            ),
        )
        for label, create_record, error in cases:
            with self.subTest(label=label), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                with self.assertRaisesRegex(ValueError, error):
                    self.archive_resource(root, create_record=create_record)
                self.assertFalse((root / "resource" / "1").exists())


if __name__ == "__main__":
    unittest.main()
