from __future__ import annotations

import copy
import math
import unittest

from builtin_implementations.pipeline.anchored_vwap_indicator import (
    AnchoredVwapIndicator,
)
from builtin_implementations.pipeline.atr_indicator import AtrIndicator
from builtin_implementations.pipeline.basic_price_bar_selector import (
    BasicPriceBarSelector,
)
from builtin_implementations.pipeline.bollinger_bands_indicator import (
    BollingerBandsIndicator,
)
from builtin_implementations.pipeline.cci_indicator import CciIndicator
from builtin_implementations.pipeline.dmi_indicator import DmiIndicator
from builtin_implementations.pipeline.ema_indicator import EmaIndicator
from builtin_implementations.pipeline.ichimoku_indicator import IchimokuIndicator
from builtin_implementations.pipeline.macd_indicator import MacdIndicator
from builtin_implementations.pipeline.mfi_indicator import MfiIndicator
from builtin_implementations.pipeline.obv_indicator import ObvIndicator
from builtin_implementations.pipeline.parabolic_sar_indicator import (
    ParabolicSarIndicator,
)
from builtin_implementations.pipeline.roc_indicator import RocIndicator
from builtin_implementations.pipeline.rsi_indicator import RsiIndicator
from builtin_implementations.pipeline.sma_indicator import SmaIndicator
from builtin_implementations.pipeline.stochastic_indicator import StochasticIndicator
from builtin_implementations.pipeline.supertrend_indicator import SupertrendIndicator
from builtin_implementations.pipeline.volume_indicator import VolumeIndicator
from builtin_implementations.pipeline.vwma_indicator import VwmaIndicator
from builtin_implementations.pipeline.williams_r_indicator import WilliamsRIndicator
from builtin_implementations.pipeline.wma_indicator import WmaIndicator
from builtin_implementations.pipeline_contracts import BUILTIN_PIPELINE_MODULES
from engine.contracts.json_schema import validate_config


ARCHIVE = {"status": "archived", "contentDigest": "sha256:" + "0" * 64}
DEFINITIONS = {item["moduleId"]: item for item in BUILTIN_PIPELINE_MODULES}


def _port(required=True):
    return {"schema": {"type": ["number", "null"]}, "required": required}


def _module(implementation, module_id, config, inputs, outputs):
    implementation.initialize(
        {
            "key": "fixture.instance",
            "kind": "Signal",
            "moduleId": module_id,
            "version": "1",
            "archive": ARCHIVE,
            "config": config,
            "inputs": {name: _port() for name in inputs},
            "outputs": {name: _port() for name in outputs},
        }
    )
    return implementation


def _exact_module(implementation, module_id, config):
    definition = DEFINITIONS[module_id]
    implementation.initialize(
        {
            "key": "fixture.instance",
            "kind": "Signal",
            "moduleId": module_id,
            "version": "1",
            "archive": ARCHIVE,
            "config": config,
            "inputs": copy.deepcopy(definition["ports"]["inputs"]),
            "outputs": copy.deepcopy(definition["ports"]["outputs"]),
        }
    )
    return implementation


