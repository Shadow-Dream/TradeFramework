#!/usr/bin/env python3

import copy
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from builtin_implementations.environment_presets import PAPER_ENVIRONMENT_ID
from builtin_implementations.environment_contracts import ACCOUNT_SCHEMA
from builtin_implementations import resources as builtin_resources
import engine_service
from engine.control import database as engine_database
from engine.repository import control_state
from engine.repository import module_definitions
from engine.service import control_api as control
from engine.compiler.environment import (
    validate_environment_definition,
)
from engine.runtime.graph_cycle import EnvironmentGraphRuntime
from engine.service.environment import get_environment_definition
from engine.authority.graph_cycle import (
    bind_compiled_cycle_graph_authority,
    verify_cycle_graph_definition_authority,
)
from engine.contracts.graph_cycle import (
    build_cycle_graph_input,
    cycle_input_contracts,
)
from engine.authority.module_definition import verify_module_definition_authority
from engine.authority.module_invocation import bind_module_invocation_authority
from engine.runtime.module_invoker import ModuleInvoker
from engine.compiler.graph import compile_module_graph_authority


NUMBER = {"type": "number"}


def module_invoker(binding, definition, **kwargs):
    binding = copy.deepcopy(binding)
    binding["inputs"] = {
        name: f"input.{name}" for name in definition["ports"]["inputs"]
    }
    binding["outputs"] = {
        name: f"output.{name}" for name in definition["ports"]["outputs"]
    }
    invocation_authority = bind_module_invocation_authority(
        binding,
        verify_module_definition_authority(definition),
    )
    return ModuleInvoker.from_authority(
        invocation_authority,
        **kwargs,
    )


def environment_runtime(
    definition,
    module_definitions,
    input_contracts,
    *,
    required_roots=None,
):
    graph_authority = compile_module_graph_authority(
        definition["graph"],
        definition["instances"],
        module_definitions,
        input_contracts,
        allowed_kinds={"Environment"},
        label="Environment Graph",
        required_roots=required_roots,
    )
    authority = bind_compiled_cycle_graph_authority(
        definition,
        graph_authority,
        allowed_kind="Environment",
        graph_label="Environment Graph",
        identity_field="environmentId",
        runtime_type="EnvironmentGraph",
    )
    return EnvironmentGraphRuntime.from_compiled_authority(authority)


