"""Contract tests for the Basic Workflow v2 application protocol."""

from __future__ import annotations

import copy
import math
import unittest
from unittest import mock

from application_protocols.basic_workflow import schemas
from application_protocols.basic_workflow.manifest import (
    MANIFEST,
    PROFILE_ID,
    PROTOCOL_ID,
    PROTOCOL_VERSION,
)
from application_protocols.basic_workflow.registry import build_registry
from application_protocols.basic_workflow.scaffolds import (
    MODULE_REQUIREMENTS,
    build_pipeline_scaffold,
    module_requirements,
)
from application_protocols.basic_workflow.visualization_presets import (
    build_visualization_preset,
)
from builtin_implementations.pipeline_contracts import BUILTIN_PIPELINE_MODULES
from builtin_implementations.visualizer_contracts import visualizer_definition_map
from engine.authority import pipeline as pipeline_authority
from engine.compiler import pipeline as pipeline_compiler
from engine.compiler import pipeline_manifest as pipeline_manifest_compiler
from engine.compiler import visualization as visualization_compiler
from engine.contracts.module import definition_key


def _digest(number):
    return "sha256:" + f"{number:064x}"


def _module_records():
    required = {
        *MODULE_REQUIREMENTS.values(),
        *module_requirements("basic-ohlcv-price-map-universe").values(),
    }
    result = []
    for index, source in enumerate(BUILTIN_PIPELINE_MODULES, start=1):
        if (source["kind"], source["moduleId"]) not in required:
            continue
        result.append(
            {
                **copy.deepcopy(source),
                "activationMode": "PythonModule",
                "parameters": {},
                "version": str(index),
                "builtin": True,
                "status": "archived",
                "contentDigest": _digest(index),
                "createdAt": "2026-08-17T00:00:00Z",
                "archive": {
                    "resourceType": "module",
                    "resourceId": f"{source['kind']}/{source['moduleId']}",
                    "root": "/detached/test/archive",
                },
            }
        )
    return result


def _definition_map(records):
    return {
        definition_key(item["kind"], item["moduleId"], item["version"]): item
        for item in records
    }


class BasicWorkflowManifestTests(unittest.TestCase):
    def test_manifest_keeps_business_contracts_outside_engine(self):
        self.assertEqual(PROTOCOL_ID, "trade.basic-workflow")
        self.assertEqual(PROTOCOL_VERSION, "2.0.0")
        self.assertEqual(PROFILE_ID, "multi-instrument-bar-position")
        self.assertEqual(MANIFEST["sampler"]["provides"], ["time", "price"])
        self.assertEqual(
            MANIFEST["analysis"],
            {
                "inputs": "declared-by-selected-analysis-graph",
                "outputs": "declared-by-selected-analysis-graph",
            },
        )


class BasicWorkflowRegistryTests(unittest.TestCase):
    def test_registry_includes_any_basic_declaration_and_ignores_outside_records(self):
        records = [
            {
                "moduleId": "user-defined-basic-signal",
                "kind": "Signal",
                "version": "7",
                "contentDigest": _digest(1),
                "protocolId": PROTOCOL_ID,
            },
            {
                "analysisId": "user-defined-basic-analysis",
                "version": "3",
                "protocolId": PROTOCOL_ID,
            },
            {
                "pipelineId": "unbound-pipeline",
                "version": "1",
            },
            {
                "environmentId": "other-protocol-environment",
                "version": "1",
                "protocolId": "vendor.other-workflow",
            },
        ]
        registry = build_registry(records)
        self.assertEqual(
            {
                (entry["resource"]["type"], entry["resource"]["id"])
                for entry in registry
            },
            {
                ("analysis", "user-defined-basic-analysis"),
                ("module", "user-defined-basic-signal"),
            },
        )
        self.assertTrue(all(entry["protocolId"] == PROTOCOL_ID for entry in registry))
        self.assertTrue(all(set(entry) == {"protocolId", "resource"} for entry in registry))

    def test_registry_rejects_duplicate_exact_resource_but_allows_versions(self):
        base = {
            "moduleId": "user-defined-basic-signal",
            "kind": "Signal",
            "version": "1",
            "contentDigest": _digest(1),
            "protocolId": PROTOCOL_ID,
        }
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            build_registry([base, copy.deepcopy(base)])
        registry = build_registry([base, {**base, "version": "2"}])
        self.assertEqual(
            [entry["resource"]["version"] for entry in registry],
            ["1", "2"],
        )


