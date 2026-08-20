#!/usr/bin/env python3
"""Operation-scope contracts for Backtest composition services."""

import unittest
from unittest import mock

from engine.contracts import data_model
from engine.service import backtests as backtest_service


class BacktestCompositionServiceScopeTests(unittest.TestCase):
    def test_public_operations_own_a_bounded_compiler_scope(self):
        observed = []

        def observe_scope(_config, request):
            observed.append(
                data_model._NORMALIZED_DATA_KEY_SCHEMA_CACHE.get() is not None
            )
            return request

        for public_name, implementation_name in (
            ("validate_backtest_composition", "_validate_backtest_composition"),
            ("freeze_backtest_request", "_freeze_backtest_request"),
        ):
            with self.subTest(operation=public_name), mock.patch.object(
                backtest_service,
                implementation_name,
                side_effect=observe_scope,
            ):
                marker = {"operation": public_name}
                self.assertIs(
                    getattr(backtest_service, public_name)({}, marker),
                    marker,
                )

        self.assertEqual(observed, [True, True])
        self.assertIsNone(data_model._NORMALIZED_DATA_KEY_SCHEMA_CACHE.get())


if __name__ == "__main__":
    unittest.main()
