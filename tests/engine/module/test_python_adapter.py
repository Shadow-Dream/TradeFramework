#!/usr/bin/env python3

from copy import deepcopy
import sys
from pathlib import Path
from threading import Thread
from unittest import mock

from builtin_implementations.pipeline.bollinger_bands_indicator import BollingerBandsIndicator
from builtin_implementations.pipeline.sma_indicator import SmaIndicator
from engine.authority import module_definition as module_definition_authority
from engine.runtime import module_implementation
from engine.runtime import python_module_adapter
from engine.runtime.module_invoker import ModuleInvoker
from strategy_devkit.module_sdk import Module, SignalModule, handle_module_command
from tests.support.module_runtime import (
    ModuleRuntimeTestCase,
    PYTHON_INITIALIZE_AND_CLOSE_FAILURE,
    module_invoker,
)


REUSABLE_OUTPUT_MODULE = r'''from copy import deepcopy

from strategy_devkit.module_sdk import SignalModule


class ReusableOutputModule(SignalModule):
    def on_initialize(self):
        self.state = {
            "count": 0,
            "cachedInput": None,
            "cachedOutput": None,
        }
        self._reusable_handle = None

    def on_snapshot(self):
        return deepcopy(self.state)

    def on_restore(self, snapshot):
        self.state = deepcopy(snapshot)
        self._reusable_handle = None

    def update(self, payload):
        self.state["count"] += 1
        value = payload["value"]
        if self.state["cachedInput"] != value:
            output_value = "invalid" if value == -1 else value
            output = {"result": {"value": output_value}}
            self.state["cachedInput"] = value
            self.state["cachedOutput"] = output
            self._reusable_handle = None
        if self.reusable_output_registration_available():
            if self._reusable_handle is None:
                self._reusable_handle = self.register_reusable_outputs(
                    self.state["cachedOutput"],
                    slot="result-cache",
                )
            return self._reusable_handle
        return deepcopy(self.state["cachedOutput"])


MODULE_CLASS = ReusableOutputModule
'''

