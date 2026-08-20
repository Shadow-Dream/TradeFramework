#!/usr/bin/env python3
"""Mechanical ownership of prepared Backtest submission issuance."""

import ast
import unittest

from tests.support.architecture_scan import ROOT, engine_source_files, parse_source


_SENSITIVE_SYMBOLS = frozenset(
    {
        "_issue",
        "_issue_prepared_submission",
        "_PREPARED_SUBMISSION_ISSUER",
    }
)


def _sensitive_references(tree, relative):
    """Find direct and string-based attempts to reach mint authority."""

    references = []

    class Visitor(ast.NodeVisitor):
        def __init__(self):
            self.scope = ["<module>"]

        def visit_FunctionDef(self, node):
            self.scope.append(node.name)
            self.generic_visit(node)
            self.scope.pop()

        visit_AsyncFunctionDef = visit_FunctionDef

        def _record(self, kind, symbol):
            references.append(
                (relative, self.scope[-1], kind, symbol)
            )

        def visit_Name(self, node):
            if (
                isinstance(node.ctx, ast.Load)
                and node.id in _SENSITIVE_SYMBOLS
            ):
                self._record("Name", node.id)
            self.generic_visit(node)

        def visit_Attribute(self, node):
            if node.attr in _SENSITIVE_SYMBOLS:
                self._record("Attribute", node.attr)
            self.generic_visit(node)

        def visit_ImportFrom(self, node):
            for alias in node.names:
                if alias.name in _SENSITIVE_SYMBOLS:
                    self._record("ImportFrom", alias.name)
            self.generic_visit(node)

        def visit_Constant(self, node):
            if isinstance(node.value, str) and node.value in _SENSITIVE_SYMBOLS:
                self._record("Constant", node.value)
            self.generic_visit(node)

    Visitor().visit(tree)
    return references


class PreparedSubmissionIssuerArchitectureTests(unittest.TestCase):
    def test_validation_route_is_pure_and_prepare_route_owns_mint_command(self):
        source = (ROOT / "engine_service.py").read_text(encoding="utf-8")
        validation_route = source.split(
            'if path == "/api/backtest-compositions/validate":', 1
        )[1].split(
            'if path == "/api/backtest-submissions/prepare":', 1
        )[0]
        prepare_route = source.split(
            'if path == "/api/backtest-submissions/prepare":', 1
        )[1].split('if path == "/api/graphs/validate":', 1)[0]

        self.assertIn("validate_backtest_composition", validation_route)
        self.assertNotIn("prepare_backtest_submission", validation_route)
        self.assertNotIn("prepared_backtest_submissions", validation_route)
        self.assertIn("prepare_backtest_submission", prepare_route)
        self.assertIn("prepared_backtest_submissions", prepare_route)

    def test_private_module_mint_has_one_complete_freeze_call_site(self):
        references = []
        freeze_calls = []
        for path in engine_source_files():
            tree = parse_source(path)
            relative = str(path.relative_to(ROOT))
            references.extend(_sensitive_references(tree, relative))

            class FreezeVisitor(ast.NodeVisitor):
                def __init__(self):
                    self.scope = ["<module>"]

                def visit_FunctionDef(self, node):
                    self.scope.append(node.name)
                    self.generic_visit(node)
                    self.scope.pop()

                visit_AsyncFunctionDef = visit_FunctionDef

                def visit_Call(self, node):
                    if (
                        isinstance(node.func, ast.Attribute)
                        and node.func.attr == "freeze_backtest_request"
                    ):
                        freeze_calls.append(
                            f"{relative}:{self.scope[-1]}"
                        )
                    self.generic_visit(node)

            FreezeVisitor().visit(tree)

        self.assertEqual(
            sorted(references),
            sorted(
                [
                    (
                        "engine/service/backtest_submissions.py",
                        "_issue_prepared_submission",
                        "Name",
                        "_PREPARED_SUBMISSION_ISSUER",
                    ),
                    (
                        "engine/service/backtest_submissions.py",
                        "prepare_backtest_submission",
                        "Name",
                        "_PREPARED_SUBMISSION_ISSUER",
                    ),
                    (
                        "engine/service/backtest_submissions.py",
                        "prepare_backtest_submission",
                        "Name",
                        "_issue_prepared_submission",
                    ),
                ]
            ),
        )
        self.assertIn(
            "engine/service/backtest_submissions.py:prepare_backtest_submission",
            freeze_calls,
        )

    def test_dynamic_and_aliased_private_mint_access_is_detected(self):
        tree = ast.parse(
            """
from engine.service.backtest_submissions import _issue as mint
direct = module._issue_prepared_submission
named = _PREPARED_SUBMISSION_ISSUER
legacy = getattr(store, "_issue")
issuer = vars(module)["_PREPARED_SUBMISSION_ISSUER"]
mint = getattr(module, "_issue_prepared_submission")
"""
        )

        references = _sensitive_references(tree, "synthetic.py")

        self.assertEqual(
            {(kind, symbol) for _, _, kind, symbol in references},
            {
                ("ImportFrom", "_issue"),
                ("Attribute", "_issue_prepared_submission"),
                ("Name", "_PREPARED_SUBMISSION_ISSUER"),
                ("Constant", "_issue"),
                ("Constant", "_PREPARED_SUBMISSION_ISSUER"),
                ("Constant", "_issue_prepared_submission"),
            },
        )


if __name__ == "__main__":
    unittest.main()
