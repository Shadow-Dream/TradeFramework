import ast
import unittest
from collections import Counter
from pathlib import Path

from tests.support.architecture_scan import (
    ROOT,
    engine_source_files as _engine_source_files,
    parse_source as _parse,
    resolve_import_from as _resolve_import_from,
)


class AuthorityIssuerArchitectureTests(unittest.TestCase):
    maxDiff = None

    def test_runtime_identity_has_one_synchronous_worker_path(self):
        from engine.worker import backtest_execution

        self.assertEqual(backtest_execution.__all__.count("execute_backtest"), 1)
        fork_calls = []
        runtime_entry_calls = []
        execution_barrier_calls = []

        class Visitor(ast.NodeVisitor):
            def __init__(self, relative):
                self.relative = relative
                self.scope = ["<module>"]

            def visit_FunctionDef(self, node):
                self.scope.append(node.name)
                self.generic_visit(node)
                self.scope.pop()

            visit_AsyncFunctionDef = visit_FunctionDef

            def visit_Call(self, node):
                function = node.func
                if (
                    isinstance(function, ast.Attribute)
                    and isinstance(function.value, ast.Name)
                ):
                    reference = f"{self.relative}:{self.scope[-1]}"
                    if function.value.id == "os" and function.attr == "fork":
                        fork_calls.append(reference)
                    elif (
                        function.value.id in {
                            "backtest_execution",
                            "backtest_worker",
                        }
                        and function.attr == "execute_backtest"
                    ):
                        runtime_entry_calls.append(reference)
                    elif (
                        self.relative == "engine/worker/backtest_execution.py"
                        and self.scope[-1] == "_execute_backtest"
                        and function.value.id == "backtest_preparation"
                        and function.attr == "prepare_backtest_execution"
                    ):
                        execution_barrier_calls.append(
                            (function.attr, node.lineno)
                        )
                elif (
                    isinstance(function, ast.Name)
                    and self.relative == "engine/worker/backtest_execution.py"
                    and self.scope[-1] == "_execute_backtest"
                    and function.id in {
                        "engine_runtime_identity",
                        "prepare_backtest_execution",
                    }
                ):
                    execution_barrier_calls.append((function.id, node.lineno))
                self.generic_visit(node)

        for path in _engine_source_files():
            Visitor(str(path.relative_to(ROOT))).visit(_parse(path))
        self.assertEqual(fork_calls, [])
        self.assertEqual(
            sorted(runtime_entry_calls),
            [
                "engine/service/backtest_execution.py:run_backtest",
                "engine/worker/backtest_runtime.py:main",
            ],
        )
        self.assertEqual(
            [name for name, _line in execution_barrier_calls],
            ["engine_runtime_identity", "prepare_backtest_execution"],
        )
        self.assertLess(
            execution_barrier_calls[0][1],
            execution_barrier_calls[1][1],
        )

    def test_graph_authority_seal_is_an_internal_compiler_bridge(self):
        from engine.authority import graph as graph_authority
        from engine.compiler import graph as graph_compiler

        self.assertNotIn(
            "seal_compiled_graph_authority",
            graph_authority.__all__,
        )
        self.assertFalse(
            hasattr(graph_compiler, "seal_compiled_graph_authority")
        )
        imports = []
        references = []
        for path in _engine_source_files():
            if path.name.startswith("test_"):
                continue
            tree = _parse(path)
            imported_names = set()
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.ImportFrom)
                    and _resolve_import_from(path, node) == "engine.authority.graph"
                ):
                    for alias in node.names:
                        if alias.name == "seal_compiled_graph_authority":
                            imported_names.add(alias.asname or alias.name)
                            imports.append(str(path.relative_to(ROOT)))
                elif (
                    isinstance(node, ast.ImportFrom)
                    and _resolve_import_from(path, node)
                    == "engine.compiler.graph"
                    and any(
                        alias.name in {
                            "seal_compiled_graph_authority",
                            "_seal_compiled_graph_authority",
                        }
                        for alias in node.names
                    )
                ):
                    references.append(
                        f"{path.relative_to(ROOT)}:<module>:compiler-reexport"
                    )

            class SealReferenceVisitor(ast.NodeVisitor):
                def __init__(self):
                    self.scope = ["<module>"]

                def visit_FunctionDef(self, node):
                    self.scope.append(node.name)
                    self.generic_visit(node)
                    self.scope.pop()

                visit_AsyncFunctionDef = visit_FunctionDef

                def visit_Name(self, node):
                    if isinstance(node.ctx, ast.Load) and node.id in imported_names:
                        references.append(
                            f"{path.relative_to(ROOT)}:{self.scope[-1]}"
                        )

                def visit_Attribute(self, node):
                    if (
                        node.attr in {
                            "seal_compiled_graph_authority",
                            "_seal_compiled_graph_authority",
                        }
                    ):
                        references.append(
                            f"{path.relative_to(ROOT)}:{self.scope[-1]}"
                        )
                    self.generic_visit(node)

                def visit_Constant(self, node):
                    if (
                        node.value in {
                            "seal_compiled_graph_authority",
                            "_seal_compiled_graph_authority",
                        }
                        and path.relative_to(ROOT)
                        != Path("engine/authority/graph.py")
                    ):
                        references.append(
                            f"{path.relative_to(ROOT)}:{self.scope[-1]}:dynamic"
                        )

            SealReferenceVisitor().visit(tree)
        self.assertEqual(imports, ["engine/compiler/graph.py"])
        self.assertEqual(
            Counter(references),
            Counter({
                "engine/compiler/graph.py:compile_module_graph_authority": 1,
                "engine/compiler/graph.py:compile_verified_module_graph_authority": 1,
            }),
        )

    def test_pipeline_plan_seal_has_only_complete_compiler_call_sites(self):
        from engine.authority import pipeline as pipeline_authority
        from engine.compiler import pipeline as pipeline_compiler

        symbol = "seal_pipeline_contract_plan_authority"
        internal_symbol = "_seal_pipeline_contract_plan_authority"
        removed_public_symbol = "bind_pipeline_contract_plan_authority"
        self.assertNotIn(symbol, pipeline_authority.__all__)
        self.assertFalse(hasattr(pipeline_authority, removed_public_symbol))
        self.assertFalse(hasattr(pipeline_compiler, symbol))
        self.assertFalse(hasattr(pipeline_compiler, removed_public_symbol))
        imports = []
        references = []
        for path in _engine_source_files():
            if path.name.startswith("test_"):
                continue
            tree = _parse(path)
            imported_names = set()
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.ImportFrom)
                    and _resolve_import_from(path, node)
                    == "engine.authority.pipeline"
                ):
                    for alias in node.names:
                        if alias.name in {symbol, removed_public_symbol}:
                            imported_names.add(alias.asname or alias.name)
                            imports.append(str(path.relative_to(ROOT)))
                elif (
                    isinstance(node, ast.ImportFrom)
                    and _resolve_import_from(path, node)
                    == "engine.compiler.pipeline"
                    and any(
                        alias.name in {
                            symbol,
                            internal_symbol,
                            removed_public_symbol,
                        }
                        for alias in node.names
                    )
                ):
                    references.append(
                        f"{path.relative_to(ROOT)}:<module>:compiler-reexport"
                    )

            class SealReferenceVisitor(ast.NodeVisitor):
                def __init__(self):
                    self.scope = ["<module>"]

                def visit_FunctionDef(self, node):
                    self.scope.append(node.name)
                    self.generic_visit(node)
                    self.scope.pop()

                visit_AsyncFunctionDef = visit_FunctionDef

                def visit_Name(self, node):
                    if (
                        isinstance(node.ctx, ast.Load)
                        and node.id in imported_names
                    ):
                        references.append(
                            f"{path.relative_to(ROOT)}:{self.scope[-1]}"
                        )

                def visit_Attribute(self, node):
                    if node.attr in {
                        symbol,
                        internal_symbol,
                        removed_public_symbol,
                    }:
                        references.append(
                            f"{path.relative_to(ROOT)}:{self.scope[-1]}"
                        )
                    self.generic_visit(node)

                def visit_Constant(self, node):
                    if (
                        node.value in {
                            symbol,
                            internal_symbol,
                            removed_public_symbol,
                        }
                        and path.relative_to(ROOT)
                        != Path("engine/authority/pipeline.py")
                    ):
                        references.append(
                            f"{path.relative_to(ROOT)}:{self.scope[-1]}:dynamic"
                        )

            SealReferenceVisitor().visit(tree)
        self.assertEqual(imports, ["engine/compiler/pipeline.py"])
        self.assertEqual(
            Counter(references),
            Counter({
                "engine/compiler/pipeline.py:bind_validated_pipeline_contract_plan": 1,
            }),
        )

    def test_validated_pipeline_plan_proof_has_one_artifact_gate_issuer(self):
        from engine.authority import pipeline as pipeline_authority

        symbol = "seal_validated_pipeline_plan_authority"
        self.assertNotIn(symbol, pipeline_authority.__all__)
        references = []
        for path in _engine_source_files():
            if path.name.startswith("test_"):
                continue
            tree = _parse(path)
            imported_names = set()
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.ImportFrom)
                    and _resolve_import_from(path, node)
                    == "engine.authority.pipeline"
                ):
                    for alias in node.names:
                        if alias.name == symbol:
                            imported_names.add(alias.asname or alias.name)

            class ReferenceVisitor(ast.NodeVisitor):
                def __init__(self):
                    self.scope = ["<module>"]

                def visit_FunctionDef(self, node):
                    self.scope.append(node.name)
                    self.generic_visit(node)
                    self.scope.pop()

                visit_AsyncFunctionDef = visit_FunctionDef

                def visit_Name(self, node):
                    if (
                        isinstance(node.ctx, ast.Load)
                        and node.id in imported_names
                    ):
                        references.append(
                            f"{path.relative_to(ROOT)}:{self.scope[-1]}"
                        )

                def visit_Attribute(self, node):
                    if node.attr == symbol:
                        references.append(
                            f"{path.relative_to(ROOT)}:{self.scope[-1]}"
                        )
                    self.generic_visit(node)

                def visit_Constant(self, node):
                    if (
                        node.value == symbol
                        and path.relative_to(ROOT)
                        != Path("engine/authority/pipeline.py")
                    ):
                        references.append(
                            f"{path.relative_to(ROOT)}:{self.scope[-1]}:dynamic"
                        )

            ReferenceVisitor().visit(tree)
        self.assertEqual(
            references,
            [
                "engine/composition/backtest.py:"
                "validate_backtest_composition_artifact"
            ],
        )

    def test_frozen_composition_graph_binder_has_only_verified_call_sites(self):
        from engine.authority import graph as graph_authority
        from engine.compiler import graph as graph_compiler

        symbol = "bind_frozen_composition_graph_authority"
        self.assertNotIn(symbol, graph_authority.__all__)
        self.assertFalse(hasattr(graph_compiler, symbol))
        self.assertFalse(hasattr(graph_compiler, "validate_compiled_graph"))
        references = []
        for path in _engine_source_files():
            if path.name.startswith("test_"):
                continue
            tree = _parse(path)
            imported_names = set()
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.ImportFrom)
                    and _resolve_import_from(path, node) == "engine.authority.graph"
                ):
                    for alias in node.names:
                        if alias.name == symbol:
                            imported_names.add(alias.asname or alias.name)

            class BinderReferenceVisitor(ast.NodeVisitor):
                def __init__(self):
                    self.scope = ["<module>"]

                def visit_FunctionDef(self, node):
                    self.scope.append(node.name)
                    self.generic_visit(node)
                    self.scope.pop()

                visit_AsyncFunctionDef = visit_FunctionDef

                def visit_Name(self, node):
                    if isinstance(node.ctx, ast.Load) and node.id in imported_names:
                        references.append(
                            f"{path.relative_to(ROOT)}:{self.scope[-1]}"
                        )

                def visit_Attribute(self, node):
                    if node.attr == symbol:
                        references.append(
                            f"{path.relative_to(ROOT)}:{self.scope[-1]}"
                        )
                    self.generic_visit(node)

                def visit_Constant(self, node):
                    if (
                        node.value == symbol
                        and path.relative_to(ROOT)
                        != Path("engine/authority/graph.py")
                    ):
                        references.append(
                            f"{path.relative_to(ROOT)}:{self.scope[-1]}:dynamic"
                        )

            BinderReferenceVisitor().visit(tree)
        self.assertEqual(
            Counter(references),
            Counter({
                "engine/compiler/pipeline.py:bind_validated_pipeline_contract_plan": 1,
                "engine/composition/backtest.py:bind_frozen_backtest_composition": 2,
            }),
        )

    def test_validated_module_input_seal_is_owned_by_graph_runtime(self):
        from engine.runtime import graph as graph_runtime
        from engine.runtime import module_invoker

        symbol = "seal_runtime_validated_module_inputs"
        internal_symbol = "_seal_runtime_validated_module_inputs"
        consumer_symbol = "invoke_validated"
        self.assertNotIn(symbol, module_invoker.__all__)
        self.assertFalse(hasattr(graph_runtime, symbol))
        self.assertNotIn(internal_symbol, graph_runtime.__all__)
        imports = []
        references = []
        consumers = []
        for path in _engine_source_files():
            if path.name.startswith("test_"):
                continue
            tree = _parse(path)
            imported_names = set()
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.ImportFrom)
                    and _resolve_import_from(path, node)
                    == "engine.runtime.module_invoker"
                ):
                    for alias in node.names:
                        if alias.name == symbol:
                            imported_names.add(alias.asname or alias.name)
                            imports.append(str(path.relative_to(ROOT)))
                elif (
                    isinstance(node, ast.ImportFrom)
                    and _resolve_import_from(path, node)
                    == "engine.runtime.graph"
                ):
                    for alias in node.names:
                        if alias.name in {symbol, internal_symbol}:
                            references.append(
                                f"{path.relative_to(ROOT)}:<module>:graph-reexport"
                            )
            class SealReferenceVisitor(ast.NodeVisitor):
                def __init__(self):
                    self.scope = ["<module>"]

                def visit_FunctionDef(self, node):
                    self.scope.append(node.name)
                    self.generic_visit(node)
                    self.scope.pop()

                visit_AsyncFunctionDef = visit_FunctionDef

                def visit_Name(self, node):
                    if isinstance(node.ctx, ast.Load) and node.id in imported_names:
                        references.append(
                            f"{path.relative_to(ROOT)}:{self.scope[-1]}"
                        )

                def visit_Attribute(self, node):
                    if node.attr in {symbol, internal_symbol}:
                        references.append(
                            f"{path.relative_to(ROOT)}:{self.scope[-1]}"
                        )
                    if node.attr == consumer_symbol:
                        consumers.append(
                            f"{path.relative_to(ROOT)}:{self.scope[-1]}"
                        )
                    self.generic_visit(node)

                def visit_Constant(self, node):
                    if (
                        node.value in {symbol, internal_symbol}
                        and path.relative_to(ROOT)
                        != Path("engine/runtime/module_invoker.py")
                    ):
                        references.append(
                            f"{path.relative_to(ROOT)}:{self.scope[-1]}:dynamic"
                        )
                    if (
                        node.value == consumer_symbol
                        and path.relative_to(ROOT)
                        != Path("engine/runtime/module_invoker.py")
                    ):
                        consumers.append(
                            f"{path.relative_to(ROOT)}:{self.scope[-1]}:dynamic"
                        )

            SealReferenceVisitor().visit(tree)
        self.assertEqual(imports, ["engine/runtime/graph.py"])
        self.assertEqual(
            Counter(references),
            Counter({"engine/runtime/graph.py:_execute_slots": 1}),
        )
        self.assertEqual(
            Counter(consumers),
            Counter({"engine/runtime/graph.py:_execute_slots": 1}),
        )

    def test_validated_observation_channel_has_one_producer_and_consumer(self):
        from engine.runtime import data_proof

        self.assertNotIn("seal_validated_observation", data_proof.__all__)
        self.assertNotIn("consume_validated_observation", data_proof.__all__)
        symbols = {
            "seal_validated_observation": (
                "engine/runtime/graph_cycle.py:execute_observation"
            ),
            "consume_validated_observation": (
                "engine/runtime/pipeline.py:execute_observation"
            ),
            "bind_observation_projection_authority": (
                "engine/composition/backtest.py:create_backtest_graph_runtimes"
            ),
        }
        references = {symbol: [] for symbol in symbols}
        for path in _engine_source_files():
            if path.name.startswith("test_"):
                continue
            relative = str(path.relative_to(ROOT))
            tree = _parse(path)
            imported = {}
            for node in ast.walk(tree):
                if not isinstance(node, ast.ImportFrom):
                    continue
                for alias in node.names:
                    if alias.name in symbols:
                        imported[alias.asname or alias.name] = alias.name

            class Visitor(ast.NodeVisitor):
                def __init__(self):
                    self.scope = ["<module>"]

                def visit_FunctionDef(self, node):
                    self.scope.append(node.name)
                    self.generic_visit(node)
                    self.scope.pop()

                visit_AsyncFunctionDef = visit_FunctionDef

                def visit_Name(self, node):
                    if isinstance(node.ctx, ast.Load) and node.id in imported:
                        references[imported[node.id]].append(
                            f"{relative}:{self.scope[-1]}"
                        )

            Visitor().visit(tree)
        self.assertEqual(
            references,
            {symbol: [expected] for symbol, expected in symbols.items()},
        )

    def test_nonisolating_data_projection_is_owned_by_backtest_kernel(self):
        references = []
        for path in _engine_source_files():
            relative = path.relative_to(ROOT)
            if path.name.startswith("test_"):
                continue
            tree = _parse(path)

            class NonisolatingProjectionVisitor(ast.NodeVisitor):
                def __init__(self):
                    self.scope = ["<module>"]

                def visit_FunctionDef(self, node):
                    self.scope.append(node.name)
                    self.generic_visit(node)
                    self.scope.pop()

                visit_AsyncFunctionDef = visit_FunctionDef

                def visit_Call(self, node):
                    for keyword in node.keywords:
                        if (
                            keyword.arg == "isolate_values"
                            and isinstance(keyword.value, ast.Constant)
                            and keyword.value.value is False
                        ):
                            references.append(
                                f"{relative}:{self.scope[-1]}"
                            )
                    self.generic_visit(node)

            NonisolatingProjectionVisitor().visit(tree)
        self.assertEqual(
            Counter(references),
            Counter({
                "engine/worker/backtest_execution.py:_execute_backtest": 1,
                "engine/runtime/pipeline.py:execute_observation": 1,
            }),
        )
