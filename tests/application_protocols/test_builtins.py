"""Runtime tests for Basic Workflow v2 BuiltIn resources."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from builtin_implementations import environment_presets
from builtin_implementations import resources as builtin_resources
from builtin_implementations.basic_workflow_contracts import SAMPLER_OUTPUT_SCHEMA
from builtin_implementations.environment.basic_multi_asset_bar_account import (
    BasicMultiAssetBarAccount,
)
from builtin_implementations.environment_contracts import ENVIRONMENT_MODULES
from builtin_implementations.pipeline.basic_absolute_position_map_constraint import (
    BasicAbsolutePositionMapConstraint,
)
from builtin_implementations.pipeline.basic_neutral_score_map import BasicNeutralScoreMap
from builtin_implementations.pipeline.basic_price_map_universe import BasicPriceMapUniverse
from builtin_implementations.pipeline.basic_score_map_position_target import (
    BasicScoreMapPositionTarget,
)
from builtin_implementations.pipeline_contracts import BUILTIN_PIPELINE_MODULES
from engine.control import database as engine_database
from engine.repository import graph_resources, module_definitions, samplers


PIPELINE_IDS = {
    "basic-price-map-universe",
    "basic-neutral-score-map",
    "basic-score-map-position-target",
    "basic-absolute-position-map-constraint",
}
ENVIRONMENT_ID = "basic-multi-asset-bar-account"


def _configured(module, config=None):
    module.config = dict(config or {})
    return module


def _bar(open_value, close_value):
    return {
        "open": float(open_value),
        "close": float(close_value),
        "high": float(max(open_value, close_value) + 1),
        "low": float(min(open_value, close_value) - 1),
    }


class BasicWorkflowDeclarationTests(unittest.TestCase):
    def test_protocol_modules_are_ordinary_recursive_map_declarations(self):
        pipeline = {
            item["moduleId"]: item
            for item in BUILTIN_PIPELINE_MODULES
            if item["moduleId"] in PIPELINE_IDS
        }
        environment = {
            item["moduleId"]: item
            for item in ENVIRONMENT_MODULES
            if item["moduleId"] == ENVIRONMENT_ID
        }
        self.assertEqual(set(pipeline), PIPELINE_IDS)
        self.assertEqual(set(environment), {ENVIRONMENT_ID})
        self.assertEqual(
            {item["kind"] for item in pipeline.values()},
            {"Universe", "Signal", "Target", "Constraint"},
        )
        approved = pipeline["basic-absolute-position-map-constraint"]["ports"]
        self.assertEqual(
            approved["outputs"]["approved"]["schema"]["additionalProperties"],
            {"type": "number"},
        )
        self.assertNotIn(
            "previousAccount",
            environment[ENVIRONMENT_ID]["ports"]["inputs"],
        )

    def test_pipeline_modules_exchange_multi_instrument_maps(self):
        prices = {"QQQ": _bar(20, 21), "SPY": _bar(10, 11)}
        selection = _configured(
            BasicPriceMapUniverse(), {"decisionPeriod": "day"}
        ).update({"day": prices})["selection"]
        self.assertEqual(selection, {"QQQ": True, "SPY": True})
        self.assertEqual(
            BasicNeutralScoreMap().update(selection)["scores"],
            {"QQQ": None, "SPY": None},
        )
        target = _configured(
            BasicScoreMapPositionTarget(),
            {"maximumAbsolutePosition": 10.0},
        )
        intent = target.update(selection, {"QQQ": -1.0, "SPY": 0.5})["intent"]
        self.assertEqual(intent, {"QQQ": -10.0, "SPY": 5.0})
        constraint = _configured(
            BasicAbsolutePositionMapConstraint(),
            {"maximumAbsolutePosition": 6.0},
        )
        self.assertEqual(constraint.update(intent)["approved"], {"SPY": 5.0})


class BasicWorkflowEnvironmentOracleTests(unittest.TestCase):
    def test_stateful_account_executes_prior_intent_and_marks_current_close(self):
        module = _configured(
            BasicMultiAssetBarAccount(),
            {
                "executionPeriod": "day",
                "initialCash": 1000.0,
                "fixedFee": 1.0,
                "feeBps": 100.0,
            },
        )
        first = module.update(
            "2026-01-02T21:00:00Z",
            {"day": {"QQQ": _bar(20, 19), "SPY": _bar(10, 11)}},
        )
        self.assertEqual(first["orders"], {})
        self.assertEqual(first["account"], {"cash": 1000.0, "positions": {}, "equity": 1000.0})

        second = module.update(
            "2026-01-05T21:00:00Z",
            {"day": {"QQQ": _bar(22, 21), "SPY": _bar(12, 13)}},
            {"QQQ": 1.0, "SPY": 2.0},
        )
        self.assertEqual(second["orders"]["QQQ"]["side"], "buy")
        self.assertEqual(second["orders"]["SPY"]["quantity"], 2.0)
        self.assertAlmostEqual(second["account"]["cash"], 951.54)
        self.assertEqual(second["account"]["positions"], {"QQQ": 1.0, "SPY": 2.0})
        self.assertAlmostEqual(second["account"]["equity"], 998.54)

        third = module.update(
            "2026-01-06T21:00:00Z",
            {"day": {"QQQ": _bar(24, 23), "SPY": _bar(14, 15)}},
            {"SPY": 0.0},
        )
        self.assertEqual(third["orders"], {
            "SPY": {"side": "sell", "quantity": 2.0, "price": 14.0, "fee": 1.28}
        })
        self.assertEqual(third["account"]["positions"], {"QQQ": 1.0, "SPY": 0.0})
        self.assertAlmostEqual(third["account"]["equity"], 1001.26)

    def test_execution_and_valuation_require_current_period_bars(self):
        module = _configured(
            BasicMultiAssetBarAccount(),
            {"executionPeriod": "day", "initialCash": 1000.0},
        )
        with self.assertRaisesRegex(ValueError, "execution bar is missing"):
            module.update(
                "2026-01-02T21:00:00Z",
                {"day": {"SPY": _bar(10, 11)}},
                {"QQQ": 1.0},
            )


class BasicWorkflowInstallTests(unittest.TestCase):
    def test_generic_installer_archives_v2_sampler_modules_and_environment(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = {
                "controlRoot": str(root / "control"),
                "releaseRoot": str(root / "release"),
                "liveRoot": str(root / "live"),
            }
            engine_database.prepare_database(config)
            first = builtin_resources.install(config)
            second = builtin_resources.install(config)

            sampler = samplers.get_sampler(config, "basic-price-map-sampler", "1")
            self.assertTrue(sampler["builtin"])
            self.assertEqual(sampler["outputSchema"], SAMPLER_OUTPUT_SCHEMA)
            self.assertEqual(
                [item.get("samplerId") for item in second if item.get("samplerId")],
                ["basic-price-map-sampler"],
            )
            environments = graph_resources.load_repository(config, "environment")
            environment = environments[
                environment_presets.BASIC_WORKFLOW_ENVIRONMENT_ID + "/1"
            ]
            self.assertTrue(environment["builtin"])
            self.assertEqual(
                environment["graph"]["inputs"]["previous-approved-intent-input"]["dataKey"],
                "last.intent.approved",
            )
            self.assertNotIn(
                "last.portfolio.account",
                {item["dataKey"] for item in environment["graph"]["inputs"].values()},
            )
            pipeline_definitions = module_definitions.load_pipeline_definitions(config)
            self.assertTrue(
                PIPELINE_IDS
                <= {
                    item["moduleId"]
                    for item in pipeline_definitions.values()
                    if item["builtin"]
                }
            )
            self.assertEqual(
                {item.get("version") for item in first if item.get("samplerId")},
                {"1"},
            )


if __name__ == "__main__":
    unittest.main()