class BacktestEnvironmentTests(unittest.TestCase):
    def test_cycle_binder_rejects_foreign_graph_before_archive_access(self):
        with mock.patch(
            "engine.archive.version.verify_record"
        ) as verify_record:
            with self.assertRaisesRegex(TypeError, "Engine-owned"):
                bind_compiled_cycle_graph_authority(
                    {},
                    {},
                    allowed_kind="Environment",
                    graph_label="Environment Graph",
                    identity_field="environmentId",
                    runtime_type="EnvironmentGraph",
                )
            verify_record.assert_not_called()

    def test_cycle_binder_reports_missing_instances_as_a_contract_error(self):
        definition = {
            "environmentId": "missing-instances",
            "version": "1",
            "graph": {"nodes": [], "inputs": {}, "outputs": {}},
        }
        graph_authority = compile_module_graph_authority(
            definition["graph"],
            {},
            {},
            {},
            label="Environment Graph",
        )
        with mock.patch(
            "engine.archive.version.verify_record",
            side_effect=lambda record: record,
        ):
            with self.assertRaisesRegex(
                ValueError,
                "Definition instances must be an object",
            ):
                bind_compiled_cycle_graph_authority(
                    definition,
                    graph_authority,
                    allowed_kind="Environment",
                    graph_label="Environment Graph",
                    identity_field="environmentId",
                    runtime_type="EnvironmentGraph",
                )

    def test_cycle_runtime_authority_is_read_only_and_terminal_errors_win(self):
        definition = {
            "environmentId": "empty-environment",
            "version": "1",
            "graph": {"nodes": [], "inputs": {}, "outputs": {}},
            "instances": {},
        }
        graph_authority = compile_module_graph_authority(
            definition["graph"], {}, {}, {}, label="Environment Graph"
        )
        with mock.patch(
            "engine.archive.version.verify_record", side_effect=lambda record: record
        ):
            authority = bind_compiled_cycle_graph_authority(
                definition,
                graph_authority,
                allowed_kind="Environment",
                graph_label="Environment Graph",
                identity_field="environmentId",
                runtime_type="EnvironmentGraph",
            )
            definition_authority = verify_cycle_graph_definition_authority(
                definition
            )
        with self.assertRaisesRegex(AttributeError, "immutable"):
            authority._compiled_graph = object()
        with self.assertRaisesRegex(AttributeError, "immutable"):
            definition_authority._definition_json = "{}"
        runtime = EnvironmentGraphRuntime.from_compiled_authority(authority)
        try:
            self.assertEqual(runtime.identity_field, "environmentId")
            self.assertEqual(runtime.runtime_type, "EnvironmentGraph")
            with self.assertRaisesRegex(AttributeError, "Engine-owned"):
                _ = runtime.runtime
            for field, value in (
                ("runtime", object()),
                ("identity_field", "forged"),
                ("runtime_type", "forged"),
            ):
                with self.subTest(field=field), self.assertRaises(AttributeError):
                    setattr(runtime, field, value)
            runtime.finalize()
            with self.assertRaisesRegex(RuntimeError, "finalized Cycle Graph"):
                runtime.execute(None, None, "")
            with self.assertRaisesRegex(RuntimeError, "finalized Cycle Graph"):
                runtime.execute_into(None, None, "", None)
        finally:
            runtime.close()

    def test_cycle_authority_rejects_definition_instances_outside_graph(self):
        definition = {
            "environmentId": "orphan-environment",
            "graph": {"nodes": [], "inputs": {}, "outputs": {}},
            "instances": {"ghost": {"not": "a Module instance"}},
        }
        authority = compile_module_graph_authority(
            definition["graph"], {}, {}, {}, label="Environment Graph"
        )
        with mock.patch(
            "engine.archive.version.verify_record", side_effect=lambda record: record
        ):
            with self.assertRaisesRegex(ValueError, "exactly match graph.nodes.*ghost"):
                bind_compiled_cycle_graph_authority(
                    definition,
                    authority,
                    allowed_kind="Environment",
                    graph_label="Environment Graph",
                    identity_field="environmentId",
                    runtime_type="EnvironmentGraph",
                )

    def test_cycle_authority_rejects_a_different_independent_node_order(self):
        definitions = {
            f"Environment/empty-{name}/1": {
                "kind": "Environment",
                "moduleId": f"empty-{name}",
                "name": f"empty-{name}",
                "activationMode": "PythonModule",
                "parameters": {},
                "version": "1",
                "status": "archived",
                "builtin": False,
                "description": "Environment graph ordering fixture.",
                "ports": {"inputs": {}, "outputs": {}},
                "configSchema": {
                    "type": "object",
                    "additionalProperties": False,
                },
                "archive": {
                    "resourceType": "module",
                    "resourceId": f"Environment/empty-{name}",
                },
            }
            for name in ("a", "b")
        }
        instances = {
            name: {
                "instanceId": name,
                "kind": "Environment",
                "moduleId": f"empty-{name}",
                "version": "1",
                "config": {},
                "inputs": {},
                "outputs": {},
            }
            for name in ("a", "b")
        }
        definition = {
            "environmentId": "ordered-environment",
            "graph": {"nodes": ["a", "b"], "inputs": {}, "outputs": {}},
            "instances": instances,
        }
        with mock.patch(
            "engine.archive.version.verify_record", side_effect=lambda record: record
        ):
            reversed_graph = compile_module_graph_authority(
                {"nodes": ["b", "a"], "inputs": {}, "outputs": {}},
                instances,
                definitions,
                {},
                allowed_kinds={"Environment"},
                label="Environment Graph",
            )
            with self.assertRaisesRegex(ValueError, "Definition node order"):
                bind_compiled_cycle_graph_authority(
                    definition,
                    reversed_graph,
                    allowed_kind="Environment",
                    graph_label="Environment Graph",
                    identity_field="environmentId",
                    runtime_type="EnvironmentGraph",
                )

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

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def environment_version(self, environment_id):
        matches = [
            item
            for item in control_state.load_state(
                self.config,
                "environments.json",
                {},
            ).values()
            if item["environmentId"] == environment_id
        ]
        return max(matches, key=lambda item: int(item["version"]))["version"]

    def test_module_repositories_share_archive_flow_but_not_storage(self):
        pipeline = module_definitions.load_pipeline_definitions(self.config)
        analysis = module_definitions.load_analysis_definitions(self.config)
        environment = module_definitions.load_environment_definitions(self.config)
        self.assertTrue(pipeline)
        self.assertTrue(analysis)
        self.assertTrue(environment)
        self.assertEqual({item["kind"] for item in analysis.values()}, {"Analyzer"})
        self.assertEqual({item["kind"] for item in environment.values()}, {"Environment"})
        self.assertFalse(set(pipeline) & set(analysis))
        self.assertFalse(set(pipeline) & set(environment))
        self.assertTrue((Path(self.config["releaseRoot"]) / "_modules").is_dir())
        self.assertTrue((Path(self.config["releaseRoot"]) / "_analysis_modules").is_dir())
        self.assertTrue((Path(self.config["releaseRoot"]) / "_environment_modules").is_dir())

    def test_environment_models_are_ordinary_archived_configurable_modules(self):
        definitions = module_definitions.load_environment_definitions(self.config)
        fee_definition = next(
            item for item in definitions.values()
            if item["moduleId"] == "fixed-plus-bps-fee-model"
        )
        binding = {
            "instanceId": "fee",
            "kind": "Environment",
            "moduleId": fee_definition["moduleId"],
            "version": fee_definition["version"],
            "config": {"fixedFee": 1.0, "feeBps": 10.0},
            "inputs": {},
            "outputs": {},
        }
        invoker = module_invoker(binding, fee_definition)
        try:
            self.assertEqual(
                invoker.invoke({"filledQuantity": 2.0, "notional": 1000.0}),
                {"fee": 2.0},
            )
        finally:
            invoker.close()

    def test_environment_identity_must_be_one_canonical_path_segment(self):
        source = get_environment_definition(
            self.config,
            PAPER_ENVIRONMENT_ID,
            self.environment_version(PAPER_ENVIRONMENT_ID),
        )
        draft = {
            key: copy.deepcopy(source[key])
            for key in {
                "schemaVersion", "environmentId", "name", "description", "instances", "graph",
            }
        }
        for invalid in ("a/", "a/.", "."):
            with self.subTest(identity=invalid), self.assertRaisesRegex(
                ValueError, "one canonical filesystem-safe path segment"
            ):
                engine_service.handle_save_environment(
                    self.config, {**draft, "environmentId": invalid}
                )

    def test_graph_cycle_input_projects_declared_current_and_previous_data(self):
        sample = {"market": {"value": 11.0}}
        previous = {"market": {"value": 10.0}, "policy": {"target": 3.0}}
        data_keys = (
            "market.value",
            "last.market.value",
            "last.policy.target",
            "decisionTime",
        )
        result = build_cycle_graph_input(
            sample, previous, "2026-01-01T00:00:00Z", data_keys
        )
        self.assertEqual(result["market"]["value"], 11.0)
        self.assertEqual(result["last"]["market"]["value"], 10.0)
        self.assertEqual(result["last"]["policy"]["target"], 3.0)
        self.assertEqual(result["decisionTime"], "2026-01-01T00:00:00Z")
        with self.assertRaisesRegex(ValueError, "reserved"):
            build_cycle_graph_input(
                {"last": {}}, previous, "2026-01-01T00:00:00Z", data_keys
            )

    def test_cycle_boundary_rejects_full_last_data_dict(self):
        with self.assertRaisesRegex(ValueError, "full 'last' Data Dict"):
            build_cycle_graph_input(
                {"market": {"value": 11.0}},
                {"market": {"value": 10.0}},
                "2026-01-01T00:00:00Z",
                ("last",),
            )
        with self.assertRaisesRegex(ValueError, "reserved cycle root"):
            cycle_input_contracts({"last.market.value": NUMBER}, {})

        definitions = module_definitions.load_environment_definitions(self.config)
        definition = copy.deepcopy(get_environment_definition(
            self.config,
            PAPER_ENVIRONMENT_ID,
            self.environment_version(PAPER_ENVIRONMENT_ID),
        ))
        first_input = next(iter(definition["graph"]["inputs"].values()))
        first_input["dataKey"] = "last"
        with self.assertRaisesRegex(ValueError, "full 'last' Data Dict"):
            validate_environment_definition(definition, definitions)

    def test_environment_graph_topologically_sorts_and_exports_only_boundaries(self):
        definitions = module_definitions.load_environment_definitions(self.config)
        definition = get_environment_definition(
            self.config,
            PAPER_ENVIRONMENT_ID,
            self.environment_version(PAPER_ENVIRONMENT_ID),
        )
        contracts = cycle_input_contracts(
            {"market.execution_value": NUMBER},
            {
                "policy.target_position": {"type": ["number", "null"]},
                "broker.account": ACCOUNT_SCHEMA,
            },
        )
        runtime = environment_runtime(
            definition,
            definitions,
            contracts,
            required_roots={"decisionTime", "market"},
        )
        try:
            topology = runtime.compiled_graph["topology"]
            self.assertLess(topology.index("fill"), topology.index("fee"))
            self.assertLess(topology.index("settlement"), topology.index("account"))
            first = runtime.execute(
                {"market": {"execution_value": 10.0}},
                {},
                "2026-01-01T00:00:00Z",
            )
            second = runtime.execute(
                {"market": {"execution_value": 11.0}},
                {**first, "policy": {"target_position": 5.0}},
                "2026-01-02T00:00:00Z",
            )
            self.assertEqual(second["broker"]["order"]["filledQuantity"], 5.0)
            self.assertEqual(second["broker"]["account"]["position"], 5.0)
            self.assertNotIn("wire", second)
            self.assertEqual(set(second), {"market", "broker"})
            self.assertEqual(second["market"]["execution_value"], 11.0)
            exposed_definition = runtime.definition
            exposed_definition["environmentId"] = "tampered"
            exposed_definition["graph"]["outputs"] = {}
            exposed_graph = runtime.compiled_graph
            exposed_graph["topology"] = []
            metadata = runtime.metadata()
            self.assertEqual(metadata["environmentId"], PAPER_ENVIRONMENT_ID)
            self.assertTrue(runtime.compiled_graph["topology"])
            self.assertTrue(runtime.output_data_keys)
            self.assertEqual(set(metadata["moduleTransports"]), set(topology))
            self.assertNotIn("graphCacheHitCount", metadata)
            self.assertNotIn("graphCacheMissCount", metadata)
        finally:
            runtime.finalize()
            runtime.close()

    def test_environment_does_not_implicitly_export_sample_data(self):
        definitions = module_definitions.load_environment_definitions(self.config)
        definition = get_environment_definition(
            self.config,
            PAPER_ENVIRONMENT_ID,
            self.environment_version(PAPER_ENVIRONMENT_ID),
        )
        contracts = cycle_input_contracts(
            {
                "market.execution_value": NUMBER,
                "private.unexported": NUMBER,
            },
            {
                "policy.target_position": {"type": ["number", "null"]},
                "broker.account": ACCOUNT_SCHEMA,
            },
        )
        runtime = environment_runtime(
            definition,
            definitions,
            contracts,
            required_roots={"decisionTime", "market", "private"},
        )
        try:
            result = runtime.execute(
                {
                    "market": {"execution_value": 10.0},
                    "private": {"unexported": 999.0},
                },
                {},
                "2026-01-01T00:00:00Z",
            )
            self.assertNotIn("private", result)
            self.assertEqual(result["market"]["execution_value"], 10.0)
        finally:
            runtime.close()

    def test_analysis_result_is_not_part_of_next_cycle_last_data(self):
        pipeline_data = {"market": {"value": 11.0}, "policy": {"target": 2.0}}
        result_cycle = {**copy.deepcopy(pipeline_data), "analysis": {"score": 0.5}}
        next_input = build_cycle_graph_input(
            {"market": {"value": 12.0}},
            pipeline_data,
            "2026-01-02T00:00:00Z",
            ("market.value", "last.policy.target"),
        )
        self.assertEqual(next_input["last"], {"policy": {"target": 2.0}})
        self.assertNotIn("analysis", next_input["last"])
        self.assertIn("analysis", result_cycle)

    def test_environment_rejects_old_schema_and_instances_outside_graph(self):
        with self.assertRaisesRegex(ValueError, "schemaVersion 2"):
            validate_environment_definition({
                "schemaVersion": 1,
                "environmentId": "old",
                "version": "1",
                "name": "Old",
                "instances": {},
                "graph": {"nodes": [], "inputs": {}, "outputs": {}},
            }, module_definitions.load_environment_definitions(self.config))
        valid = get_environment_definition(
            self.config,
            PAPER_ENVIRONMENT_ID,
            self.environment_version(PAPER_ENVIRONMENT_ID),
        )
        valid["instances"]["orphan"] = copy.deepcopy(valid["instances"]["target"])
        valid["instances"]["orphan"]["instanceId"] = "orphan"
        with self.assertRaisesRegex(ValueError, "outside its Graph"):
            validate_environment_definition(
                valid, module_definitions.load_environment_definitions(self.config)
            )


if __name__ == "__main__":
    unittest.main()
