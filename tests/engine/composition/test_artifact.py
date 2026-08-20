#!/usr/bin/env python3

import base64
import copy
import hashlib
import pickle
from pathlib import Path
from unittest import mock

from builtin_implementations import analysis_presets
from builtin_implementations import environment_presets
from engine.runtime import graph_cycle as graph_cycle_runtime
import engine_service
from engine.service import control_api as control
from engine.authority import graph as graph_authority
from engine.authority import runtime_data as runtime_data_authority
from engine.compiler import graph as graph_compiler
from engine.compiler import pipeline as pipeline_compiler
from engine.composition import backtest as backtest_composition
from engine.contracts import strict_json
from engine.contracts import digest as digest_contracts
from engine.contracts import sampler as sampler_contracts
from engine.contracts.observation_projection import observation_contract_digest
from engine.runtime.pipeline import BacktestPipelineRuntime
from engine.worker import backtest_preparation
from engine.repository import datasets as dataset_repository
from engine.repository import graph_resources
from engine.repository import pipelines as pipeline_repository
from engine.repository import samplers as sampler_repository
from engine.repository import module_definitions
from engine.service import module_publication
from engine.service import pipelines as pipeline_service
from engine.service import backtest_execution as backtest_execution_service
from engine.service import backtests as backtest_service
from tests.support.backtest_runtime import BacktestIntegrationTestCase

