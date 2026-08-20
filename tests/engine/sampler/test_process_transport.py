#!/usr/bin/env python3

import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from engine.runtime import sampler_process

class PythonSamplerProcessTransportTests(unittest.TestCase):
    @staticmethod
    def _transport(source):
        return sampler_process.SamplerProcessTransport(
            [sys.executable, "-B", "-c", source], {"request": True}
        )

    def test_binary_transport_retains_only_a_bounded_stderr_tail(self):
        source = r'''
import os
import sys
sys.stdin.buffer.readline()
os.write(2, b"x" * (128 * 1024))
os.write(1, b'{"type":"complete"}\n')
'''
        with mock.patch.object(
            sampler_process, "_PYTHON_SAMPLER_STDERR_TAIL_BYTES", 1024
        ):
            transport = self._transport(source)
            try:
                self.assertEqual(transport.read_line(), b'{"type":"complete"}')
                self.assertIsNone(transport.read_line())
                self.assertEqual(transport.wait(), 0)
                self.assertEqual(len(transport.stderr_tail), 1024)
                self.assertEqual(transport.stderr_tail, b"x" * 1024)
            finally:
                transport.close()

    def test_only_the_fixed_bubblewrap_pid_namespace_defers_session_scans(self):
        for command, expected in (
            (["/usr/bin/bwrap", "--unshare-all", "--die-with-parent"], True),
            (["/usr/bin/bwrap", "--unshare-all"], False),
            ([sys.executable, "--unshare-all", "--die-with-parent"], False),
            (["/usr/bin/bwrap", "--unshare-all", "--die-with-parent", "--as-pid-1"], False),
        ):
            with self.subTest(command=command):
                self.assertEqual(
                    sampler_process._has_pid_namespace_containment(command),
                    expected,
                )


    def test_unterminated_stdout_is_rejected_at_the_single_message_limit(self):
        source = r'''
import os
import sys
sys.stdin.buffer.readline()
while True:
    os.write(1, b"x" * 4096)
'''
        with (
            mock.patch.object(
                sampler_process, "_PYTHON_SAMPLER_MAX_MESSAGE_BYTES", 1024
            ),
            mock.patch.object(
                sampler_process, "_PYTHON_SAMPLER_TERMINATE_GRACE_SECONDS", 0.05
            ),
        ):
            transport = self._transport(source)
            try:
                with self.assertRaisesRegex(RuntimeError, "exceeded 1024 bytes"):
                    transport.read_line()
            finally:
                transport.close()
        self.assertIsNotNone(transport.process.poll())

    def test_short_unterminated_stdout_is_rejected_at_eof(self):
        source = r'''
import os
import sys
sys.stdin.buffer.readline()
os.write(1, b'{"type":"complete"}')
'''
        transport = self._transport(source)
        try:
            with self.assertRaisesRegex(RuntimeError, "unterminated runtime message"):
                transport.read_line()
        finally:
            transport.close()

    def test_silent_worker_has_a_finite_response_deadline(self):
        source = r'''
import sys
import time
sys.stdin.buffer.readline()
while True:
    time.sleep(1)
'''
        with (
            mock.patch.object(
                sampler_process, "_PYTHON_SAMPLER_RESPONSE_TIMEOUT_SECONDS", 0.1
            ),
            mock.patch.object(
                sampler_process, "_PYTHON_SAMPLER_TERMINATE_GRACE_SECONDS", 0.05
            ),
        ):
            transport = self._transport(source)
            started = time.monotonic()
            try:
                with self.assertRaisesRegex(RuntimeError, "timed out"):
                    transport.read_line()
            finally:
                transport.close()
        self.assertLess(time.monotonic() - started, 1.0)
        self.assertIsNotNone(transport.process.poll())

    def test_initial_request_write_uses_the_same_finite_deadline(self):
        source = r'''
import time
while True:
    time.sleep(1)
'''
        with (
            mock.patch.object(
                sampler_process, "_PYTHON_SAMPLER_RESPONSE_TIMEOUT_SECONDS", 0.1
            ),
            mock.patch.object(
                sampler_process, "_PYTHON_SAMPLER_TERMINATE_GRACE_SECONDS", 0.05
            ),
        ):
            started = time.monotonic()
            with self.assertRaisesRegex(RuntimeError, "timed out"):
                sampler_process.SamplerProcessTransport(
                    [sys.executable, "-B", "-c", source],
                    {"payload": "x" * (4 * 1024 * 1024)},
                )
        self.assertLess(time.monotonic() - started, 1.0)

    def test_response_deadline_is_per_message_not_a_total_sampler_limit(self):
        source = r'''
import os
import sys
import time
sys.stdin.buffer.readline()
for sequence in range(3):
    time.sleep(0.4)
    os.write(1, ("message-%d\n" % sequence).encode())
'''
        with mock.patch.object(
            sampler_process, "_PYTHON_SAMPLER_RESPONSE_TIMEOUT_SECONDS", 1.0
        ):
            transport = self._transport(source)
            try:
                self.assertEqual(
                    [transport.read_line() for _ in range(3)],
                    [b"message-0", b"message-1", b"message-2"],
                )
                self.assertIsNone(transport.read_line())
                self.assertEqual(transport.wait(), 0)
            finally:
                transport.close()

    def test_unbounded_stderr_cannot_postpone_the_response_deadline(self):
        source = r'''
import os
import sys
sys.stdin.buffer.readline()
while True:
    os.write(2, b"diagnostic output\n" * 128)
'''
        with (
            mock.patch.object(
                sampler_process, "_PYTHON_SAMPLER_RESPONSE_TIMEOUT_SECONDS", 0.1
            ),
            mock.patch.object(
                sampler_process, "_PYTHON_SAMPLER_STDERR_TAIL_BYTES", 2048
            ),
            mock.patch.object(
                sampler_process, "_PYTHON_SAMPLER_TERMINATE_GRACE_SECONDS", 0.05
            ),
        ):
            transport = self._transport(source)
            started = time.monotonic()
            try:
                with self.assertRaisesRegex(RuntimeError, "timed out"):
                    transport.read_line()
                self.assertLessEqual(len(transport.stderr_tail), 2048)
            finally:
                transport.close()
        self.assertLess(time.monotonic() - started, 1.0)
        self.assertIsNotNone(transport.process.poll())

    def test_timeout_cleans_a_term_ignoring_descendant_process(self):
        with tempfile.TemporaryDirectory() as temporary:
            pid_path = Path(temporary) / "child.pid"
            source = r'''
import subprocess
import sys
import time
sys.stdin.buffer.readline()
child = subprocess.Popen([
    sys.executable,
    "-B",
    "-c",
    "import os,signal,sys,time; "
    "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
    "open(sys.argv[1], 'w').write(str(os.getpid())); "
    "time.sleep(30)",
    sys.argv[1],
])
while not __import__('os').path.exists(sys.argv[1]):
    time.sleep(0.01)
while True:
    time.sleep(1)
'''
            with (
                mock.patch.object(
                    sampler_process, "_PYTHON_SAMPLER_RESPONSE_TIMEOUT_SECONDS", 0.15
                ),
                mock.patch.object(
                    sampler_process, "_PYTHON_SAMPLER_PROCESS_SCAN_SECONDS", 0.01
                ),
                mock.patch.object(
                    sampler_process, "_PYTHON_SAMPLER_TERMINATE_GRACE_SECONDS", 0.05
                ),
            ):
                transport = sampler_process.SamplerProcessTransport(
                    [sys.executable, "-B", "-c", source, str(pid_path)],
                    {"request": True},
                )
                try:
                    with self.assertRaisesRegex(RuntimeError, "timed out"):
                        transport.read_line()
                finally:
                    transport.close()
            child_pid = int(pid_path.read_text(encoding="ascii"))
            child_proc = Path(f"/proc/{child_pid}")
            deadline = time.monotonic() + 1.0
            while child_proc.exists() and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertFalse(child_proc.exists())
            self.assertIsNotNone(transport.process.poll())

    def test_transport_close_is_idempotent(self):
        source = "import sys,time; sys.stdin.buffer.readline(); time.sleep(30)"
        transport = self._transport(source)
        terminate = sampler_process.process_supervision.terminate_process_tree
        with mock.patch.object(
            sampler_process.process_supervision,
            "terminate_process_tree",
            wraps=terminate,
        ) as terminate_tree:
            transport.close()
            transport.close()
        self.assertEqual(terminate_tree.call_count, 1)
