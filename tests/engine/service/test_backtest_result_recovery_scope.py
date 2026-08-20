#!/usr/bin/env python3

import unittest
from contextlib import contextmanager
from unittest import mock

from engine.service import backtest_results


class BacktestResultRecoveryScopeTests(unittest.TestCase):
    def test_recovery_owns_one_bounded_contract_compilation_scope(self):
        observed = []

        @contextmanager
        def scope():
            observed.append("enter")
            try:
                yield
            finally:
                observed.append("exit")

        def recover(*_args):
            observed.append("recover")
            return {"status": "completed"}

        with (
            mock.patch.object(
                backtest_results,
                "contract_expansion_cache_scope",
                side_effect=scope,
            ),
            mock.patch.object(
                backtest_results,
                "_recover_backtest_result_catalog",
                side_effect=recover,
            ),
        ):
            result = backtest_results.recover_backtest_result_catalog(
                object(),
                "backtest-id",
                {},
            )

        self.assertEqual(result, {"status": "completed"})
        self.assertEqual(observed, ["enter", "recover", "exit"])

    def test_recovery_resets_contract_scope_after_base_exception(self):
        observed = []

        @contextmanager
        def scope():
            observed.append("enter")
            try:
                yield
            finally:
                observed.append("exit")

        with (
            mock.patch.object(
                backtest_results,
                "contract_expansion_cache_scope",
                side_effect=scope,
            ),
            mock.patch.object(
                backtest_results,
                "_recover_backtest_result_catalog",
                side_effect=KeyboardInterrupt("stop"),
            ),
            self.assertRaisesRegex(KeyboardInterrupt, "stop"),
        ):
            backtest_results.recover_backtest_result_catalog(
                object(),
                "backtest-id",
                {},
            )
        self.assertEqual(observed, ["enter", "exit"])


if __name__ == "__main__":
    unittest.main()
