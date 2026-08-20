"""Reusable archived Module Runtime fixtures for Engine tests."""

import base64
import copy
import tempfile
import unittest
from pathlib import Path

from engine.service import control_api as control
from engine.authority import module_definition as module_definition_authority
from engine.authority import module_invocation as module_invocation_authority
from engine.authority.graph import bind_compiled_graph_authority_plan
from engine.compiler.graph import compile_module_graph, compile_module_graph_authority
from engine.contracts.graph import compiled_graph_definition
from engine.service import module_publication
from engine.runtime.graph import ModuleGraphRuntime
from engine.runtime.module_invoker import ModuleInvoker


WORKER = r'''#!/usr/bin/env python3
import json
import sys
import time

configuration = {}
state = {"count": 0}
for line in sys.stdin:
    request = json.loads(line)
    command = request["command"]
    payload = request["payload"]
    if command == "initialize":
        configuration = payload["configuration"]
        mode = (configuration.get("config") or {}).get("mode", "normal")
        if mode == "stderr":
            sys.stderr.write("diagnostic" * 40000)
            sys.stderr.flush()
        if mode == "half-line":
            sys.stdout.write('{"protocolVersion":')
            sys.stdout.flush()
            time.sleep(5)
            continue
        response_payload = {
            "status": "initialized",
            "versionKey": f"{configuration['kind']}/{configuration['moduleId']}/{configuration['version']}",
        }
    elif command == "invoke":
        state["count"] += 1
        mode = (configuration.get("config") or {}).get("mode", "normal")
        value = payload.get("inputs", {}).get("payload", {}).get("value", 0)
        response_payload = {"outputs": {"result": {"value": 999 if mode == "mutate" else value}}}
        if mode == "unknown-output":
            response_payload["outputs"]["bypass"] = 2
    elif command == "snapshot":
        response_payload = {"snapshot": state}
    elif command == "restore":
        state = dict(payload.get("snapshot") or {})
        response_payload = {"status": "restored"}
    elif command == "finalize":
        response_payload = {"count": state["count"]}
    elif command == "close":
        response_payload = {"status": "closed"}
    else:
        response_payload = {}
    response = {
        "protocolVersion": "pipeline-data-v5",
        "requestId": request["requestId"],
        "success": True,
        "payload": response_payload,
        "error": "",
    }
    if (configuration.get("config") or {}).get("mode") == "oversized" and command == "initialize":
        response["payload"]["padding"] = "x" * 20000
    sys.stdout.write(json.dumps(response) + "\n")
    sys.stdout.flush()
    if command == "close":
        break
'''


PYTHON_MODULE = r'''from strategy_devkit.module_sdk import SignalModule


class DirectModule(SignalModule):
    def on_initialize(self):
        self.state = {"count": 0}

    def update(self, payload):
        self.state["count"] += 1
        value = payload["value"]
        payload["value"] = 999
        return {"result": {"value": value}}


MODULE_CLASS = DirectModule
'''


PYTHON_INITIALIZE_AND_CLOSE_FAILURE = r'''from strategy_devkit.module_sdk import SignalModule


class FailingModule(SignalModule):
    def on_initialize(self):
        raise RuntimeError("PRIMARY_INIT")

    def on_close(self):
        raise ValueError("SECONDARY_CLEANUP")

    def update(self, payload):
        return {"result": payload}


MODULE_CLASS = FailingModule
'''


PYTHON_BUNDLE_TAMPER = r'''import stat
from pathlib import Path

from strategy_devkit.module_sdk import SignalModule


RETURN_VALUE = 1.0


class TamperingModule(SignalModule):
    def on_initialize(self):
        source = Path(self.archive["root"]) / "module.py"
        source.chmod(source.stat().st_mode | stat.S_IWUSR)
        source.write_text(
            source.read_text(encoding="utf-8").replace(
                "RETURN_VALUE = 1.0",
                "RETURN_VALUE = 999.0",
            ),
            encoding="utf-8",
        )

    def update(self, payload):
        return {"result": {"value": RETURN_VALUE}}


MODULE_CLASS = TamperingModule
'''


PAYLOAD_SCHEMA = {
    "type": "object",
    "properties": {"value": {"type": "number"}},
    "required": ["value"],
    "additionalProperties": False,
}


def runtime_from_compiled_plan(plan, definitions, *, execution_root=None):
    """Test helper following the production compile/bind/authority boundary."""
    input_sources = plan.get("inputSources", {})
    authority = compile_module_graph_authority(
        compiled_graph_definition(plan),
        plan["bindings"],
        definitions,
        plan["inputContracts"],
        required_roots=plan["inputRequiredRoots"],
        source_contracts={
            source: state["contracts"] for source, state in input_sources.items()
        },
        source_required_roots={
            source: state["requiredRoots"] for source, state in input_sources.items()
        },
    )
    authority = bind_compiled_graph_authority_plan(authority, plan)
    return ModuleGraphRuntime.from_compiled_authority(
        authority,
        execution_root=execution_root,
    )


