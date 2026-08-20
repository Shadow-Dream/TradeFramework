#!/usr/bin/env python3

import base64
import json
from unittest import mock

from engine.runtime import lifecycle as runtime_lifecycle
from engine.authority.graph import compiled_graph_authority_plan
from engine.compiler.graph import (
    compile_module_graph,
    compile_module_graph_authority,
)
from engine.contracts.graph import (
    compiled_graph_output_writes,
    validate_compiled_graph,
)
from engine.contracts.data import compile_declared_data_json_proof
from engine.runtime.graph import ModuleGraphRuntime
from engine.service import module_publication
from engine.service import control_api as control
from tests.support.module_runtime import (
    ModuleRuntimeTestCase,
    PAYLOAD_SCHEMA,
    module_invoker,
    runtime_from_compiled_plan,
)

class ModuleGraphRuntimeTests(ModuleRuntimeTestCase):
    def test_module_graph_restore_uses_topology_not_snapshot_object_order(self):
        calls = []

        class Stateful:
            def __init__(self, name):
                self.name = name

            def restore(self, _snapshot):
                calls.append(self.name)

        runtime = ModuleGraphRuntime.__new__(ModuleGraphRuntime)
        runtime._closed = False
        runtime._finalized = False
        runtime._plan = {"topology": ["z", "a"]}
        runtime._invokers = {"z": Stateful("z"), "a": Stateful("a")}
        runtime.restore({"a": {}, "z": {}})
        self.assertEqual(calls, ["z", "a"])

    def test_module_graph_rejects_definition_content_under_a_false_index_key(self):
        definition = {
            "kind": "Signal",
            "moduleId": "actual",
            "version": "1",
            "ports": {"inputs": {}, "outputs": {}},
            "configSchema": {"type": "object", "additionalProperties": False},
        }
        instance = {
            "instanceId": "node",
            "kind": "Signal",
            "moduleId": "indexed",
            "version": "1",
            "config": {},
            "inputs": {},
            "outputs": {},
        }
        with mock.patch(
            "engine.archive.version.verify_record"
        ):
            with self.assertRaisesRegex(ValueError, "identity.*repository key"):
                compile_module_graph_authority(
                    {"nodes": ["node"], "inputs": {}, "outputs": {}},
                    {"node": instance},
                    {"Signal/indexed/1": definition},
                    {},
                )

    def test_module_graph_rejects_instances_outside_its_node_set(self):
        with self.assertRaisesRegex(ValueError, "exactly match graph.nodes.*ghost"):
            compile_module_graph_authority(
                {"nodes": [], "inputs": {}, "outputs": {}},
                {"ghost": {"not": "a Module instance"}},
                {},
                {},
            )

    def test_module_graph_lifecycle_and_resource_authorities_are_read_only(self):
        authority = compile_module_graph_authority(
            {"nodes": [], "inputs": {}, "outputs": {}}, {}, {}, {}
        )
        with self.assertRaisesRegex(AttributeError, "immutable"):
            authority._plan_json = "{}"
        runtime = ModuleGraphRuntime.from_compiled_authority(authority)
        try:
            with self.assertRaisesRegex(AttributeError, "Engine-owned"):
                _ = runtime.invokers
            for field, value in (
                ("invokers", {}),
                ("has_executed", False),
                ("finalized", False),
                ("closed", False),
            ):
                with self.subTest(field=field), self.assertRaises(AttributeError):
                    setattr(runtime, field, value)
        finally:
            runtime.close()

    def test_module_graph_finalize_is_an_explicit_terminal_state(self):
        authority = compile_module_graph_authority(
            {"nodes": [], "inputs": {}, "outputs": {}}, {}, {}, {}
        )
        runtime = ModuleGraphRuntime.from_compiled_authority(authority)
        runtime.finalize()
        self.assertTrue(runtime.finalized)
        operations = (
            ("execute", lambda: runtime.execute_outputs({})),
            ("snapshot", runtime.snapshot),
            ("restore", lambda: runtime.restore({})),
            ("finalize", runtime.finalize),
        )
        for operation, invoke in operations:
            with self.subTest(operation=operation), self.assertRaises(RuntimeError):
                invoke()
        runtime.close()
        with self.assertRaisesRegex(RuntimeError, "closed"):
            runtime.restore({})

    def test_module_graph_stays_finalized_after_child_finalize_failure(self):
        authority = compile_module_graph_authority(
            {"nodes": [], "inputs": {}, "outputs": {}}, {}, {}, {}
        )
        runtime = ModuleGraphRuntime.from_compiled_authority(authority)
        broken = mock.Mock()
        broken.finalize.side_effect = RuntimeError("finalize failed")
        runtime._plan["topology"] = ["broken"]
        runtime._invokers = {"broken": broken}
        with self.assertRaisesRegex(RuntimeError, "finalize failed"):
            runtime.finalize()
        self.assertTrue(runtime.finalized)
        with self.assertRaisesRegex(RuntimeError, "finalized"):
            runtime.execute_outputs({})
        runtime.close()
        broken.close.assert_called_once_with()

    def test_unresolved_optional_graph_boundary_cannot_execute_as_a_value(self):
        authority = compile_module_graph_authority(
            {
                "nodes": [],
                "inputs": {
                    "source": {
                        "dataKey": "unknown.value",
                        "wire": "wire.value",
                    },
                },
                "outputs": {
                    "result": {
                        "dataKey": "result.value",
                        "wire": "wire.value",
                    },
                },
            },
            {},
            {},
            {},
        )
        runtime = ModuleGraphRuntime.from_compiled_authority(authority)
        try:
            self.assertEqual(runtime.execute_outputs({}), {})
            with self.assertRaisesRegex(ValueError, "Graph.inputs.source.*forbidden"):
                runtime.execute_outputs({"unknown": {"value": "arbitrary"}})
        finally:
            runtime.close()

    def test_graph_rejects_invalid_boundary_value_before_module_execution(self):
        definition = self.archive_worker("strict-graph-boundary-worker")
        plan = self.compiled_plan(
            definition,
            mode="mutate",
            inputs={"payload": "wire.payload"},
            outputs={"result": "wire.result"},
            graph_inputs={
                "payload": {
                    "dataKey": "source.payload",
                    "wire": "wire.payload",
                },
            },
            graph_outputs={
                "result": {
                    "dataKey": "signal.result",
                    "wire": "wire.result",
                },
            },
            initial_contracts={"source.payload": PAYLOAD_SCHEMA},
        )
        runtime = runtime_from_compiled_plan(plan, {
            f"Signal/{definition['moduleId']}/{definition['version']}": definition,
        })
        try:
            with self.assertRaisesRegex(ValueError, "Module Graph input.source.payload.value"):
                runtime.execute_outputs({
                    "source": {"payload": {"value": "invalid"}},
                })
            self.assertEqual(runtime.snapshot()["worker"]["count"], 0)
        finally:
            runtime.close()

    def test_graph_proves_unused_declared_inputs_before_any_module_execution(self):
        calls = []

        class Invoker:
            def invoke(self, _inputs):
                calls.append("invoke")
                return {}

        runtime = ModuleGraphRuntime.__new__(ModuleGraphRuntime)
        runtime._closed = False
        runtime._finalized = False
        runtime._has_executed = False
        runtime._plan = {
            "topology": ["early"],
            "inputSources": {
                "named": {
                    "contracts": {"namedRequired": {"type": "number"}},
                    "requiredRoots": ["namedRequired"],
                },
            },
        }
        runtime._invokers = {"early": Invoker()}
        runtime._invocation_authorities = {"early": object()}
        runtime._missing = object()
        runtime._slot_wires = []
        runtime._input_plan = []
        runtime._output_plan = []
        runtime._node_plan = [("early", runtime._invokers["early"], (), ())]
        runtime._wire_validation_plan = ()
        runtime._input_contract_proof = compile_declared_data_json_proof(
            {
                "requiredButUnused": {"type": "number"},
                "unusedObject": {
                    "type": "object",
                    "properties": {"value": {"type": "number"}},
                    "required": ["value"],
                    "additionalProperties": False,
                },
            },
            required_roots={"requiredButUnused", "unusedObject"},
            contracts_expanded=True,
            path="Module Graph input",
            boundary_paths={"requiredButUnused", "unusedObject"},
        )
        runtime._input_source_contract_proofs = {
            "named": compile_declared_data_json_proof(
                {"namedRequired": {"type": "number"}},
                required_roots={"namedRequired"},
                contracts_expanded=True,
                path="Module Graph input source 'named'",
                boundary_paths={"namedRequired"},
            ),
        }
        runtime.execution_seconds = 0.0
        runtime.module_dispatch_seconds = 0.0

        for initial_data, input_sources, expected in (
            ({}, {"named": {"namedRequired": 1}}, "requiredButUnused.*unusedObject"),
            (
                {"requiredButUnused": "wrong", "unusedObject": {"value": 1}},
                {"named": {"namedRequired": 1}},
                "expected number",
            ),
            (
                {"requiredButUnused": 1, "unusedObject": {"value": 1}},
                {"named": {}},
                "namedRequired",
            ),
            (
                {"requiredButUnused": 1, "unusedObject": {"value": 1}},
                {"named": {"namedRequired": "wrong"}},
                "expected number",
            ),
            (
                {"requiredButUnused": 1, "unusedObject": {}},
                {"named": {"namedRequired": 1}},
                "unusedObject.value",
            ),
        ):
            with self.subTest(expected=expected), self.assertRaisesRegex(
                ValueError, expected
            ):
                runtime.execute_outputs(initial_data, input_sources=input_sources)
            self.assertEqual(calls, [])
            self.assertFalse(runtime.has_executed)

        runtime._node_plan = []
        self.assertEqual(
            runtime.execute_outputs(
                {
                    "requiredButUnused": 1,
                    "unusedObject": {"value": 2},
                    "compositionExtra": {"deep": object()},
                },
                input_sources={
                    "named": {
                        "namedRequired": 2,
                        "namedCompositionExtra": object(),
                    },
                },
            ),
            {},
        )

    def test_graph_validates_one_fanout_wire_once_and_isolates_each_consumer(self):
        definition = self.archive_python_module("validated-fanout")
        definitions = {
            f"Signal/{definition['moduleId']}/{definition['version']}": definition,
        }
        bindings = {
            node_id: self.binding(
                definition,
                instance_id=node_id,
                inputs={"payload": "wire.payload"},
                outputs={"result": f"wire.{node_id}.result"},
            )
            for node_id in ("first", "second")
        }
        plan = compile_module_graph(
            {
                "nodes": ["first", "second"],
                "inputs": {
                    "payload": {
                        "dataKey": "source.payload",
                        "wire": "wire.payload",
                    },
                },
                "outputs": {
                    "first-result": {
                        "dataKey": "result.first",
                        "wire": "wire.first.result",
                    },
                    "second-result": {
                        "dataKey": "result.second",
                        "wire": "wire.second.result",
                    },
                },
            },
            bindings,
            definitions,
            {"source.payload": PAYLOAD_SCHEMA},
            allowed_kinds={"Signal"},
            label="Validated fanout Graph",
        )
        validate_calls = 0
        from engine.contracts import data as data_contract

        real_compile = data_contract.compile_normalized_json_validator

        def compile_counted_validator(*args, **kwargs):
            validator = real_compile(*args, **kwargs)

            def validate(value):
                nonlocal validate_calls
                validate_calls += 1
                return validator(value)

            return validate

        with mock.patch.object(
            data_contract,
            "compile_normalized_json_validator",
            side_effect=compile_counted_validator,
        ):
            runtime = runtime_from_compiled_plan(plan, definitions)
        source = {"source": {"payload": {"value": 7.0}}}
        try:
            self.assertEqual(
                runtime.execute_outputs(source),
                {
                    "result": {
                        "first": {"value": 7.0},
                        "second": {"value": 7.0},
                    },
                },
            )
            self.assertEqual(source, {"source": {"payload": {"value": 7.0}}})
            self.assertEqual(validate_calls, 1)
        finally:
            runtime.close()

    def test_validated_graph_inputs_preserve_strict_python_alias_semantics(self):
        source = r'''from strategy_devkit.module_sdk import SignalModule


class AliasObserver(SignalModule):
    def update(self, left, right):
        return {"result": {"value": 1 if left is right else 0}}


MODULE_CLASS = AliasObserver
'''
        result = module_publication.publish_module(self.config, {
            "kind": "Signal",
            "moduleId": "validated-alias-observer",
            "name": "validated-alias-observer",
            "description": "Observe Python input alias semantics.",
            "activationMode": "PythonModule",
            "parameters": {},
            "configSchema": {"type": "object", "additionalProperties": False},
            "ports": {
                "inputs": {
                    "left": {"schema": PAYLOAD_SCHEMA},
                    "right": {"schema": PAYLOAD_SCHEMA},
                },
                "outputs": {"result": {"schema": PAYLOAD_SCHEMA}},
            },
            "files": [{
                "path": "module.py",
                "contentBase64": base64.b64encode(source.encode()).decode(),
                "executable": False,
            }],
        })
        definition = result["definition"]
        shared = {"value": 1.0}
        direct = module_invoker(self.binding(definition), definition)
        try:
            direct_result = direct.invoke({"left": shared, "right": shared})
        finally:
            direct.close()

        binding = self.binding(
            definition,
            inputs={"left": "wire.left", "right": "wire.right"},
            outputs={"result": "wire.result"},
        )
        key = f"Signal/{definition['moduleId']}/{definition['version']}"
        plan = compile_module_graph(
            {
                "nodes": [definition["moduleId"]],
                "inputs": {
                    "left": {"dataKey": "source.left", "wire": "wire.left"},
                    "right": {"dataKey": "source.right", "wire": "wire.right"},
                },
                "outputs": {
                    "result": {
                        "dataKey": "signal.result",
                        "wire": "wire.result",
                    },
                },
            },
            {definition["moduleId"]: binding},
            {key: definition},
            {"source.left": PAYLOAD_SCHEMA, "source.right": PAYLOAD_SCHEMA},
            allowed_kinds={"Signal"},
            label="Validated alias Graph",
        )
        runtime = runtime_from_compiled_plan(plan, {key: definition})
        try:
            graph_result = runtime.execute_outputs({
                "source": {"left": shared, "right": shared},
            })
        finally:
            runtime.close()

        self.assertEqual(direct_result, {"result": {"value": 0}})
        self.assertEqual(graph_result, {"signal": {"result": {"value": 0}}})

    def test_graph_invokes_modules_for_every_engine_cycle(self):
        definition = self.archive_worker("graph-per-cycle-worker")
        plan = self.compiled_plan(
            definition,
            inputs={"payload": "wire.payload"},
            outputs={"result": "wire.result"},
            graph_inputs={
                "payload": {
                    "dataKey": "source.payload",
                    "wire": "wire.payload",
                },
            },
            graph_outputs={
                "result": {
                    "dataKey": "signal.result",
                    "wire": "wire.result",
                },
            },
            initial_contracts={"source.payload": PAYLOAD_SCHEMA},
        )
        key = f"Signal/{definition['moduleId']}/{definition['version']}"
        runtime = runtime_from_compiled_plan(plan, {key: definition})
        try:
            first = runtime.execute({"source": {"payload": {"value": 1.0}}})
            second = runtime.execute({"source": {"payload": {"value": 1.0}}})
            third = runtime.execute({"source": {"payload": {"value": 2.0}}})
            self.assertEqual(
                first["outputs"], {"signal": {"result": {"value": 1.0}}}
            )
            self.assertEqual(second["outputs"], first["outputs"])
            self.assertEqual(
                third["outputs"], {"signal": {"result": {"value": 2.0}}}
            )
            self.assertEqual(runtime.snapshot()["worker"]["count"], 3)
            metrics = runtime.metadata()["moduleTransports"]["worker"]
            self.assertEqual(metrics["invocationCount"], 3)
            metadata = runtime.metadata()
            self.assertGreaterEqual(metadata["executionSeconds"], 0.0)
            self.assertGreaterEqual(metadata["moduleDispatchSeconds"], 0.0)
            self.assertGreaterEqual(metadata["graphOverheadSeconds"], 0.0)
            self.assertEqual(
                metadata["moduleTransports"]["worker"]["invocationCount"],
                3,
            )
        finally:
            runtime.close()

    def test_graph_outputs_only_execution_preserves_public_result(self):
        definition = self.archive_worker("graph-outputs-only-worker")
        plan = self.compiled_plan(
            definition,
            inputs={"payload": "wire.payload"},
            outputs={"result": "wire.result"},
            graph_inputs={
                "payload": {
                    "dataKey": "source.payload",
                    "wire": "wire.payload",
                },
            },
            graph_outputs={
                "result": {
                    "dataKey": "signal.result",
                    "wire": "wire.result",
                },
            },
            initial_contracts={"source.payload": PAYLOAD_SCHEMA},
        )
        key = f"Signal/{definition['moduleId']}/{definition['version']}"
        runtime = runtime_from_compiled_plan(plan, {key: definition})
        try:
            source = {"source": {"payload": {"value": 7.0}}}
            self.assertEqual(
                runtime.execute_outputs(source),
                {"signal": {"result": {"value": 7.0}}},
            )
            self.assertEqual(source, {"source": {"payload": {"value": 7.0}}})
        finally:
            runtime.close()

    def test_canonical_frozen_graph_preserves_parent_child_output_write_order(self):
        definitions = {}
        parent_schema = {
            "type": "object",
            "properties": {"base": {"type": "number"}},
            "required": ["base"],
            "additionalProperties": False,
        }
        plan = compile_module_graph(
            {
                "nodes": [],
                "inputs": {
                    "parent-input": {
                        "dataKey": "source.parent",
                        "wire": "wire.parent",
                    },
                    "child-input": {
                        "dataKey": "source.child",
                        "wire": "wire.child",
                    },
                },
                "outputs": {
                    # Parent must be written before its child.  The IDs are
                    # deliberately inverse to canonical object-key order.
                    "z-parent-output": {
                        "dataKey": "target",
                        "wire": "wire.parent",
                    },
                    "a-child-output": {
                        "dataKey": "target.child",
                        "wire": "wire.child",
                    },
                },
            },
            {},
            definitions,
            {
                "source.parent": parent_schema,
                "source.child": {"type": "number"},
            },
            label="Canonical output order Graph",
        )
        frozen = json.loads(json.dumps(plan, sort_keys=True, separators=(",", ":")))

        self.assertEqual(
            list(frozen["outputs"]),
            ["a-child-output", "z-parent-output"],
        )
        self.assertEqual(
            [
                edge["to"]["node"]
                for edge in frozen["edges"]
                if edge["to"]["node"] in frozen["outputs"]
            ],
            ["z-parent-output", "a-child-output"],
        )
        self.assertEqual(validate_compiled_graph(frozen), frozen)
        self.assertEqual(
            [write[0] for write in compiled_graph_output_writes(frozen)],
            ["z-parent-output", "a-child-output"],
        )

        runtime = runtime_from_compiled_plan(frozen, definitions)
        try:
            reordered_object_authority = compile_module_graph_authority(
                {
                    "nodes": frozen["nodes"],
                    "inputs": frozen["inputs"],
                    "outputs": frozen["outputs"],
                },
                {},
                definitions,
                frozen["inputContracts"],
                required_roots=frozen["inputRequiredRoots"],
                label="Reordered output object Graph",
            )
            self.assertEqual(
                [
                    edge["to"]["node"]
                    for edge in compiled_graph_authority_plan(
                        reordered_object_authority
                    )["edges"]
                    if edge["to"]["node"] in compiled_graph_authority_plan(
                        reordered_object_authority
                    )["outputs"]
                ],
                ["z-parent-output", "a-child-output"],
            )
            self.assertEqual(
                runtime.execute_outputs({
                    "source": {
                        "parent": {"base": 1},
                        "child": 2,
                    }
                }),
                {"target": {"base": 1, "child": 2}},
            )
        finally:
            runtime.close()

    def test_graph_restore_reinstates_module_state_before_the_next_cycle(self):
        definition = self.archive_worker("graph-restore-worker")
        plan = self.compiled_plan(
            definition,
            inputs={"payload": "wire.payload"},
            outputs={"result": "wire.result"},
            graph_inputs={
                "payload": {
                    "dataKey": "source.payload",
                    "wire": "wire.payload",
                },
            },
            graph_outputs={
                "result": {
                    "dataKey": "signal.result",
                    "wire": "wire.result",
                },
            },
            initial_contracts={"source.payload": PAYLOAD_SCHEMA},
        )
        key = f"Signal/{definition['moduleId']}/{definition['version']}"
        runtime = runtime_from_compiled_plan(plan, {key: definition})
        try:
            runtime.execute({"source": {"payload": {"value": 1.0}}})
            snapshot = runtime.snapshot()
            runtime.execute({"source": {"payload": {"value": 2.0}}})

            runtime.restore(snapshot)
            restored = runtime.execute({"source": {"payload": {"value": 2.0}}})

            self.assertEqual(
                restored["outputs"],
                {"signal": {"result": {"value": 2.0}}},
            )
            self.assertEqual(runtime.snapshot()["worker"]["count"], 2)
        finally:
            runtime.close()

    def test_graph_invokes_module_with_an_unbound_optional_input_each_cycle(self):
        definition = self.archive_worker(
            "graph-optional-worker",
            optional_payload=True,
        )
        plan = self.compiled_plan(
            definition,
            outputs={"result": "wire.result"},
            graph_outputs={
                "result": {
                    "dataKey": "signal.result",
                    "wire": "wire.result",
                },
            },
        )
        key = f"Signal/{definition['moduleId']}/{definition['version']}"
        runtime = runtime_from_compiled_plan(plan, {key: definition})
        try:
            first = runtime.execute({})
            second = runtime.execute({})

            self.assertEqual(
                first["outputs"],
                {"signal": {"result": {"value": 0}}},
            )
            self.assertEqual(second["outputs"], first["outputs"])
            self.assertEqual(runtime.snapshot()["worker"]["count"], 2)
        finally:
            runtime.close()

    def test_lifecycle_cleanup_continues_and_graph_closes_reverse_topology(self):
        calls = []

        class Resource:
            def __init__(self, name, fails=False, base_fails=False):
                self.name = name
                self.fails = fails
                self.base_fails = base_fails

            def close(self):
                calls.append(self.name)
                if self.base_fails:
                    raise SystemExit(self.name)
                if self.fails:
                    raise RuntimeError(self.name)

        with self.assertRaisesRegex(RuntimeError, "first"):
            runtime_lifecycle.invoke_all(
                [Resource("first", True), Resource("second")], "close"
            )
        self.assertEqual(calls, ["first", "second"])

        calls.clear()
        runtime = ModuleGraphRuntime.__new__(ModuleGraphRuntime)
        runtime._closed = False
        runtime._plan = {"topology": ["first", "second"]}
        runtime._invokers = {
            "first": Resource("first"),
            "second": Resource("second", True),
        }
        with self.assertRaisesRegex(RuntimeError, "second"):
            runtime.close()
        self.assertEqual(calls, ["second", "first"])

        calls.clear()
        runtime = ModuleGraphRuntime.__new__(ModuleGraphRuntime)
        runtime._closed = False
        runtime._plan = {"topology": ["first", "second"]}
        runtime._invokers = {
            "first": Resource("first"),
            "second": Resource("second", base_fails=True),
        }
        with self.assertRaisesRegex(SystemExit, "second"):
            runtime.close()
        self.assertEqual(calls, ["second", "first"])
