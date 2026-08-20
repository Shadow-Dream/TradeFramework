import ast
import unittest
from collections import Counter

from tests.support.architecture_scan import (
    ROOT,
    TARGET_ENGINE_ROOT,
    domain as _domain,
    imported_modules as _imports,
    module_name as _module_name,
    parse_source as _parse,
    resolve_import_from as _resolve_import_from,
    silent_handlers as _silent_handlers,
    target_files as _target_files,
    target_import_graph as _target_import_graph,
    target_layer as _target_layer,
)


TARGET_ALLOWED_DEPENDENCIES = {
    "core": frozenset({"core"}),
    "contracts": frozenset({"core", "contracts"}),
    "archive": frozenset({"core", "contracts", "archive"}),
    "control": frozenset({"core", "contracts", "archive", "control"}),
    "repository": frozenset(
        {"core", "contracts", "archive", "control", "repository"}
    ),
    "authority": frozenset({"core", "contracts", "archive", "authority"}),
    "compiler": frozenset({"core", "contracts", "authority", "compiler"}),
    "runtime": frozenset({"core", "contracts", "authority", "runtime"}),
    "composition": frozenset(
        {"core", "contracts", "authority", "compiler", "runtime", "composition"}
    ),
    "worker": frozenset(
        {
            "core",
            "contracts",
            "archive",
            "authority",
            "runtime",
            "composition",
            "worker",
        }
    ),
    "jobs": frozenset(
        {
            "core",
            "contracts",
            "archive",
            "control",
            "repository",
            "authority",
            "composition",
            "worker",
            "jobs",
        }
    ),
    "service": frozenset(
        {
            "core",
            "contracts",
            "archive",
            "control",
            "repository",
            "authority",
            "compiler",
            "runtime",
            "composition",
            "worker",
            "jobs",
            "service",
        }
    ),
}


DOMAIN_DEPENDENCIES = {
    "module": frozenset({"module"}),
    "graph": frozenset({"module", "graph"}),
    "dataset": frozenset({"dataset"}),
    "sampler": frozenset({"dataset", "sampler"}),
    "pipeline": frozenset({"module", "graph", "pipeline"}),
    "environment": frozenset({"module", "graph", "environment"}),
    "analysis": frozenset({"module", "graph", "analysis"}),
    "result": frozenset({"module", "graph", "result"}),
}
CROSS_DOMAIN_LAYERS = frozenset({"composition", "worker", "jobs", "service"})


TARGET_TO_LEGACY_IMPORT_DEBT = frozenset()


LIFECYCLE_METHODS = frozenset(
    {"close", "finalize", "snapshot", "restore", "invoke", "execute"}
)


