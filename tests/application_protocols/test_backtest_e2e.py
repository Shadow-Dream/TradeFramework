"""Real Backtest integration for the Basic Workflow v2 application protocol."""

from __future__ import annotations

import base64
import json
import tempfile
import unittest
from pathlib import Path

from application_protocols.basic_workflow.scaffolds import build_pipeline_scaffold
from builtin_implementations import analysis_presets
from builtin_implementations import environment_presets
from builtin_implementations import resources as builtin_resources
from builtin_implementations.basic_workflow_contracts import (
    SIGNAL_SCORE_SCHEMA,
    UNIVERSE_SELECTION_SCHEMA,
)
from dataset_adapters import basic_workflow as basic_workflow_dataset
from engine.control import database as engine_database
from engine.repository import datasets, module_definitions
from engine.service import backtest_execution, backtests, module_publication
from engine.service import pipelines as pipeline_service
from engine.service import result_projection


ALWAYS_LONG_SOURCE = b"""\
from strategy_devkit.module_sdk import SignalModule


class AlwaysLongBasicWorkflowSignal(SignalModule):
    def update(self, selection):
        if type(selection) is not dict or any(value is not True for value in selection.values()):
            raise ValueError("selection must map instruments to true")
        return {"scores": {instrument: 1.0 for instrument in sorted(selection)}}


MODULE_CLASS = AlwaysLongBasicWorkflowSignal
"""


class BasicWorkflowBacktestIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.config = {
            "controlRoot": str(self.root / "control"),
            "releaseRoot": str(self.root / "releases"),
            "liveRoot": str(self.root / "live"),
        }
        engine_database.prepare_database(self.config)
        self.installed = builtin_resources.install(self.config)

    def tearDown(self):
        self.temporary.cleanup()

    def _installed(self, identity_field, identity):
        matches = [
            item for item in self.installed if item.get(identity_field) == identity
        ]
        self.assertEqual(len(matches), 1)
        result = matches[0]
        self.assertTrue(result["builtin"])
        self.assertRegex(result["contentDigest"], r"^sha256:[0-9a-f]{64}$")
        return result

    def _dataset_source(self):
        root = self.root / "csv-source"
        rows = {
            "day/SPY.csv": (
                "time,open,close,high,low\n"
                "2026-01-02T21:00:00Z,100,101,102,99\n"
                "2026-01-05T21:00:00Z,200,201,202,199\n"
                "2026-01-06T21:00:00Z,300,301,302,299\n"
            ),
            "day/QQQ.csv": (
                "time,open,close,high,low\n"
                "2026-01-02T21:00:00Z,50,51,52,49\n"
                "2026-01-05T21:00:00Z,60,61,62,59\n"
                "2026-01-06T21:00:00Z,70,71,72,69\n"
            ),
        }
        for relative, content in rows.items():
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        return root

    def _publish_always_long_signal(self):
        return module_publication.publish_module(
            self.config,
            {
                "kind": "Signal",
                "moduleId": "test-basic-workflow-always-long-map",
                "name": "Test Basic Workflow Always Long Map",
                "description": "Test-only replaceable per-instrument score map.",
                "activationMode": "PythonModule",
                "parameters": {},
                "configSchema": {
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False,
                },
                "ports": {
                    "inputs": {
                        "selection": {
                            "schema": UNIVERSE_SELECTION_SCHEMA,
                            "required": True,
                        }
                    },
                    "outputs": {
                        "scores": {
                            "schema": SIGNAL_SCORE_SCHEMA,
                            "required": True,
                        }
                    },
                },
                "files": [
                    {
                        "path": "module.py",
                        "contentBase64": base64.b64encode(ALWAYS_LONG_SOURCE).decode("ascii"),
                        "executable": False,
                    }
                ],
            },
        )["definition"]

    def _project_result(self, backtest_id):
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "projection.json"
            result_projection.write_backtest_result_slice(
                self.config,
                backtest_id,
                ["cycles", "metrics", "dataKeys"],
                [],
                destination,
            )
            return json.loads(destination.read_text(encoding="utf-8"))

    def test_protocol_resources_complete_a_causal_multi_asset_backtest(self):
        dataset = basic_workflow_dataset.register_dataset(
            self.config,
            dataset_id="basic-workflow-prices",
            name="Basic Workflow Prices",
            source_root=self._dataset_source(),
            descriptor={
                "protocolId": "trade.basic-workflow",
                "protocolVersion": "2.0.0",
                "profile": "multi-instrument-bar-position",
                "cashUnit": "USD",
                "quantityUnit": "share",
                "executionConvention": "prior-approved-intent-next-bar-open",
                "valuationConvention": "current-bar-close",
            },
            source={
                "type": "test-fixture",
                "details": {"purpose": "Basic Workflow v2 E2E"},
            },
        )
        dataset_version = datasets.ensure_dataset_version(
            self.config,
            dataset["datasetId"],
            dataset["latestVersionId"],
        )
        self.assertEqual(
            dataset_version["capabilities"]["basicWorkflow"]["descriptor"]
            ["executionConvention"],
            "prior-approved-intent-next-bar-open",
        )

        definitions = list(module_definitions.load_pipeline_definitions(self.config).values())
        pipeline_draft = build_pipeline_scaffold(
            "basic-workflow-e2e",
            "Basic Workflow E2E",
            definitions,
            decision_period="day",
            position_scale=2.0,
            maximum_absolute_position=2.0,
        )
        signal = self._publish_always_long_signal()
        pipeline_draft["instances"]["signal"].update(
            {"moduleId": signal["moduleId"], "version": signal["version"], "config": {}}
        )
        pipeline = pipeline_service.archive_pipeline_if_changed(
            self.config,
            pipeline_draft,
        )["definition"]

        sampler = self._installed("samplerId", "basic-price-map-sampler")
        environment = self._installed(
            "environmentId",
            environment_presets.BASIC_WORKFLOW_ENVIRONMENT_ID,
        )
        analysis = self._installed("analysisId", analysis_presets.NEUTRAL_ANALYSIS_ID)
        request = {
            "pipeline": {"pipelineId": pipeline["pipelineId"], "version": pipeline["version"]},
            "datasetId": dataset["datasetId"],
            "datasetVersionId": dataset["latestVersionId"],
            "sampler": {
                "samplerId": sampler["samplerId"],
                "version": sampler["version"],
                "parameters": {"decisionPeriod": "day"},
            },
            "environment": {
                "environmentId": environment["environmentId"],
                "version": environment["version"],
            },
            "analysis": {"analysisId": analysis["analysisId"], "version": analysis["version"]},
        }
        frozen = backtests.freeze_backtest_request(self.config, request)
        completed = backtest_execution.run_backtest(self.config, frozen)
        self.assertEqual(completed["status"], "completed")
        self.assertEqual(completed["metrics"]["cycleCount"], 3)

        result = self._project_result(completed["backtestId"])
        first, second, third = [cycle["data"] for cycle in result["cycles"]]
        self.assertEqual(first["execution"]["orders"], {})
        self.assertEqual(first["intent"]["approved"], {"QQQ": 2.0, "SPY": 2.0})
        self.assertEqual(
            second["execution"]["orders"],
            {
                "QQQ": {"side": "buy", "quantity": 2.0, "price": 60.0, "fee": 0.0},
                "SPY": {"side": "buy", "quantity": 2.0, "price": 200.0, "fee": 0.0},
            },
        )
        self.assertEqual(second["portfolio"]["account"]["positions"], {"QQQ": 2.0, "SPY": 2.0})
        self.assertEqual(second["portfolio"]["account"]["cash"], 99480.0)
        self.assertEqual(third["execution"]["orders"], {})
        self.assertEqual(third["portfolio"]["account"]["equity"], 100224.0)
        self.assertNotIn("analysis", third)
        for data_key in (
            "price",
            "portfolio.account",
            "execution.orders",
            "intent.approved",
        ):
            self.assertIn(data_key, result["dataKeys"])


if __name__ == "__main__":
    unittest.main()
