import ast
import importlib.util
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
ARCHITECTURE_SPEC = ROOT / "docs" / "architecture.md"
TARGET_ENGINE_ROOT = ROOT / "engine"


__all__ = (
    "ARCHITECTURE_SPEC",
    "ROOT",
    "TARGET_ENGINE_ROOT",
    "domain",
    "engine_source_files",
    "imported_modules",
    "legacy_boundary_imports",
    "legacy_private_accesses",
    "module_name",
    "parse_source",
    "resolve_import_from",
    "silent_handlers",
    "target_files",
    "target_import_graph",
    "target_layer",
)


def parse_source(path):
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def target_files():
    if not TARGET_ENGINE_ROOT.exists():
        return []
    return sorted(TARGET_ENGINE_ROOT.rglob("*.py"))


def engine_source_files():
    files = {
        path
        for path in ROOT.glob("*.py")
        if not path.name.startswith("test_")
    }
    for package in (
        "engine",
        "dataset_adapters",
        "builtin_implementations",
        "strategy_devkit",
    ):
        package_root = ROOT / package
        if package_root.exists():
            files.update(package_root.rglob("*.py"))
    return sorted(files)


def module_name(path):
    relative = path.relative_to(ROOT).with_suffix("")
    parts = list(relative.parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def resolve_import_from(path, node):
    if node.level == 0:
        return node.module or ""
    source_module = module_name(path)
    package = (
        source_module
        if path.name == "__init__.py"
        else source_module.rpartition(".")[0]
    )
    relative_name = "." * node.level + (node.module or "")
    return importlib.util.resolve_name(relative_name, package)


def imported_modules(path):
    modules = set()
    for node in ast.walk(parse_source(path)):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            module = resolve_import_from(path, node)
            if module:
                modules.add(module)
                if module == "engine":
                    modules.update(
                        f"engine.{alias.name}"
                        for alias in node.names
                        if (TARGET_ENGINE_ROOT / alias.name).is_dir()
                    )
    return modules


def target_import_graph():
    paths = {path: module_name(path) for path in target_files()}
    known_modules = set(paths.values())
    graph = {module: set() for module in known_modules}
    for path, source in paths.items():
        for node in ast.walk(parse_source(path)):
            candidates = []
            if isinstance(node, ast.Import):
                candidates.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                module = resolve_import_from(path, node)
                candidates.append(module)
                candidates.extend(f"{module}.{alias.name}" for alias in node.names)
            for candidate in candidates:
                if candidate in known_modules and candidate != source:
                    graph[source].add(candidate)
    return graph


def target_layer(module):
    parts = module.split(".")
    return parts[1] if len(parts) > 1 and parts[0] == "engine" else None


def domain(module, dependency_domains):
    for part in module.split("."):
        for domain_name in dependency_domains:
            if part == domain_name or part.startswith(domain_name + "_"):
                return domain_name
    return None


def _local_module_names():
    return {path.stem for path in ROOT.glob("*.py")}


def legacy_private_accesses():
    local_modules = _local_module_names()
    findings = Counter()
    for path in sorted(ROOT.glob("*.py")):
        if path.name.startswith("test_"):
            continue
        tree = parse_source(path)
        module_aliases = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imported = alias.name.split(".")[0]
                    if imported in local_modules:
                        module_aliases[alias.asname or imported] = imported
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported = node.module.split(".")[0]
                if imported in local_modules:
                    for alias in node.names:
                        if alias.name.startswith("_"):
                            key = f"{path.name}:{imported}.{alias.name}"
                            findings[key] += 1
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Attribute)
                and node.attr.startswith("_")
                and isinstance(node.value, ast.Name)
                and node.value.id in module_aliases
            ):
                key = f"{path.name}:{module_aliases[node.value.id]}.{node.attr}"
                findings[key] += 1
    return findings


def legacy_boundary_imports(dangerous_import_targets):
    findings = set()
    for source, forbidden_targets in dangerous_import_targets.items():
        path = ROOT / f"{source}.py"
        if not path.exists():
            continue
        imported_roots = {
            module.split(".")[0]
            for module in imported_modules(path)
        }
        findings.update(
            (source, target)
            for target in imported_roots & forbidden_targets
        )
    return frozenset(findings)


class _SilentHandlerVisitor(ast.NodeVisitor):
    def __init__(self, path):
        self.path = path
        self.scope = []
        self.findings = Counter()

    def visit_ClassDef(self, node):
        self.scope.append(node.name)
        self.generic_visit(node)
        self.scope.pop()

    def visit_FunctionDef(self, node):
        self.scope.append(node.name)
        self.generic_visit(node)
        self.scope.pop()

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_ExceptHandler(self, node):
        if len(node.body) == 1 and isinstance(node.body[0], ast.Pass):
            exception = ast.unparse(node.type) if node.type is not None else "bare"
            scope = ".".join(self.scope) or "<module>"
            self.findings[f"{self.path.name}:{scope}:{exception}"] += 1
        self.generic_visit(node)


def silent_handlers(path):
    visitor = _SilentHandlerVisitor(path)
    visitor.visit(parse_source(path))
    return visitor.findings
