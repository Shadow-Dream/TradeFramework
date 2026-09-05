"""Resident Engine Projection Worker isolation and cache integration."""

from __future__ import annotations

import json
import os
import signal
import tempfile
import time
import unittest
from pathlib import Path

import engine_service
from builtin_implementations import analysis_presets
from engine.repository import datasets
from engine.repository import backtest_results as backtest_result_repository
from engine.repository import module_definitions
from engine.runtime import projection_worker
from engine.runtime import result_runtime
from engine.service import backtest_execution
from engine.service import backtests
from engine.service import pipelines
from engine.service import projection_prewarm
from engine.service import result_projection
from engine.service import sample_result_projection
from engine.service import sample_results
from engine.worker.projection_worker import ProjectionCache
from tests.support.backtest_runtime import BacktestRuntimeFixture


class ProjectionCacheTests(unittest.TestCase):
    def test_cache_is_bounded_lru_and_rejects_changed_archive_identity(self):
        cache = ProjectionCache(entry_bytes=4, total_bytes=6, max_entries=2)
        self.assertTrue(cache.put("one", {"inode": 1}, {"cycles": []}, 3))
        self.assertTrue(cache.put("two", {"inode": 2}, {"cycles": []}, 3))
        self.assertIsNotNone(cache.get("one", {"inode": 1}))
        self.assertTrue(cache.put("three", {"inode": 3}, {"cycles": []}, 3))
        self.assertNotIn("two", cache.entries)
        self.assertIsNone(cache.get("one", {"inode": 99}))
        self.assertNotIn("one", cache.entries)
        self.assertFalse(cache.put("large", {"inode": 4}, {"cycles": []}, 5))


