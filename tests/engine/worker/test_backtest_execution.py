#!/usr/bin/env python3

import base64
import os
import stat
from unittest import mock

from builtin_implementations import analysis_presets
from builtin_implementations import environment_presets
from engine.archive import backtest_result as backtest_result_archive
from engine.contracts import contract_expansion
from engine.core import build_identity
from engine.core import resource_ids
from engine.core import runtime_identity
from engine.repository import backtest_results as result_repository
from engine.runtime import sampler as sampler_runtime
from engine.service import backtest_results as backtest_result_service
from engine.service import module_publication
from engine.service import pipelines as pipeline_service
from engine.worker import backtest_execution as backtest_worker
from tests.support.backtest_runtime import BacktestIntegrationTestCase

class BacktestWorkerIntegrationTests(BacktestIntegrationTestCase):
    def test_contract_compiler_cache_is_scoped_to_one_worker_call(self):
        observed = []

        def fail_inside_scope(*_args, **_kwargs):
            observed.append(contract_expansion._EXPAND_CONTRACT_CACHE.get())
            raise RuntimeError("worker failed")

        self.assertIsNone(contract_expansion._EXPAND_CONTRACT_CACHE.get())
        with (
            mock.patch.object(
                backtest_worker,
                "_execute_backtest",
                side_effect=fail_inside_scope,
            ),
            self.assertRaisesRegex(RuntimeError, "worker failed"),
        ):
            backtest_worker.execute_backtest(
                self.config,
                {},
                backtest_id=resource_ids.new_resource_id("backtest"),
                execution_root=self.fixture.root / "failed-cache-scope",
            )
        self.assertEqual(len(observed), 1)
        self.assertIsNotNone(observed[0])
        self.assertIsNone(contract_expansion._EXPAND_CONTRACT_CACHE.get())

    def test_execution_evidence_is_an_exact_four_field_protocol(self):
        evidence = {
            "backtestId": resource_ids.new_resource_id("backtest"),
            "cycleCount": 0,
            "contentDigest": "sha256:" + "0" * 64,
            "resultSize": 1,
        }
        self.assertEqual(
            backtest_worker.require_backtest_execution_evidence(evidence),
            evidence,
        )
        with self.assertRaisesRegex(ValueError, "unsupported field.*fallback"):
            backtest_worker.require_backtest_execution_evidence({
                **evidence,
                "fallback": True,
            })

    def test_snapshot_hash_failure_precedes_local_identity_and_preparation(self):
        frozen = self.fixture.frozen_minimal_request("identity-prefix-order")
        frozen["executionSnapshot"]["snapshotHash"] = "sha256:" + "0" * 64
        with (
            mock.patch.object(
                runtime_identity,
                "engine_runtime_identity",
            ) as local_identity,
            mock.patch.object(
                backtest_worker.backtest_preparation,
                "prepare_backtest_execution",
            ) as prepare,
            self.assertRaisesRegex(ValueError, "snapshot hash is invalid"),
        ):
            backtest_worker.execute_backtest(
                self.config,
                frozen,
                backtest_id=resource_ids.new_resource_id("backtest"),
                execution_root=self.fixture.root / "identity-prefix-order",
            )
        local_identity.assert_not_called()
        prepare.assert_not_called()

    def test_local_identity_failure_precedes_preparation_and_runtime(self):
        frozen = self.fixture.frozen_minimal_request("identity-preparation-order")
        identity_error = RuntimeError("complete local identity failed")
        with (
            mock.patch.object(
                runtime_identity,
                "engine_runtime_identity",
                side_effect=identity_error,
            ),
            mock.patch.object(
                backtest_worker.backtest_preparation,
                "prepare_backtest_execution",
            ) as prepare,
            mock.patch.object(
                backtest_worker._dataset_runtime,
                "create_dataset_handle",
            ) as create_dataset,
            mock.patch.object(
                backtest_worker._sampler_runtime,
                "create_verified_sampler_runtime",
            ) as create_sampler,
            self.assertRaises(RuntimeError) as raised,
        ):
            backtest_worker.execute_backtest(
                self.config,
                frozen,
                backtest_id=resource_ids.new_resource_id("backtest"),
                execution_root=self.fixture.root / "identity-preparation-order",
            )
        self.assertIs(raised.exception, identity_error)
        prepare.assert_not_called()
        create_dataset.assert_not_called()
        create_sampler.assert_not_called()

    def test_successful_local_identity_rethrows_original_preparation_error(self):
        frozen = self.fixture.frozen_minimal_request("preparation-error-order")
        preparation_error = RuntimeError("original preparation failure")
        with (
            mock.patch.object(
                runtime_identity,
                "engine_runtime_identity",
                return_value=frozen["executionSnapshot"]["engineRuntime"],
            ) as local_identity,
            mock.patch.object(
                backtest_worker.backtest_preparation,
                "prepare_backtest_execution",
                side_effect=preparation_error,
            ) as prepare,
            self.assertRaises(RuntimeError) as raised,
        ):
            backtest_worker.execute_backtest(
                self.config,
                frozen,
                backtest_id=resource_ids.new_resource_id("backtest"),
                execution_root=self.fixture.root / "preparation-error-order",
            )
        self.assertIs(raised.exception, preparation_error)
        local_identity.assert_called_once_with()
        prepare.assert_called_once_with(
            self.config,
            frozen,
            dataset_id=frozen["datasetId"],
            execution_snapshot=frozen["executionSnapshot"],
            pipeline_id=frozen["pipeline"]["pipelineId"],
            pipeline_version=frozen["pipeline"]["version"],
        )

    def test_loaded_worker_identity_wins_over_a_new_matching_disk_hash(self):
        frozen = self.fixture.frozen_minimal_request("loaded-runtime-identity")
        expected = frozen["executionSnapshot"]["engineRuntime"]
        loaded_build_id = "engine:" + "f" * 20
        self.assertNotEqual(loaded_build_id, expected["buildId"])
        with (
            mock.patch.object(
                runtime_identity,
                "ENGINE_BUILD_ID",
                loaded_build_id,
            ),
            mock.patch.object(
                build_identity,
                "build_id",
                return_value=expected["buildId"],
            ) as current_disk_build_id,
            mock.patch.object(
                runtime_identity,
                "engine_runtime_identity",
                wraps=runtime_identity.engine_runtime_identity,
            ) as local_identity,
            mock.patch.object(
                backtest_worker.backtest_preparation,
                "prepare_backtest_execution",
            ) as prepare,
            self.assertRaisesRegex(ValueError, "different Engine/Python runtime"),
        ):
            backtest_worker.execute_backtest(
                self.config,
                frozen,
                backtest_id=resource_ids.new_resource_id("backtest"),
                execution_root=self.fixture.root / "loaded-runtime-identity",
            )
        local_identity.assert_called_once_with()
        current_disk_build_id.assert_not_called()
        prepare.assert_not_called()

    def test_direct_execution_performs_synchronous_complete_identity_check(self):
        frozen = self.fixture.frozen_minimal_request("direct-runtime-identity")
        different = {
            **frozen["executionSnapshot"]["engineRuntime"],
            "buildId": "engine:different-build",
        }
        with (
            mock.patch(
                "engine.core.runtime_identity.engine_runtime_identity",
                return_value=different,
            ) as local_identity,
            mock.patch.object(
                backtest_worker.backtest_preparation,
                "prepare_backtest_execution",
            ) as prepare,
            self.assertRaisesRegex(ValueError, "different Engine/Python runtime"),
        ):
            backtest_worker.execute_backtest(
                self.config,
                frozen,
                backtest_id=resource_ids.new_resource_id("backtest"),
                execution_root=self.fixture.root / "direct-runtime-identity",
            )
        local_identity.assert_called_once_with()
        prepare.assert_not_called()

    def test_worker_seals_before_parent_catalog_recovery(self):
        frozen = self.fixture.frozen_minimal_request("pure-worker-seal")
        backtest_id = resource_ids.new_resource_id("backtest")
        execution_root = self.fixture.root / "pure-worker-runtime"
        execution_root.mkdir()
        progress = []

        real_projection = backtest_worker.project_compiled_data_paths
        with mock.patch.object(
            backtest_worker,
            "project_compiled_data_paths",
            wraps=real_projection,
        ) as project_previous:
            evidence = backtest_worker.execute_backtest(
                self.config,
                frozen,
                backtest_id=backtest_id,
                execution_root=execution_root,
                progress_callback=lambda completed, total, phase: progress.append(
                    (completed, total, phase)
                ),
            )

        self.assertEqual(
            set(evidence),
            {"backtestId", "cycleCount", "contentDigest", "resultSize"},
        )
        self.assertEqual(evidence["backtestId"], backtest_id)
        self.assertEqual(evidence["cycleCount"], 3)
        self.assertEqual(progress[:3], [
            (0, 0, "counting"),
            (0, 3, "preparing"),
            (0, 3, "running"),
        ])
        self.assertEqual(progress[-1], (3, 3, "finalizing"))
        self.assertTrue(all(total == 3 for _, total, _ in progress[1:]))
        self.assertEqual(project_previous.call_count, 3)
        self.assertTrue(all(
            call.kwargs == {"isolate_values": False}
            for call in project_previous.call_args_list
        ))
        result_directory = backtest_result_archive.archive_directory(
            self.config["releaseRoot"],
            backtest_id,
            label="Backtest Result test directory",
        )
        self.assertTrue(result_directory.is_dir())
        self.assertFalse(result_directory.stat().st_mode & stat.S_IWUSR)
        self.assertEqual(result_repository.count_backtests(self.config), 0)

        recovered = backtest_result_service.recover_backtest_result_catalog(
            self.config,
            backtest_id,
            frozen,
        )
        self.assertEqual(recovered["metrics"]["cycleCount"], 3)
        self.assertEqual(result_repository.count_backtests(self.config), 1)

    def test_worker_rejects_sampler_length_that_disagrees_with_iteration(self):
        frozen = self.fixture.frozen_minimal_request("sampler-length-mismatch")
        with (
            mock.patch.object(
                sampler_runtime.RowMappingSampler,
                "__len__",
                return_value=4,
            ),
            self.assertRaisesRegex(
                RuntimeError,
                r"length contract mismatch: declared 4 cycle\(s\), emitted 3",
            ),
        ):
            backtest_worker.execute_backtest(
                self.config,
                frozen,
                backtest_id=resource_ids.new_resource_id("backtest"),
                execution_root=self.fixture.root / "sampler-length-mismatch",
            )

    def test_real_archived_python_module_runs_in_a_direct_pipeline_stage(self):
        source = b'''from strategy_devkit.module_sdk import UniverseModule


class DirectUniverse(UniverseModule):
    def update(self, value):
        return {"copied": value}


MODULE_CLASS = DirectUniverse
'''
        definition = module_publication.publish_module(self.config, {
            "kind": "Universe",
            "moduleId": "direct-universe-runtime",
            "name": "Direct Universe Runtime",
            "description": "Real direct-stage runtime regression fixture.",
            "activationMode": "PythonModule",
            "parameters": {},
            "configSchema": {"type": "object", "additionalProperties": False},
            "ports": {
                "inputs": {"value": {"schema": {"type": "number"}}},
                "outputs": {"copied": {"schema": {"type": "number"}}},
            },
            "files": [{
                "path": "module.py",
                "contentBase64": base64.b64encode(source).decode(),
                "executable": False,
            }],
        })["definition"]
        pipeline_id = "real-direct-stage"
        pipeline_service.archive_pipeline_if_changed(self.config, {
            "pipelineId": pipeline_id,
            "name": "Real Direct Stage",
            "config": {
                "observationInput": {
                    "whitelist": ["price.close"],
                    "blacklist": [],
                }
            },
            "instances": {
                "direct": {
                    "instanceId": "direct",
                    "kind": "Universe",
                    "moduleId": definition["moduleId"],
                    "version": definition["version"],
                    "config": {},
                    "inputs": {"value": "price.close"},
                    "outputs": {"copied": "direct.copied"},
                },
            },
            "stages": {"universe": ["direct"]},
            "signalGraph": {"nodes": [], "inputs": {}, "outputs": {}},
        })
        environment = self.passthrough_environment(
            "direct-stage-environment", ["price.close"]
        )
        analysis = self.graph_version(
            "analyses.json", "analysisId", analysis_presets.NEUTRAL_ANALYSIS_ID
        )

        result = self.execute_request(
            self.request(pipeline_id, self.row_sampler, environment, analysis),
        )
        cycles = self.result_projection(result["backtestId"], ["cycles"])["cycles"]
        self.assertEqual(
            [cycle["data"]["direct"]["copied"] for cycle in cycles],
            [10, 12, 13],
        )

    def test_temporary_python_modules_run_outside_the_engine_control_process(self):
        source = b"""import os\nfrom strategy_devkit.module_sdk import SignalModule\n\nclass PidModule(SignalModule):\n    def update(self):\n        return {\"pid\": os.getpid()}\n\nMODULE_CLASS = PidModule\n"""
        archived = module_publication.publish_module(self.config, {
            "kind": "Signal",
            "moduleId": "result-runtime-pid",
            "name": "Result Runtime PID",
            "description": "Result Runtime process-boundary fixture.",
            "activationMode": "PythonModule",
            "parameters": {},
            "configSchema": {"type": "object", "additionalProperties": False},
            "ports": {
                "inputs": {},
                "outputs": {"pid": {"schema": {"type": "integer"}}},
            },
            "files": [{
                "path": "module.py",
                "contentBase64": base64.b64encode(source).decode(),
                "executable": False,
            }],
        })["definition"]
        modules = [{
            "instanceId": "pid",
            "kind": archived["kind"],
            "moduleId": archived["moduleId"],
            "version": archived["version"],
            "config": {},
            "inputs": {},
            "outputs": {"pid": "runtime.pid"},
        }]
        pipeline = self.empty_pipeline("result-runtime-pid-pipeline")
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
        backtest = self.execute_request(
            self.request(pipeline["pipelineId"], self.row_sampler, environment, analysis),
        )
        result = self.result_projection(
            backtest["backtestId"],
            ["cycles.data.runtime.pid"],
            temporary_modules=modules,
        )
        pid = result["cycles"][0]["data"]["runtime"]["pid"]
        self.assertIsInstance(pid, int)
        self.assertNotEqual(pid, os.getpid())
