#!/usr/bin/env python3

import copy
import json
import stat
from pathlib import Path
from unittest import mock

from builtin_implementations import analysis_presets
from builtin_implementations import environment_presets
from engine.archive import dataset as dataset_archive
from engine.service import control_api as control
from engine.archive import version as version_archive
from engine.compiler import pipeline as pipeline_compiler
from engine.contracts import analysis as analysis_contracts
from engine.contracts import backtest as backtest_contracts
from engine.contracts import environment as environment_contracts
from engine.contracts import pipeline as pipeline_contracts
from engine.core import runtime_identity
from engine.repository import backtest_results as result_repository
from engine.repository import control_state
from engine.repository import datasets
from engine.repository import module_definitions
from engine.repository import pipelines as pipeline_repository
from engine.service import backtests as backtest_service
from engine.service import backtest_execution as backtest_execution_service
from engine.service import pipelines as pipeline_service
from tests.support.backtest_runtime import BacktestIntegrationTestCase

class BacktestFreezeIntegrationTests(BacktestIntegrationTestCase):
    def test_evidence_digest_contracts_preserve_historical_hash_identity(self):
        payload = {"z": "é", "a": [1, 2.0, True, None]}
        expected = (
            "15495f86e7e13c65f92ef300f4a021e45b637092740d78b83dc8869398072fcc"
        )
        self.assertEqual(
            pipeline_contracts.pipeline_manifest_digest(payload),
            expected,
        )
        self.assertEqual(
            backtest_contracts.backtest_evidence_digest(payload),
            "sha256:" + expected,
        )

    def test_pipeline_control_snapshot_contains_only_runtime_authorities(self):
        pipeline = self.empty_pipeline("minimal-control-snapshot")
        snapshot = json.loads(
            (Path(pipeline["archive"]["root"]) / "control-snapshot.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(
            set(snapshot),
            {
                "schemaVersion", "createdAt", "definition", "manifest",
                "manifestHash", "activeModuleDefinitions",
            },
        )
        metadata, execution_version = (
            pipeline_repository.load_pipeline_execution_version(
                self.config,
                pipeline["pipelineId"],
                pipeline["version"],
            )
        )
        manifest = pipeline_repository.load_pipeline_manifest(execution_version)
        owned_snapshot = pipeline_repository.load_pipeline_control_snapshot(
            execution_version,
            manifest,
        )
        self.assertEqual(metadata["currentVersion"], pipeline["version"])
        self.assertEqual(owned_snapshot, snapshot)

    def test_run_backtest_requires_an_explicit_frozen_snapshot(self):
        with self.assertRaisesRegex(ValueError, "explicitly frozen executionSnapshot"):
            backtest_execution_service.run_backtest(self.config, {})

    def test_empty_pipeline_environment_and_analysis_are_independent_versions(self):
        pipeline = self.empty_pipeline()
        sampler = self.row_sampler
        environment = self.graph_version(
            "environments.json", "environmentId", environment_presets.NEUTRAL_ENVIRONMENT_ID
        )
        analysis = self.graph_version(
            "analyses.json", "analysisId", analysis_presets.NEUTRAL_ANALYSIS_ID
        )
        result = self.execute_request(
            self.request(pipeline["pipelineId"], sampler, environment, analysis)
        )
        self.assertEqual(result["metrics"]["cycleCount"], 3)
        loaded = result_repository.get_backtest_meta(
            self.config, result["backtestId"]
        )
        chain = loaded["executionChain"]
        self.assertEqual(chain["pipeline"]["pipelineId"], pipeline["pipelineId"])
        self.assertEqual(chain["environment"]["environmentId"], environment["environmentId"])
        self.assertEqual(chain["analysis"]["analysisId"], analysis["analysisId"])
        self.assertNotIn("analysis", result["request"]["executionSnapshot"]["pipeline"]["definition"])

    def test_backtest_selects_the_requested_historical_pipeline_version(self):
        first = self.empty_pipeline("historical-pipeline")
        second = pipeline_service.archive_pipeline_if_changed(self.config, {
            "pipelineId": first["pipelineId"],
            "name": "Historical Pipeline v2",
            "config": {
                "observationInput": {"whitelist": [], "blacklist": []}
            },
            "instances": {},
            "stages": {},
            "signalGraph": {"nodes": [], "inputs": {}, "outputs": {}},
        })["definition"]
        self.assertNotEqual(first["version"], second["version"])
        sampler = self.row_sampler
        environment = self.graph_version(
            "environments.json", "environmentId", environment_presets.NEUTRAL_ENVIRONMENT_ID
        )
        analysis = self.graph_version(
            "analyses.json", "analysisId", analysis_presets.NEUTRAL_ANALYSIS_ID
        )
        request = self.request(first["pipelineId"], sampler, environment, analysis)
        request["pipeline"]["version"] = first["version"]
        frozen = backtest_service.freeze_backtest_request(self.config, request)
        self.assertEqual(frozen["executionSnapshot"]["pipeline"]["version"], first["version"])
        self.assertEqual(
            frozen["executionSnapshot"]["pipeline"]["definition"]["name"], first["name"]
        )

    def test_backtest_freezes_exact_verified_pipeline_module_definitions(self):
        module = next(
            item
            for item in module_definitions.load_pipeline_definitions(
                self.config
            ).values()
            if item["moduleId"] == "sma-indicator"
        )
        pipeline_id = "signal-pipeline"
        pipeline_service.archive_pipeline_if_changed(self.config, {
            "pipelineId": pipeline_id,
            "name": "Signal Pipeline",
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
        environment = self.passthrough_environment(
            "price-close-environment", ["price.close"]
        )
        analysis = self.graph_version(
            "analyses.json", "analysisId", analysis_presets.NEUTRAL_ANALYSIS_ID
        )
        frozen = backtest_service.freeze_backtest_request(
            self.config, self.request(pipeline_id, sampler, environment, analysis)
        )
        definitions = frozen["executionSnapshot"]["pipeline"]["moduleDefinitions"]
        self.assertEqual(set(definitions), {f"Signal/sma-indicator/{module['version']}"})
        self.assertEqual(
            frozen["executionSnapshot"]["pipeline"]["definition"]["pipelineId"],
            pipeline_id,
        )
        version_archive.verify_record(next(iter(definitions.values())))
        result = backtest_execution_service.run_backtest(self.config, frozen)
        loaded = self.result_projection(
            result["backtestId"], ["cycles", "executionChain"]
        )
        self.assertEqual(loaded["cycles"][-1]["data"]["signal"]["sma"], 12.5)
        self.assertEqual(
            loaded["executionChain"]["timings"]["resultWriter"]["scope"],
            "streamed-cycles",
        )
        self.assertGreater(
            loaded["executionChain"]["timings"]["resultWriter"][
                "encodedCycleCharacters"
            ],
            0,
        )

    def test_backtest_request_rejects_strategy_specific_routing_fields(self):
        pipeline = self.empty_pipeline("no-routing-pipeline")
        sampler = self.row_sampler
        environment = self.graph_version(
            "environments.json", "environmentId", environment_presets.NEUTRAL_ENVIRONMENT_ID
        )
        analysis = self.graph_version(
            "analyses.json", "analysisId", analysis_presets.NEUTRAL_ANALYSIS_ID
        )
        request = self.request(pipeline["pipelineId"], sampler, environment, analysis)
        request["pipelineEventRouting"] = {
            "eventDataKey": "strategy.eventType",
            "executionOnlyValues": ["open"],
        }
        with self.assertRaisesRegex(ValueError, "unsupported field.*pipelineEventRouting"):
            backtest_service.freeze_backtest_request(self.config, request)

    def test_tampered_dataset_sampler_and_snapshot_are_rejected_before_execution(self):
        pipeline = self.empty_pipeline("verification-pipeline")
        sampler = self.row_sampler
        environment = self.graph_version(
            "environments.json", "environmentId", environment_presets.NEUTRAL_ENVIRONMENT_ID
        )
        analysis = self.graph_version(
            "analyses.json", "analysisId", analysis_presets.NEUTRAL_ANALYSIS_ID
        )
        request = self.request(pipeline["pipelineId"], sampler, environment, analysis)
        frozen = backtest_service.freeze_backtest_request(self.config, request)
        changed_parameters = copy.deepcopy(frozen)
        changed_parameters["sampler"]["parameters"]["forged"] = True
        with self.assertRaisesRegex(ValueError, "execution inputs do not match"):
            backtest_execution_service.run_backtest(self.config, changed_parameters)

        changed_limit = copy.deepcopy(frozen)
        changed_limit["limit"] = 1
        with self.assertRaisesRegex(ValueError, "execution inputs do not match"):
            backtest_execution_service.run_backtest(self.config, changed_limit)

        forged = copy.deepcopy(frozen)
        forged["executionSnapshot"]["pipeline"]["manifest"]["name"] = "forged"
        with self.assertRaisesRegex(ValueError, "snapshot hash is invalid"):
            backtest_execution_service.run_backtest(self.config, forged)

        different_runtime = {
            **runtime_identity.engine_runtime_identity(),
            "buildId": "engine:different-build",
        }
        with mock.patch.object(
            runtime_identity, "engine_runtime_identity", return_value=different_runtime
        ):
            with self.assertRaisesRegex(ValueError, "different Engine/Python runtime"):
                backtest_execution_service.run_backtest(self.config, frozen)

        incomplete = copy.deepcopy(frozen)
        incomplete["executionSnapshot"].pop("samplerDefinition")
        unsigned = {
            key: value
            for key, value in incomplete["executionSnapshot"].items()
            if key != "snapshotHash"
        }
        incomplete["executionSnapshot"]["snapshotHash"] = (
            "sha256:" + control.json_digest(unsigned)
        )
        with self.assertRaisesRegex(ValueError, "samplerDefinition"):
            backtest_execution_service.run_backtest(self.config, incomplete)

        unknown = copy.deepcopy(frozen)
        unknown["executionSnapshot"]["unexpected"] = True
        unknown_unsigned = {
            key: value
            for key, value in unknown["executionSnapshot"].items()
            if key != "snapshotHash"
        }
        unknown["executionSnapshot"]["snapshotHash"] = (
            "sha256:" + control.json_digest(unknown_unsigned)
        )
        with self.assertRaisesRegex(ValueError, "unsupported field.*unexpected"):
            backtest_execution_service.run_backtest(self.config, unknown)

        incomplete_manifest = copy.deepcopy(frozen)
        pipeline_snapshot = incomplete_manifest["executionSnapshot"]["pipeline"]
        pipeline_snapshot["manifest"].pop("topology")
        pipeline_snapshot["manifestHash"] = control.json_digest(
            pipeline_snapshot["manifest"]
        )
        manifest_unsigned = {
            key: value
            for key, value in incomplete_manifest["executionSnapshot"].items()
            if key != "snapshotHash"
        }
        incomplete_manifest["executionSnapshot"]["snapshotHash"] = (
            "sha256:" + control.json_digest(manifest_unsigned)
        )
        with self.assertRaisesRegex(ValueError, "Definition identity|topology"):
            backtest_execution_service.run_backtest(self.config, incomplete_manifest)

        missing_artifact = copy.deepcopy(frozen)
        missing_artifact["executionSnapshot"].pop("compositionArtifact")
        unsigned = {
            key: value
            for key, value in missing_artifact["executionSnapshot"].items()
            if key != "snapshotHash"
        }
        missing_artifact["executionSnapshot"]["snapshotHash"] = (
            "sha256:" + control.json_digest(unsigned)
        )
        with self.assertRaisesRegex(ValueError, "compositionArtifact"):
            backtest_execution_service.run_backtest(self.config, missing_artifact)

        old_schema = copy.deepcopy(frozen)
        old_schema["executionSnapshot"]["schemaVersion"] = 9
        unsigned = {
            key: value
            for key, value in old_schema["executionSnapshot"].items()
            if key != "snapshotHash"
        }
        old_schema["executionSnapshot"]["snapshotHash"] = (
            "sha256:" + control.json_digest(unsigned)
        )
        with self.assertRaisesRegex(ValueError, "schemaVersion 12 is required"):
            backtest_execution_service.run_backtest(self.config, old_schema)

        tampered_edge = copy.deepcopy(frozen)
        artifact = tampered_edge["executionSnapshot"]["compositionArtifact"]
        artifact["environmentPlan"]["edges"].append({})
        artifact["artifactHash"] = "sha256:" + control.json_digest({
            key: value for key, value in artifact.items() if key != "artifactHash"
        })
        unsigned = {
            key: value
            for key, value in tampered_edge["executionSnapshot"].items()
            if key != "snapshotHash"
        }
        tampered_edge["executionSnapshot"]["snapshotHash"] = (
            "sha256:" + control.json_digest(unsigned)
        )
        with mock.patch(
            "engine.runtime.module_invoker.ModuleInvoker",
            side_effect=AssertionError("Pipeline Module initialized before artifact rejection"),
        ), mock.patch(
            "engine.runtime.module_invoker.ModuleInvoker",
            side_effect=AssertionError("Graph Module initialized before artifact rejection"),
        ):
            with self.assertRaisesRegex(ValueError, "edges"):
                backtest_execution_service.run_backtest(self.config, tampered_edge)

        tampered_contract = copy.deepcopy(frozen)
        artifact = tampered_contract["executionSnapshot"]["compositionArtifact"]
        artifact["cycleContracts"]["forged"] = {"type": "number"}
        artifact["artifactHash"] = "sha256:" + control.json_digest({
            key: value for key, value in artifact.items() if key != "artifactHash"
        })
        unsigned = {
            key: value
            for key, value in tampered_contract["executionSnapshot"].items()
            if key != "snapshotHash"
        }
        tampered_contract["executionSnapshot"]["snapshotHash"] = (
            "sha256:" + control.json_digest(unsigned)
        )
        with mock.patch(
            "engine.runtime.module_invoker.ModuleInvoker",
            side_effect=AssertionError("Pipeline Module initialized before artifact rejection"),
        ), mock.patch(
            "engine.runtime.module_invoker.ModuleInvoker",
            side_effect=AssertionError("Graph Module initialized before artifact rejection"),
        ):
            with self.assertRaisesRegex(ValueError, "cycleContracts"):
                backtest_execution_service.run_backtest(self.config, tampered_contract)

        tampered_digest = copy.deepcopy(frozen)
        tampered_digest["executionSnapshot"]["compositionArtifact"][
            "artifactHash"
        ] = "sha256:" + "0" * 64
        unsigned = {
            key: value
            for key, value in tampered_digest["executionSnapshot"].items()
            if key != "snapshotHash"
        }
        tampered_digest["executionSnapshot"]["snapshotHash"] = (
            "sha256:" + control.json_digest(unsigned)
        )
        with mock.patch(
            "engine.runtime.module_invoker.ModuleInvoker",
            side_effect=AssertionError("Pipeline Module initialized before artifact rejection"),
        ), mock.patch(
            "engine.runtime.module_invoker.ModuleInvoker",
            side_effect=AssertionError("Graph Module initialized before artifact rejection"),
        ):
            with self.assertRaisesRegex(ValueError, "artifact hash is invalid"):
                backtest_execution_service.run_backtest(self.config, tampered_digest)

        extra_artifact_field = copy.deepcopy(frozen)
        artifact = extra_artifact_field["executionSnapshot"]["compositionArtifact"]
        artifact["fallback"] = True
        artifact["artifactHash"] = "sha256:" + control.json_digest({
            key: value for key, value in artifact.items() if key != "artifactHash"
        })
        unsigned = {
            key: value
            for key, value in extra_artifact_field["executionSnapshot"].items()
            if key != "snapshotHash"
        }
        extra_artifact_field["executionSnapshot"]["snapshotHash"] = (
            "sha256:" + control.json_digest(unsigned)
        )
        with mock.patch(
            "engine.runtime.module_invoker.ModuleInvoker",
            side_effect=AssertionError("Pipeline Module initialized before artifact rejection"),
        ), mock.patch(
            "engine.runtime.module_invoker.ModuleInvoker",
            side_effect=AssertionError("Graph Module initialized before artifact rejection"),
        ):
            with self.assertRaisesRegex(ValueError, "unsupported field.*fallback"):
                backtest_execution_service.run_backtest(
                    self.config,
                    extra_artifact_field,
                )

        dataset = datasets.ensure_dataset_version(
            self.config,
            "prices",
            datasets.get_dataset(self.config, "prices")["latestVersionId"],
        )
        csv_path = Path(dataset["storage"]["uri"]) / "bars.csv"
        csv_path.chmod(csv_path.stat().st_mode | stat.S_IWUSR)
        csv_path.write_text(csv_path.read_text() + "\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "writable|verification failed|content hash mismatch"):
            backtest_service.freeze_backtest_request(self.config, request)

    def test_dataset_container_requires_its_sealed_manifest_file(self):
        dataset = datasets.ensure_dataset_version(
            self.config,
            "prices",
            datasets.get_dataset(self.config, "prices")["latestVersionId"],
        )
        root = Path(dataset["storage"]["uri"])
        root.chmod(root.stat().st_mode | stat.S_IWUSR)
        (root / dataset_archive.MANIFEST_NAME).unlink()
        root.chmod(root.stat().st_mode & ~0o222)
        with self.assertRaisesRegex(ValueError, "missing _dataset.json"):
            dataset_archive.verify_sealed_container(
                root, dataset["manifest"], dataset["contentHash"]
            )

    def test_composition_preflight_rejects_pipeline_draft_without_archive_side_effects(self):
        pipeline = self.empty_pipeline("preflight-pipeline")
        sampler = self.row_sampler
        environment = self.graph_version(
            "environments.json", "environmentId", environment_presets.NEUTRAL_ENVIRONMENT_ID
        )
        analysis = self.graph_version(
            "analyses.json", "analysisId", analysis_presets.NEUTRAL_ANALYSIS_ID
        )
        request = self.request(pipeline["pipelineId"], sampler, environment, analysis)
        draft = {
            key: copy.deepcopy(pipeline[key])
            for key in control.PIPELINE_DRAFT_FIELDS
            if key in pipeline
        }
        draft["name"] = "Unsaved preflight change"
        request["pipelineDraft"] = draft
        versions_before = len(pipeline_repository.pipeline_versions(self.config, pipeline["pipelineId"]))
        with self.assertRaisesRegex(ValueError, "unsupported field.*pipelineDraft"):
            backtest_service.validate_backtest_composition(self.config, request)
        self.assertEqual(
            len(pipeline_repository.pipeline_versions(self.config, pipeline["pipelineId"])),
            versions_before,
        )

    def test_composition_fixed_point_binds_one_verified_pipeline_template(self):
        pipeline = self.empty_pipeline("verified-template-pipeline")
        environment = self.graph_version(
            "environments.json", "environmentId", environment_presets.NEUTRAL_ENVIRONMENT_ID
        )
        analysis = self.graph_version(
            "analyses.json", "analysisId", analysis_presets.NEUTRAL_ANALYSIS_ID
        )
        request = self.request(
            pipeline["pipelineId"], self.row_sampler, environment, analysis
        )
        state_binder = pipeline_compiler.bind_pipeline_contract_plan
        with mock.patch.object(
            pipeline_compiler,
            "compile_pipeline_contract_plan",
            side_effect=AssertionError(
                "composition fixed-point returned to the raw strict entry"
            ),
        ), mock.patch.object(
            pipeline_compiler,
            "bind_pipeline_contract_plan",
            wraps=state_binder,
        ) as bind:
            resolved = backtest_service.resolve_backtest_composition(
                self.config, request
            )
        self.assertGreaterEqual(bind.call_count, 1)
        self.assertEqual(
            resolved["pipelinePlan"]["topology"],
            resolved["pipelineManifest"]["topology"],
        )

    def test_composition_rejects_analysis_draft_without_archiving(self):
        pipeline = self.empty_pipeline("invalid-composition-pipeline")
        sampler = self.row_sampler
        environment = self.graph_version(
            "environments.json", "environmentId", environment_presets.NEUTRAL_ENVIRONMENT_ID
        )
        analysis = self.graph_version(
            "analyses.json", "analysisId", analysis_presets.PERFORMANCE_ANALYSIS_ID
        )
        request = self.request(pipeline["pipelineId"], sampler, environment, analysis)
        analysis_draft = {
            key: copy.deepcopy(analysis[key])
            for key in analysis_contracts.ANALYSIS_DRAFT_FIELDS
            if key in analysis
        }
        analysis_draft["analysisId"] = "invalid-composition-analysis"
        numeric_change = max(
            (
                definition
                for definition in module_definitions.load_analysis_definitions(
                    self.config
                ).values()
                if definition["moduleId"] == "numeric-change-analyzer"
            ),
            key=lambda definition: int(definition["version"]),
        )
        analysis_draft["instances"] = {
            "change": {
                "instanceId": "change",
                "kind": "Analyzer",
                "moduleId": numeric_change["moduleId"],
                "version": numeric_change["version"],
                "config": {},
                "inputs": {"current": "wire.current"},
                "outputs": {
                    "change": "wire.change",
                    "return": "wire.return",
                },
            }
        }
        analysis_draft["graph"] = {
            "nodes": ["change"],
            "inputs": {
                "current-input": {
                    "dataKey": "last.missing.equity",
                    "wire": "wire.current",
                }
            },
            "outputs": {
                "change-output": {
                    "dataKey": "analysis.change",
                    "wire": "wire.change",
                }
            },
        }
        request["analysisDraft"] = analysis_draft
        versions_before = len([
            item
            for item in control_state.load_state(
                self.config,
                "analyses.json",
                {},
            ).values()
            if item["analysisId"] == analysis_draft["analysisId"]
        ])
        with self.assertRaisesRegex(ValueError, "unsupported field.*analysisDraft"):
            backtest_service.freeze_backtest_request(self.config, request)
        versions_after = len([
            item
            for item in control_state.load_state(
                self.config,
                "analyses.json",
                {},
            ).values()
            if item["analysisId"] == analysis_draft["analysisId"]
        ])
        self.assertEqual(versions_after, versions_before)

    def test_unknown_nested_backtest_field_is_rejected_before_draft_archive(self):
        pipeline = self.empty_pipeline("exact-request-pipeline")
        sampler = self.row_sampler
        environment = self.graph_version(
            "environments.json", "environmentId", environment_presets.NEUTRAL_ENVIRONMENT_ID
        )
        analysis = self.graph_version(
            "analyses.json", "analysisId", analysis_presets.NEUTRAL_ANALYSIS_ID
        )
        request = self.request(pipeline["pipelineId"], sampler, environment, analysis)
        request["sampler"]["latest"] = True
        versions_before = len(pipeline_repository.pipeline_versions(self.config, pipeline["pipelineId"]))
        with self.assertRaisesRegex(ValueError, "unsupported field.*latest"):
            backtest_service.freeze_backtest_request(self.config, request)
        self.assertEqual(
            len(pipeline_repository.pipeline_versions(self.config, pipeline["pipelineId"])),
            versions_before,
        )
