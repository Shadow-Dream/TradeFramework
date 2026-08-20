"""Application component-library contracts independent of any one strategy."""

from __future__ import annotations

import math
import unittest

from application_components.basic_workflow.account import (
    long_only_aggregate_account,
    long_only_mark_to_market_account,
    marked_position_account,
)
from application_components.basic_workflow.brokerage import (
    apply_cash_buying_power,
    apply_leverage_limit,
    apply_shortable_rule,
    constant_bps_fill_price,
    fixed_plus_bps_fee,
    immediate_settlement,
    proportional_fill,
    round_target_to_lot,
    target_delta,
)
from application_components.basic_workflow.catalog import (
    COMPONENT_IDS,
    component_catalog,
)
from application_components.basic_workflow.performance import (
    annualized_return,
    sample_return_statistics,
)


class BasicWorkflowComponentLibraryTests(unittest.TestCase):
    def test_catalog_exposes_account_broker_and_analysis_modules(self):
        catalog = component_catalog()
        self.assertEqual(set(catalog), {"account", "brokerRule", "analysis"})
        for category, module_ids in COMPONENT_IDS.items():
            self.assertEqual(
                [item["moduleId"] for item in catalog[category]],
                list(module_ids),
            )
            self.assertTrue(all(item["ports"] for item in catalog[category]))
        self.assertIn(
            "performance-metrics-analyzer",
            {item["moduleId"] for item in catalog["analysis"]},
        )

    def test_brokerage_kernels_compose_without_strategy_identity(self):
        target = round_target_to_lot(3.26, 0.1)
        target = apply_shortable_rule(target, False)
        target = apply_leverage_limit(target, 1000.0, 100.0, 0.2)
        self.assertEqual(target, 2.0)
        self.assertEqual(apply_cash_buying_power(-12.0, 1000.0, 100.0), -10.0)
        delta = target_delta(target, 0.5, 1.0)
        self.assertEqual(delta["requestedQuantity"], 1.5)
        self.assertEqual(delta["approvedQuantity"], 1.0)
        fill_price = constant_bps_fill_price(1.0, 100.0, 10.0)
        self.assertAlmostEqual(fill_price, 100.1)
        fill = proportional_fill(1.0, fill_price, 0.5)
        self.assertEqual(fill["status"], "partially-filled")
        fee = fixed_plus_bps_fee(fill["filledQuantity"], fill["notional"], 1.0, 10.0)
        settlement = immediate_settlement(fill["notional"], fill["filledQuantity"], fee)
        self.assertAlmostEqual(settlement["positionDelta"], 0.5)
        self.assertAlmostEqual(settlement["cashDelta"], -(50.05 + 1.05005))

    def test_performance_kernels_define_annualization_and_sharpe(self):
        result = sample_return_statistics(2, 0.05, 0.0125, 252.0, 0.0)
        deviation = math.sqrt(0.01125)
        self.assertAlmostEqual(
            result["annualizedVolatility"],
            deviation * math.sqrt(252.0),
        )
        self.assertAlmostEqual(
            result["sharpeRatio"],
            0.025 / deviation * math.sqrt(252.0),
        )
        self.assertAlmostEqual(annualized_return(0.1, 365.2425 * 86400), 0.1)

    def test_account_kernels_cover_instruments_and_independent_positions(self):
        self.assertEqual(
            marked_position_account(100.0, {"AAA": 2.0}, {"AAA": 12.5}),
            {"cash": 100.0, "positions": {"AAA": 2.0}, "equity": 125.0},
        )
        account = long_only_mark_to_market_account(
            1000.0,
            {
                "episode-a": {"openLots": 1.0, "averageFillPrice": 90.0},
                "episode-b": {"openLots": 2.0, "averageFillPrice": 95.0},
            },
            100.0,
        )
        self.assertEqual(account["positions"], {"episode-a": 1.0, "episode-b": 2.0})
        self.assertEqual(account["equity"], 1020.0)
        aggregate = long_only_aggregate_account(
            1000.0, "AAA", 3.0, 280.0, 100.0
        )
        self.assertEqual(aggregate["positions"], {"AAA": 3.0})
        self.assertEqual(aggregate["equity"], 1020.0)

if __name__ == "__main__":
    unittest.main()