class PythonModuleAdapterTests(ModuleRuntimeTestCase):
    def test_python_generic_update_supports_every_public_port_name(self):
        source = r'''from strategy_devkit.module_sdk import Module

class GenericPortModule(Module):
    kind = "Signal"

    def update(self, /, **inputs):
        return {
            "result-value": {
                "value": sum(inputs[name]["value"] for name in inputs),
            },
        }

MODULE_CLASS = GenericPortModule
'''
        value_schema = {
            "type": "object",
            "properties": {"value": {"type": "number"}},
            "required": ["value"],
            "additionalProperties": False,
        }
        definition = self.archive_python_module(
            "generic-port-names",
            source=source,
            ports={
                "inputs": {
                    name: {"schema": value_schema}
                    for name in ("price-close", "class", "self")
                },
                "outputs": {"result-value": {"schema": value_schema}},
            },
        )
        invoker = module_invoker(self.binding(definition), definition)
        try:
            self.assertEqual(
                invoker.invoke({
                    "price-close": {"value": 1},
                    "class": {"value": 2},
                    "self": {"value": 3},
                }),
                {"result-value": {"value": 6}},
            )
        finally:
            invoker.close()

    def test_initialize_hook_cannot_change_engine_owned_runtime_material(self):
        cases = {
            "identity": ("self.module_id = 'forged-module'", "identity"),
            "config": ("self.config['forged'] = True", "config"),
            "inputs": ("self._input_ports['payload']['required'] = False", "ports"),
            "outputs": ("self._output_ports['result']['required'] = False", "ports"),
            "archive": ("self.archive['root'] = '/tmp/forged-archive'", "archive"),
            "configuration": ("self.configuration['version'] = '999'", "version"),
        }
        source_template = r'''from strategy_devkit.module_sdk import SignalModule

class MutatingModule(SignalModule):
    def on_initialize(self):
        MUTATION

    def update(self, payload):
        return {"result": payload}

MODULE_CLASS = MutatingModule
'''
        for field, (mutation, diagnostic) in cases.items():
            with self.subTest(field=field):
                definition = self.archive_python_module(
                    f"initialize-mutates-{field}",
                    source=source_template.replace("MUTATION", mutation),
                )
                with self.assertRaisesRegex(
                    ValueError,
                    rf"Engine-owned initialization field.*{diagnostic}",
                ):
                    module_invoker(self.binding(definition), definition)

    def test_python_initialize_version_key_is_checked_against_authority(self):
        definition = self.archive_python_module("initialize-version-key")
        checker = python_module_adapter.require_initialized_module_configuration

        def check_forged_version_key(authority, actual_configuration, **actual):
            actual["actual_version_key"] = "Signal/forged/999"
            return checker(authority, actual_configuration, **actual)

        with (
            mock.patch.object(
                python_module_adapter,
                "require_initialized_module_configuration",
                new=check_forged_version_key,
            ),
            self.assertRaisesRegex(
                ValueError,
                "Engine-owned initialization field.*versionKey",
            ),
        ):
            module_invoker(self.binding(definition), definition)

    def test_reusable_output_proof_revalidates_misses_and_isolates_every_hit(self):
        definition = self.archive_python_module(
            "reusable-output-python",
            source=REUSABLE_OUTPUT_MODULE,
        )
        invoker = module_invoker(self.binding(definition), definition)
        validator_calls = []
        original_isolator = invoker._port_isolators["result"]

        def counted_isolator(value):
            validator_calls.append(value)
            return original_isolator(value)

        invoker._port_isolators["result"] = counted_isolator
        try:
            first = invoker.invoke({"payload": {"value": 1}})
            snapshot = invoker.snapshot()
            self.assertEqual(len(validator_calls), 1)

            # Neither a caller-owned Result nor the Module-retained candidate
            # may mutate the Engine-owned registered snapshot.
            first["result"]["value"] = 99
            invoker._adapter.module.state["cachedOutput"]["result"]["value"] = 777
            second = invoker.invoke({"payload": {"value": 1}})
            self.assertEqual(second, {"result": {"value": 1}})
            self.assertIsNot(second["result"], first["result"])
            self.assertEqual(len(validator_calls), 1)

            self.assertEqual(
                invoker.invoke({"payload": {"value": 2}}),
                {"result": {"value": 2}},
            )
            self.assertEqual(len(validator_calls), 2)
            with self.assertRaisesRegex(ValueError, "outputs.result.value"):
                invoker.invoke({"payload": {"value": -1}})
            self.assertEqual(len(validator_calls), 3)

            # Restore invalidates both proof generations.  The restored value
            # is therefore validated once before it can be reused again.
            invoker.restore(snapshot)
            restored = invoker.invoke({"payload": {"value": 1}})
            self.assertEqual(restored, {"result": {"value": 1}})
            self.assertEqual(len(validator_calls), 4)

            # orjson cannot encode arbitrary precision integers; the strict
            # snapshot codec remains an exact portable fallback.
            large = 1 << 80
            self.assertEqual(
                invoker.invoke({"payload": {"value": large}}),
                {"result": {"value": large}},
            )
            large_hit = invoker.invoke({"payload": {"value": large}})
            self.assertEqual(large_hit, {"result": {"value": large}})
            self.assertEqual(len(validator_calls), 5)
            self.assertEqual(invoker.transport_metrics()["invocationCount"], 7)
        finally:
            invoker.close()

    def test_reusable_output_receipts_are_adapter_bound_and_generation_scoped(self):
        definition = self.archive_python_module(
            "reusable-output-authority",
            source=REUSABLE_OUTPUT_MODULE,
        )
        first = module_invoker(
            self.binding(definition, instance_id="reusable-first"),
            definition,
        )
        second = module_invoker(
            self.binding(definition, instance_id="reusable-second"),
            definition,
        )
        try:
            first.invoke({"payload": {"value": 1}})
            second.invoke({"payload": {"value": 1}})
            old_receipt = first._adapter.invoke({"payload": {"value": 1}})
            self.assertEqual(
                first._reusable_output_owner.resolve(old_receipt),
                {"result": {"value": 1}},
            )
            with self.assertRaisesRegex(TypeError, "another adapter"):
                second._reusable_output_owner.material(old_receipt)

            first.invoke({"payload": {"value": 2}})
            with self.assertRaisesRegex(RuntimeError, "stale"):
                first._reusable_output_owner.resolve(old_receipt)
            active_receipt = first._adapter.invoke({"payload": {"value": 2}})
            first.finalize()
            with self.assertRaisesRegex(RuntimeError, "stale"):
                first._reusable_output_owner.resolve(active_receipt)
        finally:
            second.close()
            first.close()

    def test_sdk_command_path_materializes_reusable_candidates_as_plain_outputs(self):
        class DirectReusableModule(SignalModule):
            def on_initialize(self):
                self._handle = None
                self._cached_output = None

            def update(self, payload):
                if self._cached_output is None:
                    self._cached_output = {
                        "result": {"value": payload["value"]},
                    }
                if self.reusable_output_registration_available():
                    if self._handle is None:
                        self._handle = self.register_reusable_outputs(
                            self._cached_output,
                        )
                    return self._handle
                return deepcopy(self._cached_output)

        implementation = DirectReusableModule()
        implementation.initialize({
            "key": "direct.reusable",
            "kind": "Signal",
            "moduleId": "direct-reusable",
            "version": "1",
            "archive": {
                "status": "archived",
                "contentDigest": "sha256:" + "0" * 64,
            },
            "config": {},
            "inputs": {"payload": {"schema": {}, "required": True}},
            "outputs": {"result": {"schema": {}, "required": True}},
        })
        try:
            direct = implementation.update({"value": 3})
            direct["result"]["value"] = 99
            self.assertEqual(
                implementation.update({"value": 3}),
                {"result": {"value": 3}},
            )
            self.assertFalse(
                implementation.reusable_output_registration_available()
            )
            with self.assertRaisesRegex(RuntimeError, "active Engine invocation"):
                implementation.register_reusable_outputs({"result": {"value": 3}})
            for _index in range(2):
                response = handle_module_command(
                    implementation,
                    "invoke",
                    {"inputs": {"payload": {"value": 3}}},
                )
                self.assertEqual(response, {
                    "outputs": {"result": {"value": 3}},
                })
                self.assertIs(type(response["outputs"]), dict)
            self.assertIsNone(implementation._handle)
        finally:
            implementation.close()

    def test_engine_reusable_output_scope_is_exception_and_thread_local(self):
        class ScopeModule(SignalModule):
            def on_initialize(self):
                self.scope_seen = []
                self.thread_scope_seen = []
                self.nested_module = None
                self.scope_after_nested = []

            def update(self, payload):
                self.scope_seen.append(
                    self.reusable_output_registration_available()
                )
                thread = Thread(target=lambda: self.thread_scope_seen.append(
                    self.reusable_output_registration_available()
                ))
                thread.start()
                thread.join()
                if self.nested_module is not None and payload["value"] == 1:
                    Module._invoke_for_engine(
                        self.nested_module,
                        {"payload": {"value": 2}},
                    )
                    self.scope_after_nested.append(
                        self.reusable_output_registration_available()
                    )
                if payload["value"] < 0:
                    raise RuntimeError("EXPECTED_SCOPE_FAILURE")
                return {"result": {"value": payload["value"]}}

        implementation = ScopeModule()
        nested = ScopeModule()
        for module, key in (
            (implementation, "scope.reusable"),
            (nested, "scope.reusable.nested"),
        ):
            module.initialize({
                "key": key,
                "kind": "Signal",
                "moduleId": "scope-reusable",
                "version": "1",
                "archive": {
                    "status": "archived",
                    "contentDigest": "sha256:" + "0" * 64,
                },
                "config": {},
                "inputs": {"payload": {"schema": {}, "required": True}},
                "outputs": {"result": {"schema": {}, "required": True}},
            })
        implementation.nested_module = nested
        try:
            self.assertEqual(
                Module._invoke_for_engine(
                    implementation,
                    {"payload": {"value": 1}},
                ),
                {"result": {"value": 1}},
            )
            self.assertFalse(
                implementation.reusable_output_registration_available()
            )
            with self.assertRaisesRegex(RuntimeError, "EXPECTED_SCOPE_FAILURE"):
                Module._invoke_for_engine(
                    implementation,
                    {"payload": {"value": -1}},
                )
            self.assertFalse(
                implementation.reusable_output_registration_available()
            )
            self.assertEqual(implementation.scope_seen, [True, True])
            self.assertEqual(implementation.thread_scope_seen, [False, False])
            self.assertEqual(implementation.scope_after_nested, [True])
            self.assertEqual(nested.scope_seen, [True])
            self.assertEqual(nested.thread_scope_seen, [False])
        finally:
            nested.close()
            implementation.close()

    def test_python_module_invokes_directly_and_preserves_value_ownership(self):
        definition = self.archive_python_module("direct-python")
        source = {"payload": {"value": 1.0}}
        invoker = module_invoker(self.binding(definition), definition)
        try:
            for field, value in (
                ("binding", {}),
                ("definition", {}),
                ("ports", {}),
                ("adapter", object()),
            ):
                with self.subTest(field=field), self.assertRaises(AttributeError):
                    setattr(invoker, field, value)
            self.assertFalse(hasattr(invoker, "invoke_graph"))
            first = invoker.invoke(source)
            first["result"]["value"] = 99.0
            second = invoker.invoke(source)
            self.assertEqual(second, {"result": {"value": 1.0}})
            self.assertEqual(source, {"payload": {"value": 1.0}})
            self.assertEqual(invoker.snapshot(), {"count": 2})
            metrics = invoker.transport_metrics()
            self.assertEqual(metrics["runtimeMode"], "in-process-python")
            self.assertEqual(metrics["invocationCount"], 2)
            self.assertGreaterEqual(metrics["inputCopySeconds"], 0.0)
            self.assertGreaterEqual(metrics["sdkInputValidationSeconds"], 0.0)
            self.assertGreaterEqual(metrics["moduleComputeSeconds"], 0.0)
            self.assertGreaterEqual(metrics["sdkOutputValidationSeconds"], 0.0)
            self.assertGreaterEqual(metrics["outputValidationSeconds"], 0.0)
            self.assertNotIn("requestBytes", metrics)
        finally:
            invoker.close()

    def test_same_archived_version_supports_multiple_isolated_instances(self):
        execution_root = Path(self.temp.name) / "shared-execution"
        python_definition = self.archive_python_module("shared-python-version")
        process_definition = self.archive_worker("shared-process-version")
        invokers = []
        try:
            for definition in (python_definition, process_definition):
                for suffix in ("first", "second"):
                    invoker = module_invoker(
                        self.binding(
                            definition,
                            instance_id=f"{definition['moduleId']}-{suffix}",
                        ),
                        definition,
                        execution_root=execution_root,
                    )
                    invokers.append(invoker)
                    self.assertEqual(
                        invoker.invoke({"payload": {"value": 3.0}}),
                        {"result": {"value": 3.0}},
                    )
            bundles = [path for path in (execution_root / "modules").iterdir() if path.is_dir()]
            self.assertEqual(len(bundles), 2)
            self.assertTrue(all(not (path.stat().st_mode & 0o222) for path in bundles))
        finally:
            for invoker in reversed(invokers):
                invoker.close()

    def test_python_loader_removes_package_when_spec_creation_fails(self):
        definition = self.archive_python_module("spec-creation-failure")
        prefix = "_trade_archived_module_"
        before = {name for name in sys.modules if name.startswith(prefix)}
        with (
            mock.patch.object(
                python_module_adapter.importlib.util,
                "spec_from_file_location",
                return_value=None,
            ),
            self.assertRaisesRegex(ValueError, "cannot be loaded"),
        ):
            module_invoker(self.binding(definition), definition)
        after = {name for name in sys.modules if name.startswith(prefix)}
        self.assertEqual(after, before)

    def test_python_initialize_error_remains_authoritative_when_close_fails(self):
        definition = self.archive_python_module(
            "initialize-and-close-failure",
            source=PYTHON_INITIALIZE_AND_CLOSE_FAILURE,
        )
        with self.assertRaisesRegex(RuntimeError, "PRIMARY_INIT"):
            module_invoker(self.binding(definition), definition)

    def test_module_execution_namespace_is_confined_without_forbidding_subpaths(self):
        definition = self.archive_python_module("namespace-boundary")
        authority = module_definition_authority.verify_module_definition_authority(
            definition
        )
        execution_root = Path(self.temp.name) / "execution-root"
        for namespace in ("../outside", "/tmp/outside"):
            with self.subTest(namespace=namespace), self.assertRaisesRegex(
                ValueError,
                "stay within",
            ):
                module_implementation.materialize_verified_module_definition(
                    authority,
                    execution_root,
                    namespace,
                )
        material = module_implementation.materialize_verified_module_definition(
            authority,
            execution_root,
            "nested/path",
        )
        isolated = module_implementation.materialized_module_definition_material(
            material,
            authority,
        )
        self.assertTrue(
            Path(isolated["archive"]["root"]).is_relative_to(
                execution_root.resolve() / "nested" / "path"
            )
        )

        symlink = execution_root / "linked"
        symlink.parent.mkdir(parents=True, exist_ok=True)
        symlink.symlink_to(Path(self.temp.name).parent, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "symbolic link"):
            module_implementation.materialize_verified_module_definition(
                authority,
                execution_root,
                "linked/path",
            )

    def test_module_close_preserves_adapter_error_and_still_cleans_owned_root(self):
        calls = []

        class Adapter:
            def close(self):
                calls.append("adapter")
                raise RuntimeError("PRIMARY_CLOSE")

        class OwnedRoot:
            def cleanup(self):
                calls.append("root")
                raise ValueError("SECONDARY_ROOT_CLEANUP")

        invoker = ModuleInvoker.__new__(ModuleInvoker)
        invoker._lifecycle_state = "running"
        invoker._adapter = Adapter()
        invoker._owned_execution_root = OwnedRoot()
        invoker._label = "cleanup-priority"
        with self.assertRaisesRegex(RuntimeError, "PRIMARY_CLOSE"):
            invoker.close()
        self.assertEqual(calls, ["adapter", "root"])
        self.assertEqual(invoker._lifecycle_state, "closed")
        self.assertIsNone(invoker._owned_execution_root)

    def test_complete_lifecycle_uses_one_module_protocol(self):
        definition = self.archive_worker("lifecycle-worker")
        invoker = module_invoker(self.binding(definition), definition)
        try:
            self.assertEqual(invoker.invoke({"payload": {"value": 1.0}}), {"result": {"value": 1.0}})
            snapshot = invoker.snapshot()
            invoker.invoke({"payload": {"value": 2.0}})
            invoker.restore(snapshot)
            self.assertEqual(invoker.finalize(), {"count": 1})
        finally:
            invoker.close()

    def test_lifecycle_state_and_restore_json_are_identical_for_both_activation_modes(self):
        definitions = (
            self.archive_worker("process-lifecycle-state"),
            self.archive_python_module("python-lifecycle-state"),
        )
        for definition in definitions:
            with self.subTest(mode=definition["activationMode"]):
                invoker = module_invoker(self.binding(definition), definition)
                try:
                    with self.assertRaisesRegex(ValueError, "finite JSON"):
                        invoker.restore({"bad": object()})
                    invoker.finalize()
                    for operation in (
                        lambda: invoker.invoke({"payload": {"value": 1.0}}),
                        invoker.snapshot,
                        lambda: invoker.restore({}),
                        invoker.finalize,
                    ):
                        with self.assertRaisesRegex(RuntimeError, "finalized"):
                            operation()
                    self.assertEqual(invoker.close(), {"status": "closed"})
                    self.assertEqual(invoker.close(), {"status": "closed"})
                    with self.assertRaisesRegex(RuntimeError, "closed"):
                        invoker.invoke({"payload": {"value": 1.0}})
                finally:
                    invoker.close()

    def test_builtin_implementation_uses_the_common_module_base(self):
        implementation = SmaIndicator()
        self.assertIsInstance(implementation, Module)
        implementation.initialize({
            "key": "sma.instance",
            "kind": "Signal",
            "moduleId": "sma-indicator",
            "version": "1",
            "archive": {"status": "archived", "contentDigest": "sha256:" + "0" * 64},
            "config": {"period": 2},
            "inputs": {"value": {"schema": {}, "required": True}},
            "outputs": {"sma": {"schema": {}, "required": True}},
        })
        try:
            self.assertEqual(implementation.version_key, "Signal/sma-indicator/1")
        finally:
            implementation.close()

    def test_bollinger_bands_preserves_zero_multiplier(self):
        implementation = BollingerBandsIndicator()
        implementation.initialize({
            "key": "bollinger.zero",
            "kind": "Signal",
            "moduleId": "bollinger-bands-indicator",
            "version": "1",
            "archive": {"status": "archived", "contentDigest": "sha256:" + "0" * 64},
            "config": {"period": 2, "k": 0},
            "inputs": {"price": {"schema": {"type": "number"}, "required": True}},
            "outputs": {
                name: {"schema": {"type": ["number", "null"]}, "required": name == "middle"}
                for name in ("middle", "upper", "lower", "bandwidth", "percentB")
            },
        })
        try:
            implementation.invoke({"price": 10})
            result = implementation.invoke({"price": 20})
            self.assertEqual(result["middle"], 15)
            self.assertEqual(result["upper"], 15)
            self.assertEqual(result["lower"], 15)
        finally:
            implementation.close()