class BasicWorkflowPipelineScaffoldTests(unittest.TestCase):
    def test_scaffold_selects_latest_module_version_and_rejects_exact_duplicates(self):
        records = _module_records()
        universe = next(
            item for item in records
            if item["moduleId"] == "basic-price-map-universe"
        )
        newer = {
            **copy.deepcopy(universe),
            "version": "2",
            "contentDigest": _digest(202),
        }
        scaffold = build_pipeline_scaffold(
            "latest-module",
            "Latest Module",
            [newer, *records],
            decision_period="day",
        )
        self.assertEqual(scaffold["instances"]["universe"]["version"], "2")

        with self.assertRaisesRegex(ValueError, "duplicate exact Module"):
            build_pipeline_scaffold(
                "duplicate-exact",
                "Duplicate Exact",
                [*records, copy.deepcopy(universe)],
                decision_period="day",
            )

    def test_scaffold_compiles_against_recursive_price_map(self):
        records = _module_records()
        scaffold = build_pipeline_scaffold(
            "basic-pipeline",
            "Basic Pipeline",
            records,
            decision_period="day",
            position_scale=2.0,
            maximum_absolute_position=3.0,
        )
        self.assertNotIn("builtin", scaffold)
        self.assertEqual(scaffold["protocolId"], PROTOCOL_ID)
        self.assertEqual(
            scaffold["instances"]["universe"]["inputs"]["price"],
            "price",
        )
        self.assertEqual(
            scaffold["instances"]["universe"]["config"]["decisionPeriod"],
            "day",
        )
        with mock.patch("engine.archive.version.verify_record"):
            manifest = pipeline_manifest_compiler.compile_pipeline_manifest_from_definitions(
                scaffold,
                _definition_map(records),
            )
            definition_map = _definition_map(records)
            manifest_definitions = {
                definition_key(module["kind"], module["moduleId"], module["version"]):
                    definition_map[
                        definition_key(
                            module["kind"], module["moduleId"], module["version"]
                        )
                    ]
                for module in manifest["modules"]
            }
            template = pipeline_compiler.compile_pipeline_contract_template(
                manifest,
                manifest_definitions,
            )
            plan = pipeline_compiler.bind_pipeline_contract_plan(
                template,
                {
                    "time": {"type": "string"},
                    "price": schemas.PRICE_SCHEMA,
                    "portfolio.account": schemas.PORTFOLIO_ACCOUNT_SCHEMA,
                    "execution.orders": schemas.EXECUTION_ORDERS_SCHEMA,
                },
            )
        self.assertNotIn("protocolId", manifest)
        self.assertTrue(
            all("protocolId" not in module for module in manifest["modules"])
        )
        self.assertEqual(
            manifest["topology"],
            ["universe", "signal", "target", "constraint"],
        )
        material = pipeline_authority.bound_pipeline_contract_plan_material(plan)[0]
        self.assertEqual(
            material["outputContracts"]["intent.approved"],
            schemas.APPROVED_INTENT_SCHEMA,
        )

    def test_ohlcv_scaffold_uses_exact_ohlcv_universe_contract(self):
        records = _module_records()
        scaffold = build_pipeline_scaffold(
            "ohlcv-pipeline",
            "OHLCV Pipeline",
            records,
            decision_period="day",
            universe_module_id="basic-ohlcv-price-map-universe",
        )
        self.assertEqual(
            scaffold["instances"]["universe"]["moduleId"],
            "basic-ohlcv-price-map-universe",
        )
        with mock.patch("engine.archive.version.verify_record"):
            manifest = pipeline_manifest_compiler.compile_pipeline_manifest_from_definitions(
                scaffold,
                _definition_map(records),
            )
            definition_map = _definition_map(records)
            manifest_definitions = {
                definition_key(module["kind"], module["moduleId"], module["version"]):
                    definition_map[
                        definition_key(
                            module["kind"], module["moduleId"], module["version"]
                        )
                    ]
                for module in manifest["modules"]
            }
            template = pipeline_compiler.compile_pipeline_contract_template(
                manifest,
                manifest_definitions,
            )
            plan = pipeline_compiler.bind_pipeline_contract_plan(
                template,
                {
                    "time": {"type": "string"},
                    "price": schemas.OHLCV_PRICE_SCHEMA,
                    "portfolio.account": schemas.PORTFOLIO_ACCOUNT_SCHEMA,
                    "execution.orders": schemas.EXECUTION_ORDERS_SCHEMA,
                },
            )
        material = pipeline_authority.bound_pipeline_contract_plan_material(plan)[0]
        self.assertEqual(
            material["outputContracts"]["intent.approved"],
            schemas.APPROVED_INTENT_SCHEMA,
        )

    def test_signal_graph_is_replaceable_without_changing_other_stages(self):
        records = _module_records()
        scaffold = build_pipeline_scaffold(
            "replace-signal",
            "Replace Signal",
            records,
            decision_period="day",
        )
        original = {
            key: copy.deepcopy(value)
            for key, value in scaffold["instances"].items()
            if key != "signal"
        }
        signal = next(
            item for item in records if item["moduleId"] == "basic-neutral-score-map"
        )
        replacement = copy.deepcopy(signal)
        replacement.update(
            {
                "moduleId": "test-replacement-score-map",
                "name": "Replacement Score Map",
                "version": "1",
                "builtin": False,
                "contentDigest": _digest(200),
                "archive": {
                    "resourceType": "module",
                    "resourceId": "Signal/test-replacement-score-map",
                    "root": "/detached/test/archive",
                },
            }
        )
        scaffold["instances"]["signal"].update(
            {"moduleId": replacement["moduleId"], "version": replacement["version"]}
        )
        with mock.patch("engine.archive.version.verify_record"):
            pipeline_manifest_compiler.compile_pipeline_manifest_from_definitions(
                scaffold,
                _definition_map([*records, replacement]),
            )
        self.assertEqual(
            {key: value for key, value in scaffold["instances"].items() if key != "signal"},
            original,
        )

    def test_scaffold_rejects_missing_identity_and_invalid_configuration(self):
        records = _module_records()
        missing = [
            item for item in records if item["moduleId"] != "basic-neutral-score-map"
        ]
        with self.assertRaisesRegex(ValueError, "basic-neutral-score-map"):
            build_pipeline_scaffold(
                "missing", "Missing", missing, decision_period="day"
            )
        for kwargs in (
            {"decision_period": "bad.period"},
            {"decision_period": "day", "position_scale": math.nan},
            {"decision_period": "day", "maximum_absolute_position": -1},
        ):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                build_pipeline_scaffold("invalid", "Invalid", records, **kwargs)


class BasicWorkflowVisualizationPresetTests(unittest.TestCase):
    def test_preset_uses_only_explicit_period_instrument_and_declared_keys(self):
        data_keys = {
            "time": {"schema": {"type": "string"}, "required": True},
            "price.day.SPX.close": {"schema": {"type": "number"}, "required": True},
            "portfolio.account.equity": {
                "schema": {"type": "number"},
                "required": True,
            },
            "portfolio.account.positions.SPX": {
                "schema": {"type": "number"},
                "required": True,
            },
            "intent.approved.SPX": {
                "schema": {"type": "number"},
                "required": True,
            },
        }
        preset = build_visualization_preset(
            "prices",
            "UTC",
            data_keys,
            period="day",
            instrument_id="SPX",
        )
        contracts = visualization_compiler.compile_visualization_contracts(
            data_keys,
            preset,
            {},
            visualizer_definition_map(),
        )
        self.assertIn("price.day.SPX.close", contracts)
        self.assertEqual({pane["id"] for pane in preset["panes"]}, {"market", "portfolio"})
        self.assertNotIn("analysis", {pane["id"] for pane in preset["panes"]})


if __name__ == "__main__":
    unittest.main()
