import copy
import json
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from engine.service import jupyter_workspaces
from engine.control import database as engine_database
from engine.contracts import sampler as sampler_contracts
from engine.repository import samplers
from engine.repository import workspace_paths
from engine.service import sampler_workspaces
from engine.archive.sampler import sampler_runtime_bundle
from engine.authority.sampler import verify_sampler_runtime_bundle
from strategy_devkit import sampler_sdk


PROJECT_ROOT = Path(__file__).resolve().parents[3]


def row_map_draft(sampler_id="editable-row-map"):
    config = {
        "mapping": {"price.close": "close"},
        "includeUnmappedFields": False,
        "unmappedPrefix": "dataset.",
    }
    return {
        "samplerId": sampler_id,
        "name": "Test row map",
        "type": "row-map",
        "config": config,
        "parameterSchema": sampler_contracts.infer_sampler_parameter_schema(config),
        "outputSchema": {"price.close": {"type": "number"}},
        "source": "",
        "entryPoint": "",
    }


class SamplerLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.config = {
            "controlRoot": str(root / "control"),
            "releaseRoot": str(root / "releases"),
        }
        engine_database.prepare_database(self.config)
        self.definition = samplers.save_sampler(self.config, row_map_draft())
        self.default_definition = samplers.save_sampler(
            self.config, row_map_draft("engine-owned-test-row-map"), engine_owned=True
        )

    def tearDown(self):
        self.temp.cleanup()

    def test_edit_workspace_is_an_isolated_copy_and_preserves_edits(self):
        workspace = sampler_workspaces.open_edit_workspace(
            self.config, self.definition["samplerId"], self.definition["version"]
        )
        sampler_path = Path(workspace["workspacePath"]) / "sampler.json"
        draft = json.loads(sampler_path.read_text())
        self.assertEqual(draft["samplerId"], "editable-row-map")
        self.assertNotIn("version", draft)
        self.assertNotIn("archive", draft)
        self.assertTrue((Path(workspace["workspacePath"]) / ".sampler-workspace.json").is_file())
        sampler_path.write_text('{"edited": true}\n', encoding="utf-8")

        reopened = sampler_workspaces.open_edit_workspace(
            self.config, self.definition["samplerId"], self.definition["version"]
        )
        self.assertFalse(reopened["created"])
        self.assertEqual(json.loads(sampler_path.read_text()), {"edited": True})

    def test_workspace_ids_do_not_alias_after_normalization_or_truncation(self):
        self.assertNotEqual(
            sampler_workspaces.workspace_id("foo.bar", "1"),
            sampler_workspaces.workspace_id("foo+bar", "1"),
        )
        shared_prefix = "same-prefix-" + "x" * 200
        self.assertNotEqual(
            sampler_workspaces.workspace_id(shared_prefix + "a", "1"),
            sampler_workspaces.workspace_id(shared_prefix + "b", "1"),
        )

    def test_existing_workspace_with_mismatched_marker_is_rejected_without_repair(self):
        workspace = sampler_workspaces.open_edit_workspace(
            self.config, self.definition["samplerId"], self.definition["version"]
        )
        root = Path(workspace["workspacePath"])
        marker_path = root / ".sampler-workspace.json"
        sampler_path = root / "sampler.json"
        marker_path.write_text(json.dumps({
            "schemaVersion": 1,
            "workspaceId": workspace["workspaceId"],
            "sourceSamplerKey": "different::1",
            "createdAt": "2026-01-01T00:00:00Z",
        }) + "\n", encoding="utf-8")
        marker_before = marker_path.read_bytes()
        sampler_before = sampler_path.read_bytes()

        with self.assertRaisesRegex(ValueError, "identity metadata is invalid"):
            sampler_workspaces.open_edit_workspace(
                self.config, self.definition["samplerId"], self.definition["version"]
            )

        self.assertEqual(marker_path.read_bytes(), marker_before)
        self.assertEqual(sampler_path.read_bytes(), sampler_before)

    def test_sampler_scalar_types_are_rejected_before_repository_side_effects(self):
        invalid_values = (
            ("name", 123, "name must be a non-empty string"),
            ("name", "", "name must be a non-empty string"),
            ("type", 123, "type must be a string"),
            ("source", 123, "source must be a string"),
            ("entryPoint", 123, "entryPoint must be a string"),
            ("samplerId", 123, "samplerId must be a string"),
        )
        baseline = len(samplers.list_samplers(self.config))
        for field, value, message in invalid_values:
            draft = row_map_draft(f"invalid-scalar-{field}")
            draft[field] = value
            with self.subTest(field=field, value=value), self.assertRaisesRegex(
                ValueError, message
            ):
                samplers.save_sampler(self.config, draft)
            self.assertEqual(len(samplers.list_samplers(self.config)), baseline)

    def test_concurrent_identical_saves_share_one_archive_and_index_version(self):
        barrier = threading.Barrier(8)
        definitions = []
        errors = []

        def save():
            try:
                barrier.wait()
                definitions.append(
                    samplers.save_sampler(
                        self.config,
                        row_map_draft("concurrent-identical-sampler"),
                    )
                )
            except BaseException as error:
                errors.append(error)

        threads = [threading.Thread(target=save) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=20)
        self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertEqual(errors, [])
        self.assertEqual({item["version"] for item in definitions}, {"1"})
        matching = [
            item
            for item in samplers.list_samplers(self.config)
            if item["samplerId"] == "concurrent-identical-sampler"
        ]
        self.assertEqual(len(matching), 1)

    def test_concurrent_distinct_saves_serialize_version_allocation_and_index(self):
        sampler_id = "concurrent-distinct-sampler"
        first = samplers.save_sampler(self.config, row_map_draft(sampler_id))
        barrier = threading.Barrier(2)
        definitions = []
        errors = []

        def save(name):
            draft = row_map_draft(sampler_id)
            draft["name"] = name
            try:
                barrier.wait()
                definitions.append(samplers.save_sampler(self.config, draft))
            except BaseException as error:
                errors.append(error)

        threads = [
            threading.Thread(target=save, args=(name,))
            for name in ("Concurrent A", "Concurrent B")
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=20)
        self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertEqual(errors, [])
        self.assertEqual({item["version"] for item in definitions}, {"2", "3"})
        self.assertEqual({item["name"] for item in definitions}, {"Concurrent A", "Concurrent B"})
        self.assertEqual(len({item["contentDigest"] for item in definitions}), 2)

        history = [
            item
            for item in samplers.list_samplers(self.config)
            if item["samplerId"] == sampler_id
        ]
        self.assertEqual([item["version"] for item in history], ["1", "2", "3"])
        self.assertEqual(history[0], first)
        self.assertEqual(
            sorted(path.name for path in (
                Path(self.config["releaseRoot"]) / "_samplers" / sampler_id
            ).iterdir()),
            ["1", "2", "3"],
        )

    def test_post_commit_acknowledgement_and_close_errors_reconcile_to_success(self):
        original_connect = engine_database.connect_database
        for failure_kind in ("commit", "close"):
            state = {"raised": False}

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
                    if (
                        failure_kind == "commit"
                        and self.publication
                        and not state["raised"]
                    ):
                        state["raised"] = True
                        raise OSError("Sampler commit acknowledgement lost")

                def close(self):
                    self.connection.close()
                    if (
                        failure_kind == "close"
                        and self.publication
                        and not state["raised"]
                    ):
                        state["raised"] = True
                        raise OSError("Sampler post-commit close failed")

            sampler_id = f"post-commit-{failure_kind}"
            with self.subTest(failure_kind=failure_kind), mock.patch.object(
                engine_database,
                "connect_database",
                side_effect=lambda config: ConnectionProxy(original_connect(config)),
            ):
                published = samplers.save_sampler(
                    self.config,
                    row_map_draft(sampler_id),
                )
            self.assertTrue(state["raised"])
            self.assertEqual(published["version"], "1")
            self.assertEqual(samplers.get_sampler(self.config, sampler_id), published)

    def test_precommit_primary_survives_rollback_and_close_failures(self):
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
                normalized = sql.strip().upper()
                if normalized == "BEGIN IMMEDIATE":
                    self.publication = True
                if self.publication and normalized.startswith(
                    "INSERT INTO SAMPLER_DEFINITIONS"
                ):
                    raise OSError("Sampler index insert failed")
                return self.connection.execute(sql, parameters)

            def rollback(self):
                self.connection.rollback()
                if self.publication:
                    raise RuntimeError("Sampler rollback cleanup failed")

            def close(self):
                self.connection.close()
                if self.publication:
                    raise RuntimeError("Sampler close cleanup failed")

        sampler_id = "precommit-primary"
        with mock.patch.object(
            engine_database,
            "connect_database",
            side_effect=lambda config: ConnectionProxy(original_connect(config)),
        ), self.assertRaisesRegex(OSError, "Sampler index insert failed") as raised:
            samplers.save_sampler(self.config, row_map_draft(sampler_id))
        self.assertIsInstance(raised.exception.__context__, RuntimeError)
        self.assertIn("rollback cleanup failed", str(raised.exception.__context__))
        with self.assertRaisesRegex(ValueError, "Unknown Sampler"):
            samplers.get_sampler(self.config, sampler_id)
        self.assertFalse(
            (Path(self.config["releaseRoot"]) / "_samplers" / sampler_id / "1").exists()
        )

    def test_postcommit_evidence_failure_keeps_archive_for_exact_retry(self):
        original_connect = engine_database.connect_database
        state = {"committed": False}

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
                    state["committed"] = True
                    raise OSError("Sampler commit acknowledgement lost")

        def connect(config):
            if state["committed"]:
                raise RuntimeError("Sampler commit evidence is unavailable")
            return ConnectionProxy(original_connect(config))

        sampler_id = "postcommit-evidence-failure"
        with mock.patch.object(
            engine_database,
            "connect_database",
            side_effect=connect,
        ), self.assertRaisesRegex(
            OSError, "Sampler commit acknowledgement lost"
        ):
            samplers.save_sampler(self.config, row_map_draft(sampler_id))
        destination = (
            Path(self.config["releaseRoot"]) / "_samplers" / sampler_id / "1"
        )
        self.assertTrue(destination.is_dir())

        recovered = samplers.save_sampler(self.config, row_map_draft(sampler_id))
        self.assertEqual(recovered["version"], "1")
        self.assertEqual(
            len([
                item
                for item in samplers.list_samplers(self.config)
                if item["samplerId"] == sampler_id
            ]),
            1,
        )

    def test_sampler_row_gate_rejects_noncanonical_or_inconsistent_index_values(self):
        with engine_database.connect_database(self.config) as connection:
            base = dict(connection.execute(
                "SELECT * FROM sampler_definitions "
                "WHERE sampler_id = ? AND version = ?",
                (self.definition["samplerId"], self.definition["version"]),
            ).fetchone())
        aliases = (
            (
                "archive_root",
                str(Path(base["archive_root"]) / ".." / base["version"]),
                "exact canonical repository path",
            ),
            ("sampler_id", "a/b", "filesystem-safe path segment"),
            ("version", "01", "canonical positive integer"),
            ("content_digest", "sha256:" + "1" * 64, "index does not match"),
            (
                "archive_manifest_digest",
                "sha256:" + "1" * 64,
                "manifestDigest mismatch|manifest digest failed",
            ),
        )
        for field, value, message in aliases:
            row = {**base, field: value}
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, message):
                samplers._sampler_from_row(self.config, row)

    def test_workspace_repository_and_target_symlinks_have_zero_external_writes(self):
        release_root = Path(self.config["releaseRoot"])
        external_root = Path(self.temp.name) / "external-sampler-root"
        external_root.mkdir()
        sentinel = external_root / "sentinel.txt"
        sentinel.write_text("unchanged", encoding="utf-8")
        workspace_root = release_root / "_sampler_workspaces"
        workspace_root.symlink_to(external_root, target_is_directory=True)

        with self.assertRaisesRegex(ValueError, "symbolic link"):
            sampler_workspaces.open_edit_workspace(
                self.config, self.definition["samplerId"], self.definition["version"]
            )
        self.assertEqual(sentinel.read_text(encoding="utf-8"), "unchanged")
        self.assertEqual(sorted(path.name for path in external_root.iterdir()), ["sentinel.txt"])

        workspace_root.unlink()
        workspace = sampler_workspaces.open_edit_workspace(
            self.config, self.definition["samplerId"], self.definition["version"]
        )
        target = Path(workspace["workspacePath"])
        external_target = Path(self.temp.name) / "external-sampler-target"
        target.rename(external_target)
        (external_target / "SAMPLER_VERSION.md").unlink()
        target.symlink_to(external_target, target_is_directory=True)
        before = {
            path.relative_to(external_target).as_posix(): path.read_bytes()
            for path in external_target.rglob("*")
            if path.is_file()
        }
        for operation in (
            lambda: sampler_workspaces.open_edit_workspace(
                self.config, self.definition["samplerId"], self.definition["version"]
            ),
            lambda: sampler_workspaces.publish_edit_workspace(
                self.config, self.definition["samplerId"], self.definition["version"]
            ),
            lambda: jupyter_workspaces.workspace_host_path(
                self.config, workspace["workspaceId"], "sampler"
            ),
        ):
            with self.assertRaisesRegex(ValueError, "symbolic link"):
                operation()
        after = {
            path.relative_to(external_target).as_posix(): path.read_bytes()
            for path in external_target.rglob("*")
            if path.is_file()
        }
        self.assertEqual(after, before)
        self.assertFalse((external_target / "SAMPLER_VERSION.md").exists())

    def test_workspace_marker_and_sampler_files_reject_symlinks_everywhere(self):
        workspace = sampler_workspaces.open_edit_workspace(
            self.config, self.definition["samplerId"], self.definition["version"]
        )
        root = Path(workspace["workspacePath"])
        for name in (".sampler-workspace.json", "sampler.json"):
            path = root / name
            original = path.read_bytes()
            external = Path(self.temp.name) / f"external-{name.lstrip('.')}"
            external.write_bytes(original)
            path.unlink()
            path.symlink_to(external)
            for operation in (
                lambda: sampler_workspaces.open_edit_workspace(
                    self.config,
                    self.definition["samplerId"],
                    self.definition["version"],
                ),
                lambda: sampler_workspaces.publish_edit_workspace(
                    self.config,
                    self.definition["samplerId"],
                    self.definition["version"],
                ),
                lambda: jupyter_workspaces.workspace_host_path(
                    self.config, workspace["workspaceId"], "sampler"
                ),
            ):
                with self.subTest(name=name, operation=operation), self.assertRaises(
                    ValueError
                ):
                    operation()
                self.assertEqual(external.read_bytes(), original)
            path.unlink()
            path.write_bytes(original)

    def test_python_workspace_source_symlink_is_rejected_without_external_read(self):
        definition = samplers.save_sampler(self.config, {
            "samplerId": "python-workspace-symlink",
            "name": "Python Workspace Symlink",
            "type": "python-script",
            "config": {},
            "parameterSchema": sampler_contracts.infer_sampler_parameter_schema({}),
            "outputSchema": {},
            "source": (
                "def emit_samples(dataset, parameters):\n"
                "    return ()\n"
            ),
            "entryPoint": "emit_samples",
        })
        workspace = sampler_workspaces.open_edit_workspace(
            self.config, definition["samplerId"], definition["version"]
        )
        source_path = Path(workspace["workspacePath"]) / "sampler.py"
        external = Path(self.temp.name) / "external-sampler.py"
        external.write_text("raise RuntimeError('must not be read')\n", encoding="utf-8")
        original = external.read_bytes()
        source_path.unlink()
        source_path.symlink_to(external)

        for operation in (
            lambda: sampler_workspaces.open_edit_workspace(
                self.config, definition["samplerId"], definition["version"]
            ),
            lambda: sampler_workspaces.publish_edit_workspace(
                self.config, definition["samplerId"], definition["version"]
            ),
            lambda: jupyter_workspaces.workspace_host_path(
                self.config, workspace["workspaceId"], "sampler"
            ),
        ):
            with self.assertRaisesRegex(ValueError, "symbolic link"):
                operation()
            self.assertEqual(external.read_bytes(), original)

    def test_workspace_release_ancestor_symlink_is_rejected_before_mkdir(self):
        external = Path(self.temp.name) / "external-release-parent"
        external.mkdir()
        sentinel = external / "sentinel.txt"
        sentinel.write_text("unchanged", encoding="utf-8")
        alias = Path(self.temp.name) / "release-alias"
        alias.symlink_to(external, target_is_directory=True)
        escaped_config = {
            **self.config,
            "releaseRoot": str(alias / "nested-release"),
        }
        with self.assertRaisesRegex(ValueError, "symbolic link"):
            workspace_paths.sampler_workspace_root(escaped_config)
        self.assertEqual(sentinel.read_text(encoding="utf-8"), "unchanged")
        self.assertFalse((external / "nested-release").exists())

    def test_builtin_sampler_is_read_only(self):
        with self.assertRaisesRegex(ValueError, "read-only"):
            sampler_workspaces.open_edit_workspace(
                self.config,
                self.default_definition["samplerId"],
                self.default_definition["version"],
            )

    def test_sampler_identity_ownership_is_constant_across_versions(self):
        with self.assertRaisesRegex(ValueError, "ownership cannot change"):
            samplers.save_sampler(
                self.config,
                row_map_draft(self.definition["samplerId"]),
                engine_owned=True,
            )
        with self.assertRaisesRegex(ValueError, "ownership cannot change"):
            samplers.save_sampler(
                self.config,
                row_map_draft(self.default_definition["samplerId"]),
            )
        for invalid in (1, "true"):
            with self.subTest(engine_owned=invalid), self.assertRaisesRegex(
                ValueError, "must be a boolean"
            ):
                samplers.save_sampler(
                    self.config,
                    row_map_draft(f"invalid-owner-{invalid}"),
                    engine_owned=invalid,
                )

    def test_sampler_lookup_requires_explicit_identity(self):
        with self.assertRaisesRegex(ValueError, "Sampler ID is required"):
            samplers.get_sampler(self.config, "")

    def test_sampler_version_archives_its_runtime_bundle(self):
        runtime = self.definition["runtime"]
        self.assertEqual(runtime["protocol"], "row-map-in-process-v1")
        root = Path(self.definition["archive"]["root"])
        self.assertEqual(
            [item["path"] for item in runtime["assets"]],
            ["runtime/row_map_sampler_runtime.py"],
        )
        for item in runtime["assets"]:
            asset = root / item["path"]
            self.assertTrue(asset.is_file())
            self.assertFalse(asset.stat().st_mode & 0o222)

    def test_sampler_runtime_sources_have_explicit_package_owners(self):
        row_runtime, row_sources = sampler_runtime_bundle("row-map")
        python_runtime, python_sources = sampler_runtime_bundle("python-script")
        self.assertEqual(row_runtime["protocol"], "row-map-in-process-v1")
        self.assertEqual(python_runtime["protocol"], "python-script-jsonl-v1")
        self.assertEqual(
            row_sources,
            {
                "row_map_sampler_runtime.py": (
                    PROJECT_ROOT
                    / "engine/runtime/sampler_assets/row_map_sampler_runtime.py"
                )
            },
        )
        self.assertEqual(
            python_sources,
            {
                "sampler_worker.py": (
                    PROJECT_ROOT / "engine/runtime/sampler_assets/sampler_worker.py"
                ),
                "sampler_sdk.py": PROJECT_ROOT / "strategy_devkit/sampler_sdk.py",
            },
        )

    def test_sampler_runtime_manifest_rejects_missing_and_duplicate_authorities(self):
        missing = copy.deepcopy(self.definition)
        del missing["runtime"]["schemaVersion"]
        with self.assertRaisesRegex(ValueError, "missing required field.*schemaVersion"):
            verify_sampler_runtime_bundle(missing)

        duplicate = copy.deepcopy(self.definition)
        duplicate["runtime"]["assets"].append(
            copy.deepcopy(duplicate["runtime"]["assets"][0])
        )
        with self.assertRaisesRegex(ValueError, "duplicate asset"):
            verify_sampler_runtime_bundle(duplicate)

    def test_python_sampler_worker_rejects_extra_sample_fields_without_fallback(self):
        request = {
            "source": (
                "def emit_samples(dataset, parameters):\n"
                "    yield {\"decisionTime\": \"2026-01-01T00:00:00Z\", "
                "\"data\": {}, \"unexpected\": True}\n"
            ),
            "entryPoint": "emit_samples",
            "dataset": {
                "datasetId": "dataset",
                "datasetVersionId": "version",
                "storageType": "directory",
                "storagePath": "/dataset",
                "root": "/dataset",
                "contentHash": "sha256:" + "0" * 64,
                "capabilities": {},
            },
            "parameters": {},
        }
        runtime, sources = sampler_runtime_bundle("python-script")
        self.assertEqual(
            list(sources),
            ["sampler_worker.py", "sampler_sdk.py"],
        )
        with tempfile.TemporaryDirectory() as temporary:
            runtime_root = Path(temporary) / "runtime"
            runtime_root.mkdir()
            for name, source in sources.items():
                shutil.copy2(source, runtime_root / name)
            worker = runtime_root / runtime["entryAsset"]
            completed = subprocess.run(
                [sys.executable, str(worker)],
                input=json.dumps(request) + "\n",
                text=True,
                capture_output=True,
                check=False,
            )
        self.assertNotEqual(completed.returncode, 0)
        message = json.loads(completed.stdout.splitlines()[-1])
        self.assertEqual(message["type"], "error")
        self.assertIn("unsupported field(s): unexpected", message["error"])

    def test_python_sampler_sdk_rejects_type_coercion(self):
        descriptor = {
            "datasetId": "dataset",
            "datasetVersionId": "version",
            "storageType": "directory",
            "storagePath": "/dataset",
            "root": "/dataset",
            "contentHash": "sha256:" + "0" * 64,
            "capabilities": {},
        }
        invalid = dict(descriptor)
        invalid["datasetId"] = 7
        with self.assertRaisesRegex(ValueError, "datasetId.*string"):
            sampler_sdk.Dataset(invalid)
        with self.assertRaisesRegex(ValueError, "decision_time.*string"):
            sampler_sdk.sample(7, {})
        with self.assertRaisesRegex(ValueError, "data must be an object"):
            sampler_sdk.sample("2026-01-01T00:00:00Z", [])

    def test_workspace_publish_creates_only_changed_sampler_version(self):
        workspace = sampler_workspaces.open_edit_workspace(
            self.config, self.definition["samplerId"], self.definition["version"]
        )
        sampler_path = Path(workspace["workspacePath"]) / "sampler.json"
        draft = json.loads(sampler_path.read_text(encoding="utf-8"))
        draft["name"] = "Published from Workspace"
        sampler_path.write_text(json.dumps(draft, indent=2) + "\n", encoding="utf-8")

        published = sampler_workspaces.publish_edit_workspace(
            self.config, self.definition["samplerId"], self.definition["version"]
        )
        self.assertFalse(published["unchanged"])
        self.assertEqual(published["sampler"]["version"], "2")
        repeated = sampler_workspaces.publish_edit_workspace(
            self.config, self.definition["samplerId"], self.definition["version"]
        )
        self.assertTrue(repeated["unchanged"])
        self.assertEqual(repeated["sampler"]["version"], "2")

    def test_workspace_publish_requires_jupyter_termination_proof(self):
        sampler_workspaces.open_edit_workspace(
            self.config, self.definition["samplerId"], self.definition["version"]
        )
        with (
            mock.patch.object(
                jupyter_workspaces,
                "stop_workspace_server",
                side_effect=RuntimeError("termination is unproven"),
            ),
            mock.patch.object(samplers, "save_sampler") as save_sampler,
        ):
            with self.assertRaisesRegex(RuntimeError, "unproven"):
                sampler_workspaces.publish_edit_workspace(
                    self.config,
                    self.definition["samplerId"],
                    self.definition["version"],
                )
        save_sampler.assert_not_called()

    def test_sampler_output_contract_rejects_wildcards_and_arrays(self):
        for suffix, schema in (
            ("wildcard", {}),
            ("array", {"type": "array", "items": {"type": "number"}}),
            ("nested-array", {
                "type": "object",
                "properties": {
                    "values": {"type": "array", "items": {"type": "number"}},
                },
                "required": ["values"],
                "additionalProperties": False,
            }),
        ):
            draft = row_map_draft(f"invalid-{suffix}")
            draft["outputSchema"] = {"sample.value": schema}
            draft["config"] = {
                "mapping": {"sample.value": "close"},
                "includeUnmappedFields": False,
                "unmappedPrefix": "dataset.",
            }
            with self.subTest(schema=suffix), self.assertRaisesRegex(
                ValueError, "wildcard|array runtime type"
            ):
                samplers.save_sampler(self.config, draft)


if __name__ == "__main__":
    unittest.main()
