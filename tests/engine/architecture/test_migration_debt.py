import ast
import unittest
from collections import Counter

from tests.support.architecture_scan import (
    ROOT,
    imported_modules as _imports,
    legacy_boundary_imports as _legacy_boundary_imports,
    legacy_private_accesses as _legacy_private_accesses,
    parse_source as _parse,
    silent_handlers as _silent_handlers,
)


LEGACY_CROSS_MODULE_PRIVATE_DEBT = Counter()


LEGACY_DANGEROUS_IMPORT_TARGETS = {
    "module_runtime": frozenset({"backtest_jobs", "engine_service"}),
    "backtest_runtime_worker": frozenset({"backtest_jobs", "engine_service"}),
}
LEGACY_BOUNDARY_IMPORT_DEBT = frozenset()


LEGACY_SILENT_CALLBACK_DEBT = Counter()


class MigrationDebtArchitectureTests(unittest.TestCase):
    maxDiff = None

    def test_legacy_market_root_is_physically_deleted_and_has_no_callers(self):
        legacy_module = "market_" + "data"
        self.assertFalse((ROOT / f"{legacy_module}.py").exists())
        callers = []
        for package in ("engine", "tests", "strategies", "strategy_devkit", "scripts"):
            package_root = ROOT / package
            if not package_root.exists():
                continue
            for path in sorted(package_root.rglob("*.py")):
                if any(
                    imported.split(".")[0] == legacy_module
                    for imported in _imports(path)
                ):
                    callers.append(str(path.relative_to(ROOT)))
        for path in sorted(ROOT.glob("*.py")):
            if any(
                imported.split(".")[0] == legacy_module
                for imported in _imports(path)
            ):
                callers.append(str(path.relative_to(ROOT)))
        self.assertEqual(callers, [])

    def test_extracted_cycle_graph_has_no_root_compatibility_facade(self):
        self.assertFalse((ROOT / "cycle_graph.py").exists())

    def test_extracted_backtest_jobs_have_no_root_compatibility_facade(self):
        self.assertFalse((ROOT / "backtest_jobs.py").exists())

    def test_extracted_result_stream_has_no_root_compatibility_facade(self):
        self.assertFalse((ROOT / "result_archive.py").exists())
        self.assertFalse((ROOT / "result_runtime_worker.py").exists())

    def test_extracted_sampler_assets_have_no_root_compatibility_facades(self):
        for name in (
            "row_map_sampler_runtime.py",
            "sampler_sdk.py",
            "sampler_worker.py",
        ):
            with self.subTest(name=name):
                self.assertFalse((ROOT / name).exists())

    def test_extracted_dataset_and_sampler_have_no_root_compatibility_facades(self):
        self.assertFalse((ROOT / "dataset_archive.py").exists())
        self.assertFalse((ROOT / "backtest_sampling.py").exists())
        self.assertFalse((ROOT / "sampler_lifecycle.py").exists())
        self.assertFalse((ROOT / "dataset_workspaces.py").exists())
        for relative in (
            "engine/archive/dataset.py",
            "engine/archive/sampler.py",
            "engine/authority/dataset.py",
            "engine/authority/sampler.py",
            "engine/contracts/dataset.py",
            "engine/contracts/dataset_workspace.py",
            "engine/contracts/sampler.py",
            "engine/repository/dataset_publication.py",
            "engine/repository/dataset_staging.py",
            "engine/repository/datasets.py",
            "engine/repository/dataset_build_jobs.py",
            "engine/repository/dataset_build_paths.py",
            "engine/repository/dataset_recipes.py",
            "engine/repository/dataset_workspaces.py",
            "engine/repository/samplers.py",
            "engine/repository/workspace_paths.py",
            "engine/runtime/backtest_provider.py",
            "engine/runtime/dataset.py",
            "engine/runtime/dataset_build.py",
            "engine/runtime/sampler.py",
            "engine/runtime/sampler_process.py",
            "engine/service/datasets.py",
            "engine/service/dataset_builds.py",
            "engine/service/dataset_workspaces.py",
            "engine/service/sampler_workspaces.py",
        ):
            with self.subTest(path=relative):
                path = ROOT / relative
                self.assertTrue(path.is_file())
                self.assertLess(
                    len(path.read_text(encoding="utf-8").splitlines()),
                    800,
                )
        dataset_authority = __import__(
            "engine.authority.dataset", fromlist=["DatasetHandle"]
        )
        self.assertFalse(hasattr(dataset_authority, "DatasetHandle"))
        self.assertFalse((ROOT / "jupyter_workspaces.py").exists())
        for relative in (
            "engine/contracts/workspace.py",
            "engine/repository/workspace_files.py",
            "engine/runtime/process_session.py",
            "engine/runtime/jupyter_workspace.py",
            "engine/service/jupyter_proxy.py",
            "engine/service/jupyter_workspaces.py",
        ):
            with self.subTest(path=relative):
                path = ROOT / relative
                self.assertTrue(path.is_file())
                self.assertLess(
                    len(path.read_text(encoding="utf-8").splitlines()), 800
                )

    def test_extracted_repository_folders_have_no_root_compatibility_facade(self):
        self.assertFalse((ROOT / "repository_folders.py").exists())

    def test_extracted_graph_repository_has_no_root_compatibility_facade(self):
        self.assertFalse((ROOT / "graph_resources.py").exists())
        for relative in (
            "engine/contracts/archive.py",
            "engine/repository/control_state.py",
            "engine/repository/graph_resources.py",
        ):
            with self.subTest(path=relative):
                path = ROOT / relative
                self.assertTrue(path.is_file())
                self.assertLess(
                    len(path.read_text(encoding="utf-8").splitlines()),
                    800,
                )
        strategy_definitions = {
            node.name
            for node in _parse(ROOT / "engine/service/control_api.py").body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        }
        self.assertEqual(
            strategy_definitions
            & {
                "append_history_event",
                "atomic_write_json",
                "control_state_lock",
                "load_history_events",
                "load_json_file",
                "load_sanitized_history_events",
                "load_state",
                "require_resource_path_segment",
                "sanitize_history_event",
                "save_state",
                "state_path",
            },
            set(),
        )

    def test_pipeline_execution_reads_and_manifest_rebuild_have_engine_owners(self):
        for relative in (
            "engine/compiler/pipeline_manifest.py",
            "engine/repository/pipelines.py",
            "engine/service/pipelines.py",
        ):
            with self.subTest(path=relative):
                path = ROOT / relative
                self.assertTrue(path.is_file())
                self.assertLess(
                    len(path.read_text(encoding="utf-8").splitlines()),
                    800,
                )

    def test_pipeline_control_state_and_lifecycle_have_engine_owners(self):
        control_tree = _parse(ROOT / "engine/service/control_api.py")
        removed_delegates = {
            "archive_pipeline_if_changed",
            "handle_clone_pipeline",
            "handle_create_pipeline",
            "handle_disable_pipeline",
            "handle_rename_pipeline",
            "load_current_pipeline",
            "load_pipeline_store",
            "load_pipeline_version",
            "load_pipeline_version_details",
            "load_pipelines",
            "pipeline_versions",
            "save_pipeline_store",
            "validate_pipeline_store",
        }
        functions = {
            node.name: node
            for node in control_tree.body
            if isinstance(node, ast.FunctionDef)
        }
        self.assertEqual(set(functions) & removed_delegates, set())

        http_tree = _parse(ROOT / "engine_service.py")
        http_source = (ROOT / "engine_service.py").read_text(encoding="utf-8")
        self.assertIn(
            "from engine.service import pipelines as pipeline_service",
            http_source,
        )
        for legacy_call in (
            "control.archive_pipeline_if_changed",
            "control.handle_clone_pipeline",
            "control.handle_create_pipeline",
            "control.handle_disable_pipeline",
            "control.handle_rename_pipeline",
            "control.load_current_pipeline",
            "control.load_pipeline_store",
            "control.load_pipeline_version",
            "control.load_pipeline_version_details",
            "control.load_pipelines",
            "control.pipeline_versions",
        ):
            with self.subTest(legacy_call=legacy_call):
                self.assertNotIn(legacy_call, http_source)
        manual_pipeline_archive_reads = []
        for node in ast.walk(http_tree):
            if not isinstance(node, ast.BinOp) or not isinstance(node.op, ast.Div):
                continue
            if any(
                isinstance(item, ast.Constant) and item.value == "pipeline.json"
                for item in ast.walk(node)
            ):
                manual_pipeline_archive_reads.append(node.lineno)
        self.assertEqual(manual_pipeline_archive_reads, [])

    def test_visualization_has_formal_four_layer_owners(self):
        for relative in (
            "engine/contracts/visualization.py",
            "engine/compiler/visualization.py",
            "engine/repository/visualizations.py",
            "engine/service/visualizations.py",
        ):
            with self.subTest(path=relative):
                path = ROOT / relative
                self.assertTrue(path.is_file())
                self.assertLess(
                    len(path.read_text(encoding="utf-8").splitlines()),
                    800,
                )

    def test_extracted_module_catalog_has_engine_owners_and_no_root_facades(self):
        self.assertFalse((ROOT / "module_lifecycle.py").exists())
        self.assertFalse((ROOT / "builtin_archives.py").exists())
        for relative in (
            "engine/archive/version.py",
            "engine/archive/version_evidence.py",
            "builtin_implementations/pipeline_contracts.py",
            "engine/repository/module_definitions.py",
            "builtin_implementations/resources.py",
            "engine/service/module_publication.py",
            "engine/service/module_workspaces.py",
        ):
            with self.subTest(path=relative):
                path = ROOT / relative
                self.assertTrue(path.is_file())
                self.assertLess(
                    len(path.read_text(encoding="utf-8").splitlines()),
                    800,
                )

        version_archive = __import__(
            "engine.archive.version",
            fromlist=["verify_record_location_evidence"],
        )
        for name in (
            "verified_record_collection_from_index_evidence",
            "verified_record_index_material",
            "verified_record_location_material",
            "verify_record_index_location_evidence",
            "verify_record_index_locations",
            "verify_record_location_evidence",
        ):
            with self.subTest(version_reexport=name):
                self.assertFalse(hasattr(version_archive, name))

        strategy_definitions = {
            node.name
            for node in _parse(ROOT / "engine/service/control_api.py").body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        }
        self.assertEqual(
            strategy_definitions
            & {
                "decode_bundle_files",
                "load_all_definitions",
                "load_analysis_definitions",
                "load_definition_versions",
                "load_environment_definitions",
                "load_pipeline_definitions",
                "module_references",
                "module_repository_for_kind",
                "module_version_dir",
                "publish_module",
                "require_safe_relative_bundle_path",
            },
            set(),
        )

    def test_extracted_environment_and_analysis_have_no_root_facades(self):
        self.assertFalse((ROOT / "backtest_environment.py").exists())
        self.assertFalse((ROOT / "backtest_analysis.py").exists())
        for relative in (
            "engine/contracts/graph_resource.py",
            "engine/contracts/environment.py",
            "engine/contracts/analysis.py",
            "engine/compiler/environment.py",
            "engine/compiler/analysis.py",
            "builtin_implementations/environment_presets.py",
            "builtin_implementations/analysis_presets.py",
            "engine/service/environment.py",
            "engine/service/analysis.py",
            "engine/runtime/graph_cycle.py",
        ):
            with self.subTest(path=relative):
                path = ROOT / relative
                self.assertTrue(path.is_file())
                self.assertLess(
                    len(path.read_text(encoding="utf-8").splitlines()),
                    800,
                )
        private_factory_users = []
        production_paths = [
            *sorted(ROOT.glob("*.py")),
            *sorted((ROOT / "engine").rglob("*.py")),
        ]
        for path in production_paths:
            if path == ROOT / "engine/runtime/graph_cycle.py":
                continue
            for node in ast.walk(_parse(path)):
                if (
                    isinstance(node, ast.Attribute)
                    and node.attr == "_from_compiled_authority"
                ):
                    private_factory_users.append(
                        f"{path.relative_to(ROOT)}:{node.lineno}"
                    )
        self.assertEqual(private_factory_users, [])

    def test_result_owners_exist_after_root_facade_deletion(self):
        for relative in (
            "engine/repository/backtest_results.py",
            "engine/service/backtest_results.py",
        ):
            with self.subTest(path=relative):
                path = ROOT / relative
                self.assertTrue(path.is_file())
                self.assertLess(
                    len(path.read_text(encoding="utf-8").splitlines()),
                    800,
                )
    def test_extracted_backtest_composition_has_engine_owners_and_no_facade(self):
        for relative in (
            "engine/core/build_identity.py",
            "engine/core/runtime_identity.py",
            "engine/composition/backtest.py",
            "engine/service/backtests.py",
        ):
            with self.subTest(path=relative):
                path = ROOT / relative
                self.assertTrue(path.is_file())
                self.assertLess(
                    len(path.read_text(encoding="utf-8").splitlines()),
                    800,
                )
    def test_extracted_backtest_execution_has_engine_owners_and_no_facade(self):
        for relative in (
            "engine/archive/backtest_result.py",
            "engine/worker/backtest_execution.py",
            "engine/worker/backtest_runtime.py",
            "engine/service/backtest_execution.py",
        ):
            with self.subTest(path=relative):
                path = ROOT / relative
                self.assertTrue(path.is_file())
                self.assertLess(
                    len(path.read_text(encoding="utf-8").splitlines()),
                    800,
                )
        self.assertFalse((ROOT / "backtest_runtime_worker.py").exists())
        repository_source = (
            ROOT / "engine/repository/backtest_results.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn("def commit_result_catalog(", repository_source)

    def test_legacy_cross_module_private_debt_is_exact(self):
        self.assertEqual(
            _legacy_private_accesses(),
            LEGACY_CROSS_MODULE_PRIVATE_DEBT,
            "Private debt changed: removals must delete the matching baseline; additions are forbidden.",
        )

    def test_legacy_boundary_import_debt_is_exact(self):
        self.assertEqual(
            _legacy_boundary_imports(LEGACY_DANGEROUS_IMPORT_TARGETS),
            LEGACY_BOUNDARY_IMPORT_DEBT,
            "Boundary debt changed: removals must delete the matching baseline; additions are forbidden.",
        )

    def test_legacy_silent_callback_debt_is_exact(self):
        actual = Counter()
        for path in sorted(ROOT.glob("*.py")):
            if path.name.startswith("test_"):
                continue
            for key, count in _silent_handlers(path).items():
                if key.endswith(":Exception") and "._emit:" in key:
                    actual[key] += count
        self.assertEqual(
            actual,
            LEGACY_SILENT_CALLBACK_DEBT,
            "Silent callback debt changed: removals update the baseline; additions are forbidden.",
        )
