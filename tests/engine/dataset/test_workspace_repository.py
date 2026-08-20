"""Dataset Workspace repository, recovery, and lifecycle tests."""

import os
import sqlite3
from pathlib import Path
from unittest import mock

from dataset_adapters import ohlcv
from engine.contracts import workspace as workspace_contract
from engine.control import database as engine_database
from engine.repository import dataset_build_jobs
from engine.repository import dataset_recipes
from engine.repository import datasets
from engine.repository import folders as repository_folders
from engine.repository import workspace_files
from engine.repository import dataset_workspaces as workspace_repository
from engine.service import dataset_builds
from engine.service import dataset_workspaces as workspace_service
from engine.service import datasets as dataset_service
from tests.support.dataset_workspace import DatasetWorkspaceTestCase


class DatasetWorkspaceRepositoryTests(DatasetWorkspaceTestCase):
    def test_workspace_repository_symlink_is_rejected_without_external_writes(self):
        outside = Path(self.temp.name) / "outside-workspaces"
        outside.mkdir()
        managed = Path(self.config["releaseRoot"]) / "_dataset_workspaces"
        self.assertFalse(managed.exists())
        managed.symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "symbolic link"):
            workspace_repository.workspace_root(self.config)
        self.assertEqual(list(outside.iterdir()), [])

    def test_build_job_root_symlink_chain_is_rejected_without_external_writes(self):
        outside = Path(self.temp.name) / "outside-control"
        outside.mkdir()
        control = Path(self.temp.name) / "control-link"
        control.symlink_to(outside, target_is_directory=True)
        config = {**self.config, "controlRoot": str(control / "nested")}
        with self.assertRaisesRegex(ValueError, "symbolic link"):
            dataset_build_jobs.job_root(config)
        self.assertEqual(list(outside.iterdir()), [])

    def test_ohlcv_adapter_rejects_invalid_rows_instead_of_dropping_them(self):
        with self.assertRaisesRegex(ValueError, "row 3.*invalid numeric"):
            ohlcv.parse_csv(
                "date,open,high,low,close,volume\n"
                "2026-01-01,1,2,0,1,10\n"
                "2026-01-02,invalid,2,0,1,10\n"
            )

    def test_ohlcv_csv_parser_preserves_source_order(self):
        rows = ohlcv.parse_csv(
            "date,open,high,low,close,volume\n"
            "2026-01-02,1,2,0,1,10\n"
            "2026-01-01,2,3,1,2,20\n"
        )
        self.assertEqual([row["date"] for row in rows], ["2026-01-02", "2026-01-01"])

    def test_ohlcv_csv_rejects_duplicate_and_ambiguous_headers(self):
        for header in (
            "date,open,OPEN,high,low,close",
            "date,open,high,low,close,adj_close",
        ):
            with self.subTest(header=header), self.assertRaisesRegex(
                ValueError, "duplicate|multiple columns"
            ):
                ohlcv.parse_csv(header + "\n2026-01-01,1,1,2,0,1\n")

    def test_ohlcv_adapter_preserves_authoritative_availability_order(self):
        rows = [
            {
                "date": "2026-01-02", "availableAt": "2026-01-01T12:00:00Z",
                "open": 1, "high": 2, "low": 0, "close": 1, "volume": 10,
            },
            {
                "date": "2026-01-01", "availableAt": "2026-01-02T12:00:00Z",
                "open": 2, "high": 3, "low": 1, "close": 2, "volume": 20,
            },
        ]
        dataset = ohlcv.register_dataset(
            self.config, dataset_id="availability-order", name="Availability order",
            symbol="TEST", source="test", interval="d", rows=rows,
            availability_policy="timestamp_is_available_at",
        )
        records = dataset_service.get_dataset_records(
            self.config, dataset["latestVersionId"]
        )
        self.assertEqual(
            [record.event_time for record in records],
            ["2026-01-02", "2026-01-01"],
        )
        with self.assertRaisesRegex(ValueError, "non-decreasing availability"):
            ohlcv.register_dataset(
                self.config, dataset_id="invalid-availability-order",
                name="Invalid availability order", symbol="TEST", source="test",
                interval="d", rows=list(reversed(rows)),
                availability_policy="timestamp_is_available_at",
            )

    def test_archive_cascades_to_all_downstream_datasets(self):
        workspace = self.create_workspace()
        recipe = self.archive_script(
            "from pathlib import Path\nPath('out.txt').write_text('ok')\n"
        )
        dataset_builds.submit_build(self.config, {
            "workspaceId": workspace["workspaceId"],
            "outputDatasetId": "derived-features",
            "recipeId": recipe["recipeId"], "recipeVersion": recipe["version"],
        })
        result = datasets.archive_dataset(self.config, "hourly-source", "source retired")
        self.assertEqual(
            set(result["archivedDatasetIds"]), {"hourly-source", "derived-features"}
        )
        self.assertEqual(datasets.get_dataset(self.config, "derived-features")["status"], "archived")

    def test_new_schema_contains_only_the_current_script_contract(self):
        with engine_database.connect_database(self.config) as connection:
            recipe_columns = {row["name"] for row in connection.execute("PRAGMA table_info(dataset_recipes)")}
            job_columns = {row["name"] for row in connection.execute("PRAGMA table_info(dataset_build_jobs)")}
            receipt_columns = {row["name"] for row in connection.execute("PRAGMA table_info(dataset_build_submission_receipts)")}
        self.assertNotIn("entrypoint", recipe_columns)
        self.assertNotIn("parameter_schema_json", recipe_columns)
        self.assertNotIn("entrypoint", job_columns)
        self.assertNotIn("parameters_json", job_columns)
        self.assertIn("arguments_json", job_columns)
        self.assertEqual(receipt_columns, {"operation_nonce", "job_id", "submission_digest"})

    def test_workspace_sources_and_lineage_foreign_keys_are_exact(self):
        for source, message in (
            ({"datasetId": "hourly-source", "typo": True}, "unsupported field"),
            ({"datasetId": "hourly-source", "alias": ""}, "non-empty"),
            ({"datasetId": "hourly-source", "datasetVersionId": ""}, "non-empty"),
        ):
            with self.subTest(source=source), self.assertRaisesRegex(ValueError, message):
                workspace_repository.create_workspace(
                    self.config, {"workspaceId": "invalid-source", "sources": [source]}
                )
        with engine_database.connect_database(self.config) as connection:
            self.assertEqual(connection.execute("PRAGMA foreign_keys").fetchone()[0], 1)
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    """
                    INSERT INTO dataset_lineage
                    (alias, upstream_dataset_id, upstream_version_id,
                     downstream_dataset_id, downstream_version_id, build_job_id, created_at)
                    VALUES ('source', 'missing', 'missing@sha256:00',
                            'missing', 'missing@sha256:11', '', '2026-01-01T00:00:00Z')
                    """
                )

    def test_startup_reclaims_all_terminal_internal_workspaces(self):
        workspaces = [
            workspace_repository.create_workspace(
                self.config,
                {"workspaceId": f"internal-{status}", "sources": [{"datasetId": "hourly-source"}]},
                internal=True,
            )
            for status in ("draft", "failed", "published")
        ]
        with engine_database.connect_database(self.config) as connection:
            for status in ("failed", "published"):
                connection.execute(
                    "UPDATE dataset_workspaces SET status = ? WHERE workspace_id = ?",
                    (status, f"internal-{status}"),
                )
            connection.commit()
        workspace_service.reconcile_internal_workspaces(self.config)
        with engine_database.connect_database(self.config) as connection:
            count = connection.execute(
                "SELECT COUNT(*) FROM dataset_workspaces WHERE internal = 1"
            ).fetchone()[0]
        self.assertEqual(count, 0)
        self.assertTrue(all(not Path(item["workspacePath"]).exists() for item in workspaces))

    def test_startup_reclaims_workspace_created_before_index_commit(self):
        orphan = workspace_repository.workspace_root(self.config) / "create-crash-orphan"
        orphan.mkdir()
        (orphan / "user-data.txt").write_text("unindexed", encoding="utf-8")
        workspace_repository.reconcile_workspace_directories(self.config)
        self.assertFalse(orphan.exists())

    def test_workspace_create_recovers_a_late_commit_error(self):
        original_connect = engine_database.connect_database
        raised = []

        class ConnectionProxy:
            def __init__(self, connection):
                self.connection = connection
                self.creation = False
            def __enter__(self): return self
            def __exit__(self, kind, value, traceback):
                return self.connection.__exit__(kind, value, traceback)
            def __getattr__(self, name): return getattr(self.connection, name)
            def execute(self, sql, parameters=()):
                if "INSERT INTO dataset_workspaces" in sql:
                    self.creation = True
                return self.connection.execute(sql, parameters)
            def commit(self):
                self.connection.commit()
                if self.creation and not raised:
                    raised.append(True)
                    raise RuntimeError("driver reported commit late")

        with mock.patch.object(
            engine_database, "connect_database",
            side_effect=lambda config: ConnectionProxy(original_connect(config)),
        ):
            workspace = workspace_repository.create_workspace(
                self.config,
                {"workspaceId": "late-workspace-commit", "sources": [{"datasetId": "hourly-source"}]},
            )
        self.assertTrue(raised)
        self.assertTrue(Path(workspace["workspacePath"]).is_dir())
        self.assertEqual(workspace_repository.get_workspace(self.config, workspace["workspaceId"]), workspace)

    def test_workspace_create_identity_and_name_types_are_strict(self):
        for request, message in (
            ({"workspaceId": 7, "sources": [{"datasetId": "hourly-source"}]}, "ID"),
            ({"name": 7, "sources": [{"datasetId": "hourly-source"}]}, "name"),
        ):
            with self.subTest(request=request), self.assertRaisesRegex(ValueError, message):
                workspace_repository.create_workspace(self.config, request)

    def test_recovery_accepts_canonical_paths_from_relative_roots(self):
        relative = {
            **self.config,
            "controlRoot": os.path.relpath(self.config["controlRoot"], Path.cwd()),
            "releaseRoot": os.path.relpath(self.config["releaseRoot"], Path.cwd()),
        }
        workspace = workspace_repository.create_workspace(
            relative,
            {"workspaceId": "relative-recovery", "sources": [{"datasetId": "hourly-source"}]},
        )
        workspace_repository.reconcile_workspace_directories(relative)
        job_id = "relative-terminal-job"
        root = dataset_build_jobs.job_root(relative) / job_id
        root.mkdir()
        scratch = root / "workspace"
        scratch.mkdir()
        (scratch / "value.txt").write_text("scratch", encoding="utf-8")
        self.assertTrue(dataset_build_jobs.discard_execution_workspace(relative, job_id, scratch))
        self.assertFalse(root.exists())
        workspace_service.delete_workspace(relative, workspace["workspaceId"])

    def test_workspace_lifecycle_and_immutable_script_versions(self):
        workspace = self.create_workspace("lifecycle")
        workspace_path = Path(workspace["workspacePath"])
        (workspace_path / ".workspace.json").write_text("{broken", encoding="utf-8")
        renamed = workspace_repository.rename_workspace(
            self.config, workspace["workspaceId"], "Renamed"
        )
        self.assertEqual(renamed["name"], "Renamed")
        folder = repository_folders.create_folder(self.config, "data", "Workspace folder")
        projected_id = repository_folders.shared_item_id("workspaces", workspace["workspaceId"])
        repository_folders.assign_item(self.config, "data", projected_id, folder["folderId"])
        runtime = workspace_files.instance_runtime_root(
            self.config, workspace_contract.workspace_slug(workspace["workspaceId"], "dataset")
        )
        (runtime / "stale-package.py").write_text("stale", encoding="utf-8")
        self.assertTrue(workspace_service.delete_workspace(self.config, workspace["workspaceId"])["deleted"])
        self.assertFalse(runtime.exists())
        self.assertNotIn(projected_id, repository_folders.load_state(self.config)["assignments"]["data"])

        recipe = dataset_recipes.save_recipe(self.config, {
            "recipeId": "lifecycle-script", "name": "Readable name",
            "scriptText": "from pathlib import Path\nPath('output.txt').write_text('ok')\n",
        })
        unchanged = dataset_recipes.save_recipe(self.config, {
            "recipeId": "lifecycle-script", "name": "Readable name",
            "scriptText": "from pathlib import Path\nPath('output.txt').write_text('ok')\n",
        })
        changed = dataset_recipes.save_recipe(self.config, {
            "recipeId": "lifecycle-script", "name": "Readable name",
            "scriptText": "from pathlib import Path\nPath('output.txt').write_text('changed')\n",
        })
        self.assertEqual((recipe["version"], unchanged["version"], changed["version"]), ("1", "1", "2"))
        self.assertEqual(dataset_recipes.get_recipe(self.config, recipe["recipeId"], "1")["version"], "1")
