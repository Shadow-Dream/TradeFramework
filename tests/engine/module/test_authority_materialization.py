#!/usr/bin/env python3

import copy
import sys
from pathlib import Path
from unittest import mock

from engine.authority import module_definition as module_definition_authority
from engine.authority import graph as graph_authority
from engine.authority import module_invocation as module_invocation_authority
from engine.authority import module_material
from engine.runtime import module_implementation
from engine.runtime import process_module_adapter
from engine.runtime import python_module_adapter
from engine.contracts.module import MODULE_INSTANCE_FIELDS
from engine.authority.graph import compiled_graph_authority_plan
from engine.compiler.graph import compile_module_graph_authority
from engine.runtime.module_invoker import ModuleInvoker
from engine.runtime.graph import ModuleGraphRuntime
from engine.runtime.pipeline import BacktestPipelineRuntime
from engine.archive import version as version_archive
from tests.support.module_runtime import (
    ModuleRuntimeTestCase,
    PAYLOAD_SCHEMA,
    PYTHON_BUNDLE_TAMPER,
    module_invoker,
)

class ModuleAuthorityMaterializationTests(ModuleRuntimeTestCase):
    def test_verified_module_definition_authority_is_sealed_and_detached(self):
        definition = self.archive_python_module("sealed-definition-authority")
        authority = (
            module_definition_authority.verify_module_definition_authority(
                definition
            )
        )
        original = module_definition_authority.verified_module_definition_material(
            authority
        )
        with self.assertRaisesRegex(AttributeError, "immutable"):
            authority._definition_json = "{}"
        exposed = module_definition_authority.verified_module_definition_material(
            authority
        )
        exposed["ports"] = {}
        self.assertEqual(
            module_definition_authority.verified_module_definition_material(
                authority
            ),
            original,
        )

    def test_graph_authority_rejects_malformed_plan_before_runtime_resources(self):
        compiled = compile_module_graph_authority(
            {"nodes": [], "inputs": {}, "outputs": {}},
            {},
            {},
            {},
        )
        malformed = compiled_graph_authority_plan(compiled)
        del malformed["inputs"]
        with mock.patch(
            "engine.runtime.module_invoker.ModuleInvoker.from_authority"
        ) as create_resource:
            with self.assertRaisesRegex(ValueError, "missing required field.*inputs"):
                graph_authority.seal_compiled_graph_authority(
                    malformed,
                    {},
                )
        create_resource.assert_not_called()

    def test_adapter_factories_reject_raw_runtime_material(self):
        with self.assertRaisesRegex(TypeError, "Engine-owned"):
            python_module_adapter.create_python_module_adapter({})
        with self.assertRaisesRegex(TypeError, "Engine-owned"):
            process_module_adapter.create_process_module_adapter({})

    def test_module_materializer_checks_nominal_authority_before_cache_lookup(self):
        class ForgedAuthority:
            def __hash__(self):
                raise AssertionError("forged authority reached the cache")

        with self.assertRaisesRegex(TypeError, "Engine-owned"):
            module_implementation.materialize_verified_module_definition(
                ForgedAuthority(),
                Path(self.temp.name) / "forged-authority",
            )

    def test_pipeline_executable_authorities_cannot_be_replaced(self):
        runtime = BacktestPipelineRuntime.__new__(BacktestPipelineRuntime)
        for field, value in (
            ("module_invokers", {}),
            ("signal_runtime", object()),
        ):
            with self.subTest(field=field), self.assertRaises(AttributeError):
                setattr(runtime, field, value)

    def test_same_stack_compiled_authority_does_not_recompile_the_graph(self):
        definitions = {}
        authority = compile_module_graph_authority(
            {"nodes": [], "inputs": {}, "outputs": {}},
            {},
            definitions,
            {},
            label="Authority Graph",
        )
        definitions["tampered"] = {}
        detached = compiled_graph_authority_plan(authority)
        detached["nodes"].append("mutated")
        with mock.patch(
            "engine.contracts.graph.validate_compiled_graph",
            side_effect=AssertionError("same-stack authority was revalidated"),
        ):
            runtime = ModuleGraphRuntime.from_compiled_authority(authority)
        try:
            self.assertEqual(runtime.plan["nodes"], [])
            self.assertEqual(runtime.definitions, {})
            self.assertEqual(runtime.execute_outputs({}), {})
        finally:
            runtime.close()
        with self.assertRaisesRegex(TypeError, "compiled Graph authority"):
            ModuleGraphRuntime.from_compiled_authority(
                compiled_graph_authority_plan(authority)
            )

    def test_compiled_authority_owns_only_its_referenced_definitions(self):
        definition = self.archive_python_module("authority-definition-isolation")
        original_definition = copy.deepcopy(definition)
        key = f"Signal/{definition['moduleId']}/{definition['version']}"
        binding = {
            "instanceId": "worker",
            "kind": "Signal",
            "moduleId": definition["moduleId"],
            "version": definition["version"],
            "config": {},
            "inputs": {"payload": "wire.payload"},
            "outputs": {"result": "wire.result"},
        }
        graph = {
            "nodes": ["worker"],
            "inputs": {
                "payload-input": {
                    "dataKey": "source.payload",
                    "wire": "wire.payload",
                }
            },
            "outputs": {
                "result-output": {
                    "dataKey": "signal.result",
                    "wire": "wire.result",
                }
            },
        }
        strict_verify = version_archive.verify_record
        with mock.patch(
            "engine.archive.version.verify_record",
            wraps=strict_verify,
        ) as verify_record:
            authority = compile_module_graph_authority(
                graph,
                {"worker": binding},
                {key: definition, "Signal/unused/1": {"unreferenced": True}},
                {"source.payload": PAYLOAD_SCHEMA},
                allowed_kinds={"Signal"},
            )
            runtime = ModuleGraphRuntime.from_compiled_authority(authority)
        self.assertEqual(verify_record.call_count, 1)
        definition["ports"]["outputs"]["result"]["schema"] = {
            "type": "string"
        }
        try:
            self.assertEqual(runtime.definitions, {key: original_definition})
            exposed_definitions = runtime.definitions
            exposed_definitions[key]["description"] = "external mutation"
            self.assertEqual(runtime.definitions, {key: original_definition})
            exposed_plan = runtime.plan
            exposed_plan["topology"] = []
            self.assertEqual(runtime.plan["topology"], ["worker"])
            self.assertFalse(hasattr(runtime, "rebind_compiled_authority"))
        finally:
            runtime.close()

    def test_module_graph_runtime_has_no_raw_or_rebind_compatibility_path(self):
        from engine.runtime.graph_cycle import (
            AnalysisGraphRuntime,
            CycleGraphRuntime,
            EnvironmentGraphRuntime,
        )

        for runtime_type in (
            ModuleGraphRuntime,
            CycleGraphRuntime,
            EnvironmentGraphRuntime,
            AnalysisGraphRuntime,
            BacktestPipelineRuntime,
        ):
            with self.subTest(runtime=runtime_type.__name__), self.assertRaisesRegex(
                TypeError,
                "Engine-owned",
            ):
                runtime_type()
        with self.assertRaisesRegex(TypeError, "Engine-owned"):
            ModuleInvoker()
        self.assertFalse(hasattr(ModuleGraphRuntime, "rebind_compiled_authority"))
        self.assertFalse(
            hasattr(BacktestPipelineRuntime, "_from_bound_plan")
        )
        with self.assertRaisesRegex(TypeError, "compiled Pipeline authority"):
            BacktestPipelineRuntime.from_compiled_authority(object())

    def test_compiled_binding_contains_only_instance_execution_fields(self):
        definition = self.archive_worker("compiled-binding-worker")
        plan = self.compiled_plan(
            definition,
            inputs={"payload": "wire.payload"},
            outputs={"result": "wire.result"},
            graph_inputs={
                "payload": {"dataKey": "source.payload", "wire": "wire.payload"},
            },
            graph_outputs={
                "result": {"dataKey": "signal.result", "wire": "wire.result"},
            },
            initial_contracts={"source.payload": PAYLOAD_SCHEMA},
        )
        binding = plan["bindings"]["worker"]
        self.assertEqual(set(binding), set(MODULE_INSTANCE_FIELDS))
        self.assertFalse({"activationMode", "parameters", "ports"} & set(binding))

    def test_runtime_rejects_binding_ports_as_a_second_contract_authority(self):
        definition = self.archive_worker("binding-ports-worker")
        binding = self.binding(definition)
        binding["ports"] = definition["ports"]
        with self.assertRaisesRegex(ValueError, "unsupported field.*ports"):
            module_invoker(binding, definition)

    def test_verified_definition_materializes_once_for_multiple_instances(self):
        execution_root = Path(self.temp.name) / "verified-shared-execution"
        definition = self.archive_python_module("verified-shared-python-version")
        authority = module_definition_authority.verify_module_definition_authority(definition)
        fingerprinted = []
        original_fingerprint = module_material.directory_tree_fingerprint

        def record_fingerprint(root):
            fingerprinted.append(Path(root).resolve())
            return original_fingerprint(root)

        invokers = []
        with mock.patch(
            "engine.authority.module_material.directory_tree_fingerprint",
            side_effect=record_fingerprint,
        ):
            try:
                for suffix in ("first", "second"):
                    binding = self.binding(
                        definition,
                        instance_id=f"verified-shared-{suffix}",
                    )
                    invocation_authority = (
                        module_invocation_authority.bind_module_invocation_authority(
                            binding,
                            authority,
                        )
                    )
                    invokers.append(ModuleInvoker.from_authority(
                        invocation_authority,
                        execution_root=execution_root,
                    ))
                self.assertEqual(
                    [item.invoke({"payload": {"value": 3.0}}) for item in invokers],
                    [
                        {"result": {"value": 3.0}},
                        {"result": {"value": 3.0}},
                    ],
                )
            finally:
                for invoker in reversed(invokers):
                    invoker.close()

        source = Path(definition["archive"]["root"]).resolve()
        self.assertEqual(fingerprinted.count(source), 1)
        staging = [
            path for path in fingerprinted
            if path.name.startswith(".module-bundle-")
        ]
        self.assertEqual(len(staging), 1)
        self.assertEqual(len(fingerprinted), 3)

    def test_cached_module_bundle_is_reverified_before_another_instance_loads(self):
        execution_root = Path(self.temp.name) / "tamper-shared-execution"
        definition = self.archive_python_module(
            "tamper-shared-python-version",
            source=PYTHON_BUNDLE_TAMPER,
        )
        definition_authority = (
            module_definition_authority.verify_module_definition_authority(definition)
        )
        first_authority = (
            module_invocation_authority.bind_module_invocation_authority(
                self.binding(definition, instance_id="tamper-first"),
                definition_authority,
            )
        )
        first = ModuleInvoker.from_authority(
            first_authority,
            execution_root=execution_root,
        )
        try:
            self.assertEqual(
                first.invoke({"payload": {"value": 3.0}}),
                {"result": {"value": 1.0}},
            )
            second_authority = (
                module_invocation_authority.bind_module_invocation_authority(
                    self.binding(definition, instance_id="tamper-second"),
                    definition_authority,
                )
            )
            with self.assertRaisesRegex(
                ValueError,
                "execution bundle does not match",
            ):
                ModuleInvoker.from_authority(
                    second_authority,
                    execution_root=execution_root,
                )
        finally:
            first.close()

    def test_python_module_uses_common_base_and_declares_matching_kind(self):
        direct_base = r'''from strategy_devkit.module_sdk import Module

class InvalidSignal(Module):
    kind = "Signal"
    def update(self, payload):
        return {"result": payload}

MODULE_CLASS = InvalidSignal
'''
        forged_kind = r'''from strategy_devkit import EnvironmentModule

class InvalidSignal(EnvironmentModule):
    def update(self, payload):
        return {"result": payload}

MODULE_CLASS = InvalidSignal
'''
        definition = self.archive_python_module(
            "direct-base-signal",
            source=direct_base,
        )
        invoker = module_invoker(self.binding(definition), definition)
        try:
            self.assertEqual(
                invoker.invoke({"payload": {"value": 3}}),
                {"result": {"value": 3}},
            )
        finally:
            invoker.close()

        definition = self.archive_python_module(
            "forged-kind-signal",
            source=forged_kind,
        )
        with self.assertRaisesRegex(TypeError, "does not match Definition kind"):
            module_invoker(self.binding(definition), definition)

    def test_archived_python_module_cannot_replace_sdk_lifecycle_methods(self):
        definition_override = r'''from strategy_devkit.module_sdk import SignalModule

class InvalidLifecycle(SignalModule):
    def initialize(self, configuration):
        return {"status": "bypassed"}

    def update(self, payload):
        return {"result": payload}

MODULE_CLASS = InvalidLifecycle
'''
        export_monkeypatch = r'''from strategy_devkit.module_sdk import SignalModule

class PatchedLifecycle(SignalModule):
    def update(self, payload):
        return {"result": payload}

PatchedLifecycle.invoke = lambda self, inputs: {"result": {"value": 999}}
MODULE_CLASS = PatchedLifecycle
'''
        inherited_monkeypatch = r'''from strategy_devkit.module_sdk import SignalModule

class SharedBase(SignalModule):
    def update(self, payload):
        return {"result": payload}

class ExportedModule(SharedBase):
    pass

SharedBase.snapshot = lambda self: {"bypassed": True}
MODULE_CLASS = ExportedModule
'''
        for module_id, source, member in (
            ("definition-lifecycle-override", definition_override, "initialize"),
            ("export-lifecycle-monkeypatch", export_monkeypatch, "invoke"),
            ("inherited-lifecycle-monkeypatch", inherited_monkeypatch, "snapshot"),
        ):
            with self.subTest(module_id=module_id):
                definition = self.archive_python_module(module_id, source=source)
                loaded_before = {
                    name
                    for name in sys.modules
                    if name.startswith("_trade_archived_module_")
                }
                with self.assertRaisesRegex(
                    TypeError,
                    rf"Engine-owned member.*{member}",
                ):
                    module_invoker(self.binding(definition), definition)
                self.assertEqual(
                    {
                        name
                        for name in sys.modules
                        if name.startswith("_trade_archived_module_")
                    },
                    loaded_before,
                )
