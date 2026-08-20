import json
import os
import stat
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

import engine_service
from engine.contracts import module as module_contracts
from builtin_implementations import resources as builtin_resources
from engine.control import database as engine_database
from engine.repository import folders as repository_folders
from engine.repository import module_definitions


PROJECT_ROOT = Path(__file__).resolve().parents[3]


class RepositoryFolderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.config = {
            "controlRoot": str(root / "control"),
            "releaseRoot": str(root / "releases"),
            "liveRoot": str(root / "live"),
        }
        engine_database.prepare_database(self.config)
        builtin_resources.install(self.config)

    def tearDown(self):
        self.temp.cleanup()

    def test_only_multi_kind_module_repository_has_type_roots(self):
        pipeline = repository_folders.repository_tree(self.config, "modules")
        analysis = repository_folders.repository_tree(self.config, "analysis-modules")
        environment = repository_folders.repository_tree(self.config, "environment-modules")

        self.assertEqual(
            {item["name"] for item in pipeline["folders"] if not item["parentId"]},
            set(repository_folders.MODULE_FIXED_FOLDERS),
        )
        self.assertEqual(
            {item["path"] for item in analysis["folders"]},
            {"/BuiltIn"},
        )
        self.assertEqual(
            {item["path"] for item in environment["folders"]},
            {"/BuiltIn"},
        )

    def test_builtin_items_are_fixed_in_their_own_repository(self):
        analyzer_key = next(
            key for key in module_definitions.load_analysis_definitions(self.config)
            if key.startswith("Analyzer/cycle-count-analyzer/")
        )
        environment_key = next(
            key
            for key in module_definitions.load_environment_definitions(self.config)
            if key.startswith("Environment/fixed-plus-bps-fee-model/")
        )
        analyzer = module_definitions.load_analysis_definitions(self.config)[analyzer_key]
        environment = module_definitions.load_environment_definitions(self.config)[
            environment_key
        ]
        analyzer_placement = repository_folders.item_folder(
            self.config, "analysis-modules", analyzer_key, analyzer
        )
        environment_placement = repository_folders.item_folder(
            self.config,
            "environment-modules",
            environment_key,
            environment,
        )
        self.assertEqual(analyzer_placement["folderPath"], "/BuiltIn")
        self.assertEqual(environment_placement["folderPath"], "/BuiltIn")
        with self.assertRaisesRegex(ValueError, "Built-in Modules"):
            repository_folders.assign_item(
                self.config,
                "analysis-modules",
                analyzer_key,
                "",
                module_definition=analyzer,
            )

    def test_single_kind_module_repositories_accept_root_user_folders(self):
        folder = repository_folders.create_folder(
            self.config, "analysis-modules", "Research", ""
        )
        self.assertEqual(folder["path"], "/Research")
        loose = repository_folders.create_folder(
            self.config, "environment-modules", "Loose", ""
        )
        self.assertEqual(loose["path"], "/Loose")
        custom = {
            "kind": "Analyzer",
            "moduleId": "custom-performance",
            "builtin": False,
        }
        item_id = "Analyzer/custom-performance"
        self.assertEqual(
            repository_folders.item_folder(
                self.config, "analysis-modules", item_id, custom
            )["folderPath"],
            "/",
        )
        self.assertEqual(
            repository_folders.assign_item(
                self.config,
                "analysis-modules",
                item_id,
                folder["folderId"],
                module_definition=custom,
            )["folderPath"],
            "/Research",
        )
        builtin_id = repository_folders._repository_builtin_folder_id(
            "analysis-modules"
        )
        with self.assertRaisesRegex(ValueError, "BuiltIn"):
            repository_folders.assign_item(
                self.config,
                "analysis-modules",
                item_id,
                builtin_id,
                module_definition=custom,
            )

    def test_multi_kind_module_repository_keeps_fixed_type_roots(self):
        with self.assertRaisesRegex(ValueError, "top-level folders are fixed"):
            repository_folders.create_folder(
                self.config, "modules", "Loose", ""
            )

    def test_folder_state_corruption_is_rejected_not_normalized(self):
        state_path = Path(self.config["controlRoot"]) / repository_folders.STATE_FILE
        state = repository_folders.load_state(self.config)
        state["assignments"]["analysis-modules"]["Analyzer/example"] = (
            repository_folders._repository_builtin_folder_id("analysis-modules")
        )
        state_path.write_text(json.dumps(state), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "reserved BuiltIn"):
            repository_folders.load_state(self.config)

    def test_v8_folder_state_migrates_flat_roots_and_versioned_item_ids(self):
        state_path = Path(self.config["controlRoot"]) / repository_folders.STATE_FILE
        state = repository_folders._empty_state()
        state["schemaVersion"] = 8
        research_id = "folder-research"
        state["folders"]["analysis-modules"][research_id] = {
            "folderId": research_id,
            "name": "Research",
            "parentId": repository_folders._fixed_folder_id("Analyzer"),
            "fixed": False,
        }
        state["assignments"]["analysis-modules"] = {
            "Analyzer/performance/2": research_id,
            "Analyzer/performance/10": research_id,
            "Analyzer/default/1": repository_folders._fixed_folder_id("Analyzer"),
        }
        archived_id = "folder-archived"
        state["folders"]["analyses"][archived_id] = {
            "folderId": archived_id,
            "name": "Archived",
            "parentId": "",
            "fixed": False,
        }
        state["assignments"]["analyses"] = {
            "returns::2": archived_id,
            "returns::10": archived_id,
        }
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(json.dumps(state), encoding="utf-8")

        migrated = repository_folders.prepare(self.config)

        self.assertEqual(migrated["schemaVersion"], repository_folders.SCHEMA_VERSION)
        self.assertEqual(
            migrated["folders"]["analysis-modules"][research_id]["parentId"],
            "",
        )
        self.assertEqual(
            migrated["assignments"]["analysis-modules"],
            {"Analyzer/performance": research_id},
        )
        self.assertEqual(
            migrated["assignments"]["analyses"],
            {"returns": archived_id},
        )
        self.assertEqual(repository_folders.load_state(self.config), migrated)

    def test_backtest_catalog_contains_results_while_environments_are_independent(self):
        catalog = engine_service.repository_catalog(self.config, "backtest")
        sources = {item.get("sourceRepository") for item in catalog["items"]}
        self.assertNotIn("environments", sources)
        self.assertNotIn("analysis-modules", sources)
        self.assertNotIn("environment-modules", sources)
        self.assertFalse(any(item.get("resourceType", "").endswith("Module") for item in catalog["items"]))

    def test_graph_resource_catalogs_expose_one_stable_item_at_latest_version(self):
        environments = [
            {"environmentId": "fees", "version": "2", "name": "Fees v2"},
            {"environmentId": "fees", "version": "10", "name": "Fees"},
            {"environmentId": "slippage", "version": "1", "name": "Slippage"},
        ]
        analyses = [
            {"analysisId": "returns", "version": "3", "name": "Returns"},
            {"analysisId": "returns", "version": "1", "name": "Returns v1"},
        ]
        folder = repository_folders.create_folder(
            self.config, "environments", "Trading Costs"
        )
        repository_folders.assign_item(
            self.config, "environments", "fees", folder["folderId"]
        )

        with (
            mock.patch.object(
                engine_service.environment_service,
                "environment_definitions",
                return_value=environments,
            ),
            mock.patch.object(
                engine_service.analysis_service,
                "analysis_definitions",
                return_value=analyses,
            ),
        ):
            environment_catalog = engine_service.repository_catalog(
                self.config, "environments"
            )
            analysis_catalog = engine_service.repository_catalog(
                self.config, "analyses"
            )

        self.assertEqual(environment_catalog["total"], 2)
        self.assertEqual(
            {item["itemId"] for item in environment_catalog["items"]},
            {"fees", "slippage"},
        )
        fees = next(
            item for item in environment_catalog["items"]
            if item["itemId"] == "fees"
        )
        self.assertEqual(fees["version"], "10")
        self.assertEqual(fees["versionKey"], "fees::10")
        self.assertEqual(fees["folderPath"], "/Trading Costs")
        self.assertEqual(analysis_catalog["total"], 1)
        self.assertEqual(analysis_catalog["items"][0]["itemId"], "returns")
        self.assertEqual(analysis_catalog["items"][0]["version"], "3")
        self.assertEqual(analysis_catalog["items"][0]["versionKey"], "returns::3")

    def test_module_catalogs_expose_one_stable_item_at_latest_version(self):
        module_versions = {
            "Analyzer/performance/2": {
                "kind": "Analyzer",
                "moduleId": "performance",
                "version": "2",
                "name": "Performance v2",
            },
            "Analyzer/performance/10": {
                "kind": "Analyzer",
                "moduleId": "performance",
                "version": "10",
                "name": "Performance",
            },
        }
        with (
            mock.patch.object(
                engine_service,
                "engine_module_definitions",
                return_value=module_versions,
            ),
            mock.patch.object(
                engine_service,
                "analysis_module_definitions",
                return_value=module_versions,
            ),
            mock.patch.object(
                engine_service,
                "environment_module_definitions",
                return_value=module_versions,
            ),
        ):
            catalogs = {
                repository: engine_service.repository_items(self.config, repository)
                for repository in (
                    "modules",
                    "analysis-modules",
                    "environment-modules",
                )
            }

        for items in catalogs.values():
            self.assertEqual(set(items), {"Analyzer/performance"})
            item = items["Analyzer/performance"]
            self.assertEqual(item["itemId"], "Analyzer/performance")
            self.assertEqual(item["version"], "10")
            self.assertEqual(item["versionKey"], "Analyzer/performance/10")

    def test_module_catalogs_are_distinct(self):
        pipeline = engine_service.repository_catalog(self.config, "modules")
        analysis = engine_service.repository_catalog(self.config, "analysis-modules")
        environment = engine_service.repository_catalog(self.config, "environment-modules")
        self.assertTrue(
            {item["kind"] for item in pipeline["items"]}
            <= module_contracts.ENGINE_MODULE_KINDS
        )
        self.assertIn("Signal", {item["kind"] for item in pipeline["items"]})
        self.assertEqual({item["kind"] for item in analysis["items"]}, {"Analyzer"})
        self.assertEqual({item["kind"] for item in environment["items"]}, {"Environment"})

    def test_submission_endpoints_enforce_repository_kind(self):
        common = {
            "activationMode": "ProcessRunner",
            "parameters": {"command": "python3", "arguments": []},
            "configSchema": {"type": "object", "properties": {}},
            "ports": {"inputs": {}, "outputs": {}},
            "name": "wrong",
            "description": "wrong repository fixture",
            "files": [],
        }
        with self.assertRaisesRegex(ValueError, "Invalid Pipeline"):
            engine_service.handle_add_engine_module(self.config, {
                **common, "kind": "Analyzer", "moduleId": "wrong",
            })
        with self.assertRaisesRegex(ValueError, "Invalid Analysis"):
            engine_service.handle_add_analysis_module(self.config, {
                **common, "kind": "Environment", "moduleId": "wrong",
            })
        with self.assertRaisesRegex(ValueError, "Invalid Environment"):
            engine_service.handle_add_environment_module(self.config, {
                **common, "kind": "Signal", "moduleId": "wrong",
            })

    def test_repository_catalog_holds_one_folder_snapshot(self):
        first = repository_folders.create_folder(
            self.config, "scripts", "First"
        )
        second = repository_folders.create_folder(
            self.config, "scripts", "Second"
        )
        item_ids = ("recipe-one::1", "recipe-two::1")
        for item_id in item_ids:
            repository_folders.assign_item(
                self.config, "scripts", item_id, first["folderId"]
            )

        catalog_inside_snapshot = threading.Event()
        allow_catalog_to_continue = threading.Event()
        writer_finished = threading.Event()
        failures = []
        result = {}

        def blocked_repository_items(_config, _repository):
            catalog_inside_snapshot.set()
            if not allow_catalog_to_continue.wait(5):
                raise TimeoutError("Catalog snapshot test was not released.")
            return {
                item_id: {"itemId": item_id, "label": item_id}
                for item_id in item_ids
            }

        def read_catalog():
            try:
                result.update(engine_service.repository_catalog(self.config, "scripts"))
            except BaseException as exc:
                failures.append(exc)

        def move_item():
            try:
                repository_folders.assign_item(
                    self.config,
                    "scripts",
                    item_ids[0],
                    second["folderId"],
                )
            except BaseException as exc:
                failures.append(exc)
            finally:
                writer_finished.set()

        with mock.patch.object(
            engine_service,
            "repository_items",
            side_effect=blocked_repository_items,
        ):
            reader = threading.Thread(target=read_catalog)
            reader.start()
            self.assertTrue(catalog_inside_snapshot.wait(5))
            writer = threading.Thread(target=move_item)
            writer.start()
            self.assertFalse(writer_finished.wait(0.2))
            allow_catalog_to_continue.set()
            reader.join(5)
            writer.join(5)

        self.assertFalse(reader.is_alive())
        self.assertFalse(writer.is_alive())
        self.assertEqual(failures, [])
        self.assertEqual(
            {item["folderPath"] for item in result["items"]},
            {"/First"},
        )
        self.assertEqual(
            repository_folders.item_folder(
                self.config, "scripts", item_ids[0]
            )["folderPath"],
            "/Second",
        )

    def test_repository_readers_share_lock_and_writer_is_exclusive(self):
        release_readers = threading.Event()
        reader_entered = [threading.Event(), threading.Event()]
        writer_finished = threading.Event()
        failures = []

        def read_snapshot(index):
            try:
                with repository_folders.repository_read_snapshot(self.config):
                    reader_entered[index].set()
                    if not release_readers.wait(5):
                        raise TimeoutError("Repository reader test was not released.")
            except BaseException as exc:
                failures.append(exc)

        def mutate():
            try:
                repository_folders.create_folder(
                    self.config, "scripts", "After readers"
                )
            except BaseException as exc:
                failures.append(exc)
            finally:
                writer_finished.set()

        readers = [
            threading.Thread(target=read_snapshot, args=(index,))
            for index in range(2)
        ]
        for reader in readers:
            reader.start()
        self.assertTrue(reader_entered[0].wait(5))
        self.assertTrue(reader_entered[1].wait(5))
        writer = threading.Thread(target=mutate)
        writer.start()
        self.assertFalse(writer_finished.wait(0.2))
        release_readers.set()
        for reader in readers:
            reader.join(5)
        writer.join(5)

        self.assertTrue(writer_finished.is_set())
        self.assertFalse(any(reader.is_alive() for reader in readers))
        self.assertFalse(writer.is_alive())
        self.assertEqual(failures, [])

    def test_repository_shared_snapshot_blocks_cross_process_mutation(self):
        child_reader_script = "\n".join((
            "import json, sys; from engine.repository import folders as repository_folders",
            "config = json.loads(sys.argv[1])",
            "with repository_folders.repository_read_snapshot(config):",
            "    print('read', flush=True)",
        ))
        child_script = "\n".join((
            "import json, sys; from engine.repository import folders as repository_folders",
            "config = json.loads(sys.argv[1])",
            "print('ready', flush=True)",
            "repository_folders.create_folder(config, 'scripts', 'From child')",
            "print('done', flush=True)",
        ))
        process = None
        try:
            with repository_folders.repository_read_snapshot(self.config):
                reader = subprocess.run(
                    [
                        sys.executable,
                        "-c",
                        child_reader_script,
                        json.dumps(self.config),
                    ],
                    cwd=PROJECT_ROOT,
                    text=True,
                    capture_output=True,
                    timeout=5,
                    check=False,
                )
                self.assertEqual(reader.returncode, 0, reader.stderr)
                self.assertEqual(reader.stdout.strip(), "read")
                process = subprocess.Popen(
                    [sys.executable, "-c", child_script, json.dumps(self.config)],
                    cwd=PROJECT_ROOT,
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
                self.assertEqual(process.stdout.readline().strip(), "ready")
                with self.assertRaises(subprocess.TimeoutExpired):
                    process.wait(timeout=0.2)
            stdout, stderr = process.communicate(timeout=5)
            self.assertEqual(process.returncode, 0, stderr)
            self.assertEqual(stdout.strip(), "done")
        finally:
            if process is not None and process.poll() is None:
                process.kill()
                process.wait()
        self.assertIn(
            "/From child",
            {
                folder["path"]
                for folder in repository_folders.repository_tree(
                    self.config, "scripts"
                )["folders"]
            },
        )

    def test_save_state_flushes_file_and_directory_around_replace(self):
        state = repository_folders.load_state(self.config)
        events = []
        real_fsync = os.fsync
        real_replace = os.replace

        def tracked_fsync(file_descriptor):
            kind = (
                "directory-fsync"
                if stat.S_ISDIR(os.fstat(file_descriptor).st_mode)
                else "file-fsync"
            )
            events.append(kind)
            return real_fsync(file_descriptor)

        def tracked_replace(source, destination):
            events.append("replace")
            return real_replace(source, destination)

        with mock.patch.object(repository_folders.os, "fsync", tracked_fsync), \
                mock.patch.object(repository_folders.os, "replace", tracked_replace):
            repository_folders.save_state(self.config, state)

        self.assertEqual(
            events,
            ["file-fsync", "replace", "directory-fsync"],
        )
        self.assertEqual(repository_folders.load_state(self.config), state)


if __name__ == "__main__":
    unittest.main()