class TechnicalIndicatorContractTests(unittest.TestCase):
    def test_new_indicators_have_exact_strict_contracts(self):
        expected = {
            "cci-indicator": (
                {"source"},
                {"cci"},
                {"period"},
            ),
            "williams-r-indicator": (
                {"high", "low", "close"},
                {"williamsR"},
                {"period"},
            ),
            "dmi-indicator": (
                {"high", "low", "close"},
                {"plusDI", "minusDI", "adx"},
                {"diPeriod", "adxSmoothing"},
            ),
            "supertrend-indicator": (
                {"high", "low", "close"},
                {"supertrend", "direction"},
                {"atrPeriod", "multiplier"},
            ),
            "mfi-indicator": (
                {"high", "low", "close", "volume"},
                {"mfi"},
                {"period"},
            ),
            "parabolic-sar-indicator": (
                {"high", "low", "close"},
                {"sar", "direction"},
                {"start", "increment", "maximum"},
            ),
            "volume-indicator": ({"volume"}, {"volume"}, set()),
            "anchored-vwap-indicator": (
                {"source", "volume", "reset"},
                {"vwap"},
                {"anchor"},
            ),
            "ichimoku-indicator": (
                {"high", "low", "close"},
                {"conversion", "base", "spanA", "spanB", "lagging"},
                {"conversionPeriod", "basePeriod", "spanBPeriod"},
            ),
        }
        for module_id, (inputs, outputs, config) in expected.items():
            with self.subTest(module_id=module_id):
                definition = DEFINITIONS[module_id]
                self.assertEqual(definition["kind"], "Signal")
                self.assertEqual(set(definition["ports"]["inputs"]), inputs)
                self.assertEqual(set(definition["ports"]["outputs"]), outputs)
                self.assertEqual(
                    set(definition["configSchema"]["properties"]), config
                )
                self.assertFalse(definition["configSchema"]["additionalProperties"])
                with self.assertRaisesRegex(ValueError, "extra.*unexpected"):
                    validate_config(
                        {"extra": 1}, definition["configSchema"], path="config"
                    )
                if config:
                    first_field = next(iter(config))
                    first_schema = definition["configSchema"]["properties"][first_field]
                    if first_schema.get("minimum", 0) >= 1:
                        with self.assertRaises(ValueError):
                            validate_config(
                                {first_field: 0},
                                definition["configSchema"],
                                path="config",
                            )

    def test_revised_defaults_match_tradingview_forms(self):
        self.assertEqual(
            DEFINITIONS["roc-indicator"]["configSchema"]["properties"]["period"]["default"],
            9,
        )
        stochastic = DEFINITIONS["stochastic-indicator"]["configSchema"]["properties"]
        self.assertEqual(
            {name: stochastic[name]["default"] for name in stochastic},
            {"period": 14, "dPeriod": 3, "smoothKPeriod": 3},
        )
        anchor = DEFINITIONS["anchored-vwap-indicator"]["configSchema"]["properties"]["anchor"]
        self.assertEqual(anchor["default"], "month")
        self.assertEqual(anchor["enum"], ["dataset", "week", "month", "year"])
        with self.assertRaises(ValueError):
            validate_config(
                {"anchor": "day"},
                DEFINITIONS["anchored-vwap-indicator"]["configSchema"],
                path="config",
            )

    def test_price_bar_selector_requires_ohlcv_and_emits_explicit_calendar_resets(self):
        definition = DEFINITIONS["basic-price-bar-selector"]
        price_schema = definition["ports"]["inputs"]["price"]["schema"]
        bar_schema = price_schema["additionalProperties"]["additionalProperties"]
        self.assertIn("volume", bar_schema["required"])
        self.assertEqual(
            set(definition["ports"]["outputs"]),
            {
                "eventTime", "open", "high", "low", "close", "hlc3", "volume",
                "resetDataset", "resetWeek", "resetMonth", "resetYear",
            },
        )
        module = _exact_module(
            BasicPriceBarSelector(),
            "basic-price-bar-selector",
            {"decisionPeriod": "day", "instrumentId": "US-TSLA"},
        )

        def invoke(event_time, close):
            return module.invoke({
                "price": {
                    "day": {
                        "US-TSLA": {
                            "eventTime": event_time,
                            "open": close - 1,
                            "high": close + 1,
                            "low": close - 2,
                            "close": close,
                            "volume": 1000,
                        }
                    }
                }
            })

        try:
            first = invoke("2026-01-30T21:00:00Z", 100)
            month = invoke("2026-02-02T21:00:00Z", 101)
            same = invoke("2026-02-03T21:00:00Z", 102)
            year = invoke("2027-01-04T21:00:00Z", 103)
            self.assertEqual(
                [first[name] for name in (
                    "resetDataset", "resetWeek", "resetMonth", "resetYear"
                )],
                [True, True, True, True],
            )
            self.assertEqual(
                [month[name] for name in (
                    "resetDataset", "resetWeek", "resetMonth", "resetYear"
                )],
                [False, True, True, False],
            )
            self.assertEqual(
                [same[name] for name in (
                    "resetDataset", "resetWeek", "resetMonth", "resetYear"
                )],
                [False, False, False, False],
            )
            self.assertEqual(
                [year[name] for name in (
                    "resetDataset", "resetWeek", "resetMonth", "resetYear"
                )],
                [False, True, True, True],
            )
            self.assertEqual(first["hlc3"], (101 + 98 + 100) / 3)
            with self.assertRaisesRegex(ValueError, "strictly increasing"):
                invoke("2027-01-04T21:00:00Z", 104)
        finally:
            module.close()


