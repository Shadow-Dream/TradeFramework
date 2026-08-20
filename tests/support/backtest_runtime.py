"""Small, public Backtest fixtures shared by Engine integration tests."""

import json
import tempfile
import unittest
from pathlib import Path

from builtin_implementations import analysis_presets
from builtin_implementations import environment_presets
from builtin_implementations import resources as builtin_resources
import engine_service
from dataset_adapters import ohlcv
from engine.control import database as engine_database
from engine.contracts import sampler as sampler_contracts
from engine.repository import datasets
from engine.repository import module_definitions
from engine.repository import pipelines as pipeline_repository
from engine.repository import samplers
from engine.repository import control_state
from engine.service import result_projection as result_projection_service
from engine.service import backtest_execution as backtest_execution_service
from engine.service import backtests as backtest_service
from engine.service import pipelines as pipeline_service


class BacktestRuntimeFixture:
    """Own a complete minimal repository with a pinned OHLCV Dataset."""

    def __init__(self):
        self._temporary = None

    def open(self):
        if self._temporary is not None:
            raise RuntimeError("Backtest runtime fixture is already open.")
        self._temporary = tempfile.TemporaryDirectory()
        root = Path(self._temporary.name)
        self.root = root
        self.config = {
            "controlRoot": str(root / "control"),
            "releaseRoot": str(root / "releases"),
            "liveRoot": str(root / "live"),
        }
        engine_database.prepare_database(self.config)
        builtin_resources.install(self.config)
        rows = [
            {
                "date": "2026-01-01",
                "open": 10,
                "high": 11,
                "low": 9,
                "close": 10,
                "volume": 100,
            },
            {
                "date": "2026-01-02",
                "open": 10,
                "high": 13,
                "low": 10,
                "close": 12,
                "volume": 120,
            },
            {
                "date": "2026-01-03",
                "open": 12,
                "high": 14,
                "low": 11,
                "close": 13,
                "volume": 130,
            },
        ]
        ohlcv.register_dataset(
            self.config,
            dataset_id="prices",
            name="Prices",
            symbol="SPY",
            source="test",
            interval="d",
            rows=rows,
            availability_policy="bar_end_utc",
        )
        sampler_config = {
            "mapping": {"price.close": "close"},
            "includeUnmappedFields": False,
            "unmappedPrefix": "dataset.",
        }
        self.row_sampler = samplers.save_sampler(
            self.config,
            {
                "samplerId": "test-price-close-row-map",
                "name": "Test price close row map",
                "type": "row-map",
                "config": sampler_config,
                "parameterSchema": sampler_contracts.infer_sampler_parameter_schema(
                    sampler_config
                ),
                "outputSchema": {"price.close": {"type": "number"}},
                "source": "",
                "entryPoint": "",
            },
        )
        return self

    def close(self):
        if self._temporary is not None:
            self._temporary.cleanup()
            self._temporary = None

    def graph_version(self, state_file, identity_field, identity):
        return next(
            item
            for item in control_state.load_state(
                self.config,
                state_file,
                {},
            ).values()
            if item[identity_field] == identity
        )

    def empty_pipeline(self, pipeline_id="empty-pipeline"):
        return pipeline_service.archive_pipeline_if_changed(
            self.config,
            {
                "pipelineId": pipeline_id,
                "name": pipeline_id,
                "config": {
                    "observationInput": {"whitelist": [], "blacklist": []}
                },
                "instances": {},
                "stages": {},
                "signalGraph": {"nodes": [], "inputs": {}, "outputs": {}},
            },
        )["definition"]

    def request(self, pipeline_id, sampler, environment, analysis):
        pipeline = pipeline_repository.load_current_pipeline(self.config, pipeline_id)
        dataset = datasets.get_dataset(self.config, "prices")
        return {
            "pipeline": {
                "pipelineId": pipeline_id,
                "version": pipeline["version"],
            },
            "datasetId": "prices",
            "datasetVersionId": dataset["latestVersionId"],
            "sampler": {
                "samplerId": sampler["samplerId"],
                "version": sampler["version"],
                "parameters": {},
            },
            "environment": {
                "environmentId": environment["environmentId"],
                "version": environment["version"],
            },
            "analysis": {
                "analysisId": analysis["analysisId"],
                "version": analysis["version"],
            },
        }

    def frozen_minimal_request(self, pipeline_id="minimal-backtest"):
        pipeline = self.empty_pipeline(pipeline_id)
        environment = self.graph_version(
            "environments.json",
            "environmentId",
            environment_presets.NEUTRAL_ENVIRONMENT_ID,
        )
        analysis = self.graph_version(
            "analyses.json",
            "analysisId",
            analysis_presets.NEUTRAL_ANALYSIS_ID,
        )
        request = self.request(
            pipeline["pipelineId"], self.row_sampler, environment, analysis
        )
        return backtest_service.freeze_backtest_request(self.config, request)

    def run_minimal_backtest(self, pipeline_id="minimal-backtest"):
        return backtest_execution_service.run_backtest(
            self.config,
            self.frozen_minimal_request(pipeline_id),
        )


class BacktestIntegrationTestCase(unittest.TestCase):
    """TestCase base exposing the shared repository through public helpers."""

    def setUp(self):
        self.fixture = BacktestRuntimeFixture().open()
        self.config = self.fixture.config
        self.row_sampler = self.fixture.row_sampler

    def tearDown(self):
        self.fixture.close()

    def result_projection(self, backtest_id, paths, temporary_modules=None):
        with tempfile.TemporaryDirectory(prefix="trade-test-result-slice-") as root:
            destination = Path(root) / "result.json"
            modules = [] if temporary_modules is None else temporary_modules
            result_projection_service.write_backtest_result_slice(
                self.config,
                backtest_id,
                paths,
                modules,
                destination,
                module_definitions_loader=(
                    lambda: module_definitions.load_pipeline_definitions(self.config)
                    if modules
                    else None
                ),
            )
            return json.loads(destination.read_text(encoding="utf-8"))

    def graph_version(self, state_file, identity_field, identity):
        return self.fixture.graph_version(state_file, identity_field, identity)

    def empty_pipeline(self, pipeline_id="empty-pipeline"):
        return self.fixture.empty_pipeline(pipeline_id)

    def request(self, pipeline_id, sampler, environment, analysis):
        return self.fixture.request(pipeline_id, sampler, environment, analysis)

    def execute_request(self, request):
        return backtest_execution_service.run_backtest(
            self.config,
            backtest_service.freeze_backtest_request(self.config, request),
        )

    def passthrough_environment(self, environment_id, data_keys):
        inputs = {}
        outputs = {}
        for index, data_key in enumerate(data_keys):
            wire = f"wire.input_{index}"
            inputs[f"input-{index}"] = {"dataKey": data_key, "wire": wire}
            outputs[f"output-{index}"] = {"dataKey": data_key, "wire": wire}
        return engine_service.handle_save_environment(
            self.config,
            {
                "schemaVersion": 2,
                "environmentId": environment_id,
                "name": environment_id,
                "description": "Explicit test-only Environment boundary.",
                "instances": {},
                "graph": {"nodes": [], "inputs": inputs, "outputs": outputs},
            },
        )["definition"]


__all__ = ("BacktestIntegrationTestCase", "BacktestRuntimeFixture")
