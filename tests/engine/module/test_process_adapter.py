#!/usr/bin/env python3

import base64
import sys
import tempfile
import time
from pathlib import Path
from unittest import mock

from engine.authority import module_definition as module_definition_authority
from engine.runtime import process_module_adapter
from engine.runtime import process_supervision
from engine.runtime import module_implementation
from engine.service import module_publication
from engine.service import control_api as control
from tests.support.module_runtime import (
    ModuleRuntimeTestCase,
    WORKER,
    module_invoker,
)

class ProcessModuleAdapterTests(ModuleRuntimeTestCase):
    def test_process_runner_supports_every_public_port_name(self):
        source = WORKER.replace(
            'value = payload.get("inputs", {}).get("payload", {}).get("value", 0)',
            'value = sum(payload["inputs"][name]["value"] '
            'for name in ("price-close", "class", "self"))',
        ).replace(
            'response_payload = {"outputs": {"result": {"value": 999 if mode == "mutate" else value}}}',
            'response_payload = {"outputs": {"result-value": {"value": value}}}',
        )
        value_schema = {
            "type": "object",
            "properties": {"value": {"type": "number"}},
            "required": ["value"],
            "additionalProperties": False,
        }
        definition = self.archive_worker(
            "generic-process-port-names",
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

    def test_process_runtime_receives_only_isolated_definition_paths(self):
        definition = module_publication.publish_module(self.config, {
            "kind": "Signal",
            "moduleId": "runtime-isolated-definition",
            "name": "runtime-isolated-definition",
            "description": "Runtime ProcessRunner isolation fixture.",
            "activationMode": "ProcessRunner",
            "parameters": {
                "command": "{{moduleRoot}}/worker.py",
                "arguments": ["--source={{moduleRoot}}/worker.py"],
                "workingDirectory": "{{moduleRoot}}",
                "requestTimeoutSeconds": 2,
                "maxResponseBytes": 8 * 1024 * 1024,
            },
            "configSchema": {
                "type": "object",
                "properties": {"mode": {"type": "string"}},
                "additionalProperties": False,
            },
            "ports": {
                "inputs": {"payload": {"schema": {
                    "type": "object",
                    "properties": {"value": {"type": "number"}},
                    "required": ["value"],
                    "additionalProperties": False,
                }}},
                "outputs": {"result": {"schema": {
                    "type": "object",
                    "properties": {"value": {"type": "number"}},
                    "required": ["value"],
                    "additionalProperties": False,
                }}},
            },
            "files": [{
                "path": "worker.py",
                "contentBase64": base64.b64encode(WORKER.encode()).decode(),
                "executable": True,
            }],
        })["definition"]
        archive_root = Path(definition["archive"]["root"]).resolve()
        captured = []
        original = process_module_adapter.module_adapter_material

        def capture(authority, **kwargs):
            material = original(authority, **kwargs)
            captured.append(material[1])
            return material

        with mock.patch.object(
            process_module_adapter,
            "module_adapter_material",
            side_effect=capture,
        ):
            invoker = module_invoker(self.binding(definition), definition)
        try:
            isolated = captured[-1]
            isolated_root = Path(isolated["archive"]["root"]).resolve()
            self.assertNotEqual(isolated_root, archive_root)
            self.assertNotIn(str(archive_root), repr(isolated))
            self.assertEqual(
                isolated["parameters"]["command"],
                str(isolated_root / "worker.py"),
            )
            self.assertEqual(
                isolated["parameters"]["arguments"],
                [f"--source={isolated_root / 'worker.py'}"],
            )
            self.assertEqual(
                Path(isolated["parameters"]["workingDirectory"]).resolve(),
                isolated_root,
            )
        finally:
            invoker.close()

    def test_process_materialization_rejects_symlinked_execution_namespace(self):
        definition = self.archive_worker("process-symlink-namespace")
        authority = module_definition_authority.verify_module_definition_authority(
            definition
        )
        execution_root = Path(self.temp.name) / "process-symlink-execution"
        execution_root.mkdir()
        (execution_root / "linked").symlink_to(
            Path(self.temp.name).parent,
            target_is_directory=True,
        )
        with self.assertRaisesRegex(ValueError, "symbolic link"):
            module_implementation.materialize_verified_module_definition(
                authority,
                execution_root,
                "linked/process",
            )

    def test_process_runner_rejects_execution_assets_outside_its_archive(self):
        with tempfile.TemporaryDirectory() as external:
            request = {
                "kind": "Signal",
                "moduleId": "external-runner",
                "name": "external-runner",
                "description": "Must not execute mutable external assets.",
                "activationMode": "ProcessRunner",
                "parameters": {
                    "command": sys.executable,
                    "arguments": [str(Path(external) / "worker.py")],
                    "workingDirectory": external,
                },
                "configSchema": {"type": "object", "additionalProperties": False},
                "ports": {"inputs": {}, "outputs": {}},
                "files": [{
                    "path": "worker.py",
                    "contentBase64": base64.b64encode(WORKER.encode()).decode(),
                    "executable": True,
                }],
            }
            with self.assertRaisesRegex(ValueError, "Module root token"):
                module_publication.publish_module(self.config, request)

    def test_process_adapter_cleans_up_when_initialize_raises_base_exception(self):
        definition = self.archive_worker("initialize-base-exception-worker")
        shutdowns = []
        original_shutdown = process_module_adapter._ProcessAdapter._shutdown_process

        def shutdown(adapter):
            shutdowns.append(adapter.process.pid)
            return original_shutdown(adapter)

        with (
            mock.patch.object(
                process_module_adapter._ProcessAdapter,
                "_request",
                side_effect=SystemExit("initialize interrupted"),
            ),
            mock.patch.object(
                process_module_adapter._ProcessAdapter,
                "_shutdown_process",
                new=shutdown,
            ),
            self.assertRaisesRegex(SystemExit, "initialize interrupted"),
        ):
            module_invoker(self.binding(definition), definition)
        self.assertEqual(len(shutdowns), 1)

    def test_process_adapter_owns_child_from_the_first_post_spawn_operation(self):
        definition = self.archive_worker("early-initialize-base-exception-worker")
        original_terminate = process_module_adapter.terminate_process_tree
        with (
            mock.patch.object(
                process_module_adapter.os,
                "set_blocking",
                side_effect=SystemExit("setup interrupted"),
            ),
            mock.patch.object(
                process_module_adapter,
                "terminate_process_tree",
                wraps=original_terminate,
            ) as terminate,
            self.assertRaisesRegex(SystemExit, "setup interrupted"),
        ):
            module_invoker(self.binding(definition), definition)
        self.assertGreaterEqual(terminate.call_count, 1)
        process = terminate.call_args.args[0]
        self.assertIsNotNone(process.poll())

    def test_process_request_timeout_covers_blocked_stdin_writes(self):
        worker = r'''#!/usr/bin/env python3
import json
import sys
import time

request = json.loads(sys.stdin.readline())
configuration = request["payload"]["configuration"]
response = {
    "protocolVersion": "pipeline-data-v5",
    "requestId": request["requestId"],
    "success": True,
    "payload": {
        "status": "initialized",
        "versionKey": f"{configuration['kind']}/{configuration['moduleId']}/{configuration['version']}",
    },
    "error": "",
}
sys.stdout.write(json.dumps(response) + "\n")
sys.stdout.flush()
while True:
    time.sleep(1)
'''
        definition = module_publication.publish_module(self.config, {
            "kind": "Signal",
            "moduleId": "blocked-stdin-worker",
            "name": "blocked-stdin-worker",
            "description": "Request write deadline regression fixture.",
            "activationMode": "ProcessRunner",
            "parameters": {
                "command": "{{moduleRoot}}/worker.py",
                "arguments": [],
                "workingDirectory": "{{moduleRoot}}",
                "requestTimeoutSeconds": 0.2,
                "maxResponseBytes": 1024 * 1024,
            },
            "configSchema": {
                "type": "object",
                "properties": {"mode": {"type": "string"}},
                "additionalProperties": False,
            },
            "ports": {
                "inputs": {"payload": {"schema": {"type": "string"}}},
                "outputs": {"result": {"schema": {"type": "string"}}},
            },
            "files": [{
                "path": "worker.py",
                "contentBase64": base64.b64encode(worker.encode()).decode(),
                "executable": True,
            }],
        })["definition"]
        invoker = module_invoker(self.binding(definition), definition)
        started = time.monotonic()
        try:
            with self.assertRaisesRegex(ValueError, "timed out"):
                invoker.invoke({"payload": "x" * (4 * 1024 * 1024)})
            self.assertLess(time.monotonic() - started, 2.0)
            self.assertIsNotNone(invoker._adapter.process.poll())
        finally:
            try:
                invoker.close()
            except ValueError:
                pass

    def test_process_close_acknowledgement_requires_worker_exit(self):
        hanging_close = WORKER.replace(
            'if command == "close":\n        break',
            'if command == "close":\n        time.sleep(30)',
        )
        definition = self.archive_worker(
            "hanging-close-worker",
            source=hanging_close,
        )
        invoker = module_invoker(self.binding(definition), definition)
        started = time.monotonic()
        with self.assertRaisesRegex(ValueError, "acknowledged close but did not exit"):
            invoker.close()
        self.assertLess(time.monotonic() - started, 7.0)
        self.assertIsNotNone(invoker._adapter.process.poll())

    def test_process_close_reaps_a_late_child_after_the_root_exits(self):
        marker = Path(self.temp.name) / "late-close-child.pid"
        late_child_worker = f'''#!/usr/bin/env python3
import json
import os
from pathlib import Path
import sys
import time

marker = {str(marker)!r}
for line in sys.stdin:
    request = json.loads(line)
    command = request["command"]
    if command == "initialize":
        configuration = request["payload"]["configuration"]
        payload = {{
            "status": "initialized",
            "versionKey": f"{{configuration['kind']}}/{{configuration['moduleId']}}/{{configuration['version']}}",
        }}
    elif command == "close":
        payload = {{"status": "closed"}}
    else:
        payload = {{}}
    response = {{
        "protocolVersion": "pipeline-data-v5",
        "requestId": request["requestId"],
        "success": True,
        "payload": payload,
        "error": "",
    }}
    sys.stdout.write(json.dumps(response) + "\\n")
    sys.stdout.flush()
    if command == "close":
        child = os.fork()
        if child == 0:
            os.setsid()
            Path(marker).write_text(str(os.getpid()), encoding="ascii")
            time.sleep(30)
            os._exit(0)
        break
'''
        definition = self.archive_worker(
            "late-close-child-worker",
            source=late_child_worker,
        )
        invoker = module_invoker(self.binding(definition), definition)
        with self.assertRaisesRegex(
            ValueError,
            "acknowledged close but did not exit",
        ):
            invoker.close()
        self.assertTrue(marker.exists())
        child_pid = int(marker.read_text(encoding="ascii"))
        identity = process_supervision.process_identity(child_pid)
        if identity is not None:
            state = Path(f"/proc/{child_pid}/stat").read_text(
                encoding="ascii"
            ).split(")", 1)[1].split()[0]
            self.assertEqual(state, "Z")

    def test_process_timeout_reaps_term_resistant_descendants(self):
        pid_path = Path(self.temp.name) / "term-resistant-child.pid"
        tree_worker = r'''#!/usr/bin/env python3
import json
import subprocess
import sys
import time

child = None
for line in sys.stdin:
    request = json.loads(line)
    command = request["command"]
    if command == "initialize":
        child = subprocess.Popen([
            sys.executable,
            "-c",
            "import os,signal,sys,time; "
            "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
            "open(sys.argv[1], 'w').write(str(os.getpid())); time.sleep(60)",
            "child.pid",
        ])
        payload = {
            "status": "initialized",
            "versionKey": "Signal/tree-worker/1",
        }
    elif command == "invoke":
        time.sleep(60)
        continue
    else:
        payload = {"status": "closed"} if command == "close" else {}
    response = {
        "protocolVersion": "pipeline-data-v5",
        "requestId": request["requestId"],
        "success": True,
        "payload": payload,
        "error": "",
    }
    sys.stdout.write(json.dumps(response) + "\n")
    sys.stdout.flush()
'''.replace('"child.pid",', f'{str(pid_path)!r},')
        definition = self.archive_worker(
            "tree-worker", source=tree_worker, timeout=0.2
        )
        invoker = module_invoker(self.binding(definition, "tree"), definition)
        deadline = time.monotonic() + 2
        while not pid_path.is_file() and time.monotonic() < deadline:
            time.sleep(0.01)
        child_pid = int(pid_path.read_text(encoding="utf-8"))
        with self.assertRaisesRegex(ValueError, "timed out"):
            invoker.invoke({"payload": {"value": 1.0}})
        deadline = time.monotonic() + 3
        while Path(f"/proc/{child_pid}").exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertFalse(Path(f"/proc/{child_pid}").exists())
        try:
            invoker.close()
        except ValueError:
            pass

    def test_process_module_is_invoked_for_every_engine_cycle(self):
        definition = self.archive_worker("per-cycle-worker")
        invoker = module_invoker(self.binding(definition), definition)
        try:
            first = invoker.invoke({"payload": {"value": 1.0}})
            first["result"]["value"] = 99.0
            second = invoker.invoke({"payload": {"value": 1.0}})
            self.assertEqual(second, {"result": {"value": 1.0}})
            self.assertEqual(invoker.snapshot()["count"], 2)
            invoker.invoke({"payload": {"value": 2.0}})
            self.assertEqual(invoker.snapshot()["count"], 3)
            metrics = invoker.transport_metrics()
            self.assertEqual(metrics["invocationCount"], 3)
            self.assertGreater(metrics["requestBytes"], 0)
            self.assertGreater(metrics["responseBytes"], 0)
        finally:
            invoker.close()

    def test_stderr_is_drained_without_blocking_stdout(self):
        definition = self.archive_worker("stderr-worker")
        invoker = module_invoker(self.binding(definition, "stderr"), definition)
        try:
            self.assertEqual(invoker.invoke({"payload": {"value": 3.0}})["result"]["value"], 3.0)
        finally:
            invoker.close()

    def test_half_line_times_out_and_oversized_response_is_rejected(self):
        half = self.archive_worker("half-worker", timeout=0.2)
        with self.assertRaisesRegex(ValueError, "timed out"):
            module_invoker(self.binding(half, "half-line"), half)

        oversized = self.archive_worker("oversized-worker", max_bytes=1024)
        with self.assertRaisesRegex(ValueError, "exceeded 1024 bytes"):
            module_invoker(self.binding(oversized, "oversized"), oversized)

    def test_archive_is_never_the_process_working_directory(self):
        definition = self.archive_worker("isolated-worker")
        archive_root = Path(definition["archive"]["root"])
        before = sorted(path.relative_to(archive_root).as_posix() for path in archive_root.rglob("*"))
        invoker = module_invoker(self.binding(definition), definition)
        invoker.close()
        after = sorted(path.relative_to(archive_root).as_posix() for path in archive_root.rglob("*"))
        self.assertEqual(before, after)
        self.assertFalse(any(path.name == "__pycache__" for path in archive_root.rglob("*")))
