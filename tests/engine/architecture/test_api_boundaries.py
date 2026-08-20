import ast
import importlib
import unittest
from pathlib import Path

from tests.support.architecture_scan import (
    ROOT,
    TARGET_ENGINE_ROOT,
    engine_source_files as _engine_source_files,
    imported_modules as _imports,
    parse_source as _parse,
)


FORBIDDEN_COMPATIBILITY_TOKENS = frozenset(
    {
        "pipelineEventRouting",
        "executionOnlyValues",
        "invoke_on_input_change",
        "invocationPolicy",
        "transientDataKeys",
        "isolate_runtime_value",
    }
)


AUTHORITY_BOUND_RAW_CONSTRUCTORS = frozenset(
    {
        "DatasetHandle",
        "RowMappingSampler",
        "PythonScriptSampler",
        "ModuleInvoker",
        "ModuleGraphRuntime",
        "CycleGraphRuntime",
        "EnvironmentGraphRuntime",
        "AnalysisGraphRuntime",
        "BacktestPipelineRuntime",
    }
)


class ApiBoundaryArchitectureTests(unittest.TestCase):
    maxDiff = None

    def test_mining_release_has_its_own_config_boundary_and_no_engine_facade(self):
        retired_root = "strategy_" + "submit_api"
        violations = []
        for path in sorted((ROOT / "mining").rglob("*.py")):
            for imported in sorted(_imports(path)):
                if imported.split(".")[0] in {"engine", retired_root}:
                    violations.append(
                        f"{path.relative_to(ROOT)} imports {imported}"
                    )
        self.assertEqual(violations, [])

        self.assertFalse((ROOT / f"{retired_root}.py").exists())

    def test_generic_graph_engine_contains_no_builtin_business_preset(self):
        forbidden = {
            "standard-paper-environment",
            "standard-performance-analysis",
            "market.execution_value",
            "last.policy.target_position",
            "broker.account",
            "broker.order",
            "analysis.performance",
            "fixed-plus-bps-fee-model",
            "performance-metrics-analyzer",
            "ohlc.candles",
            "series.line",
            "overlay.markers",
        }
        roots = (
            TARGET_ENGINE_ROOT / "contracts",
            TARGET_ENGINE_ROOT / "repository",
            TARGET_ENGINE_ROOT / "runtime",
            TARGET_ENGINE_ROOT / "compiler",
        )
        findings = []
        for root in roots:
            for path in sorted(root.rglob("*.py")):
                text = path.read_text(encoding="utf-8")
                for token in sorted(forbidden):
                    if token in text:
                        findings.append(
                            f"{path.relative_to(ROOT)} contains {token}"
                        )
        self.assertEqual(findings, [])

        removed_catalogs = (
            "builtin_analysis_modules.py",
            "builtin_environment_modules.py",
            "builtin_pipeline_modules.py",
            "visualizer.py",
        )
        self.assertEqual(
            [
                name
                for name in removed_catalogs
                if (TARGET_ENGINE_ROOT / "contracts" / name).exists()
            ],
            [],
        )

    def test_pipeline_compiler_authority_runtime_have_owned_boundaries(self):
        pipeline_paths = (
            ROOT / "engine/contracts/pipeline.py",
            ROOT / "engine/authority/pipeline.py",
            ROOT / "engine/compiler/pipeline.py",
            ROOT / "engine/runtime/pipeline.py",
        )
        forbidden_roots = {"strategy_" + "submit_api"}
        violations = []
        for path in pipeline_paths:
            self.assertTrue(path.is_file())
            self.assertLess(
                len(path.read_text(encoding="utf-8").splitlines()),
                800,
            )
            for imported in sorted(_imports(path)):
                if imported in forbidden_roots:
                    violations.append(
                        f"{path.relative_to(ROOT)} imports root legacy {imported}"
                    )
                if (
                    path.relative_to(ROOT) == Path("engine/authority/pipeline.py")
                    and imported.startswith("engine.compiler")
                ):
                    violations.append(
                        f"{path.relative_to(ROOT)} reverses authority/compiler DAG via {imported}"
                    )
        self.assertEqual(violations, [])

    def test_extracted_data_model_has_no_data_compatibility_reexports(self):
        data_contracts = importlib.import_module("engine.contracts.data")
        data_model = importlib.import_module("engine.contracts.data_model")
        legacy_private_symbols = {
            "_CompiledValidationPlan",
            "_ANNOTATION_KEYS",
            "_JSON_TYPES",
            "_SCHEMA_KEYS",
            "_compile_validation_plan",
            "_compiled_validation_failure",
            "_declared_types",
            "_json_type",
            "_json_value_in",
            "_json_values_equal",
            "_raise_compiled_validation_failure",
            "_schema_label_normalized",
            "_schema_possible_runtime_types",
            "_schema_types_normalized",
            "_validate_data_key_schema",
            "_validate_normalized_json_value",
        }
        moved_symbols = set(data_model.__all__) | legacy_private_symbols
        self.assertEqual(set(vars(data_contracts)) & moved_symbols, set())

    def test_extracted_result_execution_has_no_result_reexports(self):
        result_contracts = importlib.import_module("engine.contracts.result")
        result_execution = importlib.import_module(
            "engine.contracts.result_execution"
        )
        moved_symbols = set(result_execution.__all__) | {
            "_nonnegative_number",
            "_require_contract_map",
            "_require_graph",
            "_require_pipeline",
            "_require_string_list",
            "_require_timings",
            "_require_transport",
        }
        self.assertEqual(set(vars(result_contracts)) & moved_symbols, set())
        for relative in (
            "engine/contracts/result.py",
            "engine/contracts/result_execution.py",
        ):
            with self.subTest(path=relative):
                self.assertLess(
                    len((ROOT / relative).read_text(encoding="utf-8").splitlines()),
                    800,
                )
        self.assertIn(
            "engine.contracts.result_execution",
            _imports(ROOT / "engine/contracts/result.py"),
        )
        self.assertIn(
            "engine.contracts.result_execution",
            _imports(ROOT / "engine/repository/backtest_results.py"),
        )

    def test_extracted_data_path_has_no_data_compatibility_reexports(self):
        data_contracts = importlib.import_module("engine.contracts.data")
        data_path = importlib.import_module("engine.contracts.data_path")
        moved_symbols = set(data_path.__all__) | {
            "_PATH_SEGMENT_PATTERN",
            "_split_data_path_text",
        }
        self.assertEqual(set(vars(data_contracts)) & moved_symbols, set())

    def test_extracted_data_compatibility_has_no_data_reexports(self):
        data_contracts = importlib.import_module("engine.contracts.data")
        compatibility = importlib.import_module(
            "engine.contracts.data_compatibility"
        )
        legacy_private_symbols = {
            "_context_with_branch",
            "_plain_schema_subset",
            "_schema_accepts_normalized_value",
            "_schema_base",
            "_schemas_compatible_normalized",
            "_source_context",
            "_source_context_disjoint_schema",
            "_source_context_is_empty",
            "_source_context_literals",
            "_source_context_subset_atom",
            "_source_context_subset_schema",
            "_source_context_subset_union",
            "_target_context",
        }
        moved_symbols = set(compatibility.__all__) | legacy_private_symbols
        self.assertEqual(set(vars(data_contracts)) & moved_symbols, set())

    def test_extracted_contract_expansion_has_no_data_reexports(self):
        data_contracts = importlib.import_module("engine.contracts.data")
        expansion = importlib.import_module(
            "engine.contracts.contract_expansion"
        )
        legacy_private_symbols = {
            "_literal_data_key_schema",
            "_required_path_schema",
            "_schema_child_schema",
            "_schema_explicit_child_names",
            "_schema_intersection",
            "_schema_union",
        }
        moved_symbols = set(expansion.__all__) | legacy_private_symbols
        self.assertEqual(set(vars(data_contracts)) & moved_symbols, set())

    def test_extracted_contract_reducer_has_no_data_reexports(self):
        data_contracts = importlib.import_module("engine.contracts.data")
        reducer = importlib.import_module("engine.contracts.contract_reducer")
        legacy_private_symbols = {
            "_created_path_schema",
            "_optional_schema_union",
            "_require_write_structure_compatibility",
            "_schema_structure_categories",
            "_write_nested_schema",
        }
        moved_symbols = set(reducer.__all__) | legacy_private_symbols
        self.assertEqual(set(vars(data_contracts)) & moved_symbols, set())

    def test_backtest_job_repository_is_the_only_jobs_sql_owner(self):
        violations = []
        jobs_root = TARGET_ENGINE_ROOT / "jobs"
        for path in sorted(jobs_root.glob("*.py")):
            if path.name == "repository.py":
                continue
            for node in ast.walk(_parse(path)):
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr in {
                        "connect_database",
                        "prepare_database",
                        "execute",
                        "executemany",
                        "executescript",
                    }
                ):
                    violations.append(
                        f"{path.relative_to(ROOT)}:{node.lineno}:{node.func.attr}"
                    )
        self.assertEqual(violations, [])

    def test_writer_launchers_use_the_shared_outer_subreaper(self):
        violations = []
        jobs_root = TARGET_ENGINE_ROOT / "jobs"
        for path in sorted(jobs_root.glob("*.py")):
            for node in ast.walk(_parse(path)):
                if isinstance(node, (ast.Import, ast.ImportFrom)):
                    imported = (
                        [alias.name for alias in node.names]
                        if isinstance(node, ast.Import)
                        else [node.module or ""]
                    )
                    if any(name == "subprocess" for name in imported):
                        violations.append(
                            f"{path.relative_to(ROOT)}:{node.lineno}:subprocess"
                        )
        self.assertEqual(violations, [])
        writer_launchers = {
            "engine/runtime/dataset_build.py": "dataset:",
            "engine/runtime/jupyter_workspace.py": "jupyter:",
            "engine/runtime/result_runtime.py": "result:",
            "engine/worker/backtest_supervisor.py": "backtest:",
        }
        discovered = {}
        for path in _engine_source_files():
            starts = [
                node
                for node in ast.walk(_parse(path))
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "start"
                    and isinstance(node.func.value, ast.Attribute)
                    and node.func.value.attr == "PROCESS_SESSIONS"
                )
            ]
            if starts:
                discovered[str(path.relative_to(ROOT))] = len(starts)
        self.assertEqual(
            discovered,
            {relative: 1 for relative in writer_launchers},
        )
        for relative, prefix in writer_launchers.items():
            with self.subTest(path=relative):
                path = ROOT / relative
                source = path.read_text(encoding="utf-8")
                self.assertIn(prefix, source)
                self.assertFalse(any(
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "Popen"
                    for node in ast.walk(_parse(path))
                ))
        supervisor_source = (
            ROOT / "engine/worker/backtest_supervisor.py"
        ).read_text(encoding="utf-8")
        self.assertIn("engine.worker.backtest_runtime", supervisor_source)
        self.assertNotIn("backtest_runtime_worker.py", supervisor_source)
        process_session_source = (
            ROOT / "engine/runtime/process_session.py"
        ).read_text(encoding="utf-8")
        self.assertIn('"pass_fds": tuple(supervisor_pass_fds)', process_session_source)
        self.assertIn(
            "primary_type.__init__(primary, command, close_fds=True)",
            process_session_source,
        )
        self.assertNotIn("parentPid", (
            ROOT / "engine/worker/backtest_runtime.py"
        ).read_text(encoding="utf-8"))
        self.assertNotIn("parentPid", (
            ROOT / "engine/worker/result_runtime.py"
        ).read_text(encoding="utf-8"))
        result_runtime_source = (
            ROOT / "engine/runtime/result_runtime.py"
        ).read_text(encoding="utf-8")
        self.assertIn("engine.worker.result_verifier", result_runtime_source)
        self.assertIn("verify_result_archive_in_runtimes", result_runtime_source)
        self.assertNotIn("subprocess", (
            ROOT / "engine/service/backtest_results.py"
        ).read_text(encoding="utf-8"))
        popen_owners = {}
        for path in _engine_source_files():
            references = [
                node
                for node in ast.walk(_parse(path))
                if (
                    isinstance(node, ast.Attribute)
                    and node.attr == "Popen"
                    and isinstance(node.value, ast.Name)
                    and node.value.id == "subprocess"
                )
            ]
            if references:
                popen_owners[str(path.relative_to(ROOT))] = len(references)
        self.assertEqual(
            popen_owners,
            {
                "engine/runtime/process_session.py": 2,
                "engine/runtime/process_module_adapter.py": 1,
                "engine/runtime/process_module_supervisor.py": 1,
                "engine/runtime/sampler_process.py": 1,
            },
        )

    def test_engine_has_no_strategy_implementation_dependency(self):
        violations = []
        for path in _engine_source_files():
            tree = _parse(path)
            for imported in sorted(_imports(path)):
                if imported == "strategies" or imported.startswith("strategies."):
                    violations.append(
                        f"{path.relative_to(ROOT)} imports strategy implementation {imported}"
                    )
                if imported == "vendor" or imported.startswith("vendor."):
                    violations.append(
                        f"{path.relative_to(ROOT)} imports external strategy {imported}"
                    )
                if (
                    str(path.relative_to(ROOT)).startswith("engine/")
                    and (
                        imported == "builtin_implementations"
                        or imported.startswith("builtin_implementations.")
                    )
                ):
                    violations.append(
                        f"{path.relative_to(ROOT)} imports product BuiltIns {imported}"
                    )
        self.assertEqual(violations, [])

    def test_engine_wall_clock_has_one_authority(self):
        violations = []
        authority = Path("engine/core/clock.py")
        for path in _engine_source_files():
            relative = path.relative_to(ROOT)
            if relative == authority:
                continue
            for node in ast.walk(_parse(path)):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in {
                    "utc_now",
                    "_utc_now",
                }:
                    violations.append(f"{relative}:{node.lineno}: duplicate {node.name}")
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "now"
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id == "datetime"
                ):
                    violations.append(
                        f"{relative}:{node.lineno}: direct datetime.now bypasses Engine clock"
                    )
        self.assertEqual(violations, [])

    def test_deleted_compatibility_protocols_stay_absent(self):
        violations = []
        for path in _engine_source_files():
            tree = _parse(path)
            for node in ast.walk(tree):
                values = []
                if isinstance(node, ast.Name):
                    values.append(node.id)
                elif isinstance(node, ast.Attribute):
                    values.append(node.attr)
                elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                    values.append(node.value)
                for value in values:
                    for token in FORBIDDEN_COMPATIBILITY_TOKENS:
                        if token in value:
                            violations.append(
                                f"{path.relative_to(ROOT)}:{getattr(node, 'lineno', 0)}: {token}"
                            )
        self.assertEqual(violations, [])

    def test_authority_bound_runtime_raw_construction_stays_zero(self):
        violations = []
        for path in _engine_source_files():
            for node in ast.walk(_parse(path)):
                if not isinstance(node, ast.Call):
                    continue
                if isinstance(node.func, ast.Name):
                    name = node.func.id
                elif isinstance(node.func, ast.Attribute):
                    name = node.func.attr
                else:
                    continue
                if name in AUTHORITY_BOUND_RAW_CONSTRUCTORS:
                    violations.append(f"{path.relative_to(ROOT)}:{node.lineno}: {name}(...)")
        self.assertEqual(violations, [])
