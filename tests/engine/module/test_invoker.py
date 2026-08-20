#!/usr/bin/env python3

import math
from unittest import mock

from engine.authority import module_definition as module_definition_authority
from engine.authority import module_invocation as module_invocation_authority
from engine.contracts.data import isolate_json_value
from engine.runtime.module_invoker import ModuleInvoker
from tests.support.module_runtime import (
    ModuleRuntimeTestCase,
    module_invoker,
)

class ModuleInvokerTests(ModuleRuntimeTestCase):
    def test_runtime_value_isolation_preserves_json_ownership_and_aliases(self):
        shared = {"value": 1.0}
        source = {"left": shared, "right": shared, "items": [shared]}
        isolated = isolate_json_value(source)

        self.assertEqual(isolated, source)
        self.assertIsNot(isolated, source)
        self.assertIs(isolated["left"], isolated["right"])
        self.assertIs(isolated["left"], isolated["items"][0])
        isolated["left"]["value"] = 2.0
        self.assertEqual(source["left"]["value"], 1.0)

    def test_invocation_isolates_inputs_and_rejects_undeclared_outputs(self):
        definition = self.archive_worker("boundary-worker")
        source = {"payload": {"value": 1.0}}
        invoker = module_invoker(self.binding(definition, "mutate"), definition)
        try:
            self.assertEqual(invoker.invoke(source), {"result": {"value": 999}})
        finally:
            invoker.close()
        self.assertEqual(source, {"payload": {"value": 1.0}})

        invoker = module_invoker(self.binding(definition, "unknown-output"), definition)
        try:
            with self.assertRaisesRegex(ValueError, "undeclared output"):
                invoker.invoke(source)
        finally:
            invoker.close()

    def test_module_inputs_have_no_compiled_validation_bypass(self):
        definition = self.archive_worker("strict-module-input-worker")
        invoker = module_invoker(self.binding(definition), definition)
        try:
            self.assertFalse(hasattr(invoker, "invoke_compiled"))
            self.assertFalse(hasattr(ModuleInvoker, "from_compiled_authority"))
            self.assertFalse(
                hasattr(
                    module_invocation_authority,
                    "compile_module_inputs_authority",
                )
            )
            with self.assertRaisesRegex(ValueError, "payload.value"):
                invoker.invoke({"payload": {"value": "invalid"}})
            with self.assertRaisesRegex(TypeError, "validated input authority"):
                invoker.invoke_validated({"payload": {"value": "invalid"}})
            self.assertEqual(invoker.snapshot(), {"count": 0})
        finally:
            invoker.close()

    def test_validated_module_input_proof_is_bound_and_one_shot(self):
        from engine.runtime import module_invoker as module_invoker_runtime

        definition = self.archive_worker("one-shot-module-input-worker")
        first = module_invoker(
            self.binding(definition, instance_id="first"),
            definition,
        )
        second = module_invoker(
            self.binding(definition, instance_id="second"),
            definition,
        )
        proof = module_invoker_runtime.seal_runtime_validated_module_inputs(
            first._invocation_authority,
            {"payload": {"value": 3.0}},
        )
        try:
            with self.assertRaisesRegex(TypeError, "do not match"):
                second.invoke_validated(proof)
            self.assertEqual(first.invoke_validated(proof), {
                "result": {"value": 3.0},
            })
            with self.assertRaisesRegex(RuntimeError, "already been consumed"):
                first.invoke_validated(proof)
            self.assertEqual(first.snapshot(), {"count": 1})
            self.assertEqual(second.snapshot(), {"count": 0})
        finally:
            second.close()
            first.close()

    def test_validated_input_fast_clone_preserves_exact_values_and_splits_aliases(self):
        from engine.runtime import module_invoker as module_invoker_runtime

        shared = {"unicode": "\u4ea4\u6613", "negativeZero": -0.0}
        source = {
            "left": shared,
            "right": shared,
            "items": [shared],
        }
        isolated = module_invoker_runtime._isolate_validated_json_tree(source)

        self.assertEqual(isolated, source)
        self.assertIsNot(isolated, source)
        self.assertIsNot(isolated["left"], isolated["right"])
        self.assertIsNot(isolated["left"], isolated["items"][0])
        self.assertEqual(
            math.copysign(1.0, isolated["left"]["negativeZero"]),
            -1.0,
        )
        isolated["left"]["unicode"] = "changed"
        self.assertEqual(source["left"]["unicode"], "\u4ea4\u6613")

    def test_validated_input_fast_clone_falls_back_without_changing_big_integers(self):
        from engine.runtime import module_invoker as module_invoker_runtime

        source = {"payload": {"wide": 1 << 100}}
        isolated = module_invoker_runtime._isolate_validated_json_tree(source)
        self.assertEqual(isolated, source)
        self.assertIsNot(isolated["payload"], source["payload"])

        accelerator = mock.Mock()
        accelerator.dumps.side_effect = TypeError("unsupported")
        with mock.patch.object(module_invoker_runtime, "_orjson", accelerator):
            fallback = module_invoker_runtime._isolate_validated_json_tree(
                {"payload": [1, 2, 3]}
            )
        self.assertEqual(fallback, {"payload": [1, 2, 3]})
        accelerator.dumps.assert_called_once()

    def test_validated_python_module_cannot_mutate_retained_prior_input(self):
        from engine.runtime import module_invoker as module_invoker_runtime

        definition = self.archive_python_module(
            "retained-validated-input",
            source=r'''from strategy_devkit.module_sdk import SignalModule


class RetainedPayloadSignal(SignalModule):
    def on_initialize(self):
        self.retained = None

    def update(self, payload):
        if self.retained is not None:
            self.retained["value"] = 999.0
        value = payload["value"]
        payload["value"] = -1.0
        self.retained = payload
        return {"result": {"value": value}}


MODULE_CLASS = RetainedPayloadSignal
''',
        )
        invoker = module_invoker(self.binding(definition), definition)
        first = {"payload": {"value": 1.0}}
        second = {"payload": {"value": 2.0}}
        try:
            first_proof = module_invoker_runtime.seal_runtime_validated_module_inputs(
                invoker._invocation_authority,
                first,
            )
            self.assertEqual(
                invoker.invoke_validated(first_proof),
                {"result": {"value": 1.0}},
            )
            self.assertEqual(first, {"payload": {"value": 1.0}})

            second_proof = module_invoker_runtime.seal_runtime_validated_module_inputs(
                invoker._invocation_authority,
                second,
            )
            self.assertEqual(
                invoker.invoke_validated(second_proof),
                {"result": {"value": 2.0}},
            )
            self.assertEqual(first, {"payload": {"value": 1.0}})
            self.assertEqual(second, {"payload": {"value": 2.0}})
        finally:
            invoker.close()

    def test_unknown_activation_mode_is_rejected(self):
        definition = self.archive_worker("unknown-activation-mode")
        definition["activationMode"] = "ExternalRuntime"
        with self.assertRaisesRegex(ValueError, "invalid activationMode"):
            module_definition_authority.verify_module_definition_authority(
                definition
            )