class BacktestArtifactIntegrationTests(BacktestIntegrationTestCase):
    def test_observation_authority_does_not_make_optional_roots_required(self):
        environment_authority = object()
        pipeline_authority_value = object()
        environment_contracts = {
            "required": {
                "type": "object",
                "properties": {"value": {"type": "number"}},
                "required": ["value"],
                "additionalProperties": False,
            },
            "optional": {
                "type": "object",
                "properties": {"value": {"type": "string"}},
                "required": ["value"],
                "additionalProperties": False,
            },
        }
        expanded_declared = {
            **environment_contracts,
            "required.value": {"type": "number"},
            "optional.value": {"type": "string"},
        }
        pipeline_material = (
            "pipeline",
            "1",
            "hash",
            object(),
            {
                "inputContracts": expanded_declared,
                "inputRequiredRoots": ["required"],
                "observationInput": {
                    "whitelist": ["optional", "required"],
                    "blacklist": [],
                },
                "observationContractDigest": observation_contract_digest(
                    environment_contracts,
                    {"required"},
                ),
            },
            object(),
            {},
        )
        with mock.patch.object(
            runtime_data_authority,
            "_environment_output_contract_state",
            return_value=(environment_contracts, frozenset({"required"})),
        ), mock.patch.object(
            runtime_data_authority.pipeline_authority,
            "compiled_pipeline_authority_material",
            return_value=pipeline_material,
        ), mock.patch.object(
            runtime_data_authority.pipeline_authority,
            "pipeline_contract_template_material",
            return_value={
                "config": {
                    "observationInput": {
                        "whitelist": ["optional", "required"],
                        "blacklist": [],
                    }
                }
            },
        ):
            authority = (
                runtime_data_authority.bind_observation_projection_authority(
                    environment_authority,
                    pipeline_authority_value,
                )
            )
        self.assertIs(
            runtime_data_authority.require_observation_projection_authority(
                authority,
                environment_authority=environment_authority,
                pipeline_authority_value=pipeline_authority_value,
            ),
            authority,
        )

    def test_composed_observation_proof_feeds_pipeline_exactly_once(self):
        frozen = self.fixture.frozen_minimal_request(
            "environment-pipeline-proof"
        )
        request = copy.deepcopy(frozen)
        snapshot = request["executionSnapshot"]
        prepared = backtest_preparation.prepare_backtest_execution(
            self.config,
            request,
            dataset_id=request["datasetId"],
            execution_snapshot=snapshot,
            pipeline_id=request["pipeline"]["pipelineId"],
            pipeline_version=request["pipeline"]["version"],
        )
        pipeline, environment, analysis, _contracts, _roots = (
            backtest_composition.create_backtest_graph_runtimes(
                execution_root=None,
                verified_composition=prepared.verified_composition,
            )
        )
        try:
            artifact = snapshot["compositionArtifact"]
            sample = {}
            for data_key in artifact["samplerContracts"]:
                if data_key.count(".") == 1:
                    root, child = data_key.split(".")
                    sample.setdefault(root, {})[child] = 10.0
            proof = environment.execute_observation(
                sample,
                {},
                "2026-01-01T00:00:00Z",
            )
            current = pipeline.execute_observation(proof)
            self.assertIsInstance(current, dict)
            with self.assertRaisesRegex(RuntimeError, "already been consumed"):
                pipeline.execute_observation(proof)
        finally:
            for resource in (analysis, environment, pipeline):
                resource.close()

    def test_validated_artifact_authority_is_immutable_and_plan_bound(self):
        frozen = self.fixture.frozen_minimal_request(
            "validated-artifact-authority"
        )
        artifact = frozen["executionSnapshot"]["compositionArtifact"]
        proof = backtest_composition.validate_backtest_composition_artifact(
            artifact
        )
        self.assertEqual(
            backtest_composition.validated_backtest_artifact_material(proof),
            artifact,
        )
        for operation in (
            lambda: copy.copy(proof),
            lambda: copy.deepcopy(proof),
            lambda: pickle.dumps(proof),
        ):
            with self.assertRaisesRegex(TypeError, "cannot be"):
                operation()

        changed_artifact = (
            backtest_composition.validate_backtest_composition_artifact(artifact)
        )
        object.__setattr__(changed_artifact, "_artifact_json", "{}")
        with self.assertRaisesRegex(ValueError, "canonical authority"):
            backtest_composition.validated_backtest_artifact_material(
                changed_artifact
            )

        changed_plan = (
            backtest_composition.validate_backtest_composition_artifact(artifact)
        )
        plan_proof = (
            backtest_composition.validated_backtest_artifact_pipeline_plan(
                changed_plan
            )
        )
        replacement = "{}"
        replacement_digest = hashlib.sha256(replacement.encode("utf-8")).hexdigest()
        object.__setattr__(plan_proof, "_plan_json", replacement)
        object.__setattr__(plan_proof, "_plan_digest", replacement_digest)
        object.__setattr__(
            changed_plan,
            "_pipeline_plan_digest",
            replacement_digest,
        )
        with self.assertRaisesRegex(ValueError, "does not belong"):
            backtest_composition.validated_backtest_artifact_material(
                changed_plan
            )

    def test_environment_outputs_feed_pipeline_while_analysis_never_feeds_last(self):
        pipeline = pipeline_service.archive_pipeline_if_changed(self.config, {
            "pipelineId": "paper-pipeline",
            "name": "paper-pipeline",
            "config": {
                "observationInput": {
                    "whitelist": ["broker"],
                    "blacklist": [],
                }
            },
            "instances": {},
            "stages": {},
            "signalGraph": {"nodes": [], "inputs": {}, "outputs": {}},
        })["definition"]
        sampler = sampler_repository.save_sampler(self.config, {
            "samplerId": "execution-value-sampler",
            "name": "Execution Value",
            "type": "row-map",
            "config": {
                "mapping": {"market.execution_value": "close"},
                "includeUnmappedFields": False,
                "unmappedPrefix": "dataset.",
            },
            "parameterSchema": sampler_contracts.infer_sampler_parameter_schema({
                "mapping": {"market.execution_value": "close"},
                "includeUnmappedFields": False,
                "unmappedPrefix": "dataset.",
            }),
            "outputSchema": {"market.execution_value": {"type": "number"}},
            "source": "",
            "entryPoint": "",
        })
        environment = self.graph_version(
            "environments.json", "environmentId", environment_presets.PAPER_ENVIRONMENT_ID
        )
        analysis = self.graph_version(
            "analyses.json", "analysisId", analysis_presets.PERFORMANCE_ANALYSIS_ID
        )
        result = self.execute_request(
            self.request(pipeline["pipelineId"], sampler, environment, analysis)
        )
        cycles = self.result_projection(result["backtestId"], ["cycles"])["cycles"]
        self.assertEqual(len(cycles), 3)
        self.assertIn("broker", cycles[-1]["data"])
        self.assertIn("analysis", cycles[-1]["data"])
        performance = cycles[-1]["data"]["analysis"]["performance"]
        self.assertEqual(performance["observationCount"], 3)
        self.assertEqual(
            result["metrics"]["analysis"],
            {"analysis": cycles[-1]["data"]["analysis"]},
        )
        self.assertNotIn(
            "analysis",
            result["request"]["executionSnapshot"]["pipeline"]["definition"],
        )

    def test_pipeline_cannot_read_sampler_data_not_exported_by_environment(self):
        module = next(
            item
            for item in module_definitions.load_pipeline_definitions(
                self.config
            ).values()
            if item["moduleId"] == "sma-indicator"
        )
        pipeline_id = "environment-boundary-pipeline"
        pipeline_service.archive_pipeline_if_changed(self.config, {
            "pipelineId": pipeline_id,
            "name": "Environment Boundary Pipeline",
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
                    "moduleId": module["moduleId"],
                    "version": module["version"],
                    "config": {"period": 2},
                    "inputs": {"value": "wire.close"},
                    "outputs": {"sma": "wire.sma"},
                },
            },
            "stages": {},
            "signalGraph": {
                "nodes": ["sma"],
                "inputs": {"close": {"dataKey": "price.close", "wire": "wire.close"}},
                "outputs": {"sma-output": {"dataKey": "signal.sma", "wire": "wire.sma"}},
            },
        })
        sampler = self.row_sampler
        environment = self.graph_version(
            "environments.json", "environmentId", environment_presets.NEUTRAL_ENVIRONMENT_ID
        )
        analysis = self.graph_version(
            "analyses.json", "analysisId", analysis_presets.NEUTRAL_ANALYSIS_ID
        )
        with self.assertRaisesRegex(ValueError, "unavailable Observation DataKey 'price.close'"):
            backtest_service.freeze_backtest_request(
                self.config,
                self.request(pipeline_id, sampler, environment, analysis),
            )

    def test_composition_artifact_preserves_parent_child_order_across_canonical_worker_transport(self):
        direct_source = b'''from strategy_devkit.module_sdk import UniverseModule


class OrderedParentChildUniverse(UniverseModule):
    def update(self, value):
        return {
            "z_parent": {"base": value},
            "a_child": value + 100,
        }


MODULE_CLASS = OrderedParentChildUniverse
'''
        object_schema = {
            "type": "object",
            "properties": {"base": {"type": "number"}},
            "required": ["base"],
            "additionalProperties": False,
        }
        direct_definition = module_publication.publish_module(self.config, {
            "kind": "Universe",
            "moduleId": "ordered-parent-child-universe",
            "name": "Ordered parent-child Universe",
            "description": "Writes a parent before its child DataKey.",
            "activationMode": "PythonModule",
            "parameters": {},
            "configSchema": {"type": "object", "additionalProperties": False},
            "ports": {
                "inputs": {"value": {"schema": {"type": "number"}}},
                "outputs": {
                    "z_parent": {"schema": object_schema},
                    "a_child": {"schema": {"type": "number"}},
                },
            },
            "files": [{
                "path": "module.py",
                "contentBase64": base64.b64encode(direct_source).decode(),
                "executable": False,
            }],
        })["definition"]
        pipeline_id = "canonical-composition-pipeline"
        pipeline = pipeline_service.archive_pipeline_if_changed(self.config, {
            "pipelineId": pipeline_id,
            "name": "Canonical composition Pipeline",
            "config": {
                "observationInput": {
                    "whitelist": ["x.child"],
                    "blacklist": [],
                }
            },
            "instances": {
                "ordered": {
                    "instanceId": "ordered",
                    "kind": "Universe",
                    "moduleId": direct_definition["moduleId"],
                    "version": direct_definition["version"],
                    "config": {},
                    "inputs": {"value": "x.child"},
                    "outputs": {
                        "z_parent": "pipelineState",
                        "a_child": "pipelineState.child",
                    },
                },
            },
            "stages": {"universe": ["ordered"]},
            "signalGraph": {"nodes": [], "inputs": {}, "outputs": {}},
        })["definition"]
        sampler_config = {
            "mapping": {"x.base": "close", "x.child": "high"},
            "includeUnmappedFields": False,
            "unmappedPrefix": "dataset.",
        }
        sampler = sampler_repository.save_sampler(self.config, {
            "samplerId": "canonical-composition-sampler",
            "name": "Canonical composition sampler",
            "type": "row-map",
            "config": sampler_config,
            "parameterSchema": sampler_contracts.infer_sampler_parameter_schema(
                sampler_config
            ),
            "outputSchema": {
                "x.base": {"type": "number"},
                "x.child": {"type": "number"},
            },
            "source": "",
            "entryPoint": "",
        })
        environment = engine_service.handle_save_environment(self.config, {
            "schemaVersion": 2,
            "environmentId": "canonical-parent-child-environment",
            "name": "Canonical parent-child Environment",
            "description": "Preserves overlapping ordered boundary writes.",
            "instances": {},
            "graph": {
                "nodes": [],
                "inputs": {
                    "parent-input": {"dataKey": "x", "wire": "wire.parent"},
                    "child-input": {
                        "dataKey": "x.child",
                        "wire": "wire.child",
                    },
                },
                "outputs": {
                    "z-parent": {"dataKey": "x", "wire": "wire.parent"},
                    "a-child": {
                        "dataKey": "x.child",
                        "wire": "wire.child",
                    },
                },
            },
        })["definition"]
        analysis = engine_service.handle_save_analysis(self.config, {
            "schemaVersion": 1,
            "analysisId": "canonical-parent-child-analysis",
            "name": "Canonical parent-child Analysis",
            "description": "Preserves overlapping ordered result writes.",
            "instances": {},
            "graph": {
                "nodes": [],
                "inputs": {
                    "parent-input": {
                        "source": "currentPipeline",
                        "dataKey": "pipelineState",
                        "wire": "wire.parent",
                    },
                    "child-input": {
                        "source": "currentPipeline",
                        "dataKey": "pipelineState.child",
                        "wire": "wire.child",
                    },
                },
                "outputs": {
                    "z-parent": {
                        "dataKey": "result",
                        "wire": "wire.parent",
                    },
                    "a-child": {
                        "dataKey": "result.child",
                        "wire": "wire.child",
                    },
                },
            },
        })["definition"]
        frozen = backtest_service.freeze_backtest_request(
            self.config,
            self.request(pipeline["pipelineId"], sampler, environment, analysis),
        )
        artifact_before = frozen["executionSnapshot"]["compositionArtifact"]
        for plan_field in ("environmentPlan", "analysisPlan"):
            plan = artifact_before[plan_field]
            output_ids = set(plan["outputs"])
            self.assertEqual(
                [
                    edge["to"]["node"]
                    for edge in plan["edges"]
                    if edge["to"]["node"] in output_ids
                ],
                ["z-parent", "a-child"],
            )

        transported = strict_json.loads(strict_json.dumps(frozen, sort_keys=True))
        transported_snapshot = transported["executionSnapshot"]
        self.assertEqual(
            list(transported_snapshot["environmentDefinition"]["graph"]["outputs"]),
            ["a-child", "z-parent"],
        )
        self.assertEqual(
            list(transported_snapshot["analysisDefinition"]["graph"]["outputs"]),
            ["a-child", "z-parent"],
        )
        frozen_pipeline_module = transported_snapshot["pipeline"]["manifest"][
            "modules"
        ][0]
        self.assertEqual(
            list(frozen_pipeline_module["outputs"]),
            ["a_child", "z_parent"],
        )
        artifact_after = transported_snapshot["compositionArtifact"]
        for plan_field in ("pipelinePlan", "environmentPlan", "analysisPlan"):
            self.assertEqual(
                strict_json.dumps(artifact_before[plan_field], sort_keys=True),
                strict_json.dumps(artifact_after[plan_field], sort_keys=True),
            )

        for state_file in (
            "pipelines.json",
            "environments.json",
            "analyses.json",
            "modules.json",
            "environment-modules.json",
            "analysis-modules.json",
        ):
            (Path(self.config["controlRoot"]) / state_file).unlink()
        apply_graph_contracts = backtest_composition.apply_compiled_graph_contracts
        asset_digest = digest_contracts.sha256_file_digest
        with mock.patch.object(
            backtest_service,
            "resolve_backtest_composition",
            side_effect=AssertionError("worker returned to the freeze resolver"),
        ), mock.patch.object(
            dataset_repository,
            "get_dataset",
            side_effect=AssertionError("worker read the current Dataset index"),
        ), mock.patch.object(
            pipeline_repository,
            "load_pipeline_execution_version",
            side_effect=AssertionError("worker read the current Pipeline index"),
        ), mock.patch.object(
            sampler_repository,
            "get_sampler_execution_version",
            side_effect=AssertionError("worker read the current Sampler index"),
        ), mock.patch.object(
            graph_resources,
            "load_version",
            side_effect=AssertionError("worker read a current Graph index"),
        ), mock.patch.object(
            module_definitions,
            "load_definition_versions",
            side_effect=AssertionError("worker read the current Module index"),
        ), mock.patch.object(
            graph_compiler,
            "compile_module_graph",
            side_effect=AssertionError("worker returned to the raw fixed-point compiler"),
        ), mock.patch.object(
            graph_compiler,
            "compile_module_graph_authority",
            side_effect=AssertionError("worker recompiled a raw Graph authority"),
        ), mock.patch.object(
            graph_compiler,
            "compile_verified_module_graph",
            side_effect=AssertionError("worker returned to a preliminary Graph compiler"),
        ), mock.patch.object(
            graph_compiler,
            "compile_verified_module_graph_authority",
            side_effect=AssertionError("worker recompiled a verified Graph authority"),
        ), mock.patch.object(
            graph_authority,
            "bind_compiled_graph_authority_plan",
            side_effect=AssertionError("worker rebound a recompiled Graph plan"),
        ), mock.patch.object(
            BacktestPipelineRuntime,
            "__init__",
            side_effect=AssertionError("worker used the public raw Pipeline Runtime"),
        ), mock.patch.object(
            graph_cycle_runtime.EnvironmentGraphRuntime,
            "__init__",
            side_effect=AssertionError("worker used the raw Environment Runtime"),
        ), mock.patch.object(
            graph_cycle_runtime.AnalysisGraphRuntime,
            "__init__",
            side_effect=AssertionError("worker used the raw Analysis Runtime"),
        ), mock.patch.object(
            pipeline_compiler,
            "bind_pipeline_contract_plan",
            side_effect=AssertionError("worker rebuilt the Pipeline contract plan"),
        ), mock.patch.object(
            pipeline_compiler,
            "pipeline_contract_template_from_verified_authorities",
            side_effect=AssertionError("worker entered the compile-time Pipeline template path"),
        ), mock.patch.object(
            backtest_composition,
            "apply_compiled_graph_contracts",
            wraps=apply_graph_contracts,
        ) as apply_contracts, mock.patch.object(
            digest_contracts,
            "sha256_file_digest",
            wraps=asset_digest,
        ) as sampler_asset_digest:
            result = backtest_execution_service.run_backtest(
                self.config,
                transported,
            )
        self.assertEqual(result["metrics"]["cycleCount"], 3)
        self.assertEqual(apply_contracts.call_count, 2)
        self.assertEqual(sampler_asset_digest.call_count, 2)
        cycles = self.result_projection(result["backtestId"], ["cycles"])["cycles"]
        self.assertEqual(
            [cycle["data"]["pipelineState"] for cycle in cycles],
            [
                {"base": 11, "child": 111},
                {"base": 13, "child": 113},
                {"base": 14, "child": 114},
            ],
        )
        self.assertEqual(
            [cycle["data"]["result"] for cycle in cycles],
            [
                {"base": 11, "child": 111},
                {"base": 13, "child": 113},
                {"base": 14, "child": 114},
            ],
        )

        reordered = copy.deepcopy(transported)
        artifact = reordered["executionSnapshot"]["compositionArtifact"]
        environment_plan = artifact["environmentPlan"]
        environment_plan["edges"].reverse()
        environment_plan["requiredOutputs"].reverse()
        artifact["artifactHash"] = "sha256:" + control.json_digest({
            key: value for key, value in artifact.items() if key != "artifactHash"
        })
        unsigned = {
            key: value
            for key, value in reordered["executionSnapshot"].items()
            if key != "snapshotHash"
        }
        reordered["executionSnapshot"]["snapshotHash"] = (
            "sha256:" + control.json_digest(unsigned)
        )
        with mock.patch.object(
            backtest_composition,
            "create_backtest_graph_runtimes",
            side_effect=AssertionError("Runtime construction began before artifact rejection"),
        ):
            with self.assertRaisesRegex(ValueError, "environmentPlan"):
                backtest_execution_service.run_backtest(self.config, reordered)