class DependencyDagArchitectureTests(unittest.TestCase):
    maxDiff = None

    def test_target_package_dependency_dag(self):
        violations = []
        for path in _target_files():
            relative = path.relative_to(TARGET_ENGINE_ROOT)
            if relative.name == "__init__.py" and len(relative.parts) == 1:
                source_layer = None
            else:
                source_layer = relative.parts[0]
                if source_layer not in TARGET_ALLOWED_DEPENDENCIES:
                    violations.append(f"{relative}: unknown target package '{source_layer}'")
                    continue
            for imported in sorted(_imports(path)):
                target_layer = _target_layer(imported)
                if target_layer is None:
                    continue
                if source_layer is None:
                    violations.append(
                        f"{relative}: engine/__init__.py must not re-export {imported}"
                    )
                elif target_layer not in TARGET_ALLOWED_DEPENDENCIES[source_layer]:
                    violations.append(
                        f"{relative}: {source_layer} must not depend on {imported}"
                    )
        self.assertEqual(violations, [])

    def test_target_to_legacy_import_debt_is_exact(self):
        target_names = set(TARGET_ALLOWED_DEPENDENCIES)
        findings = set()
        for path in _target_files():
            for imported in _imports(path):
                root_name = imported.split(".")[0]
                if root_name == "engine":
                    continue
                if (ROOT / f"{root_name}.py").is_file() or root_name in target_names:
                    findings.add((str(path.relative_to(ROOT)), imported))
        self.assertEqual(
            frozenset(findings),
            TARGET_TO_LEGACY_IMPORT_DEBT,
            "Target-to-legacy debt changed: removals update the baseline; additions are forbidden.",
        )

    def test_target_domain_boundaries(self):
        violations = []
        for path in _target_files():
            relative = path.relative_to(TARGET_ENGINE_ROOT)
            source_layer = relative.parts[0] if len(relative.parts) > 1 else None
            if source_layer in CROSS_DOMAIN_LAYERS or source_layer is None:
                continue
            source_domain = _domain(_module_name(path), DOMAIN_DEPENDENCIES)
            if source_domain is None:
                continue
            for imported in sorted(_imports(path)):
                if not imported.startswith("engine."):
                    continue
                target_domain = _domain(imported, DOMAIN_DEPENDENCIES)
                if (
                    target_domain is not None
                    and target_domain not in DOMAIN_DEPENDENCIES[source_domain]
                ):
                    violations.append(
                        f"{relative}: {source_domain} must not depend on {target_domain} ({imported})"
                    )
        self.assertEqual(violations, [])

    def test_target_import_graph_has_no_cycles(self):
        graph = _target_import_graph()
        state = {}
        stack = []
        cycles = []

        def visit(module):
            state[module] = "visiting"
            stack.append(module)
            for dependency in sorted(graph[module]):
                if state.get(dependency) == "visiting":
                    start = stack.index(dependency)
                    cycles.append(" -> ".join(stack[start:] + [dependency]))
                elif state.get(dependency) is None:
                    visit(dependency)
            stack.pop()
            state[module] = "visited"

        for module in sorted(graph):
            if state.get(module) is None:
                visit(module)
        self.assertEqual(cycles, [])

    def test_target_packages_have_no_cross_module_private_imports(self):
        violations = []
        for path in _target_files():
            tree = _parse(path)
            aliases = {}
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name.startswith("engine."):
                            aliases[alias.asname or alias.name.split(".")[0]] = alias.name
                elif isinstance(node, ast.ImportFrom):
                    module = _resolve_import_from(path, node)
                    if not module.startswith("engine"):
                        continue
                    for alias in node.names:
                        if alias.name.startswith("_"):
                            violations.append(
                                f"{path.relative_to(ROOT)}:{node.lineno}: "
                                f"private import {module}.{alias.name}"
                            )
                        aliases[alias.asname or alias.name] = f"{module}.{alias.name}"
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.Attribute)
                    and node.attr.startswith("_")
                    and isinstance(node.value, ast.Name)
                    and node.value.id in aliases
                ):
                    violations.append(
                        f"{path.relative_to(ROOT)}:{node.lineno}: "
                        f"private access {aliases[node.value.id]}.{node.attr}"
                    )
        self.assertEqual(violations, [])

    def test_backtest_execution_worker_has_no_control_plane_imports(self):
        path = TARGET_ENGINE_ROOT / "worker" / "backtest_execution.py"
        forbidden = []
        for imported in sorted(_imports(path)):
            if imported.startswith((
                "engine.repository",
                "engine.control",
                "engine.service",
            )) or (ROOT / f"{imported.split('.')[0]}.py").is_file():
                forbidden.append(imported)
        self.assertEqual(forbidden, [])

    def test_backtest_preparation_is_static_and_identity_gate_precedes_runtime(self):
        preparation_path = TARGET_ENGINE_ROOT / "worker" / "backtest_preparation.py"
        forbidden_imports = sorted(
            imported
            for imported in _imports(preparation_path)
            if imported.startswith((
                "engine.runtime",
                "engine.worker",
                "importlib",
                "os",
                "subprocess",
                "threading",
            ))
        )
        self.assertEqual(forbidden_imports, [])
        forbidden_calls = {
            "create_dataset_handle",
            "create_verified_sampler_runtime",
            "create_backtest_graph_runtimes",
            "fork",
            "materialize_verified_module_definition",
            "Popen",
        }
        observed_forbidden = []
        for node in ast.walk(_parse(preparation_path)):
            if not isinstance(node, ast.Call):
                continue
            name = (
                node.func.attr
                if isinstance(node.func, ast.Attribute)
                else node.func.id
                if isinstance(node.func, ast.Name)
                else ""
            )
            if name in forbidden_calls:
                observed_forbidden.append(f"{node.lineno}:{name}")
        self.assertEqual(observed_forbidden, [])

        execution_path = TARGET_ENGINE_ROOT / "worker" / "backtest_execution.py"
        ordered_calls = {}
        for node in ast.walk(_parse(execution_path)):
            if not isinstance(node, ast.Call):
                continue
            name = (
                node.func.attr
                if isinstance(node.func, ast.Attribute)
                else node.func.id
                if isinstance(node.func, ast.Name)
                else ""
            )
            if name in {
                "backtest_evidence_digest",
                "engine_runtime_identity",
                "prepare_backtest_execution",
                "create_dataset_handle",
                "create_verified_sampler_runtime",
            }:
                ordered_calls.setdefault(name, []).append(node.lineno)
        self.assertEqual(
            {name: len(lines) for name, lines in ordered_calls.items()},
            {
                "backtest_evidence_digest": 1,
                "engine_runtime_identity": 1,
                "prepare_backtest_execution": 1,
                "create_dataset_handle": 1,
                "create_verified_sampler_runtime": 1,
            },
        )
        line = {name: values[0] for name, values in ordered_calls.items()}
        self.assertLess(
            line["backtest_evidence_digest"],
            line["engine_runtime_identity"],
        )
        self.assertLess(
            line["engine_runtime_identity"],
            line["prepare_backtest_execution"],
        )
        self.assertLess(
            line["prepare_backtest_execution"], line["create_dataset_handle"]
        )
        self.assertLess(
            line["create_dataset_handle"],
            line["create_verified_sampler_runtime"],
        )

    def test_target_packages_have_no_silent_blanket_fallback(self):
        violations = Counter()
        for path in _target_files():
            for key, count in _silent_handlers(path).items():
                exception = key.rsplit(":", 1)[-1]
                if exception in {"bare", "Exception", "BaseException"}:
                    violations[key] += count
        self.assertEqual(violations, Counter())

    def test_target_packages_do_not_make_lifecycle_optional(self):
        violations = []
        for path in _target_files():
            for node in ast.walk(_parse(path)):
                if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
                    continue
                if node.func.id not in {"getattr", "hasattr"} or len(node.args) < 2:
                    continue
                method = node.args[1]
                if isinstance(method, ast.Constant) and method.value in LIFECYCLE_METHODS:
                    violations.append(
                        f"{path.relative_to(ROOT)}:{node.lineno}: optional {method.value}"
                    )
        self.assertEqual(violations, [])
