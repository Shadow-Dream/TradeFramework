import unittest

from engine.compiler.graph import compile_module_graph
from engine.contracts.contract_reducer import write_contract_path
from engine.contracts.data import validate_data_json
from engine.contracts.contract_expansion import contract_path_required
from engine.contracts.data_path import (
    compile_data_path_plan,
    get_data_path,
    project_compiled_data_paths,
    project_data_paths,
    set_data_path,
    set_data_segments_copy_on_write,
    split_data_path,
)
from engine.contracts.module import validate_instance_wiring
from tests.support.module_runtime import runtime_from_compiled_plan


class PipelineRuntimeContractTests(unittest.TestCase):
    def test_ordered_contract_writes_match_parent_child_runtime_semantics(self):
        number = {"type": "number"}
        string = {"type": "string"}
        contracts = write_contract_path({}, "x.a", number)
        contracts = write_contract_path(
            contracts,
            "x",
            {
                "type": "object",
                "properties": {"b": string},
                "required": ["b"],
                "additionalProperties": False,
            },
        )
        self.assertNotIn("x.a", contracts)
        self.assertEqual(contracts["x.b"], string)

        contracts = write_contract_path(contracts, "x.c", number)
        self.assertEqual(contracts["x.b"], string)
        self.assertEqual(contracts["x.c"], number)

    def test_optional_nested_write_preserves_presence_without_making_child_required(self):
        contracts = write_contract_path({}, "price.close", {"type": "number"})
        contracts = write_contract_path(
            contracts, "price.maybe", {"type": "number"}, required=False
        )
        self.assertTrue(
            contract_path_required(
                contracts, "price.close", required_roots={"price"}
            )
        )
        self.assertFalse(
            contract_path_required(
                contracts, "price.maybe", required_roots={"price"}
            )
        )
        validate_data_json({"price": {"close": 1.0}}, contracts)

    def test_ordered_write_preserves_unaffected_literal_and_composition_constraints(self):
        contracts = write_contract_path(
            {"x": {"const": {"a": 1, "b": "fixed"}}},
            "x.a",
            {"type": "number"},
        )
        validate_data_json({"x": {"a": 2.0, "b": "fixed"}}, contracts)
        with self.assertRaisesRegex(ValueError, "const"):
            validate_data_json({"x": {"a": 2.0, "b": "changed"}}, contracts)

        object_branch = {
            "type": "object",
            "properties": {"a": {"type": "number"}},
            "required": ["a"],
            "additionalProperties": False,
        }
        composed = write_contract_path(
            {"x": {"allOf": [object_branch, object_branch]}},
            "x.a",
            {"type": "number"},
        )
        validate_data_json({"x": {"a": 3}}, composed)

    def test_null_string_is_not_an_unconnected_port_placeholder(self):
        module_definition = {
            "ports": {
                "inputs": {"high": {"schema": {"type": "number"}, "required": True}},
                "outputs": {"atr": {"schema": {"type": "number"}}},
            },
        }
        module_instance = {
            "instanceId": "atr",
            "inputs": {"high": "null"},
            "outputs": {"atr": "wire.atr"},
        }
        with self.assertRaisesRegex(ValueError, "requires input port 'high'"):
            validate_instance_wiring(module_instance, module_definition)

    def test_nested_data_path_reads_and_writes_real_json(self):
        data = {}
        set_data_path(data, "price.close", 123.45)
        set_data_path(data, "price.volume", 900)
        self.assertEqual(data, {"price": {"close": 123.45, "volume": 900}})
        self.assertEqual(get_data_path(data, "price"), {"close": 123.45, "volume": 900})

    def test_nested_data_path_rejects_scalar_parent_collision(self):
        with self.assertRaisesRegex(ValueError, "collides"):
            set_data_path({"price": 123.45}, "price.close", 123.45)

    def test_analysis_merge_detaches_previous_cycle_object_projection(self):
        current = {"state": {"pipeline": 1}}
        previous = project_data_paths(current, ["state"], isolate_values=False)
        set_data_segments_copy_on_write(
            current,
            ("state", "analysis"),
            2,
        )
        self.assertEqual(current, {"state": {"pipeline": 1, "analysis": 2}})
        self.assertEqual(previous, {"state": {"pipeline": 1}})
        self.assertIsNot(current["state"], previous["state"])

    def test_shallow_previous_snapshot_survives_three_cycle_graph_order(self):
        integer = {"type": "integer"}
        nested_schema = {
            "type": "object",
            "properties": {"value": integer},
            "required": ["value"],
            "additionalProperties": False,
        }
        state_schema = {
            "type": "object",
            "properties": {
                "nested": nested_schema,
                "pipelineValue": integer,
            },
            "required": ["nested", "pipelineValue"],
            "additionalProperties": False,
        }

        def graph_runtime(graph, contracts, label):
            plan = compile_module_graph(
                graph,
                {},
                {},
                contracts,
                label=label,
            )
            return runtime_from_compiled_plan(plan, {})

        environment = graph_runtime(
            {
                "nodes": [],
                "inputs": {
                    "state-input": {
                        "dataKey": "incoming.state",
                        "wire": "wire.state",
                    },
                },
                "outputs": {
                    "state-output": {
                        "dataKey": "state",
                        "wire": "wire.state",
                    },
                },
            },
            {"incoming.state": state_schema},
            "Previous-state pass-through Environment Graph",
        )
        pipeline = graph_runtime(
            {
                "nodes": [],
                "inputs": {
                    "state-input": {
                        "dataKey": "state",
                        "wire": "wire.state",
                    },
                    "pipeline-value-input": {
                        "dataKey": "write.pipelineValue",
                        "wire": "wire.pipelineValue",
                    },
                    "nested-value-input": {
                        "dataKey": "write.nestedValue",
                        "wire": "wire.nestedValue",
                    },
                },
                "outputs": {
                    "state-output": {
                        "dataKey": "state",
                        "wire": "wire.state",
                    },
                    "pipeline-value-output": {
                        "dataKey": "state.pipelineValue",
                        "wire": "wire.pipelineValue",
                    },
                    "nested-value-output": {
                        "dataKey": "state.nested.value",
                        "wire": "wire.nestedValue",
                    },
                },
            },
            {
                "state": state_schema,
                "write.pipelineValue": integer,
                "write.nestedValue": integer,
            },
            "Copy-on-write Pipeline Graph",
        )
        analysis = graph_runtime(
            {
                "nodes": [],
                "inputs": {
                    "state-input": {
                        "dataKey": "state",
                        "wire": "wire.state",
                    },
                    "previous-value-input": {
                        "dataKey": "last.pipelineValue",
                        "wire": "wire.previousPipelineValue",
                    },
                    "current-nested-input": {
                        "dataKey": "state.nested",
                        "wire": "wire.currentNested",
                    },
                },
                "outputs": {
                    "state-output": {
                        "dataKey": "state",
                        "wire": "wire.state",
                    },
                    "previous-value-output": {
                        "dataKey": "state.analysis.previousPipelineValue",
                        "wire": "wire.previousPipelineValue",
                    },
                    "current-nested-output": {
                        "dataKey": "state.analysis.currentNested",
                        "wire": "wire.currentNested",
                    },
                },
            },
            {
                "state": state_schema,
                "last.pipelineValue": integer,
            },
            "Copy-on-write Analysis Graph",
        )
        previous_plan = compile_data_path_plan(("state",))
        previous = {
            "state": {
                "nested": {"value": 0},
                "pipelineValue": 0,
            },
        }
        snapshots = [previous]
        try:
            for cycle_index in range(1, 4):
                previous_cycle_data = previous

                current = environment.execute_outputs({
                    "incoming": {"state": previous_cycle_data["state"]},
                })
                self.assertIs(
                    current["state"],
                    previous_cycle_data["state"],
                    "Environment pass-through must exercise the worst-case alias.",
                )

                pipeline.execute_outputs_into(
                    {
                        "state": current["state"],
                        "write": {
                            "pipelineValue": cycle_index,
                            "nestedValue": cycle_index,
                        },
                    },
                    current,
                )
                self.assertEqual(
                    previous_cycle_data["state"]["pipelineValue"],
                    cycle_index - 1,
                )
                self.assertEqual(
                    previous_cycle_data["state"]["nested"]["value"],
                    cycle_index - 1,
                )
                self.assertIsNot(current["state"], previous_cycle_data["state"])
                self.assertIsNot(
                    current["state"]["nested"],
                    previous_cycle_data["state"]["nested"],
                )

                previous = project_compiled_data_paths(
                    current,
                    previous_plan,
                    isolate_values=False,
                )
                self.assertIs(previous["state"], current["state"])
                snapshots.append(previous)

                analysis.execute_outputs_into(
                    {
                        "state": current["state"],
                        "last": {
                            "pipelineValue": previous_cycle_data["state"][
                                "pipelineValue"
                            ],
                        },
                    },
                    current,
                )
                self.assertEqual(
                    current["state"]["analysis"]["previousPipelineValue"],
                    cycle_index - 1,
                )
                self.assertEqual(
                    current["state"]["analysis"]["currentNested"],
                    {"value": cycle_index},
                )
                self.assertNotIn("analysis", previous["state"])
                self.assertNotIn("analysis", previous_cycle_data["state"])
                self.assertIsNot(current["state"], previous["state"])

            for cycle_index, snapshot in enumerate(snapshots):
                self.assertEqual(
                    snapshot,
                    {
                        "state": {
                            "nested": {"value": cycle_index},
                            "pipelineValue": cycle_index,
                        },
                    },
                )
        finally:
            analysis.close()
            pipeline.close()
            environment.close()

    def test_components_can_add_sibling_fields_under_one_data_dict_root(self):
        contracts = {}
        for path, schema in (
            ("strategy.sample.price", {"type": "number"}),
            ("strategy.account.equity", {"type": "number"}),
            ("strategy.analysis.sharpe", {"type": ["number", "null"]}),
        ):
            contracts = write_contract_path(contracts, path, schema)
        self.assertIn("strategy.sample.price", contracts)
        self.assertIn("strategy.account.equity", contracts)
        self.assertIn("strategy.analysis.sharpe", contracts)

    def test_signal_graph_exports_do_not_replace_environment_siblings(self):
        current = {"strategy": {"account": {"equity": 100000.0}}}
        set_data_segments_copy_on_write(
            current, split_data_path("strategy.signal.value"), 1
        )
        self.assertEqual(current, {
            "strategy": {
                "account": {"equity": 100000.0},
                "signal": {"value": 1},
            },
        })

    def test_pipeline_nested_write_detaches_direct_sample_passthrough(self):
        sample = {"market": {"bar": {"close": 100.0}}}
        current = {"market": {"bar": sample["market"]["bar"]}}
        set_data_segments_copy_on_write(
            current, split_data_path("market.bar.signal"), 1
        )
        self.assertEqual(
            current,
            {"market": {"bar": {"close": 100.0, "signal": 1}}},
        )
        self.assertEqual(sample, {"market": {"bar": {"close": 100.0}}})
        self.assertIsNot(current["market"]["bar"], sample["market"]["bar"])
