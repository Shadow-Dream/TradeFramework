import ast
import re
import unittest

from tests.support.architecture_scan import (
    ARCHITECTURE_SPEC,
    ROOT,
    TARGET_ENGINE_ROOT,
    imported_modules as _imports,
)


class ArchitectureStructureTests(unittest.TestCase):
    maxDiff = None

    def test_engine_production_modules_remain_bounded(self):
        oversized = {
            str(path.relative_to(ROOT)): len(
                path.read_text(encoding="utf-8").splitlines()
            )
            for path in sorted(TARGET_ENGINE_ROOT.rglob("*.py"))
            if len(path.read_text(encoding="utf-8").splitlines()) >= 800
        }
        self.assertEqual(oversized, {})

    def test_engine_tests_are_not_root_modules_or_private_test_dependencies(self):
        self.assertEqual(sorted(path.name for path in ROOT.glob("test_*.py")), [])
        violations = []
        for path in sorted((ROOT / "tests").rglob("test_*.py")):
            for imported in _imports(path):
                if imported.startswith("tests.") and imported.rsplit(".", 1)[-1].startswith(
                    "test_"
                ):
                    violations.append(
                        f"{path.relative_to(ROOT)} imports test module {imported}"
                    )
        self.assertEqual(violations, [])

    def test_partitioned_backtest_tests_remain_bounded(self):
        roots = tuple(
            ROOT / "tests" / "engine" / name
            for name in (
                "sampler",
                "composition",
                "worker",
                "result",
                "dataset",
                "module",
                "pipeline",
                "architecture",
            )
        )
        oversized = {
            str(path.relative_to(ROOT)): len(path.read_text(encoding="utf-8").splitlines())
            for root in roots
            for path in sorted(root.glob("test_*.py"))
            if len(path.read_text(encoding="utf-8").splitlines()) >= 800
        }
        self.assertEqual(oversized, {})

    def test_authoritative_spec_does_not_embed_private_workloads(self):
        text = ARCHITECTURE_SPEC.read_text(encoding="utf-8")
        constants = {
            name: value
            for name, value in re.findall(
                r"^(ENGINE_ARCHITECTURE_SCHEMA_VERSION)\s*=\s*([0-9.]+)$",
                text,
                flags=re.MULTILINE,
            )
        }
        self.assertEqual(
            constants,
            {"ENGINE_ARCHITECTURE_SCHEMA_VERSION": "1"},
        )
        self.assertNotIn("REFERENCE_CYCLE_COUNT", text)
        self.assertNotIn("BACKTEST_RUNTIME_WALL_LIMIT_SECONDS", text)