class CommodityChannelIndexTests(unittest.TestCase):
    def test_matches_documented_formula_and_has_a_full_window_warmup(self):
        module = _module(
            CciIndicator(),
            "cci-indicator",
            {"period": 5},
            ("source",),
            ("cci",),
        )
        try:
            values = [29.0, 31.0, 30.0, 33.0, 35.0, 34.0]
            results = [module.invoke({"source": value})["cci"] for value in values]
            self.assertEqual(results[:4], [None] * 4)
            self.assertAlmostEqual(results[4], 118.055555555555, places=10)
            self.assertAlmostEqual(results[5], 55.555555555556, places=10)
        finally:
            module.close()

    def test_flat_window_and_missing_value_are_null_without_false_signal(self):
        module = _module(
            CciIndicator(),
            "cci-indicator",
            {"period": 2},
            ("source",),
            ("cci",),
        )
        try:
            self.assertIsNone(module.invoke({"source": 10})["cci"])
            self.assertIsNone(module.invoke({"source": 10})["cci"])
            self.assertIsNone(module.invoke({"source": None})["cci"])
        finally:
            module.close()


class WilliamsPercentRangeTests(unittest.TestCase):
    def test_matches_documented_formula_and_has_a_full_window_warmup(self):
        module = _module(
            WilliamsRIndicator(),
            "williams-r-indicator",
            {"period": 3},
            ("high", "low", "close"),
            ("williamsR",),
        )
        try:
            bars = [(10, 7, 8), (12, 8, 11), (15, 9, 12), (14, 10, 13)]
            results = [
                module.invoke({"high": high, "low": low, "close": close})[
                    "williamsR"
                ]
                for high, low, close in bars
            ]
            self.assertEqual(results[:2], [None, None])
            self.assertAlmostEqual(results[2], -37.5)
            self.assertAlmostEqual(results[3], -28.571428571429, places=10)
        finally:
            module.close()

    def test_zero_range_is_null_and_invalid_bar_is_rejected(self):
        module = _module(
            WilliamsRIndicator(),
            "williams-r-indicator",
            {"period": 1},
            ("high", "low", "close"),
            ("williamsR",),
        )
        try:
            self.assertIsNone(
                module.invoke({"high": 10, "low": 10, "close": 10})["williamsR"]
            )
            with self.assertRaisesRegex(ValueError, "high below low"):
                module.invoke({"high": 9, "low": 10, "close": 9.5})
        finally:
            module.close()


class DirectionalMovementIndexTests(unittest.TestCase):
    def test_wilder_seed_and_smoothing_have_explicit_null_warmup(self):
        module = _module(
            DmiIndicator(),
            "dmi-indicator",
            {"diPeriod": 3, "adxSmoothing": 2},
            ("high", "low", "close"),
            ("plusDI", "minusDI", "adx"),
        )
        try:
            bars = [
                (10, 8, 9),
                (12, 9, 11),
                (13, 10, 12),
                (12, 8, 9),
                (14, 9, 13),
                (15, 11, 12),
            ]
            results = [
                module.invoke({"high": high, "low": low, "close": close})
                for high, low, close in bars
            ]
            self.assertEqual(results[:2], [DmiIndicator._empty()] * 2)
            self.assertAlmostEqual(results[2]["plusDI"], 37.5)
            self.assertAlmostEqual(results[2]["minusDI"], 0.0)
            self.assertIsNone(results[2]["adx"])
            self.assertAlmostEqual(results[3]["plusDI"], 21.428571428571, places=10)
            self.assertAlmostEqual(results[3]["minusDI"], 21.428571428571, places=10)
            self.assertAlmostEqual(results[3]["adx"], 50.0, places=10)
            self.assertAlmostEqual(results[4]["plusDI"], 29.70297029703, places=10)
            self.assertAlmostEqual(results[4]["minusDI"], 11.881188118812, places=10)
            self.assertAlmostEqual(results[4]["adx"], 46.428571428571, places=10)
        finally:
            module.close()

    def test_flat_market_uses_zero_for_defined_zero_denominators(self):
        module = _module(
            DmiIndicator(),
            "dmi-indicator",
            {"diPeriod": 1, "adxSmoothing": 1},
            ("high", "low", "close"),
            ("plusDI", "minusDI", "adx"),
        )
        try:
            result = module.invoke({"high": 10, "low": 10, "close": 10})
            self.assertEqual(result, {"plusDI": 0.0, "minusDI": 0.0, "adx": 0.0})
        finally:
            module.close()


class RevisedLegacyIndicatorTests(unittest.TestCase):
    def test_rsi_uses_wilder_seed_and_rma(self):
        module = _exact_module(RsiIndicator(), "rsi-indicator", {"period": 3})
        try:
            results = [
                module.invoke({"price": value})["rsi"]
                for value in (10, 11, 13, 12, 14)
            ]
            self.assertEqual(results[:3], [None, None, None])
            self.assertAlmostEqual(results[3], 75.0)
            self.assertAlmostEqual(results[4], 85.714285714286, places=10)
        finally:
            module.close()


