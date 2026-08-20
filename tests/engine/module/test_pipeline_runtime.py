#!/usr/bin/env python3

from unittest import mock

from engine.authority import module_definition as module_definition_authority
from engine.authority import module_invocation as module_invocation_authority
from engine.authority import pipeline as pipeline_authority
from engine.contracts.module import MODULE_INSTANCE_FIELDS
from engine.compiler import pipeline as pipeline_compiler
from engine.runtime.pipeline import BacktestPipelineRuntime
from engine.service import control_api as control
from tests.support.module_runtime import ModuleRuntimeTestCase

class PipelineRuntimeIntegrationTests(ModuleRuntimeTestCase):
    def test_pipeline_initializes_and_finalizes_in_complete_execution_topology(self):
        events = []

        class FakeInvoker:
            def __init__(self, binding, definition, *_args, **_kwargs):
                self.name = binding["instanceId"]
                self.adapter = object()
                self.ports = definition["ports"]
                events.append(("initialize", self.name))

            @classmethod
            def from_authority(cls, authority, *_args, **_kwargs):
                binding, definition_authority = (
                    module_invocation_authority.module_invocation_material(authority)
                )
                return cls(
                    binding,
                    module_definition_authority.verified_module_definition_material(
                        definition_authority
                    ),
                )

            def finalize(self):
                events.append(("finalize", self.name))

            def close(self):
                events.append(("close", self.name))

        class FakeGraphRuntime:
            def __init__(self):
                self.invokers = {}
                events.append(("initialize", "signal-graph"))

            @classmethod
            def from_compiled_authority(cls, *_args, **_kwargs):
                return cls()

            def finalize(self):
                events.append(("finalize", "signal-graph"))

            def close(self):
                events.append(("close", "signal-graph"))

        def binding(key, kind, module_id):
            return {
                "key": key,
                "instanceId": key,
                "kind": kind,
                "moduleId": module_id,
                "version": "1",
                "config": {},
                "inputs": {},
                "outputs": {},
            }

        def definition(item):
            return {
                "kind": item["kind"],
                "moduleId": item["moduleId"],
                "name": item["moduleId"],
                "activationMode": "ProcessRunner",
                "parameters": {"command": "python3", "arguments": []},
                "configSchema": {"type": "object", "additionalProperties": False},
                "ports": {"inputs": {}, "outputs": {}},
                "description": "Pipeline lifecycle fixture.",
                "version": "1",
                "builtin": False,
                "archive": {
                    "resourceType": "module",
                    "resourceId": f"{item['kind']}/{item['moduleId']}",
                },
            }

        modules = [
            binding("universe", "Universe", "universe-module"),
            binding("signal", "Signal", "signal-module"),
            binding("target", "Target", "target-module"),
        ]
        definitions = {
            f"{item['kind']}/{item['moduleId']}/1": definition(item)
            for item in modules
        }
        manifest = {
            "name": "Lifecycle topology",
            "config": {
                "observationInput": {"whitelist": [], "blacklist": []}
            },
            "modules": [modules[0], modules[2], modules[1]],
            "topology": ["universe", "signal", "target"],
            "universe": ["universe"],
            "signalGraph": {"nodes": ["signal"], "inputs": {}, "outputs": {}},
            "target": ["target"],
            "constraint": [],
        }
        manifest_hash = control.json_digest(manifest)
        snapshot = {
            "pipelineId": "topology",
            "version": "1",
            "definition": {
                "pipelineId": "topology",
                "name": "Lifecycle topology",
                "config": {
                    "observationInput": {"whitelist": [], "blacklist": []}
                },
                "instances": {
                    item["instanceId"]: {
                        field: item[field] for field in MODULE_INSTANCE_FIELDS
                    }
                    for item in modules
                },
                "stages": {
                    "universe": ["universe"],
                    "target": ["target"],
                    "constraint": [],
                },
                "signalGraph": {"nodes": ["signal"], "inputs": {}, "outputs": {}},
                "version": "1",
                "manifestHash": manifest_hash,
            },
            "manifest": manifest,
            "manifestHash": manifest_hash,
            "moduleDefinitions": definitions,
        }
        with (
            mock.patch("engine.runtime.module_invoker.ModuleInvoker", FakeInvoker),
            mock.patch("engine.runtime.graph.ModuleGraphRuntime", FakeGraphRuntime),
            mock.patch("engine.archive.version.verify_record"),
        ):
            template = pipeline_compiler.compile_pipeline_contract_template(
                manifest,
                definitions,
            )
            bound_plan = pipeline_compiler.bind_pipeline_contract_plan(template, {})
            compiled_authority = pipeline_authority.bind_compiled_pipeline_authority(
                pipeline_authority.verify_pipeline_definition_authority(
                    snapshot["definition"]
                ),
                template,
                bound_plan,
            )
            with self.assertRaisesRegex(AttributeError, "immutable"):
                compiled_authority._compiled_plan_json = "{}"
            # A compiled Pipeline authority owns detached, immutable execution
            # facts; later mutation of the compiler's working objects cannot
            # reorder resources or alter its direct plan.
            pipeline_authority.bound_pipeline_contract_plan(bound_plan)[
                "directPlans"
            ][0]["phase"] = "post"
            pipeline_authority.pipeline_contract_template_material(template)[
                "directPlans"
            ][0]["phase"] = "post"
            runtime = BacktestPipelineRuntime.from_compiled_authority(
                compiled_authority
            )
            self.assertEqual(events, [
                ("initialize", "universe"),
                ("initialize", "signal-graph"),
                ("initialize", "target"),
            ])
            events.clear()
            runtime.finalize()
            self.assertEqual(events, [
                ("finalize", "target"),
                ("finalize", "signal-graph"),
                ("finalize", "universe"),
            ])
            with self.assertRaisesRegex(RuntimeError, "finalized"):
                runtime.execute_observation(None)
            with self.assertRaisesRegex(RuntimeError, "finalized"):
                runtime.finalize()
            events.clear()
            runtime.close()
            self.assertEqual(events, [
                ("close", "target"),
                ("close", "signal-graph"),
                ("close", "universe"),
            ])
            events.clear()
            runtime.close()
            self.assertEqual(events, [])
            with self.assertRaisesRegex(RuntimeError, "closed"):
                runtime.execute_observation(None)

        class SystemExitInvoker(FakeInvoker):
            def __init__(self, binding, definition, *_args, **_kwargs):
                super().__init__(binding, definition, *_args, **_kwargs)
                if self.name == "target":
                    raise SystemExit("target initialize")

        events.clear()
        with (
            mock.patch("engine.runtime.module_invoker.ModuleInvoker", SystemExitInvoker),
            mock.patch("engine.runtime.graph.ModuleGraphRuntime", FakeGraphRuntime),
            mock.patch("engine.archive.version.verify_record"),
        ):
            template = pipeline_compiler.compile_pipeline_contract_template(
                manifest,
                definitions,
            )
            bound_plan = pipeline_compiler.bind_pipeline_contract_plan(template, {})
            compiled_authority = pipeline_authority.bind_compiled_pipeline_authority(
                pipeline_authority.verify_pipeline_definition_authority(
                    snapshot["definition"]
                ),
                template,
                bound_plan,
            )
            with self.assertRaisesRegex(SystemExit, "target initialize"):
                BacktestPipelineRuntime.from_compiled_authority(
                    compiled_authority
                )
        self.assertEqual(events, [
            ("initialize", "universe"),
            ("initialize", "signal-graph"),
            ("initialize", "target"),
            ("close", "signal-graph"),
            ("close", "universe"),
        ])

    def test_pipeline_direct_slot_preserves_optional_input_semantics(self):
        class FakeInvoker:
            ports = {
                "inputs": {
                    "optional": {
                        "schema": {"type": "number"},
                        "required": False,
                    }
                },
                "outputs": {},
            }

        runtime = BacktestPipelineRuntime.__new__(BacktestPipelineRuntime)
        runtime._missing = object()
        runtime._bindings = {
            "input": {
                "inputs": {"optional": "source.optional"},
                "outputs": {},
            }
        }
        runtime._module_invokers = {"input": FakeInvoker()}
        runtime._pre_node_ids = ["input"]
        runtime._post_node_ids = []
        runtime._direct_plans = [{
            "nodeId": "input",
            "inputs": [{
                "port": "optional",
                "dataKey": "source.optional",
                "required": False,
            }],
            "outputs": [],
        }]
        plan = runtime._bind_node_execution_plan(["input"])
        self.assertFalse(plan[0][2][0][3])
        self.assertEqual(
            runtime._read_direct_module_inputs("input", {}, plan[0][2]),
            {},
        )

        runtime._direct_plans[0]["inputs"][0]["required"] = True
        required_plan = runtime._bind_node_execution_plan(["input"])
        with self.assertRaisesRegex(ValueError, "source.optional"):
            runtime._read_direct_module_inputs("input", {}, required_plan[0][2])