class ProjectionWorkerIntegrationTests(unittest.TestCase):
    def setUp(self):
        projection_prewarm.shutdown_projection_prewarm()
        projection_worker.shutdown_projection_worker()
        self.fixture = BacktestRuntimeFixture().open()
        self.config = self.fixture.config

    def tearDown(self):
        projection_prewarm.shutdown_projection_prewarm()
        projection_worker.shutdown_projection_worker()
        self.fixture.close()

    def _temporary_sma(self, period):
        definitions = module_definitions.load_pipeline_definitions(self.config)
        sma = max(
            (
                definition for definition in definitions.values()
                if definition["kind"] == "Signal"
                and definition["moduleId"] == "sma-indicator"
            ),
            key=lambda definition: int(definition["version"]),
        )
        return [{
            "instanceId": "worker-sma",
            "kind": "Signal",
            "moduleId": sma["moduleId"],
            "version": sma["version"],
            "config": {"period": period},
            "inputs": {"value": "price.close"},
            "outputs": {"sma": "indicator.sma"},
        }]

    def _project(self, backtest_id, period):
        with tempfile.TemporaryDirectory(prefix="trade-worker-test-") as root:
            destination = Path(root) / "projection.json"
            result_projection.write_backtest_result_slice(
                self.config,
                backtest_id,
                ["cycles.data.indicator.sma"],
                self._temporary_sma(period),
                destination,
                module_definitions_loader=(
                    lambda: module_definitions.load_pipeline_definitions(self.config)
                ),
            )
            return json.loads(destination.read_text(encoding="utf-8"))

    def _project_columns(self, backtest_id, period, *, window=None):
        with tempfile.TemporaryDirectory(prefix="trade-worker-columns-") as root:
            destination = Path(root) / "projection.json"
            result_projection.write_backtest_result_slice(
                self.config,
                backtest_id,
                ["cycles.data.indicator.sma"],
                self._temporary_sma(period),
                destination,
                module_definitions_loader=(
                    lambda: module_definitions.load_pipeline_definitions(self.config)
                ),
                projection_format="columns-v2",
                window=window,
            )
            return json.loads(destination.read_text(encoding="utf-8"))

    def _project_base(self, backtest_id):
        with tempfile.TemporaryDirectory(prefix="trade-worker-base-") as root:
            destination = Path(root) / "projection.json"
            result_projection.write_backtest_result_slice(
                self.config,
                backtest_id,
                ["cycles.data.signal.sma"],
                [],
                destination,
            )
            return json.loads(destination.read_text(encoding="utf-8"))

    def _project_disposable_oracle(self, backtest_id, period):
        evidence = backtest_result_repository.load_result_archive_evidence(
            self.config, backtest_id, verify_digest=False
        )
        with tempfile.TemporaryDirectory(prefix="trade-worker-oracle-") as root:
            destination = Path(root) / "projection.json"
            result_runtime.write_result_projection_in_runtime(
                evidence,
                ["cycles.data.indicator.sma"],
                self._temporary_sma(period),
                module_definitions.load_pipeline_definitions(self.config),
                destination,
            )
            return json.loads(destination.read_text(encoding="utf-8"))

    def _project_sample_base(self):
        dataset = datasets.get_dataset(self.config, "prices")
        materialized = sample_results.materialize_sample_result(self.config, {
            "datasetId": "prices",
            "datasetVersionId": dataset["latestVersionId"],
            "sampler": {
                "samplerId": self.fixture.row_sampler["samplerId"],
                "version": self.fixture.row_sampler["version"],
                "parameters": {},
            },
        })
        with tempfile.TemporaryDirectory(prefix="trade-worker-sample-") as root:
            destination = Path(root) / "projection.json"
            sample_result_projection.write_sample_result_slice(
                self.config,
                materialized["view"]["sampleResultId"],
                ["cycles.data.price.close"],
                [],
                destination,
            )
            return json.loads(destination.read_text(encoding="utf-8"))

    def _run_backtest(self, pipeline_id):
        definitions = module_definitions.load_pipeline_definitions(self.config)
        sma = next(
            definition for definition in definitions.values()
            if definition["kind"] == "Signal"
            and definition["moduleId"] == "sma-indicator"
        )
        pipelines.archive_pipeline_if_changed(self.config, {
            "pipelineId": pipeline_id,
            "name": pipeline_id,
            "config": {
                "observationInput": {
                    "whitelist": ["price.close"],
                    "blacklist": [],
                }
            },
            "instances": {
                "sma": {
                    "instanceId": "sma",
                    "kind": "Signal",
                    "moduleId": sma["moduleId"],
                    "version": sma["version"],
                    "config": {"period": 2},
                    "inputs": {"value": "wire.close"},
                    "outputs": {"sma": "wire.sma"},
                }
            },
            "stages": {},
            "signalGraph": {
                "nodes": ["sma"],
                "inputs": {
                    "close": {"dataKey": "price.close", "wire": "wire.close"}
                },
                "outputs": {
                    "sma-output": {
                        "dataKey": "signal.sma", "wire": "wire.sma"
                    }
                },
            },
        })
        environment = engine_service.handle_save_environment(self.config, {
            "schemaVersion": 2,
            "environmentId": f"{pipeline_id}-environment",
            "name": f"{pipeline_id}-environment",
            "description": "Projection Worker test Environment.",
            "instances": {},
            "graph": {
                "nodes": [],
                "inputs": {
                    "price-input": {
                        "dataKey": "price.close", "wire": "wire.price"
                    }
                },
                "outputs": {
                    "price-output": {
                        "dataKey": "price.close", "wire": "wire.price"
                    }
                },
            },
        })["definition"]
        analysis = self.fixture.graph_version(
            "analyses.json", "analysisId", analysis_presets.NEUTRAL_ANALYSIS_ID
        )
        frozen = backtests.freeze_backtest_request(
            self.config,
            self.fixture.request(
                pipeline_id,
                self.fixture.row_sampler,
                environment,
                analysis,
            ),
        )
        return backtest_execution.run_backtest(self.config, frozen)

    def test_worker_reuses_verified_frames_but_not_signal_instances(self):
        completed = self._run_backtest("projection-worker-cache")
        backtest_id = completed["backtestId"]
        base = self._project_base(backtest_id)
        warmed = projection_worker.projection_worker_status()
        first = self._project(backtest_id, 2)
        hot = projection_worker.projection_worker_status()
        second = self._project(backtest_id, 2)
        repeated = projection_worker.projection_worker_status()
        oracle = self._project_disposable_oracle(backtest_id, 2)
        self.assertEqual(
            [cycle["data"]["signal"]["sma"] for cycle in base["cycles"]],
            [None, 11.0, 12.5],
        )
        self.assertEqual(first, second)
        self.assertEqual(first, oracle)
        self.assertEqual(
            [cycle["data"]["indicator"]["sma"] for cycle in second["cycles"]],
            [None, 11.0, 12.5],
        )
        self.assertEqual(warmed["workerPid"], hot["workerPid"])
        self.assertEqual(hot["workerPid"], repeated["workerPid"])
        self.assertEqual(hot["cacheHits"], warmed["cacheHits"] + 1)
        # The second exact request is served by the Engine persistent cache;
        # the Worker frame cache is not entered again.
        self.assertEqual(repeated["cacheHits"], hot["cacheHits"])
        sample = self._project_sample_base()
        shared = projection_worker.projection_worker_status()
        self.assertEqual(shared["poolSize"], 2)
        self.assertGreaterEqual(shared["requests"], repeated["requests"] + 1)
        self.assertEqual(shared["cacheMisses"], repeated["cacheMisses"] + 1)
        self.assertEqual(
            [cycle["data"]["price"]["close"] for cycle in sample["cycles"]],
            [10, 12, 13],
        )

    def test_dead_worker_fails_no_request_over_and_restarts_next_request(self):
        completed = self._run_backtest("projection-worker-restart")
        backtest_id = completed["backtestId"]
        self._project_base(backtest_id)
        before = projection_worker.projection_worker_status()
        os.kill(before["workerPid"], signal.SIGKILL)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            session = projection_worker.process_session.PROCESS_SESSIONS.get(
                projection_worker.PROJECTION_WORKER_SESSION_KEY
            )
            if session is not None and session.poll() is not None:
                break
            time.sleep(0.02)
        projected = self._project(backtest_id, 3)
        after = projection_worker.projection_worker_status()
        self.assertNotEqual(before["workerPid"], after["workerPid"])
        self.assertEqual(
            [cycle["data"]["indicator"]["sma"] for cycle in projected["cycles"]],
            [None, None, 35.0 / 3.0],
        )

    def test_columnar_projection_is_exact_row_oracle_and_window_is_output_only(self):
        completed = self._run_backtest("projection-worker-columns")
        backtest_id = completed["backtestId"]
        rows = self._project(backtest_id, 2)
        columns = self._project_columns(backtest_id, 2)
        path = "cycles.data.indicator.sma"
        self.assertEqual(columns["projectionSchemaVersion"], 2)
        self.assertEqual(columns["projectionFormat"], "columns-v2")
        self.assertEqual(columns["columnOrder"], [path])
        self.assertEqual(columns["columns"][path]["absent"], [])
        self.assertEqual(
            columns["columns"][path]["values"],
            [cycle["data"]["indicator"]["sma"] for cycle in rows["cycles"]],
        )
        self.assertEqual(columns["totalRowCount"], len(rows["cycles"]))
        self.assertEqual(
            columns["window"], {"startIndex": 0, "endIndexExclusive": 3}
        )

        windowed = self._project_columns(
            backtest_id,
            2,
            window={"startIndex": 1, "endIndexExclusive": 3},
        )
        self.assertEqual(windowed["totalRowCount"], 3)
        self.assertEqual(
            windowed["window"], {"startIndex": 1, "endIndexExclusive": 3}
        )
        self.assertEqual(windowed["columns"][path]["values"], [11.0, 12.5])
        status = projection_worker.projection_worker_status()
        self.assertGreaterEqual(status["planCacheMisses"], 1)
        self.assertGreaterEqual(status["planCacheHits"], 2)


if __name__ == "__main__":
    unittest.main()