class ExistingCoreIndicatorFormulaTests(unittest.TestCase):
    def test_moving_average_family_uses_one_parameterized_module_each(self):
        cases = (
            (SmaIndicator(), "sma-indicator", "sma", 2.0),
            (WmaIndicator(), "wma-indicator", "wma", 14 / 6),
        )
        for implementation, module_id, output, expected in cases:
            with self.subTest(module_id=module_id):
                module = _exact_module(implementation, module_id, {"period": 3})
                try:
                    values = [module.invoke({"value": value})[output] for value in (1, 2, 3)]
                    self.assertEqual(values[:2], [None, None])
                    self.assertAlmostEqual(values[2], expected)
                finally:
                    module.close()

        ema = _exact_module(EmaIndicator(), "ema-indicator", {"period": 3})
        try:
            values = [ema.invoke({"value": value})["ema"] for value in (1, 2, 3)]
            self.assertEqual(values, [1, 1.5, 2.25])
        finally:
            ema.close()

    def test_vwma_and_obv_use_nonnegative_volume(self):
        vwma = _exact_module(VwmaIndicator(), "vwma-indicator", {"period": 2})
        try:
            self.assertIsNone(vwma.invoke({"price": 10, "volume": 1})["vwma"])
            self.assertEqual(vwma.invoke({"price": 20, "volume": 3})["vwma"], 17.5)
            with self.assertRaisesRegex(ValueError, "negative volume"):
                vwma.invoke({"price": 20, "volume": -1})
        finally:
            vwma.close()

        obv = _exact_module(ObvIndicator(), "obv-indicator", {})
        try:
            values = [
                obv.invoke({"close": close, "volume": volume})["obv"]
                for close, volume in ((10, 100), (11, 200), (10, 50), (10, 30))
            ]
            self.assertEqual(values, [0, 200, 150, 150])
        finally:
            obv.close()

    def test_bollinger_uses_population_deviation_and_macd_has_three_outputs(self):
        bands = _exact_module(
            BollingerBandsIndicator(),
            "bollinger-bands-indicator",
            {"period": 3, "k": 2},
        )
        try:
            values = [bands.invoke({"price": value}) for value in (1, 2, 3)]
            deviation = math.sqrt(2 / 3)
            self.assertEqual(values[:2], [{
                "middle": None,
                "upper": None,
                "lower": None,
                "bandwidth": None,
                "percentB": None,
            }] * 2)
            self.assertEqual(values[2]["middle"], 2)
            self.assertAlmostEqual(values[2]["upper"], 2 + 2 * deviation)
            self.assertAlmostEqual(values[2]["lower"], 2 - 2 * deviation)
        finally:
            bands.close()

        macd = _exact_module(
            MacdIndicator(),
            "macd-indicator",
            {"fastPeriod": 2, "slowPeriod": 3, "signalPeriod": 2},
        )
        try:
            self.assertEqual(
                macd.invoke({"price": 1}),
                {"macd": 0.0, "signal": 0.0, "histogram": 0.0},
            )
            second = macd.invoke({"price": 2})
            self.assertAlmostEqual(second["macd"], 1 / 6)
            self.assertAlmostEqual(second["signal"], 1 / 9)
            self.assertAlmostEqual(second["histogram"], 1 / 18)
        finally:
            macd.close()

    def test_atr_uses_true_range_wilder_seed_and_rma(self):
        module = _exact_module(AtrIndicator(), "atr-indicator", {"period": 3})
        try:
            bars = [(10, 8, 9), (12, 9, 11), (13, 10, 12), (12, 8, 9)]
            values = [
                module.invoke({"high": high, "low": low, "close": close})["atr"]
                for high, low, close in bars
            ]
            self.assertEqual(values[:2], [None, None])
            self.assertAlmostEqual(values[2], 8 / 3)
            self.assertAlmostEqual(values[3], 28 / 9)
        finally:
            module.close()

    def test_roc_is_percent_and_uses_exact_lag(self):
        module = _exact_module(RocIndicator(), "roc-indicator", {"period": 2})
        try:
            values = [module.invoke({"price": price})["roc"] for price in (10, 12, 15)]
            self.assertEqual(values[:2], [None, None])
            self.assertAlmostEqual(values[2], 50.0)
        finally:
            module.close()

    def test_stochastic_applies_k_then_d_smoothing(self):
        module = _exact_module(
            StochasticIndicator(),
            "stochastic-indicator",
            {"period": 3, "smoothKPeriod": 2, "dPeriod": 2},
        )
        try:
            bars = [
                (10, 0, 5),
                (12, 2, 9),
                (14, 4, 12),
                (16, 6, 15),
                (18, 8, 17),
            ]
            values = [
                module.invoke({"high": high, "low": low, "close": close})
                for high, low, close in bars
            ]
            self.assertEqual(values[:3], [{"k": None, "d": None}] * 3)
            self.assertAlmostEqual(values[3]["k"], 89.285714285714, places=10)
            self.assertIsNone(values[3]["d"])
            self.assertAlmostEqual(values[4]["k"], 92.857142857143, places=10)
            self.assertAlmostEqual(values[4]["d"], 91.071428571429, places=10)
        finally:
            module.close()


