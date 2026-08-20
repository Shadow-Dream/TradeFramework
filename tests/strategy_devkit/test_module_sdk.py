import io
import json
import unittest
from unittest import mock

from strategy_devkit import module_sdk
from strategy_devkit.module_sdk import (
    Module,
    SignalModule,
    handle_module_command,
    serve_module,
)
from strategy_devkit.bundle import SDK_BUNDLE_FILES, sdk_bundle_files


def configuration(inputs, outputs, config=None):
    return {
        "key": "signal.test",
        "kind": "Signal",
        "moduleId": "test-signal",
        "version": "1",
        "archive": {"status": "archived", "contentDigest": "sha256:" + "0" * 64},
        "config": config or {},
        "inputs": inputs,
        "outputs": outputs,
    }


class AddModule(SignalModule):
    def update(self, left, right=None):
        return {"total": left + (right or 0) + self.config.get("offset", 0)}


class PayloadModule(SignalModule):
    def update(self, /, **inputs):
        return {"result": inputs}


class PythonModuleSdkTests(unittest.TestCase):
    def test_concrete_module_must_implement_update(self):
        class MissingUpdate(SignalModule):
            pass

        module = MissingUpdate()
        with self.assertRaisesRegex(ValueError, "must implement update"):
            module.initialize({
                "key": "missing-update",
                "kind": "Signal",
                "moduleId": "missing-update",
                "version": "1",
                "config": {},
                "inputs": {},
                "outputs": {},
                "archive": {
                    "status": "archived",
                    "contentDigest": "sha256:" + "0" * 64,
                },
            })

    def setUp(self):
        self.module = AddModule()
        handle_module_command(self.module, "initialize", {
            "configuration": configuration(
                {
                    "left": {"schema": {}, "required": True},
                    "right": {"schema": {}, "required": False},
                },
                {"total": {"schema": {}, "required": True}},
                {"offset": 2},
            )
        })

    def test_named_inputs_are_forwarded_and_outputs_are_wrapped(self):
        result = handle_module_command(self.module, "invoke", {
            "inputs": {"left": 3, "right": 4}
        })
        self.assertEqual(result, {"outputs": {"total": 9}})

    def test_worker_bundle_contains_every_repository_facade(self):
        self.assertEqual(
            [item["path"] for item in sdk_bundle_files()],
            [f"strategy_devkit/{name}" for name in SDK_BUNDLE_FILES],
        )

    def test_optional_input_may_be_absent(self):
        result = handle_module_command(self.module, "invoke", {"inputs": {"left": 3}})
        self.assertEqual(result, {"outputs": {"total": 5}})

    def test_validated_engine_inputs_are_bound_one_shot_and_skip_only_duplicates(self):
        inputs = {"left": 3}
        with mock.patch.object(
            module_sdk,
            "sorted",
            wraps=sorted,
            create=True,
        ) as sorting:
            self.assertEqual(self.module.invoke(inputs), {"total": 5})
        direct_sort_calls = sorting.call_count

        issuer = object()
        with self.assertRaisesRegex(TypeError, "bound adapter issuer"):
            Module._issue_validated_engine_inputs(self.module, inputs)
        with self.assertRaisesRegex(TypeError, "bound adapter issuer"):
            Module._issue_validated_engine_inputs(
                self.module,
                inputs,
                _issuer=object(),
            )
        Module._bind_validated_input_issuer(self.module, issuer)
        with self.assertRaisesRegex(RuntimeError, "already bound"):
            Module._bind_validated_input_issuer(self.module, object())

        other = AddModule()
        other.initialize(dict(self.module.configuration))
        wrong_module = Module._issue_validated_engine_inputs(
            self.module,
            inputs,
            _issuer=issuer,
        )
        try:
            with self.assertRaisesRegex(TypeError, "do not match"):
                Module._invoke_for_engine(
                    other,
                    inputs,
                    _validated_inputs=wrong_module,
                )
        finally:
            other.close()

        authority = Module._issue_validated_engine_inputs(
            self.module,
            inputs,
            _issuer=issuer,
        )
        with mock.patch.object(
            module_sdk,
            "sorted",
            wraps=sorted,
            create=True,
        ) as sorting:
            self.assertEqual(
                Module._invoke_for_engine(
                    self.module,
                    inputs,
                    _validated_inputs=authority,
                ),
                {"total": 5},
            )
        self.assertEqual(direct_sort_calls - sorting.call_count, 2)
        with self.assertRaisesRegex(RuntimeError, "already been consumed"):
            Module._invoke_for_engine(
                self.module,
                inputs,
                _validated_inputs=authority,
            )

        mismatched = Module._issue_validated_engine_inputs(
            self.module,
            inputs,
            _issuer=issuer,
        )
        with self.assertRaisesRegex(TypeError, "do not match"):
            Module._invoke_for_engine(
                self.module,
                dict(inputs),
                _validated_inputs=mismatched,
            )
        with self.assertRaisesRegex(RuntimeError, "already been consumed"):
            Module._invoke_for_engine(
                self.module,
                inputs,
                _validated_inputs=mismatched,
            )
        with self.assertRaisesRegex(TypeError, "requires validated Engine inputs"):
            Module._invoke_for_engine(
                self.module,
                inputs,
                _validated_inputs=object(),
            )

        # Public and bare private calls never receive the nominal authority.
        for invoke in (
            self.module.invoke,
            lambda value: Module._invoke_for_engine(self.module, value),
        ):
            with self.subTest(invoke=invoke):
                with self.assertRaisesRegex(ValueError, "undeclared input"):
                    invoke({"left": 3, "hidden": 10})
                with self.assertRaisesRegex(ValueError, "missing required input"):
                    invoke({})

    def test_transport_context_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "transportContext"):
            handle_module_command(self.module, "invoke", {
                "inputs": {"left": 3},
                "transportContext": {"left": 99},
            })

    def test_generic_mapping_captures_every_public_port_name(self):
        module = PayloadModule()
        handle_module_command(module, "initialize", {
            "configuration": configuration(
                {
                    name: {"schema": {}, "required": True}
                    for name in ("price-close", "class", "self")
                },
                {"result": {"schema": {}, "required": True}},
            )
        })
        try:
            values = {"price-close": 10, "class": 20, "self": 30}
            self.assertEqual(
                handle_module_command(module, "invoke", {"inputs": values}),
                {"outputs": {"result": values}},
            )
        finally:
            module.close()

    def test_generic_mapping_receiver_must_be_positional_only(self):
        class AmbiguousReceiver(SignalModule):
            def update(self, **inputs):
                return {"result": inputs}

        with self.assertRaisesRegex(ValueError, "receiver positional-only"):
            handle_module_command(AmbiguousReceiver(), "initialize", {
                "configuration": configuration(
                    {"self": {"schema": {}, "required": True}},
                    {"result": {"schema": {}, "required": True}},
                )
            })

    def test_generic_mapping_cannot_be_mixed_with_named_parameters(self):
        class MixedInputs(SignalModule):
            def update(self, value, **inputs):
                return {"result": {"value": value, **inputs}}

        with self.assertRaisesRegex(ValueError, "invalid parameter.*inputs"):
            handle_module_command(MixedInputs(), "initialize", {
                "configuration": configuration(
                    {"value": {"schema": {}, "required": True}},
                    {"result": {"schema": {}, "required": True}},
                )
            })

    def test_optional_port_requires_a_python_default(self):
        class MissingDefault(SignalModule):
            def update(self, value):
                return {"result": value}

        with self.assertRaisesRegex(ValueError, "Python defaults"):
            handle_module_command(MissingDefault(), "initialize", {
                "configuration": configuration(
                    {"value": {"schema": {}, "required": False}},
                    {"result": {"schema": {}, "required": True}},
                )
            })

    def test_engine_owned_module_lifecycle_members_cannot_be_overridden(self):
        engine_owned = (
            "__init__",
            "__new__",
            "__getattribute__",
            "__setattr__",
            "__delattr__",
            "initialize",
            "_validate_implementation",
            "_compute",
            "invoke",
            "finalize",
            "snapshot",
            "restore",
            "close",
            "_require_active",
            "_require_running",
        )
        for name in engine_owned:
            with self.subTest(name=name):
                with self.assertRaisesRegex(TypeError, "Engine-owned member"):
                    type(
                        f"Invalid{name}",
                        (SignalModule,),
                        {name: lambda self, *args, **kwargs: None},
                    )

        with self.assertRaisesRegex(TypeError, "Engine-owned member.*_initialized"):
            class InvalidLifecycleState(SignalModule):
                _initialized = True

                def update(self, value):
                    return {"result": value}

        class InvokeMixin:
            def invoke(self, inputs):
                return {"bypass": inputs}

        with self.assertRaisesRegex(TypeError, "Engine-owned member.*invoke"):
            class InvalidMixinModule(InvokeMixin, SignalModule):
                def update(self, value):
                    return {"result": value}

    def test_export_time_monkeypatch_is_rejected_by_command_dispatch(self):
        class PatchedModule(AddModule):
            pass

        PatchedModule.invoke = lambda self, inputs: {"bypass": inputs}
        with self.assertRaisesRegex(TypeError, "Engine-owned member.*invoke"):
            handle_module_command(PatchedModule(), "initialize", {
                "configuration": configuration(
                    {
                        "left": {"schema": {}, "required": True},
                        "right": {"schema": {}, "required": False},
                    },
                    {"total": {"schema": {}, "required": True}},
                )
            })

    def test_undeclared_input_and_output_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "undeclared input"):
            handle_module_command(self.module, "invoke", {
                "inputs": {"left": 3, "hidden": 10}
            })

        class BadOutput(SignalModule):
            def update(self, value):
                return {"wrong": value}

        module = BadOutput()
        handle_module_command(module, "initialize", {
            "configuration": configuration(
                {"value": {"schema": {}, "required": True}},
                {"result": {"schema": {}, "required": True}},
            )
        })
        with self.assertRaisesRegex(ValueError, "undeclared output"):
            handle_module_command(module, "invoke", {"inputs": {"value": 3}})

    def test_required_output_cannot_be_omitted(self):
        class MissingOutput(SignalModule):
            def update(self, value):
                return {}

        module = MissingOutput()
        handle_module_command(module, "initialize", {
            "configuration": configuration(
                {"value": {"schema": {}, "required": True}},
                {"result": {"schema": {}, "required": True}},
            )
        })
        with self.assertRaisesRegex(ValueError, "required output"):
            handle_module_command(module, "invoke", {"inputs": {"value": 3}})

    def test_json_line_protocol_uses_pipeline_data_v5(self):
        request = {
            "protocolVersion": "pipeline-data-v5",
            "requestId": "request-1",
            "command": "initialize",
            "payload": {"configuration": configuration(
                {
                    "left": {"schema": {}, "required": True},
                    "right": {"schema": {}, "required": False},
                },
                {"total": {"schema": {}, "required": True}},
            )},
        }
        output = io.StringIO()
        serve_module(AddModule(), io.StringIO(json.dumps(request) + "\n"), output)
        response = json.loads(output.getvalue())
        self.assertEqual(response["protocolVersion"], "pipeline-data-v5")
        self.assertTrue(response["success"])
        self.assertEqual(response["payload"]["status"], "initialized")
        self.assertNotIn("invocationPolicy", response["payload"])

    def test_common_state_snapshot_restore_and_finalized_state_are_enforced(self):
        class StatefulModule(SignalModule):
            def on_initialize(self):
                self.state = {"total": 0}

            def update(self, value):
                self.state["total"] += value
                return {"total": self.state["total"]}

        module = StatefulModule()
        handle_module_command(module, "initialize", {"configuration": configuration(
            {"value": {"schema": {}, "required": True}},
            {"total": {"schema": {}, "required": True}},
        )})
        handle_module_command(module, "invoke", {"inputs": {"value": 3}})
        snapshot = handle_module_command(module, "snapshot", {})["snapshot"]
        handle_module_command(module, "invoke", {"inputs": {"value": 4}})
        handle_module_command(module, "restore", {"snapshot": snapshot})
        restored = handle_module_command(module, "invoke", {"inputs": {"value": 2}})
        self.assertEqual(restored["outputs"]["total"], 5)
        handle_module_command(module, "finalize", {})
        for command, payload in (
            ("invoke", {"inputs": {"value": 1}}),
            ("snapshot", {}),
            ("restore", {"snapshot": snapshot}),
            ("finalize", {}),
        ):
            with self.subTest(command=command):
                with self.assertRaisesRegex(RuntimeError, "finalized"):
                    handle_module_command(module, command, payload)

    def test_common_snapshot_rejects_non_json_state(self):
        self.module.state = {"invalid": {1, 2}}
        with self.assertRaisesRegex(ValueError, "finite JSON"):
            handle_module_command(self.module, "snapshot", {})

    def test_close_hook_is_called_and_worker_stops(self):
        class ClosingModule(AddModule):
            def on_initialize(self):
                self.closed = False

            def on_close(self):
                self.closed = True

        module = ClosingModule()
        requests = [
            {
                "protocolVersion": "pipeline-data-v5",
                "requestId": "close-1",
                "command": "close",
                "payload": {},
            },
            {
                "protocolVersion": "pipeline-data-v5",
                "requestId": "unreachable",
                "command": "invoke",
                "payload": {},
            },
        ]
        output = io.StringIO()
        serve_module(
            module,
            io.StringIO("".join(json.dumps(item) + "\n" for item in requests)),
            output,
        )
        responses = output.getvalue().splitlines()
        self.assertTrue(module.closed)
        self.assertEqual(len(responses), 1)
        self.assertEqual(json.loads(responses[0])["payload"], {"status": "closed"})


if __name__ == "__main__":
    unittest.main()
