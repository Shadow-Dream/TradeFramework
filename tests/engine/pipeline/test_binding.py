import json
import unittest
from unittest import mock

from engine.service import control_api as control
from engine.authority import module_definition as module_definition_authority
from engine.authority import pipeline as pipeline_authority
from engine.authority.graph import compiled_graph_authority_plan
from engine.compiler.graph import (
    compile_module_graph,
    compile_module_graph_authority,
    compile_verified_module_graph,
    compile_verified_module_graph_authority,
)
from engine.compiler import pipeline as pipeline_compiler
from engine.compiler import pipeline_manifest as pipeline_manifest_compiler
from engine.contracts.graph import (
    compiled_graph_definition,
    compiled_graph_output_writes,
    validate_compiled_graph,
)
from tests.support.pipeline_contract import compiled_graph, definition, instance


class PipelineBindingTests(unittest.TestCase):
    def test_pipeline_compiler_passes_only_signal_owned_instances_to_signal_graph(self):
        number = {"schema": {"type": "number"}, "required": True}
        definitions = {
            "Universe/source/1": definition(
                "source", outputs={"value": number}, kind="Universe"
            ),
            "Signal/identity/1": definition(
                "identity", inputs={"value": number}, outputs={"value": number}
            ),
        }
        for module_definition in definitions.values():
            module_definition.update({
                "name": module_definition["moduleId"],
                "activationMode": "PythonModule",
                "parameters": {},
                "description": "test module",
                "builtin": False,
            })

        def draft(*, with_signal):
            instances = {
                "source": instance(
                    "source", "source", outputs={"value": "stage.value"}, kind="Universe"
                )
            }
            signal_graph = {"nodes": [], "inputs": {}, "outputs": {}}
            if with_signal:
                instances["identity"] = instance(
                    "identity",
                    "identity",
                    inputs={"value": "wire.value"},
                    outputs={"value": "wire.result"},
                )
                signal_graph = {
                    "nodes": ["identity"],
                    "inputs": {
                        "value": {"dataKey": "stage.value", "wire": "wire.value"}
                    },
                    "outputs": {
                        "result": {"dataKey": "signal.value", "wire": "wire.result"}
                    },
                }
            return {
                "pipelineId": "owned-signal-instances",
                "name": "Owned Signal instances",
                "config": {
                    "observationInput": {"whitelist": [], "blacklist": []}
                },
                "instances": instances,
                "stages": {"universe": ["source"]},
                "signalGraph": signal_graph,
            }

        with mock.patch(
            "engine.archive.version.verify_record", side_effect=lambda record: record
        ):
            empty_signal = control.compile_pipeline_manifest_from_definitions(
                draft(with_signal=False), definitions
            )
            mixed = control.compile_pipeline_manifest_from_definitions(
                draft(with_signal=True), definitions
            )
            directly_owned = (
                pipeline_manifest_compiler.compile_pipeline_manifest_from_definitions(
                    draft(with_signal=True), definitions
                )
            )

        self.assertEqual(empty_signal["signalGraph"]["nodes"], [])
        self.assertEqual(mixed["signalGraph"]["nodes"], ["identity"])
        self.assertEqual(mixed, directly_owned)

    def test_canonical_graph_does_not_use_binding_input_object_order(self):
        number_port = {"schema": {"type": "number"}}
        definitions = {
            "Signal/sink/1": definition(
                "sink",
                inputs={"z": number_port, "a": number_port},
            )
        }
        instances = {
            "sink": instance(
                "sink",
                "sink",
                inputs={"z": "wire.z", "a": "wire.a"},
            )
        }
        graph = {
            "nodes": ["sink"],
            "inputs": {
                "z-input": {"dataKey": "source.z", "wire": "wire.z"},
                "a-input": {"dataKey": "source.a", "wire": "wire.a"},
            },
            "outputs": {},
        }
        initial = {
            "source.z": {"type": "number"},
            "source.a": {"type": "number"},
        }
        with mock.patch(
            "engine.archive.version.verify_record",
            side_effect=lambda record: record,
        ):
            plan = compile_module_graph(
                graph, instances, definitions, initial, label="Port order Graph"
            )
            frozen = json.loads(json.dumps(plan, sort_keys=True))
            self.assertEqual(
                [edge["to"]["port"] for edge in frozen["edges"]],
                ["a", "z"],
            )
            self.assertEqual(validate_compiled_graph(frozen), frozen)
            rebound = compile_module_graph_authority(
                compiled_graph_definition(frozen),
                frozen["bindings"],
                definitions,
                frozen["inputContracts"],
                required_roots=frozen["inputRequiredRoots"],
                label="Port order Graph",
            )
        self.assertEqual(compiled_graph_authority_plan(rebound), frozen)

    def test_verified_preliminary_graph_is_plan_only_and_authority_is_strict(self):
        number_port = {"schema": {"type": "number"}}
        definitions = {
            "Signal/sink/1": definition("sink", inputs={"value": number_port})
        }
        instances = {
            "sink": instance(
                "sink",
                "sink",
                inputs={"value": "wire.unknown"},
            )
        }
        graph = {
            "nodes": ["sink"],
            "inputs": {
                "unknown": {
                    "dataKey": "not.composed",
                    "wire": "wire.unknown",
                }
            },
            "outputs": {},
        }
        with mock.patch(
            "engine.archive.version.verify_record",
            side_effect=lambda record: record,
        ):
            authorities = {
                key: module_definition_authority.verify_module_definition_authority(
                    value
                )
                for key, value in definitions.items()
            }
            plan = compile_verified_module_graph(
                graph,
                instances,
                authorities,
                {},
                allowed_kinds={"Signal"},
                strict_sources=False,
            )
            with self.assertRaisesRegex(TypeError, "strict_sources"):
                compile_verified_module_graph_authority(
                    graph,
                    instances,
                    authorities,
                    {},
                    allowed_kinds={"Signal"},
                    strict_sources=False,
                )
        self.assertEqual(plan["inputContracts"], {})
        self.assertEqual(plan["edges"][0]["from"]["schema"], {"type": "number"})

    def test_pipeline_state_binding_preserves_canonical_parent_child_write_order(self):
        parent_schema = {
            "type": "object",
            "properties": {"base": {"type": "number"}},
            "required": ["base"],
            "additionalProperties": False,
        }
        definitions = {
            "Signal/parent-writer/1": definition(
                "parent-writer",
                outputs={
                    "value": {"schema": parent_schema, "required": True},
                },
            ),
            "Signal/child-writer/1": definition(
                "child-writer",
                outputs={
                    "value": {"schema": {"type": "number"}, "required": True},
                },
            ),
        }
        instances = {
            "parent": instance(
                "parent", "parent-writer", outputs={"value": "wire.parent"}
            ),
            "child": instance(
                "child", "child-writer", outputs={"value": "wire.child"}
            ),
        }
        signal_plan = compiled_graph(
            {
                "nodes": ["parent", "child"],
                "inputs": {},
                "outputs": {
                    "z-parent": {"dataKey": "state", "wire": "wire.parent"},
                    "a-child": {
                        "dataKey": "state.child",
                        "wire": "wire.child",
                    },
                },
            },
            instances,
            definitions,
        )
        canonical_plan = json.loads(json.dumps(signal_plan, sort_keys=True))
        self.assertEqual(list(canonical_plan["outputs"]), ["a-child", "z-parent"])
        self.assertEqual(
            [item[0] for item in compiled_graph_output_writes(canonical_plan)],
            ["z-parent", "a-child"],
        )
        modules = [
            {"key": node_id, **instances[node_id]}
            for node_id in ("parent", "child")
        ]
        manifest = {
            "name": "Ordered writes",
            "config": {
                "observationInput": {"whitelist": [], "blacklist": []}
            },
            "modules": modules,
            "topology": ["parent", "child"],
            "universe": [],
            "signalGraph": compiled_graph_definition(canonical_plan),
            "target": [],
            "constraint": [],
        }
        with mock.patch(
            "engine.archive.version.verify_record",
            side_effect=lambda record: record,
        ):
            definition_authorities = {
                key: module_definition_authority.verify_module_definition_authority(value)
                for key, value in definitions.items()
            }
        template = pipeline_authority.pipeline_contract_template_from_verified_authorities(
            manifest, definitions, definition_authorities
        )
        definitions["Signal/child-writer/1"]["ports"]["outputs"]["value"][
            "schema"
        ] = {"type": "string"}
        with mock.patch(
            "engine.archive.version.verify_record",
            side_effect=lambda record: record,
        ):
            bound = pipeline_compiler.bind_pipeline_contract_plan(template, {})
        (
            plan,
            signal_authority,
            _direct_invocations,
        ) = pipeline_authority.bound_pipeline_contract_plan_material(bound)
        self.assertEqual(
            [item[0] for item in compiled_graph_output_writes(plan["signalPlan"])],
            ["z-parent", "a-child"],
        )
        self.assertEqual(
            plan["signalPlan"]["outputContracts"],
            signal_plan["outputContracts"],
        )

        self.assertEqual(
            [
                item[0]
                for item in compiled_graph_output_writes(
                    compiled_graph_authority_plan(signal_authority)
                )
            ],
            ["z-parent", "a-child"],
        )
        self.assertEqual(
            compiled_graph_authority_plan(signal_authority)["outputContracts"],
            signal_plan["outputContracts"],
        )

    def test_graph_typed_map_inputs_check_type_and_presence_without_leaf_declarations(self):
        number_port = {"schema": {"type": "number"}, "required": False}
        string_port = {"schema": {"type": "string"}, "required": False}
        required_number_port = {"schema": {"type": "number"}, "required": True}
        graph = {
            "nodes": ["consumer"],
            "inputs": {"source": {"dataKey": "x.a", "wire": "source"}},
            "outputs": {},
        }
        numeric_map = {
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": {"type": "number"},
        }

        optional_definition = definition(
            "consumer", inputs={"value": number_port}
        )
        optional_instance = instance(
            "consumer", "consumer", inputs={"value": "source"}
        )
        compiled_graph(
            graph,
            {"consumer": optional_instance},
            {"Signal/consumer/1": optional_definition},
            {"x": numeric_map},
        )

        wrong_definition = definition(
            "consumer", inputs={"value": string_port}
        )
        with self.assertRaisesRegex(ValueError, "schema mismatch"):
            compiled_graph(
                graph,
                {"consumer": optional_instance},
                {"Signal/consumer/1": wrong_definition},
                {"x": numeric_map},
            )

        required_definition = definition(
            "consumer", inputs={"value": required_number_port}
        )
        with self.assertRaisesRegex(ValueError, "optional wire"):
            compiled_graph(
                graph,
                {"consumer": optional_instance},
                {"Signal/consumer/1": required_definition},
                {"x": numeric_map},
            )

        required_map = {**numeric_map, "required": ["a"]}
        compiled_graph(
            graph,
            {"consumer": optional_instance},
            {"Signal/consumer/1": required_definition},
            {"x": required_map},
        )

    def test_compiled_graph_preserves_optional_ancestor_presence_on_revalidation(self):
        number = {"type": "number"}
        graph = {
            "nodes": [],
            "inputs": {"source": {"dataKey": "x.a", "wire": "wire"}},
            "outputs": {"target": {"dataKey": "y", "wire": "wire"}},
        }
        initial = {
            "x": {
                "type": "object",
                "properties": {"a": number},
                "required": [],
                "additionalProperties": False,
            }
        }
        with mock.patch(
            "engine.archive.version.verify_record",
            side_effect=lambda record: record,
        ):
            plan = compile_module_graph(graph, {}, {}, initial)
            self.assertEqual(plan["requiredOutputs"], [])
            self.assertEqual(plan["inputContracts"]["x"]["required"], [])
            self.assertEqual(validate_compiled_graph(plan), plan)

    def test_signal_graph_topology_sorts_modules_and_excludes_boundaries(self):
        number = {"schema": {"type": "number"}}
        definitions = {
            "Signal/producer/1": definition("producer", outputs={"value": number}),
            "Signal/consumer/1": definition("consumer", inputs={"value": number}, outputs={"result": number}),
        }
        instances = {
            "consumer": instance("consumer", "consumer", inputs={"value": "wire.value"}, outputs={"result": "wire.result"}),
            "producer": instance("producer", "producer", outputs={"value": "wire.value"}),
        }
        graph = {
            "nodes": ["consumer", "producer"],
            "inputs": {"price-input": {"dataKey": "price.close", "wire": "wire.price"}},
            "outputs": {"result-output": {"dataKey": "signal.result", "wire": "wire.result"}},
        }
        plan = compiled_graph(graph, instances, definitions, {"price.close": {"type": "number"}})
        self.assertEqual(plan["topology"], ["producer", "consumer"])
        self.assertNotIn("price-input", plan["bindings"])
        self.assertNotIn("result-output", plan["bindings"])

    def test_signal_graph_rejects_missing_wire_and_cycle(self):
        number = {"schema": {"type": "number"}}
        definitions = {
            "Signal/first/1": definition("first", inputs={"value": number}, outputs={"value": number}),
            "Signal/second/1": definition("second", inputs={"value": number}, outputs={"value": number}),
        }
        missing = {"first": instance("first", "first", inputs={"value": "missing"}, outputs={"value": "first"})}
        with self.assertRaisesRegex(ValueError, "unknown wire"):
            compiled_graph({"nodes": ["first"], "inputs": {}, "outputs": {}}, missing, definitions)

        cyclic = {
            "first": instance("first", "first", inputs={"value": "second"}, outputs={"value": "first"}),
            "second": instance("second", "second", inputs={"value": "first"}, outputs={"value": "second"}),
        }
        with self.assertRaisesRegex(ValueError, "contains a cycle"):
            compiled_graph({"nodes": ["first", "second"], "inputs": {}, "outputs": {}}, cyclic, definitions)

    def test_nonstrict_unknown_graph_input_is_not_inferred_from_first_consumer(self):
        definitions = {
            "Signal/number/1": definition(
                "number", inputs={"value": {"schema": {"type": "number"}}}
            ),
            "Signal/integer/1": definition(
                "integer", inputs={"value": {"schema": {"type": "integer"}}}
            ),
        }
        instances = {
            "number": instance("number", "number", inputs={"value": "source"}),
            "integer": instance("integer", "integer", inputs={"value": "source"}),
        }
        graph_base = {
            "inputs": {"source": {"dataKey": "unknown.value", "wire": "source"}},
            "outputs": {},
        }
        plans = []
        with mock.patch(
            "engine.archive.version.verify_record",
            side_effect=lambda record: record,
        ):
            for order in (["number", "integer"], ["integer", "number"]):
                plans.append(
                    compile_module_graph(
                        {**graph_base, "nodes": order},
                        instances,
                        definitions,
                        {},
                        allowed_kinds={"Signal"},
                        label="Signal Graph",
                        strict_sources=False,
                    )
                )
        self.assertEqual(plans[0]["inputContracts"], {})
        self.assertEqual(plans[1]["inputContracts"], {})

    def test_graph_output_data_key_uniqueness_is_not_platform_enforced(self):
        number = {"schema": {"type": "number"}}
        definitions = {
            "Signal/source/1": definition("source", outputs={"left": number, "right": number}),
        }
        instances = {
            "source": instance("source", "source", outputs={"left": "wire.left", "right": "wire.right"}),
        }
        plan = compiled_graph({
            "nodes": ["source"],
            "inputs": {},
            "outputs": {
                "left-output": {"dataKey": "signal.value", "wire": "wire.left"},
                "right-output": {"dataKey": "signal.value", "wire": "wire.right"},
            },
        }, instances, definitions)
        self.assertIn("signal.value", plan["outputContracts"])