class VolumeIndicatorTests(unittest.TestCase):
    def test_mfi_uses_only_directional_typical_price_flows(self):
        module = _exact_module(MfiIndicator(), "mfi-indicator", {"period": 3})
        try:
            bars = [(11, 9, 10, 1), (12, 10, 11, 1), (10, 8, 9, 1), (13, 11, 12, 1)]
            values = [
                module.invoke({"high": h, "low": low, "close": c, "volume": volume})["mfi"]
                for h, low, c, volume in bars
            ]
            self.assertEqual(values[:2], [None, None])
            self.assertAlmostEqual(values[2], 55.0)
            self.assertAlmostEqual(values[3], 71.875)
        finally:
            module.close()

    def test_anchored_vwap_resets_only_from_explicit_input(self):
        module = _exact_module(
            AnchoredVwapIndicator(),
            "anchored-vwap-indicator",
            {"anchor": "week"},
        )
        try:
            self.assertEqual(module.invoke({"source": 10, "volume": 2, "reset": True})["vwap"], 10)
            self.assertAlmostEqual(
                module.invoke({"source": 12, "volume": 1, "reset": False})["vwap"],
                32 / 3,
            )
            self.assertEqual(module.invoke({"source": 20, "volume": 3, "reset": True})["vwap"], 20)
        finally:
            module.close()

    def test_volume_pass_through_rejects_negative_values(self):
        module = _exact_module(VolumeIndicator(), "volume-indicator", {})
        try:
            self.assertEqual(module.invoke({"volume": 123})["volume"], 123)
            with self.assertRaisesRegex(ValueError, "negative volume"):
                module.invoke({"volume": -1})
        finally:
            module.close()


class OverlayIndicatorTests(unittest.TestCase):
    def test_supertrend_initializes_down_then_uses_final_bands(self):
        module = _exact_module(
            SupertrendIndicator(),
            "supertrend-indicator",
            {"atrPeriod": 2, "multiplier": 1},
        )
        try:
            bars = [(10, 8, 9), (12, 9, 11), (14, 11, 13.5)]
            values = [
                module.invoke({"high": h, "low": low, "close": close})
                for h, low, close in bars
            ]
            self.assertEqual(values[0], {"supertrend": None, "direction": None})
            self.assertEqual(values[1], {"supertrend": 13.0, "direction": "down"})
            self.assertAlmostEqual(values[2]["supertrend"], 9.75)
            self.assertEqual(values[2]["direction"], "up")
        finally:
            module.close()

    def test_parabolic_sar_uses_two_prior_ranges_and_can_reverse(self):
        module = _exact_module(
            ParabolicSarIndicator(),
            "parabolic-sar-indicator",
            {"start": 0.02, "increment": 0.02, "maximum": 0.2},
        )
        try:
            bars = [(10, 8, 9), (12, 9, 11), (14, 10, 13), (13, 7, 8)]
            values = [
                module.invoke({"high": h, "low": low, "close": close})
                for h, low, close in bars
            ]
            self.assertEqual(values[0], {"sar": None, "direction": None})
            self.assertEqual(values[1], {"sar": 8, "direction": "up"})
            self.assertEqual(values[2], {"sar": 8, "direction": "up"})
            self.assertEqual(values[3], {"sar": 14, "direction": "down"})
        finally:
            module.close()

    def test_ichimoku_outputs_unshifted_values_for_visualizer_displacement(self):
        module = _exact_module(
            IchimokuIndicator(),
            "ichimoku-indicator",
            {"conversionPeriod": 2, "basePeriod": 3, "spanBPeriod": 4},
        )
        try:
            values = [
                module.invoke({"high": high, "low": high - 2, "close": high - 1})
                for high in (10, 12, 14, 16)
            ]
            self.assertIsNone(values[0]["conversion"])
            self.assertEqual(values[1]["conversion"], 10)
            self.assertEqual(values[2]["base"], 11)
            self.assertEqual(values[2]["spanA"], 11.5)
            self.assertEqual(values[3]["spanB"], 12)
            self.assertEqual(values[3]["lagging"], 15)
        finally:
            module.close()

if __name__ == "__main__":
    unittest.main()
