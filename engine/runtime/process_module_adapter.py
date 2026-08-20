#!/usr/bin/env python3
"""External-process transport for one Engine-frozen ProcessRunner Module."""

from __future__ import annotations

import copy
import os
from pathlib import Path
import selectors
import subprocess
import sys
import time

from engine.contracts import strict_json
from engine.contracts.module import (
    PROTOCOL_VERSION,
    normalize_module_parameters,
    require_exact_fields,
)
from engine.runtime.module_adapter import (
    InvocationAdapter,
    module_adapter_material,
    module_configuration,
)
from engine.runtime.process_supervision import (
    SUPERVISOR_FORCED_DESCENDANT_EXIT_CODE,
    identity_alive,
    process_identity,
    process_group_alive,
    refresh_process_tree,
    terminate_process_tree,
)


__all__ = ("create_process_module_adapter",)


_DEFAULT_REQUEST_TIMEOUT_SECONDS = 30.0
_DEFAULT_MAX_RESPONSE_BYTES = 8 * 1024 * 1024
_STDERR_TAIL_BYTES = 64 * 1024


class _ProcessAdapter(InvocationAdapter):
    def __init__(self, authority):
        binding, definition, _ports, _mode = module_adapter_material(
            authority,
            expected_activation_mode="ProcessRunner",
        )
        self.binding = binding
        parameters = normalize_module_parameters(
            definition["parameters"],
            activation_mode="ProcessRunner",
            label=f"Module '{binding['instanceId']}' parameters",
        )
        command = parameters["command"].strip()
        arguments = parameters["arguments"]
        self.request_timeout = self._positive_number(
            parameters.get(
                "requestTimeoutSeconds", _DEFAULT_REQUEST_TIMEOUT_SECONDS
            ),
            "requestTimeoutSeconds",
        )
        self.max_response_bytes = int(self._positive_number(
            parameters.get("maxResponseBytes", _DEFAULT_MAX_RESPONSE_BYTES),
            "maxResponseBytes",
        ))
        self.process = None
        self.root_identity = None
        self.process_group_id = None
        self.session_id = None
        self.known_processes = {}
        self.selector = None
        self.stdout_buffer = bytearray()
        self.stderr_tail = bytearray()
        self.sequence = 0
        self.request_count = 0
        self.request_bytes = 0
        self.response_bytes = 0
        self.command_counts = {}
        self.command_seconds = {}
        self._json_encoder = lambda value: strict_json.dumps(
            value, separators=(",", ":")
        )
        self._json_decoder = strict_json.loads
        supervisor = Path(__file__).with_name("process_module_supervisor.py")
        self.process = subprocess.Popen(
            [
                sys.executable,
                str(supervisor),
                command,
                *[str(item) for item in arguments],
            ],
            cwd=parameters.get("workingDirectory") or None,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=False,
            bufsize=0,
            close_fds=True,
            start_new_session=True,
        )
        try:
            self.root_identity = process_identity(self.process.pid)
            if self.root_identity is None:
                raise RuntimeError(
                    f"Module '{binding['instanceId']}' process identity is unavailable."
                )
            self.process_group_id = os.getpgid(self.process.pid)
            self.session_id = os.getsid(self.process.pid)
            if (
                self.process_group_id != self.process.pid
                or self.session_id != self.process.pid
            ):
                raise RuntimeError(
                    f"Module '{binding['instanceId']}' did not start in its Engine-owned session."
                )
            self.known_processes = {self.process.pid: self.root_identity}
            refresh_process_tree(
                self.known_processes,
                self.process.pid,
                root_identity=self.root_identity,
            )
            os.set_blocking(self.process.stdin.fileno(), False)
            self.selector = selectors.DefaultSelector()
            self.selector.register(
                self.process.stdout, selectors.EVENT_READ, "stdout"
            )
            self.selector.register(
                self.process.stderr, selectors.EVENT_READ, "stderr"
            )
            initialized = self._request(
                "initialize",
                {
                    "configuration": module_configuration(authority)
                },
            )
            require_exact_fields(
                initialized,
                allowed={"status", "versionKey"},
                required={"status", "versionKey"},
                label=f"Module '{binding['instanceId']}' initialize response",
            )
            expected_version_key = (
                f"{binding['kind']}/{binding['moduleId']}/{binding['version']}"
            )
            if (
                initialized["status"] != "initialized"
                or initialized["versionKey"] != expected_version_key
            ):
                raise ValueError(
                    f"Module '{binding['instanceId']}' returned an invalid initialize response."
                )
        except BaseException:
            cleanup_errors = []
            try:
                self._abort_process()
            except BaseException as cleanup_error:
                cleanup_errors.append(cleanup_error)
            try:
                self._shutdown_process()
            except BaseException as cleanup_error:
                cleanup_errors.append(cleanup_error)
            # The initialization exception remains authoritative.  Cleanup is
            # all-attempt and never selects another execution path.
            raise

    @staticmethod
    def _positive_number(value, name):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"Module parameter {name} must be a positive number.")
        if value <= 0:
            raise ValueError(f"Module parameter {name} must be a positive number.")
        return value

    def _append_stderr(self, chunk):
        self.stderr_tail.extend(chunk)
        if len(self.stderr_tail) > _STDERR_TAIL_BYTES:
            del self.stderr_tail[:-_STDERR_TAIL_BYTES]

    def _stderr_detail(self):
        return bytes(self.stderr_tail).decode("utf-8", errors="replace").strip()

    def _consume_read_event(self, key, command):
        chunk = os.read(key.fileobj.fileno(), 65536)
        if key.data == "stderr":
            if chunk:
                self._append_stderr(chunk)
            else:
                self.selector.unregister(key.fileobj)
            return
        if chunk:
            self.stdout_buffer.extend(chunk)
            if len(self.stdout_buffer) > self.max_response_bytes:
                raise ValueError(
                    f"Module '{self.binding['instanceId']}' response exceeded "
                    f"{self.max_response_bytes} bytes during {command}."
                )
            return
        self.selector.unregister(key.fileobj)
        detail = self._stderr_detail()
        suffix = f": {detail}" if detail else "."
        raise ValueError(
            f"Module '{self.binding['instanceId']}' closed stdout during {command}{suffix}"
        )

    def _write_request(self, encoded, command, deadline):
        offset = 0
        self.selector.register(self.process.stdin, selectors.EVENT_WRITE, "stdin")
        try:
            while offset < len(encoded):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise ValueError(
                        f"Module '{self.binding['instanceId']}' timed out during {command}."
                    )
                events = self.selector.select(timeout=remaining)
                if not events:
                    raise ValueError(
                        f"Module '{self.binding['instanceId']}' timed out during {command}."
                    )
                for key, _mask in events:
                    if key.data == "stdin":
                        try:
                            written = os.write(
                                key.fileobj.fileno(), memoryview(encoded)[offset:]
                            )
                        except BlockingIOError:
                            continue
                        except BrokenPipeError as exc:
                            raise ValueError(
                                f"Module '{self.binding['instanceId']}' closed stdin during {command}."
                            ) from exc
                        if written <= 0:
                            raise ValueError(
                                f"Module '{self.binding['instanceId']}' closed stdin during {command}."
                            )
                        offset += written
                    else:
                        self._consume_read_event(key, command)
        finally:
            try:
                self.selector.unregister(self.process.stdin)
            except (KeyError, ValueError):
                pass

    def _read_response_line(self, command, deadline):
        while True:
            newline = self.stdout_buffer.find(b"\n")
            if newline >= 0:
                line = bytes(self.stdout_buffer[:newline])
                del self.stdout_buffer[: newline + 1]
                return line
            if len(self.stdout_buffer) > self.max_response_bytes:
                raise ValueError(
                    f"Module '{self.binding['instanceId']}' response exceeded "
                    f"{self.max_response_bytes} bytes during {command}."
                )
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ValueError(
                    f"Module '{self.binding['instanceId']}' timed out during {command}."
                )
            events = self.selector.select(timeout=remaining)
            if not events:
                raise ValueError(
                    f"Module '{self.binding['instanceId']}' timed out during {command}."
                )
            for key, _mask in events:
                self._consume_read_event(key, command)

    def _request(self, command, payload):
        if self.process.poll() is not None:
            detail = self._stderr_detail()
            raise ValueError(
                f"Module '{self.binding['instanceId']}' exited with code "
                f"{self.process.returncode}: {detail}"
            )
        self.sequence += 1
        request_id = f"{self.binding['instanceId']}:{self.sequence}"
        message = {
            "protocolVersion": PROTOCOL_VERSION,
            "requestId": request_id,
            "command": command,
            "payload": payload,
        }
        encoded = (self._json_encoder(message) + "\n").encode("utf-8")
        started = time.perf_counter()
        deadline = time.monotonic() + self.request_timeout
        self.request_count += 1
        self.request_bytes += len(encoded)
        self.command_counts[command] = self.command_counts.get(command, 0) + 1
        try:
            self._write_request(encoded, command, deadline)
            line = self._read_response_line(command, deadline)
        except BaseException:
            cleanup_errors = []
            try:
                self._abort_process()
            except BaseException as cleanup_error:
                cleanup_errors.append(cleanup_error)
            raise
        self.response_bytes += len(line) + 1
        self.command_seconds[command] = (
            self.command_seconds.get(command, 0.0)
            + (time.perf_counter() - started)
        )
        if len(line) > self.max_response_bytes:
            raise ValueError(
                f"Module '{self.binding['instanceId']}' response exceeded "
                f"{self.max_response_bytes} bytes during {command}."
            )
        try:
            response = self._json_decoder(line.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise ValueError(
                f"Module '{self.binding['instanceId']}' returned invalid JSON during {command}."
            ) from exc
        if not isinstance(response, dict):
            raise ValueError(
                f"Module '{self.binding['instanceId']}' returned a non-object response."
            )
        require_exact_fields(
            response,
            allowed={"protocolVersion", "requestId", "success", "payload", "error"},
            required={"protocolVersion", "requestId", "success", "payload", "error"},
            label=f"Module '{self.binding['instanceId']}' response",
        )
        if (
            response["protocolVersion"] != PROTOCOL_VERSION
            or response["requestId"] != request_id
        ):
            raise ValueError(
                f"Module '{self.binding['instanceId']}' returned an invalid protocol response."
            )
        if not isinstance(response["success"], bool) or not isinstance(
            response["error"], str
        ):
            raise ValueError(
                f"Module '{self.binding['instanceId']}' returned invalid success/error fields."
            )
        if not response["success"]:
            if not response["error"].strip():
                raise ValueError(
                    f"Module '{self.binding['instanceId']}' returned a failure without an error."
                )
            raise ValueError(
                f"Module '{self.binding['instanceId']}' failed: {response['error']}"
            )
        if response["error"]:
            raise ValueError(
                f"Module '{self.binding['instanceId']}' returned an error for a successful response."
            )
        if not isinstance(response["payload"], dict):
            raise ValueError(
                f"Module '{self.binding['instanceId']}' response payload must be an object."
            )
        return response["payload"]

    def _abort_process(self):
        terminate_process_tree(
            self.process,
            self.known_processes,
            terminate_grace=1.0,
            kill_grace=0.5,
            owns_process_group=True,
            session_id=self.session_id,
            process_group_id=self.process_group_id,
        )

    def invoke(self, inputs):
        response = self._request("invoke", {"inputs": inputs})
        require_exact_fields(
            response,
            allowed={"outputs"},
            required={"outputs"},
            label=f"Module '{self.binding['instanceId']}' invoke response",
        )
        return response["outputs"]

    def transport_metrics(self):
        return {
            "runtimeMode": "external-process",
            "requestCount": self.request_count,
            "requestBytes": self.request_bytes,
            "responseBytes": self.response_bytes,
            "commandCounts": dict(self.command_counts),
            "commandSeconds": dict(self.command_seconds),
        }

    def finalize(self):
        return self._request("finalize", {})

    def snapshot(self):
        response = self._request("snapshot", {})
        require_exact_fields(
            response,
            allowed={"snapshot"},
            required={"snapshot"},
            label=f"Module '{self.binding['instanceId']}' snapshot response",
        )
        return response["snapshot"]

    def restore(self, snapshot):
        response = self._request("restore", {"snapshot": copy.deepcopy(snapshot)})
        require_exact_fields(
            response,
            allowed={"status"},
            required={"status"},
            label=f"Module '{self.binding['instanceId']}' restore response",
        )
        if response["status"] != "restored":
            raise ValueError(
                f"Module '{self.binding['instanceId']}' returned an invalid restore response."
            )
        return response

    def _shutdown_process(self):
        first_error = None
        forced = False
        if self.selector is not None:
            try:
                self.selector.close()
            except BaseException as exc:
                first_error = first_error or exc
        if self.process is None:
            return first_error, forced
        try:
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                self.process.poll()
                refresh_process_tree(
                    self.known_processes,
                    self.process.pid,
                    root_identity=self.root_identity,
                    session_id=self.session_id,
                    process_group_id=self.process_group_id,
                )
                if (
                    self.process.poll() is not None
                    and not process_group_alive(
                        self.process_group_id,
                        self.root_identity,
                        session_id=self.session_id,
                    )
                    and not any(
                        identity_alive(identity)
                        for pid, identity in self.known_processes.items()
                        if pid != self.process.pid
                    )
                ):
                    break
                time.sleep(0.01)
            survivors = any(
                identity_alive(identity)
                for identity in self.known_processes.values()
            ) or process_group_alive(
                self.process_group_id,
                self.root_identity,
                session_id=self.session_id,
            )
            if survivors:
                forced = True
                terminate_process_tree(
                    self.process,
                    self.known_processes,
                    terminate_grace=1.0,
                    kill_grace=1.0,
                    owns_process_group=True,
                    session_id=self.session_id,
                    process_group_id=self.process_group_id,
                )
        except BaseException as exc:
            first_error = first_error or exc
        for stream in (self.process.stdin, self.process.stdout, self.process.stderr):
            if stream:
                try:
                    stream.close()
                except BaseException as exc:
                    first_error = first_error or exc
        return first_error, forced

    def close(self):
        close_error = None
        response = None
        if self.process.poll() is None:
            try:
                response = self._request("close", {})
                require_exact_fields(
                    response,
                    allowed={"status"},
                    required={"status"},
                    label=f"Module '{self.binding['instanceId']}' close response",
                )
                if response["status"] != "closed":
                    raise ValueError(
                        f"Module '{self.binding['instanceId']}' returned an invalid close response."
                    )
            except BaseException as exc:
                close_error = exc
        try:
            shutdown_error, forced = self._shutdown_process()
        except BaseException as exc:
            shutdown_error, forced = exc, False
        if close_error or shutdown_error:
            raise close_error or shutdown_error
        if forced:
            raise ValueError(
                f"Module '{self.binding['instanceId']}' acknowledged close but did not exit."
            )
        if self.process.returncode == SUPERVISOR_FORCED_DESCENDANT_EXIT_CODE:
            raise ValueError(
                f"Module '{self.binding['instanceId']}' acknowledged close but did not exit."
            )
        if self.process.returncode != 0:
            raise ValueError(
                f"Module '{self.binding['instanceId']}' exited with code "
                f"{self.process.returncode} after close."
            )
        return response


def create_process_module_adapter(authority):
    """Create the private external-process adapter for a verified ProcessRunner."""
    return _ProcessAdapter(authority)
