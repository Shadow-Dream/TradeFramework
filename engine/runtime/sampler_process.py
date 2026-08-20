#!/usr/bin/env python3
"""Bounded JSON-lines transport for isolated Python Sampler workers."""

from __future__ import annotations

import os
import selectors
import subprocess
import threading
import time

from engine.contracts import strict_json
from engine.runtime import process_supervision


_PYTHON_SAMPLER_RESPONSE_TIMEOUT_SECONDS = 300.0
_PYTHON_SAMPLER_MAX_MESSAGE_BYTES = 64 * 1024 * 1024
_PYTHON_SAMPLER_STDERR_TAIL_BYTES = 64 * 1024
_PYTHON_SAMPLER_IO_CHUNK_BYTES = 64 * 1024
_PYTHON_SAMPLER_PROCESS_SCAN_SECONDS = 0.25
_PYTHON_SAMPLER_TERMINATE_GRACE_SECONDS = 1.0


def _has_pid_namespace_containment(command):
    return (
        bool(command)
        and os.path.basename(str(command[0])) == "bwrap"
        and "--unshare-all" in command
        and "--die-with-parent" in command
        and "--as-pid-1" not in command
    )


class SamplerProcessTransport:
    """Bounded binary JSON-lines transport for one Python Sampler worker."""

    def __init__(self, command, request):
        self._close_lock = threading.Lock()
        self._closed = False
        self.process = None
        self.root_identity = None
        self.selector = None
        self.stdout_buffer = bytearray()
        self.stderr_tail = bytearray()
        self.stdout_open = True
        self.stderr_open = True
        self.known_processes = {}
        # The production Python Sampler is always the command inside a fresh
        # bubblewrap PID namespace.  Its namespace reaper is a kernel-backed
        # descendant boundary, so periodic tree refreshes need not also scan
        # every host process merely to rediscover session members.  The final
        # forced refresh still performs the full session proof before cleanup.
        self._pid_namespace_contained = _has_pid_namespace_containment(command)
        self.next_process_scan = 0.0
        try:
            self.process = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=False,
                bufsize=0,
                close_fds=True,
                start_new_session=True,
            )
            self.root_identity = process_supervision.process_identity(self.process.pid)
            if self.root_identity is None:
                raise RuntimeError(
                    "Python Sampler worker PID identity could not be established."
                )
            self.known_processes[self.process.pid] = self.root_identity
            self.selector = selectors.DefaultSelector()
            for stream in (self.process.stdin, self.process.stdout, self.process.stderr):
                os.set_blocking(stream.fileno(), False)
            self.selector.register(self.process.stdout, selectors.EVENT_READ, "stdout")
            self.selector.register(self.process.stderr, selectors.EVENT_READ, "stderr")
            encoded = (
                strict_json.dumps(request, separators=(",", ":")) + "\n"
            ).encode("utf-8")
            self._write_request(encoded)
        except BaseException as primary_error:
            cleanup_error = None
            for _attempt in range(2):
                try:
                    self.close()
                    break
                except BaseException as exc:
                    cleanup_error = cleanup_error or exc
            if cleanup_error is not None:
                primary_error.__context__ = cleanup_error
            raise primary_error.with_traceback(primary_error.__traceback__)

    def _track_processes(self, *, force=False):
        if self._closed:
            return
        now = time.monotonic()
        if force or now >= self.next_process_scan:
            process_supervision.refresh_process_tree(
                self.known_processes,
                self.process.pid,
                root_identity=self.root_identity,
                session_id=self.process.pid,
                process_group_id=self.process.pid,
                discover_session_members=(
                    force or not self._pid_namespace_contained
                ),
            )
            self.next_process_scan = now + _PYTHON_SAMPLER_PROCESS_SCAN_SECONDS

    def stderr_detail(self):
        return bytes(self.stderr_tail).decode("utf-8", errors="replace").strip()

    def _append_stderr(self, chunk):
        self.stderr_tail.extend(chunk)
        if len(self.stderr_tail) > _PYTHON_SAMPLER_STDERR_TAIL_BYTES:
            del self.stderr_tail[:-_PYTHON_SAMPLER_STDERR_TAIL_BYTES]

    def _unregister(self, stream):
        try:
            self.selector.unregister(stream)
        except (KeyError, ValueError):
            pass

    def _consume_read(self, key):
        try:
            chunk = os.read(key.fileobj.fileno(), _PYTHON_SAMPLER_IO_CHUNK_BYTES)
        except BlockingIOError:
            return
        if key.data == "stderr":
            if chunk:
                self._append_stderr(chunk)
            else:
                self.stderr_open = False
                self._unregister(key.fileobj)
            return
        if chunk:
            self.stdout_buffer.extend(chunk)
            newline = self.stdout_buffer.find(b"\n")
            first_line_bytes = newline if newline >= 0 else len(self.stdout_buffer)
            if first_line_bytes > _PYTHON_SAMPLER_MAX_MESSAGE_BYTES:
                raise RuntimeError(
                    "Python Sampler runtime message exceeded "
                    f"{_PYTHON_SAMPLER_MAX_MESSAGE_BYTES} bytes."
                )
        else:
            self.stdout_open = False
            self._unregister(key.fileobj)

    def _select(self, deadline):
        while True:
            self._track_processes()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise RuntimeError("Python Sampler timed out waiting for a runtime response.")
            scan_wait = max(0.001, self.next_process_scan - time.monotonic())
            events = self.selector.select(timeout=min(remaining, scan_wait))
            if events:
                return events
            if time.monotonic() >= deadline:
                raise RuntimeError("Python Sampler timed out waiting for a runtime response.")
            if self.process.poll() is not None and not self.stdout_open:
                return ()

    def _write_request(self, encoded):
        deadline = time.monotonic() + _PYTHON_SAMPLER_RESPONSE_TIMEOUT_SECONDS
        offset = 0
        self.selector.register(self.process.stdin, selectors.EVENT_WRITE, "stdin")
        try:
            while offset < len(encoded):
                for key, _mask in self._select(deadline):
                    if key.data != "stdin":
                        self._consume_read(key)
                        continue
                    try:
                        written = os.write(
                            key.fileobj.fileno(), memoryview(encoded)[offset:]
                        )
                    except BlockingIOError:
                        continue
                    except BrokenPipeError as exc:
                        raise RuntimeError(
                            "Python Sampler closed stdin during initialization."
                        ) from exc
                    if written <= 0:
                        raise RuntimeError(
                            "Python Sampler closed stdin during initialization."
                        )
                    offset += written
        finally:
            self._unregister(self.process.stdin)
        self.process.stdin.close()

    def read_line(self):
        deadline = time.monotonic() + _PYTHON_SAMPLER_RESPONSE_TIMEOUT_SECONDS
        while True:
            newline = self.stdout_buffer.find(b"\n")
            if newline >= 0:
                if newline > _PYTHON_SAMPLER_MAX_MESSAGE_BYTES:
                    raise RuntimeError(
                        "Python Sampler runtime message exceeded "
                        f"{_PYTHON_SAMPLER_MAX_MESSAGE_BYTES} bytes."
                    )
                line = bytes(self.stdout_buffer[:newline])
                del self.stdout_buffer[: newline + 1]
                return line
            if len(self.stdout_buffer) > _PYTHON_SAMPLER_MAX_MESSAGE_BYTES:
                raise RuntimeError(
                    "Python Sampler runtime message exceeded "
                    f"{_PYTHON_SAMPLER_MAX_MESSAGE_BYTES} bytes."
                )
            if not self.stdout_open:
                if self.stdout_buffer:
                    raise RuntimeError(
                        "Python Sampler emitted an unterminated runtime message."
                    )
                return None
            events = self._select(deadline)
            for key, _mask in events:
                if key.data == "stdin":
                    continue
                self._consume_read(key)

    def wait(self):
        deadline = time.monotonic() + _PYTHON_SAMPLER_RESPONSE_TIMEOUT_SECONDS
        while self.process.poll() is None:
            events = self._select(deadline)
            for key, _mask in events:
                if key.data != "stdin":
                    self._consume_read(key)
        return self.process.returncode

    def close(self):
        with self._close_lock:
            if self._closed:
                return
            self._closed = True
        if self.process is None:
            return
        first_error = None
        termination_error = None
        try:
            process_supervision.refresh_process_tree(
                self.known_processes,
                self.process.pid,
                root_identity=self.root_identity,
                session_id=self.process.pid,
                process_group_id=self.process.pid,
            )
        except BaseException as exc:
            first_error = first_error or exc
        try:
            process_supervision.terminate_process_tree(
                self.process,
                self.known_processes,
                terminate_grace=_PYTHON_SAMPLER_TERMINATE_GRACE_SECONDS,
                kill_grace=_PYTHON_SAMPLER_TERMINATE_GRACE_SECONDS,
                owns_process_group=True,
                session_id=self.process.pid,
                process_group_id=self.process.pid,
            )
        except BaseException as exc:
            termination_error = exc
            first_error = first_error or exc
        if self.selector is not None:
            try:
                self.selector.close()
            except BaseException as exc:
                first_error = first_error or exc
        for stream in (self.process.stdin, self.process.stdout, self.process.stderr):
            if stream is not None:
                try:
                    stream.close()
                except BaseException as exc:
                    first_error = first_error or exc
        if termination_error is not None:
            with self._close_lock:
                self._closed = False
        if first_error is not None:
            raise first_error
