#!/usr/bin/env python3

import copy
import tempfile
import unittest
from pathlib import Path

from builtin_implementations import analysis_presets
from engine.compiler import analysis as analysis_compiler
from engine.contracts import analysis as analysis_contracts
from engine.service import analysis as analysis_service
from builtin_implementations import resources as builtin_resources
import engine_service
from engine.control import database as engine_database
from engine.repository import control_state
from engine.repository import module_definitions
from engine.repository import pipelines as pipeline_repository
from engine.authority.graph_cycle import (
    bind_compiled_cycle_graph_authority,
)
from engine.contracts.graph_cycle import (
    CURRENT_PIPELINE_SOURCE,
    cycle_input_contracts,
)
from engine.compiler.graph import compile_module_graph_authority
from engine.runtime.graph_cycle import AnalysisGraphRuntime, CycleGraphRuntime


def analysis_runtime(
    definition,
    module_definitions,
    input_contracts,
    *,
    required_roots=None,
    source_contracts=None,
    source_required_roots=None,
):
    graph_authority = compile_module_graph_authority(
        definition["graph"],
        definition["instances"],
        module_definitions,
        input_contracts,
        allowed_kinds={"Analyzer"},
        label="Analysis Graph",
        required_roots=required_roots,
        source_contracts=source_contracts,
        source_required_roots=source_required_roots,
    )
    authority = bind_compiled_cycle_graph_authority(
        definition,
        graph_authority,
        allowed_kind="Analyzer",
        graph_label="Analysis Graph",
        identity_field="analysisId",
        runtime_type="AnalysisGraph",
    )
    return AnalysisGraphRuntime.from_compiled_authority(authority)


class CycleGraphSourceMaterializationTests(unittest.TestCase):
    def test_named_sources_cannot_collide_with_internal_cycle_categories(self):
        for source in ("decision", "previous"):
            with self.subTest(source=source):
                runtime = object.__new__(CycleGraphRuntime)
                runtime._source_data_keys = {source: ("x",)}
                runtime._input_plan = (
                    ("sample", ("x",), None, ("x",)),
                    (source, ("x",), source, ("x",)),
                )
                graph_input = runtime._materialize_input(
                    {"x": 42},
                    {},
                    "2026-01-01T00:00:00Z",
                    {source: {"x": 99}},
                )
                self.assertEqual(graph_input, {"x": 42})


class AnalysisResourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        root = Path(cls.temp.name)
        cls.config = {
            "controlRoot": str(root / "control"),
            "releaseRoot": str(root / "releases"),
            "liveRoot": str(root / "live"),
        }
        engine_database.prepare_database(cls.config)
        builtin_resources.install(cls.config)
        cls.modules = module_definitions.load_analysis_definitions(cls.config)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def performance_definition(self):
        records = control_state.load_state(self.config, "analyses.json", {})
        version = max(
            (
                item for item in records.values()
                if item["analysisId"] == analysis_presets.PERFORMANCE_ANALYSIS_ID
            ),
            key=lambda item: int(item["version"]),
        )["version"]
        return analysis_service.get_analysis_definition(
            self.config,
            analysis_presets.PERFORMANCE_ANALYSIS_ID,
            version,
        )

    def test_performance_analysis_runs_as_independent_graph(self):
        runtime = analysis_runtime(
            self.performance_definition(),
            self.modules,
            cycle_input_contracts(
                {"time": {"type": "string"}},
                {},
            ),
            source_contracts={
                CURRENT_PIPELINE_SOURCE: {
                    "broker.account.equity": {"type": "number"},
                },
            },
        )
        try:
            sample = {"time": "2026-01-01T00:00:00Z"}
            previous = {"broker": {"account": {"equity": 90.0}}}
            completed = {"broker": {"account": {"equity": 100.0}}}
            before_sample = copy.deepcopy(sample)
            before_previous = copy.deepcopy(previous)
            first = runtime.execute(
                sample,
                previous,
                sample["time"],
                source_data={CURRENT_PIPELINE_SOURCE: completed},
            )
            second = runtime.execute(
                {"time": "2026-01-02T00:00:00Z"},
                completed,
                "2026-01-02T00:00:00Z",
                source_data={
                    CURRENT_PIPELINE_SOURCE: {
                        "broker": {"account": {"equity": 110.0}},
                    },
                },
            )
            self.assertEqual(sample, before_sample)
            self.assertEqual(previous, before_previous)
            self.assertEqual(first["analysis"]["performance"]["observationCount"], 1)
            self.assertAlmostEqual(second["analysis"]["performance"]["totalReturn"], 0.1)
            self.assertAlmostEqual(
                second["analysis"]["performance"]["observationsPerYear"],
                365.2425,
            )
            metadata = runtime.metadata()
            self.assertEqual(metadata["type"], "AnalysisGraph")
            self.assertEqual(metadata["topology"], ["performance"])
            self.assertEqual(
                metadata["moduleTransports"]["performance"]["invocationCount"],
                2,
            )
            self.assertNotIn("graphCacheHitCount", metadata)
            self.assertNotIn("graphCacheMissCount", metadata)
        finally:
            runtime.close()

    def test_performance_annualization_uses_elapsed_time_not_a_fixed_frequency(self):
        definition = self.performance_definition()
        self.assertNotIn("periodsPerYear", definition["instances"]["performance"]["config"])
        runtime = analysis_runtime(
            definition,
            self.modules,
            cycle_input_contracts(
                {"time": {"type": "string"}},
                {},
            ),
            source_contracts={
                CURRENT_PIPELINE_SOURCE: {
                    "broker.account.equity": {"type": "number"},
                },
            },
        )
        try:
            runtime.execute(
                {"time": "2024-01-01T00:00:00Z"},
                {},
                "2024-01-01T00:00:00Z",
                source_data={
                    CURRENT_PIPELINE_SOURCE: {
                        "broker": {"account": {"equity": 100.0}},
                    },
                },
            )
            result = runtime.execute(
                {"time": "2025-01-01T00:00:00Z"},
                {},
                "2025-01-01T00:00:00Z",
                source_data={
                    CURRENT_PIPELINE_SOURCE: {
                        "broker": {"account": {"equity": 110.0}},
                    },
                },
            )["analysis"]["performance"]
            expected = 1.1 ** (365.2425 / 366.0) - 1.0
            self.assertAlmostEqual(result["annualizedReturn"], expected)
            self.assertAlmostEqual(result["observationsPerYear"], 365.2425 / 366.0)
        finally:
            runtime.close()

    def test_analysis_can_compare_completed_pipeline_with_prior_cycle_in_one_invoke(self):
        numeric = max(
            (
                item for item in self.modules.values()
                if item["moduleId"] == "numeric-change-analyzer"
            ),
            key=lambda item: int(item["version"]),
        )
        archived = engine_service.handle_save_analysis(self.config, {
            "schemaVersion": 1,
            "analysisId": "current-versus-prior-analysis",
            "name": "Current versus prior",
            "description": "Exercises both explicit temporal input sources.",
            "instances": {
                "change": {
                    "instanceId": "change",
                    "kind": "Analyzer",
                    "moduleId": numeric["moduleId"],
                    "version": numeric["version"],
                    "config": {},
                    "inputs": {
                        "current": "wire.current",
                        "previous": "wire.previous",
                    },
                    "outputs": {
                        "change": "wire.change",
                        "return": "wire.return",
                    },
                },
            },
            "graph": {
                "nodes": ["change"],
                "inputs": {
                    "current": {
                        "dataKey": "broker.account.equity",
                        "wire": "wire.current",
                        "source": CURRENT_PIPELINE_SOURCE,
                    },
                    "previous": {
                        "dataKey": "last.broker.account.equity",
                        "wire": "wire.previous",
                    },
                },
                "outputs": {
                    "change-output": {
                        "dataKey": "analysis.change",
                        "wire": "wire.change",
                    },
                    "return-output": {
                        "dataKey": "analysis.return",
                        "wire": "wire.return",
                    },
                },
            },
        })["definition"]
        cycle_contracts = cycle_input_contracts(
            {},
            {"broker.account.equity": {"type": "number"}},
        )
        source_contracts = {
            CURRENT_PIPELINE_SOURCE: {
                "broker.account.equity": {"type": "number"},
            },
        }
        runtime = analysis_runtime(
            archived,
            self.modules,
            cycle_contracts,
            required_roots={"decisionTime"},
            source_contracts=source_contracts,
        )
        try:
            first = runtime.execute(
                {},
                {},
                "2026-01-01T00:00:00Z",
                source_data={
                    CURRENT_PIPELINE_SOURCE: {
                        "broker": {"account": {"equity": 100.0}},
                    },
                },
            )
            second = runtime.execute(
                {},
                {"broker": {"account": {"equity": 95.0}}},
                "2026-01-02T00:00:00Z",
                source_data={
                    CURRENT_PIPELINE_SOURCE: {
                        "broker": {"account": {"equity": 110.0}},
                    },
                },
            )
            self.assertIsNone(first["analysis"]["change"])
            self.assertEqual(second["analysis"]["change"], 15.0)
            self.assertEqual(
                runtime.source_data_keys,
                {CURRENT_PIPELINE_SOURCE: ("broker.account.equity",)},
            )
            with self.assertRaisesRegex(ValueError, "missing currentPipeline"):
                runtime.execute({}, {}, "2026-01-03T00:00:00Z")
        finally:
            runtime.close()

        with self.assertRaisesRegex(ValueError, "optional wire"):
            analysis_runtime(
                archived,
                self.modules,
                cycle_contracts,
                required_roots={"decisionTime"},
                source_contracts=source_contracts,
                source_required_roots={CURRENT_PIPELINE_SOURCE: ()},
            )

    def test_analysis_versions_are_system_assigned_and_not_pipeline_children(self):
        source = self.performance_definition()
        draft = {
            "schemaVersion": 1,
            "analysisId": "custom-performance",
            "name": "Custom Performance",
            "description": "test",
            "instances": source["instances"],
            "graph": source["graph"],
        }
        first = engine_service.handle_save_analysis(self.config, draft)
        unchanged = engine_service.handle_save_analysis(self.config, draft)
        changed = engine_service.handle_save_analysis(
            self.config, {**draft, "description": "changed"}
        )
        self.assertEqual(first["definition"]["version"], "1")
        self.assertEqual(unchanged["definition"]["version"], "1")
        self.assertEqual(changed["definition"]["version"], "2")
        self.assertTrue(unchanged["unchanged"])
        pipeline_store = pipeline_repository.load_pipeline_store(self.config)
        self.assertNotIn("custom-performance", pipeline_store["pipelines"])

    def test_graph_draft_validation_accepts_the_standard_named_source(self):
        definition = self.performance_definition()
        draft = {
            key: copy.deepcopy(definition[key])
            for key in analysis_contracts.ANALYSIS_DRAFT_FIELDS
        }
        result = engine_service.validate_graph_draft(
            self.config,
            {"resourceType": "analysis", "draft": draft},
        )
        self.assertEqual(result["scope"], "internal")
        self.assertEqual(result["topology"], ["performance"])

    def test_analysis_identity_must_be_one_canonical_path_segment(self):
        source = self.performance_definition()
        draft = {
            key: copy.deepcopy(source[key])
            for key in analysis_contracts.ANALYSIS_DRAFT_FIELDS
        }
        for invalid in ("a/", "a/.", "."):
            with self.subTest(identity=invalid), self.assertRaisesRegex(
                ValueError, "one canonical filesystem-safe path segment"
            ):
                engine_service.handle_save_analysis(
                    self.config, {**draft, "analysisId": invalid}
                )

    def test_only_explicit_analysis_outputs_are_exported(self):
        definition = self.performance_definition()
        counter = next(
            item for item in self.modules.values()
            if item["moduleId"] == "cycle-count-analyzer"
        )
        definition["instances"]["unconnected"] = {
            "instanceId": "unconnected",
            "kind": "Analyzer",
            "moduleId": counter["moduleId"],
            "version": counter["version"],
            "config": {},
            "inputs": {},
            "outputs": {"count": "wire.unconnected"},
        }
        definition["graph"]["nodes"].append("unconnected")
        archived = engine_service.handle_save_analysis(self.config, {
            "schemaVersion": 1,
            "analysisId": "explicit-output-analysis",
            "name": "Explicit Output Analysis",
            "description": "test",
            "instances": definition["instances"],
            "graph": definition["graph"],
        })["definition"]
        runtime = analysis_runtime(
            archived,
            self.modules,
            cycle_input_contracts(
                {"time": {"type": "string"}},
                {},
            ),
            source_contracts={
                CURRENT_PIPELINE_SOURCE: {
                    "broker.account.equity": {"type": "number"},
                },
            },
        )
        try:
            outputs = runtime.execute(
                {"time": "2026-01-01T00:00:00Z"},
                {},
                "2026-01-01T00:00:00Z",
                source_data={
                    CURRENT_PIPELINE_SOURCE: {
                        "broker": {"account": {"equity": 100.0}},
                    },
                },
            )
            self.assertEqual(set(outputs), {"analysis"})
            self.assertNotIn("unconnected", outputs)
        finally:
            runtime.close()

    def test_analysis_rejects_instances_outside_graph(self):
        definition = self.performance_definition()
        definition["instances"]["orphan"] = copy.deepcopy(definition["instances"]["performance"])
        definition["instances"]["orphan"]["instanceId"] = "orphan"
        with self.assertRaisesRegex(ValueError, "outside its Graph"):
            analysis_compiler.validate_analysis_definition(definition, self.modules)


if __name__ == "__main__":
    unittest.main()
