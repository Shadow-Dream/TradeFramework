#!/usr/bin/env python3

import copy
from unittest import mock

from engine.compiler import result_projection as result_projection_compiler
from engine.runtime import result_projection as result_projection_runtime
from engine.authority import module_definition as module_definition_authority
from tests.support.backtest_runtime import BacktestIntegrationTestCase
from tests.support.pipeline_contract import definition as module_definition

class TemporaryResultRuntimeTests(BacktestIntegrationTestCase):
    def run_mock_result_cycles(self, result, temporary_modules, plan, invoker_class):
        with (
            mock.patch("engine.runtime.module_invoker.ModuleInvoker", invoker_class),
            mock.patch(
                "engine.contracts.result.compile_cycle_validator",
                return_value=lambda _data: None,
            ),
        ):
            with result_projection_runtime.ResultCycleProcessor(
                result.get("dataKeys", {}), (plan, {}, frozenset())
            ) as processor:
                cycles = []
                for index, item in enumerate(result["cycles"]):
                    cycle = {
                        "schemaVersion": 3,
                        "cycleId": f"cycle-{index}",
                        "decisionTime": f"2026-01-{index + 1:02d}T00:00:00Z",
                        "data": copy.deepcopy(item["data"]),
                    }
                    cycles.append(processor.prepare_cycle(index, cycle))
                processor.finalize()
                return cycles

    def test_temporary_result_modules_finalize_before_close(self):
        events = []

        class FakeInvoker:
            def __init__(self, *_args, **_kwargs):
                self.ports = {
                    "inputs": {"value": {"required": True}},
                    "outputs": {"value": {"required": True}},
                }

            @classmethod
            def from_authority(cls, *_args, **_kwargs):
                return cls()

            def invoke(self, inputs):
                events.append(("invoke", inputs["value"]))
                return {"value": inputs["value"] + 1}

            def finalize(self):
                events.append(("finalize", None))

            def close(self):
                events.append(("close", None))

        plan = [{
            "binding": {
                "instanceId": "temporary",
                "inputs": {"value": "source.value"},
                "outputs": {"value": "derived.value"},
            },
            "invocationAuthority": object(),
            "ports": {
                "inputs": {"value": {"required": True}},
                "outputs": {"value": {"required": True}},
            },
            "inputPlan": (("value", "source.value"),),
            "outputPlan": (("value", "derived.value"),),
        }]
        result = {
            "schemaVersion": 8,
            "cycles": [{"data": {"source": {"value": 2}}}],
            "dataKeys": {},
        }
        cycles = self.run_mock_result_cycles(
            result, [{"instanceId": "temporary"}], plan, FakeInvoker
        )
        self.assertEqual(cycles[0]["data"]["derived"]["value"], 3)
        self.assertEqual(events, [("invoke", 2), ("finalize", None), ("close", None)])

    def test_unchanged_result_contract_validates_each_cycle_once(self):
        validator = mock.Mock()
        cycle = {
            "schemaVersion": 3,
            "cycleId": "cycle-0",
            "decisionTime": "2026-01-01T00:00:00Z",
            "data": {},
        }
        with mock.patch(
            "engine.contracts.result.compile_cycle_validator",
            return_value=validator,
        ):
            with result_projection_runtime.ResultCycleProcessor({}) as processor:
                processor.prepare_cycle(0, cycle)
        validator.assert_called_once_with(cycle["data"])

    def test_temporary_result_contract_retains_post_transform_validation(self):
        base_validator = mock.Mock()
        final_validator = mock.Mock()
        cycle = {
            "schemaVersion": 3,
            "cycleId": "cycle-0",
            "decisionTime": "2026-01-01T00:00:00Z",
            "data": {},
        }
        with mock.patch(
            "engine.contracts.result.compile_cycle_validator",
            side_effect=(base_validator, final_validator),
        ):
            with result_projection_runtime.ResultCycleProcessor(
                {}, ([], {}, frozenset())
            ) as processor:
                processor.prepare_cycle(0, cycle)
        base_validator.assert_called_once_with(cycle["data"])
        final_validator.assert_called_once_with(cycle["data"])

    def test_body_failure_remains_primary_when_every_processor_cleanup_fails(self):
        primary = RuntimeError("projection failed first")
        invoker_cleanup = OSError("module cleanup failed later")
        identity_cleanup = OSError("identity cleanup failed later")
        root_cleanup = OSError("execution root cleanup failed later")
        processor = result_projection_runtime.ResultCycleProcessor({})
        real_identity_index = processor.base_cycle_ids
        invoker = mock.Mock()
        invoker.close.side_effect = invoker_cleanup
        processor.invokers = [({}, invoker)]
        processor.base_cycle_ids = mock.Mock()
        processor.base_cycle_ids.close.side_effect = identity_cleanup
        processor.execution_root = mock.Mock()
        processor.execution_root.cleanup.side_effect = root_cleanup

        try:
            with self.assertRaisesRegex(RuntimeError, "projection failed first") as raised:
                with processor:
                    raise primary
        finally:
            real_identity_index.close()

        self.assertIs(raised.exception, primary)
        self.assertIs(raised.exception.__context__, invoker_cleanup)
        processor.base_cycle_ids.close.assert_called_once()
        invoker.close.assert_called_once()

    def test_zero_cycle_temporary_modules_are_validated_and_run_lifecycle(self):
        events = []

        class FakeInvoker:
            def __init__(self, *_args, **_kwargs):
                self.ports = {"inputs": {}, "outputs": {}}
                events.append("initialize")

            @classmethod
            def from_authority(cls, *_args, **_kwargs):
                return cls()

            def finalize(self):
                events.append("finalize")

            def close(self):
                events.append("close")

        plan = [{
            "binding": {
                "instanceId": "temporary",
                "inputs": {},
                "outputs": {},
            },
            "invocationAuthority": object(),
            "ports": {"inputs": {}, "outputs": {}},
            "inputPlan": (),
            "outputPlan": (),
        }]
        result = {"schemaVersion": 8, "cycles": [], "dataKeys": {}}
        self.assertEqual(
            self.run_mock_result_cycles(
                result, [{"instanceId": "temporary"}], plan, FakeInvoker
            ),
            [],
        )
        self.assertEqual(events, ["initialize", "finalize", "close"])

    def test_result_projection_paths_resolve_typed_maps_and_reject_scalar_children(self):
        data_keys = {
            "dynamic": {
                "label": "dynamic",
                "schema": {
                    "type": "object",
                    "properties": {},
                    "required": [],
                    "additionalProperties": {"type": "number"},
                },
                "required": True,
                "source": {"path": "cycles.data.dynamic"},
                "encoding": {
                    "time": "decisionTime",
                    "value": "data.dynamic",
                },
            }
        }
        with result_projection_runtime.ResultCycleProcessor(data_keys) as processor:
            processor.require_projection_paths(["cycles.data.dynamic.a"])
            with self.assertRaisesRegex(ValueError, "unknown cycle path"):
                processor.require_projection_paths(["cycles.cycleId.child"])

    def test_temporary_module_skips_missing_optional_input(self):
        seen = []

        class FakeInvoker:
            def __init__(self, *_args, **_kwargs):
                self.ports = {
                    "inputs": {"value": {"required": False}},
                    "outputs": {},
                }

            @classmethod
            def from_authority(cls, *_args, **_kwargs):
                return cls()

            def invoke(self, inputs):
                seen.append(inputs)
                return {}

            def finalize(self):
                return None

            def close(self):
                return None

        plan = [{
            "binding": {
                "instanceId": "temporary",
                "inputs": {"value": "source.optional"},
                "outputs": {},
            },
            "invocationAuthority": object(),
            "ports": {
                "inputs": {"value": {"required": False}},
                "outputs": {},
            },
            "inputPlan": (("value", "source.optional"),),
            "outputPlan": (),
        }]
        result = {
            "schemaVersion": 8,
            "cycles": [{"data": {}}],
            "dataKeys": {},
        }
        self.run_mock_result_cycles(
            result, [{"instanceId": "temporary"}], plan, FakeInvoker
        )
        self.assertEqual(seen, [{}])

    def test_temporary_modules_initialize_before_cycle_topology_execution(self):
        events = []

        class FakeInvoker:
            def __init__(self, binding, *_args, **_kwargs):
                self.name = binding["instanceId"]
                self.ports = {
                    "inputs": {
                        name: {"required": True}
                        for name in binding["inputs"]
                    },
                    "outputs": {
                        name: {"required": True}
                        for name in binding["outputs"]
                    },
                }
                events.append(("initialize", self.name, None))

            @classmethod
            def from_authority(cls, authority, *_args, **_kwargs):
                return cls(authority)

            def invoke(self, inputs):
                value = next(iter(inputs.values()), 0)
                events.append(("invoke", self.name, value))
                return {"value": value + 1}

            def finalize(self):
                events.append(("finalize", self.name, None))

            def close(self):
                events.append(("close", self.name, None))

        plan = [
            {
                "binding": {
                    "instanceId": "first",
                    "inputs": {"value": "source.value"},
                    "outputs": {"value": "middle.value"},
                },
                "invocationAuthority": None,
                "ports": {
                    "inputs": {"value": {"required": True}},
                    "outputs": {"value": {"required": True}},
                },
                "inputPlan": (("value", "source.value"),),
                "outputPlan": (("value", "middle.value"),),
            },
            {
                "binding": {
                    "instanceId": "second",
                    "inputs": {"value": "middle.value"},
                    "outputs": {"value": "derived.value"},
                },
                "invocationAuthority": None,
                "ports": {
                    "inputs": {"value": {"required": True}},
                    "outputs": {"value": {"required": True}},
                },
                "inputPlan": (("value", "middle.value"),),
                "outputPlan": (("value", "derived.value"),),
            },
        ]
        for node in plan:
            node["invocationAuthority"] = node["binding"]
        result = {
            "schemaVersion": 8,
            "cycles": [
                {"data": {"source": {"value": 1}}},
                {"data": {"source": {"value": 10}}},
            ],
            "dataKeys": {},
        }
        cycles = self.run_mock_result_cycles(
            result, [{"instanceId": "temporary"}], plan, FakeInvoker
        )
        self.assertEqual(cycles[0]["data"]["derived"]["value"], 3)
        self.assertEqual(cycles[1]["data"]["derived"]["value"], 12)
        self.assertEqual(events, [
            ("initialize", "first", None),
            ("initialize", "second", None),
            ("invoke", "first", 1),
            ("invoke", "second", 2),
            ("invoke", "first", 10),
            ("invoke", "second", 11),
            ("finalize", "second", None),
            ("finalize", "first", None),
            ("close", "second", None),
            ("close", "first", None),
        ])

    def test_temporary_module_plan_uses_prewrite_and_nested_path_contracts(self):
        def definition(module_id, inputs, outputs):
            return module_definition(
                module_id,
                inputs=inputs,
                outputs=outputs,
            )

        string_port = {"schema": {"type": "string"}, "required": True}
        self_definition = definition(
            "self-transform",
            {"value": string_port},
            {"value": string_port},
        )
        self_module = {
            "instanceId": "self",
            "kind": "Signal",
            "moduleId": "self-transform",
            "version": "1",
            "config": {},
            "inputs": {"value": "x"},
            "outputs": {"value": "x"},
        }
        definitions = {"Signal/self-transform/1": self_definition}
        with mock.patch("engine.archive.version.verify_record"):
            with self.assertRaisesRegex(ValueError, "schema mismatch"):
                result_projection_compiler.compile_temporary_module_plan(
                    {"dataKeys": {"x": {
                        "schema": {"type": "number"}, "required": True,
                    }}},
                    [self_module],
                    definitions,
                )

        object_schema = {
            "type": "object",
            "properties": {"b": {"type": "string"}},
            "required": ["b"],
            "additionalProperties": False,
        }
        producer_definition = definition(
            "nested-producer",
            {},
            {"value": {"schema": object_schema, "required": True}},
        )
        consumer_definition = definition(
            "nested-consumer",
            {"value": string_port},
            {"value": string_port},
        )
        producer = {
            "instanceId": "producer",
            "kind": "Signal",
            "moduleId": "nested-producer",
            "version": "1",
            "config": {},
            "inputs": {},
            "outputs": {"value": "a"},
        }
        consumer = {
            "instanceId": "consumer",
            "kind": "Signal",
            "moduleId": "nested-consumer",
            "version": "1",
            "config": {},
            "inputs": {"value": "a.b"},
            "outputs": {"value": "c"},
        }
        definitions = {
            "Signal/nested-producer/1": producer_definition,
            "Signal/nested-consumer/1": consumer_definition,
        }
        with mock.patch("engine.archive.version.verify_record"):
            plan, contracts, _required_roots = result_projection_compiler.compile_temporary_module_plan(
                {"dataKeys": {}},
                [consumer, producer],
                definitions,
            )
        self.assertEqual(
            [node["binding"]["instanceId"] for node in plan],
            ["producer", "consumer"],
        )
        self.assertEqual(contracts["a.b"], {"type": "string"})

        optional_definition = definition(
            "optional-consumer",
            {"value": {"schema": {"type": "string"}, "required": False}},
            {"value": string_port},
        )
        optional_module = {
            "instanceId": "optional",
            "kind": "Signal",
            "moduleId": "optional-consumer",
            "version": "1",
            "config": {},
            "inputs": {"value": "absent.value"},
            "outputs": {"value": "optional.value"},
        }
        with mock.patch("engine.archive.version.verify_record"):
            with self.assertRaisesRegex(ValueError, "references unknown DataKey"):
                result_projection_compiler.compile_temporary_module_plan(
                    {"dataKeys": {}},
                    [optional_module],
                    {"Signal/optional-consumer/1": optional_definition},
                )

        typed_map = {
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": {"type": "number"},
        }
        typed_definition = definition(
            "typed-consumer",
            {"value": {"schema": {"type": "number"}, "required": False}},
            {"value": {"schema": {"type": "number"}, "required": True}},
        )
        typed_module = {
            "instanceId": "typed",
            "kind": "Signal",
            "moduleId": "typed-consumer",
            "version": "1",
            "config": {},
            "inputs": {"value": "dynamic.a"},
            "outputs": {"value": "derived.value"},
        }
        with mock.patch("engine.archive.version.verify_record"):
            typed_plan, _, _ = result_projection_compiler.compile_temporary_module_plan(
                {"dataKeys": {
                    "dynamic": {"schema": typed_map, "required": True},
                }},
                [typed_module],
                {"Signal/typed-consumer/1": typed_definition},
            )
        self.assertEqual(typed_plan[0]["binding"]["instanceId"], "typed")

        number_port = {"schema": {"type": "number"}, "required": True}
        destructive_definition = definition(
            "replace-parent",
            {},
            {
                "value": {
                    "schema": {
                        "type": "object",
                        "properties": {"b": {"type": "string"}},
                        "required": ["b"],
                        "additionalProperties": False,
                    },
                    "required": True,
                }
            },
        )
        reader_definition = definition(
            "read-initial-child",
            {"value": number_port},
            {"value": number_port},
        )
        destructive = {
            "instanceId": "replace",
            "kind": "Signal",
            "moduleId": "replace-parent",
            "version": "1",
            "config": {},
            "inputs": {},
            "outputs": {"value": "x"},
        }
        reader = {
            "instanceId": "read",
            "kind": "Signal",
            "moduleId": "read-initial-child",
            "version": "1",
            "config": {},
            "inputs": {"value": "x.a"},
            "outputs": {"value": "y"},
        }
        with mock.patch("engine.archive.version.verify_record"):
            destructive_plan, destructive_contracts, _destructive_required_roots = (
                result_projection_compiler.compile_temporary_module_plan(
                    {"dataKeys": {
                        "x": {
                            "schema": {
                                "type": "object",
                                "properties": {"a": {"type": "number"}},
                                "required": ["a"],
                                "additionalProperties": False,
                            },
                            "required": True,
                        },
                        "x.a": {"schema": {"type": "number"}, "required": True},
                    }},
                    [destructive, reader],
                    {
                        "Signal/replace-parent/1": destructive_definition,
                        "Signal/read-initial-child/1": reader_definition,
                    },
                )
            )
        self.assertEqual(
            [node["binding"]["instanceId"] for node in destructive_plan],
            ["read", "replace"],
        )
        self.assertNotIn("x.a", destructive_contracts)

        preserving_parent_definition = definition(
            "preserving-parent",
            {},
            {
                "parent": {
                    "schema": {
                        "type": "object",
                        "properties": {"a": {"type": "number"}},
                        "required": ["a"],
                        "additionalProperties": False,
                    },
                    "required": False,
                },
                "ready": {"schema": {"type": "number"}, "required": True},
            },
        )
        after_parent_definition = definition(
            "after-parent",
            {
                "value": {"schema": {"type": "number"}, "required": True},
                "ready": {"schema": {"type": "number"}, "required": True},
            },
            {"value": {"schema": {"type": "number"}, "required": True}},
        )
        preserving_parent = {
            "instanceId": "preserve",
            "kind": "Signal",
            "moduleId": "preserving-parent",
            "version": "1",
            "config": {},
            "inputs": {},
            "outputs": {"parent": "x", "ready": "q"},
        }
        after_parent = {
            "instanceId": "after",
            "kind": "Signal",
            "moduleId": "after-parent",
            "version": "1",
            "config": {},
            "inputs": {"value": "x.a", "ready": "q"},
            "outputs": {"value": "y"},
        }
        with mock.patch("engine.archive.version.verify_record"):
            preserving_plan, _, _ = result_projection_compiler.compile_temporary_module_plan(
                {"dataKeys": {
                    "x": {
                        "schema": {
                            "type": "object",
                            "properties": {"a": {"type": "number"}},
                            "required": ["a"],
                            "additionalProperties": False,
                        },
                        "required": True,
                    },
                }},
                [after_parent, preserving_parent],
                {
                    "Signal/preserving-parent/1": preserving_parent_definition,
                    "Signal/after-parent/1": after_parent_definition,
                },
            )
        self.assertEqual(
            [node["binding"]["instanceId"] for node in preserving_plan],
            ["preserve", "after"],
        )

    def test_temporary_output_plan_ignores_binding_object_order(self):
        object_schema = {
            "type": "object",
            "properties": {
                "base": {"type": "number"},
                "child": {"type": "number"},
            },
            "required": ["base"],
            "additionalProperties": False,
        }
        definition = module_definition(
            "ordered-output",
            outputs={
                "z_parent": {"schema": object_schema, "required": True},
                "a_child": {
                    "schema": {"type": "number"},
                    "required": True,
                },
            },
        )

        def module(outputs):
            return {
                "instanceId": "ordered",
                "kind": "Signal",
                "moduleId": "ordered-output",
                "version": "1",
                "config": {},
                "inputs": {},
                "outputs": outputs,
            }

        definitions = {"Signal/ordered-output/1": definition}
        with mock.patch("engine.archive.version.verify_record"):
            original = result_projection_compiler.compile_temporary_module_plan(
                {"dataKeys": {}},
                [module({"a_child": "x.child", "z_parent": "x"})],
                definitions,
            )
            reordered = result_projection_compiler.compile_temporary_module_plan(
                {"dataKeys": {}},
                [module({"z_parent": "x", "a_child": "x.child"})],
                definitions,
            )
        self.assertEqual(
            original[0][0]["outputPlan"],
            (("z_parent", "x"), ("a_child", "x.child")),
        )
        self.assertEqual(original[0][0]["outputPlan"], reordered[0][0]["outputPlan"])
        self.assertEqual(original[1:], reordered[1:])

    def test_temporary_modules_verify_each_definition_once(self):
        definition = module_definition(
            "shared-temporary",
            outputs={
                "value": {"schema": {"type": "number"}, "required": True},
            },
        )
        modules = [
            {
                "instanceId": instance_id,
                "kind": "Signal",
                "moduleId": "shared-temporary",
                "version": "1",
                "config": {},
                "inputs": {},
                "outputs": {"value": output},
            }
            for instance_id, output in (("first", "a"), ("second", "b"))
        ]
        verify = module_definition_authority.verify_module_definition_authority
        with (
            mock.patch("engine.archive.version.verify_record"),
            mock.patch.object(
                module_definition_authority,
                "verify_module_definition_authority",
                wraps=verify,
            ) as verify_authority,
        ):
            plan, _contracts, _roots = result_projection_compiler.compile_temporary_module_plan(
                {"dataKeys": {}},
                modules,
                {"Signal/shared-temporary/1": definition},
            )
        self.assertEqual(len(plan), 2)
        self.assertEqual(verify_authority.call_count, 1)

    def test_temporary_module_rejects_misindexed_definition_identity(self):
        definition = {
            "kind": "Signal",
            "moduleId": "different",
            "version": "1",
            "status": "archived",
            "configSchema": {"type": "object", "additionalProperties": False},
            "ports": {
                "inputs": {},
                "outputs": {
                    "value": {"schema": {"type": "number"}, "required": True},
                },
            },
        }
        module = {
            "instanceId": "temporary",
            "kind": "Signal",
            "moduleId": "expected",
            "version": "1",
            "config": {},
            "inputs": {},
            "outputs": {"value": "x"},
        }
        with self.assertRaisesRegex(ValueError, "identity.*repository key"):
            result_projection_compiler.compile_temporary_module_plan(
                {"dataKeys": {}},
                [module],
                {"Signal/expected/1": definition},
            )