def module_invoker(binding, definition, **kwargs):
    """Test helper following the verified Definition authority boundary."""
    binding = copy.deepcopy(binding)
    for direction in ("inputs", "outputs"):
        for port_name, port in definition["ports"][direction].items():
            if port.get("required", True) and port_name not in binding[direction]:
                binding[direction][port_name] = f"direct.{direction}.{port_name}"
    definition_authority = (
        module_definition_authority.verify_module_definition_authority(definition)
    )
    invocation_authority = (
        module_invocation_authority.bind_module_invocation_authority(
            binding,
            definition_authority,
        )
    )
    return ModuleInvoker.from_authority(
        invocation_authority,
        **kwargs,
    )


class ModuleRuntimeTestCase(unittest.TestCase):
    """Own one isolated repository and public Module archive helpers."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.config = {
            "controlRoot": str(root / "control"),
            "releaseRoot": str(root / "releases"),
            "liveRoot": str(root / "live"),
        }

    def tearDown(self):
        self.temp.cleanup()

    def archive_worker(
        self,
        module_id,
        *,
        max_bytes=8 * 1024 * 1024,
        timeout=2,
        optional_payload=False,
        source=WORKER,
        ports=None,
    ):
        result = module_publication.publish_module(self.config, {
            "kind": "Signal",
            "moduleId": module_id,
            "name": module_id,
            "description": "Module runtime boundary fixture.",
            "activationMode": "ProcessRunner",
            "parameters": {
                "command": "{{moduleRoot}}/worker.py",
                "arguments": [],
                "workingDirectory": "{{moduleRoot}}",
                "requestTimeoutSeconds": timeout,
                "maxResponseBytes": max_bytes,
            },
            "configSchema": {
                "type": "object",
                "properties": {"mode": {"type": "string"}},
                "additionalProperties": False,
            },
            "ports": ports if ports is not None else {
                "inputs": {
                    "payload": {
                        "schema": PAYLOAD_SCHEMA,
                        "required": not optional_payload,
                    }
                },
                "outputs": {"result": {"schema": PAYLOAD_SCHEMA}},
            },
            "files": [{
                "path": "worker.py",
                "contentBase64": base64.b64encode(source.encode()).decode(),
                "executable": True,
            }],
        })
        return result["definition"]

    def archive_python_module(
        self,
        module_id,
        *,
        source=PYTHON_MODULE,
        ports=None,
    ):
        result = module_publication.publish_module(self.config, {
            "kind": "Signal",
            "moduleId": module_id,
            "name": module_id,
            "description": "In-process Python Module fixture.",
            "activationMode": "PythonModule",
            "parameters": {},
            "configSchema": {"type": "object", "additionalProperties": False},
            "ports": ports if ports is not None else {
                "inputs": {"payload": {"schema": PAYLOAD_SCHEMA}},
                "outputs": {"result": {"schema": PAYLOAD_SCHEMA}},
            },
            "files": [{
                "path": "module.py",
                "contentBase64": base64.b64encode(source.encode()).decode(),
                "executable": False,
            }],
        })
        return result["definition"]

    @staticmethod
    def binding(
        definition,
        mode="normal",
        *,
        instance_id=None,
        inputs=None,
        outputs=None,
    ):
        return {
            "instanceId": instance_id or definition["moduleId"],
            "kind": definition["kind"],
            "moduleId": definition["moduleId"],
            "version": definition["version"],
            "config": (
                {"mode": mode}
                if definition["activationMode"] == "ProcessRunner"
                else {}
            ),
            "inputs": (
                {
                    name: f"input.{name}"
                    for name in definition["ports"]["inputs"]
                }
                if inputs is None
                else inputs
            ),
            "outputs": (
                {
                    name: f"output.{name}"
                    for name in definition["ports"]["outputs"]
                }
                if outputs is None
                else outputs
            ),
        }

    def compiled_plan(
        self,
        definition,
        *,
        instance_id="worker",
        mode="normal",
        inputs=None,
        outputs=None,
        graph_inputs=None,
        graph_outputs=None,
        initial_contracts=None,
    ):
        binding = self.binding(
            definition,
            mode,
            instance_id=instance_id,
            inputs=inputs,
            outputs=outputs,
        )
        key = f"Signal/{definition['moduleId']}/{definition['version']}"
        return compile_module_graph(
            {
                "nodes": [instance_id],
                "inputs": graph_inputs or {},
                "outputs": graph_outputs or {},
            },
            {instance_id: binding},
            {key: definition},
            initial_contracts or {},
            allowed_kinds={"Signal"},
            label="Runtime test Signal Graph",
        )


__all__ = (
    "ModuleRuntimeTestCase",
    "PAYLOAD_SCHEMA",
    "PYTHON_BUNDLE_TAMPER",
    "PYTHON_INITIALIZE_AND_CLOSE_FAILURE",
    "PYTHON_MODULE",
    "WORKER",
    "module_invoker",
    "runtime_from_compiled_plan",
)
