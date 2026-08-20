import unittest
from pathlib import Path

from builtin_implementations.pipeline_contracts import BUILTIN_PIPELINE_MODULES
from engine.compiler.pipeline_manifest import normalize_pipeline_draft
from engine.contracts.json_schema import validate_config
from engine.contracts.module import ENGINE_MODULE_KINDS
from engine.contracts.pipeline import require_pipeline_plan, validate_pipeline_manifest


class PipelineArtifactContractTests(unittest.TestCase):
    def test_malformed_signal_plan_uses_the_strict_contract_error_boundary(self):
        base = {
            "inputContracts": {},
            "inputRequiredRoots": [],
            "outputContracts": {},
            "outputRequiredRoots": [],
            "allContracts": {},
            "allRequiredRoots": [],
            "observationInput": {"whitelist": [], "blacklist": []},
            "topology": [],
            "directPlans": [],
        }
        for malformed in ({}, None, []):
            with self.subTest(signal_plan=malformed):
                with self.assertRaises(ValueError) as raised:
                    require_pipeline_plan({
                        **base,
                        "signalPlan": malformed,
                    })
                self.assertNotIsInstance(raised.exception, (KeyError, TypeError))

    def test_graph_boundaries_are_not_registered_as_modules(self):
        module_ids = {
            definition["moduleId"] for definition in BUILTIN_PIPELINE_MODULES
        }
        self.assertFalse(module_ids & {"data-input", "data-output", "graph-input", "graph-output"})

    def test_builtin_signal_config_schemas_reject_unknown_fields(self):
        sma = next(
            item
            for item in BUILTIN_PIPELINE_MODULES
            if item["moduleId"] == "sma-indicator"
        )
        self.assertFalse(sma["configSchema"]["additionalProperties"])
        with self.assertRaisesRegex(ValueError, "peroid.*unexpected"):
            validate_config({"peroid": 20}, sma["configSchema"], path="config")

    def test_removed_execution_stage_and_kind_are_rejected(self):
        execution = {
            "key": "custom.execution",
            "instanceId": "custom.execution",
            "kind": "Execution",
            "moduleId": "custom-execution",
            "version": "1",
            "config": {},
            "inputs": {},
            "outputs": {"anything": "custom.nested.value"},
        }
        manifest = {
            "name": "custom-execution-output",
            "config": {
                "observationInput": {"whitelist": [], "blacklist": []}
            },
            "modules": [execution],
            "topology": [execution["key"]],
            "universe": [],
            "target": [],
            "constraint": [],
            "execution": [execution["key"]],
            "signalGraph": {"nodes": [], "inputs": {}, "outputs": {}},
        }
        with self.assertRaisesRegex(ValueError, "unsupported field.*execution"):
            validate_pipeline_manifest(manifest)

        manifest.pop("execution")
        with self.assertRaisesRegex(ValueError, "invalid kind 'Execution'"):
            validate_pipeline_manifest(manifest)

        with self.assertRaisesRegex(ValueError, "unsupported stage.*execution"):
            normalize_pipeline_draft({
                "pipelineId": "removed-execution-stage",
                "name": "Removed execution stage",
                "config": {
                    "observationInput": {"whitelist": [], "blacklist": []}
                },
                "instances": {},
                "stages": {
                    "universe": [],
                    "target": [],
                    "constraint": [],
                    "execution": [],
                },
                "signalGraph": {"nodes": [], "inputs": {}, "outputs": {}},
            })
        self.assertEqual(
            ENGINE_MODULE_KINDS,
            frozenset({"Universe", "Signal", "Target", "Constraint"}),
        )

    def test_production_contract_code_contains_no_removed_type_aliases(self):
        root = Path(__file__).resolve().parents[3]
        files = [
            root / "engine" / "contracts" / "data.py",
            root / "engine" / "contracts" / "sampler.py",
            root / "engine" / "contracts" / "module.py",
            root / "engine" / "contracts" / "pipeline.py",
            root / "engine" / "compiler" / "pipeline_manifest.py",
            root / "engine" / "contracts" / "visualization.py",
            root / "engine" / "compiler" / "visualization.py",
            root / "engine" / "repository" / "visualizations.py",
            root / "engine" / "service" / "visualizations.py",
            root / "engine" / "worker" / "backtest_execution.py",
            root / "engine" / "service" / "control_api.py",
            root / "strategy_devkit" / "module_sdk.py",
            root / "web" / "chart_core.js", root / "web" / "module_graph_litegraph.js",
        ]
        removed = [
            "pipeline-data-v" + "2", "series" + ".price", "series" + ".indicator",
            "action" + ".order", "event" + ".marker", "portfolio-target" + ".list",
            "observation" + ".map", "order" + ".list", "signal" + ".direction",
            "Execution" + "Module", "stages." + "execution",
        ]
        violations = []
        for path in files:
            text = path.read_text(encoding="utf-8")
            violations.extend(f"{path.name}: {token}" for token in removed if token in text)
        self.assertEqual(violations, [])
