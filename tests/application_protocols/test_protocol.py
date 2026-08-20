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
    required = set(MODULE_REQUIREMENTS.values())
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
    def test_registry_pins_only_protocol_owned_v2_resources(self):
        identities = (
            ("samplerId", "basic-price-map-sampler", None),
            ("environmentId", "basic-multi-asset-paper-environment", None),
            ("moduleId", "basic-price-map-universe", "Universe"),
            ("moduleId", "basic-neutral-score-map", "Signal"),
            ("moduleId", "basic-score-map-position-target", "Target"),
            ("moduleId", "basic-absolute-position-map-constraint", "Constraint"),
        )
        records = []
        for index, (field, resource_id, kind) in enumerate(identities, start=1):
            record = {
                field: resource_id,
                "version": str(index),
                "contentDigest": _digest(index),
                "builtin": True,
            }
            if kind:
                record["kind"] = kind
            records.append(record)
        records.append(
            {
                "analysisId": "basic-workflow-performance-analysis",
                "version": "1",
                "contentDigest": _digest(100),
                "builtin": True,
            }
        )
        registry = build_registry(records)
        self.assertEqual(len(registry), len(identities))
        self.assertEqual(
            [entry["role"] for entry in registry],
            sorted(entry["role"] for entry in registry),
        )
        self.assertNotIn(
            "basic-workflow-performance-analysis",
            {entry["resource"]["id"] for entry in registry},
        )

    def test_registry_rejects_symbolic_or_unowned_known_identity(self):
        base = {
            "moduleId": "basic-neutral-score-map",
            "kind": "Signal",
            "version": "1",
            "contentDigest": _digest(1),
            "builtin": True,
        }
        for changes in (
            {"version": "latest"},
            {"version": "01"},
            {"contentDigest": "not-evidence"},
            {"kind": "Analyzer"},
            {"builtin": False},
        ):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                build_registry([{**base, **changes}])
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            build_registry([base, copy.deepcopy(base)])


class BasicWorkflowPipelineScaffoldTests(unittest.TestCase):
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
        self.assertNotIn("protocolId", scaffold)
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
            template = pipeline_compiler.compile_pipeline_contract_template(
                manifest,
                _definition_map(records),
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
        self.assertEqual(
            manifest["topology"],
            ["universe", "signal", "target", "constraint"],
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
