"""Regression tests for the shared supervised process-session owner."""

import fcntl
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

from engine.runtime import process_session
from engine.runtime import process_supervision


class PrimaryFailure(BaseException):
    pass


class CleanupFailure(BaseException):
    pass


class ProcessSessionTests(unittest.TestCase):
    def start_sleep(self, registry, key):
        return registry.start(
            key,
            [sys.executable, "-c", "import time; time.sleep(60)"],
            cwd="/",
            env=process_session.minimal_host_environment(),
            max_output_bytes=1024,
        )

    def cleanup(self, registry, key):
        if registry.get(key) is not None:
            registry.stop(key, terminate_grace=0.2, kill_grace=2.0)

    def test_tail_init_baseexception_retains_partial_authority_when_cleanup_unproven(self):
        registry = process_session.ProcessSessionRegistry()
        primary = PrimaryFailure("tail construction failed")
        cleanup = CleanupFailure("termination proof failed")
        key = "tail-failure:one"
        with (
            mock.patch.object(
                process_supervision,
                "BoundedStreamTail",
                side_effect=primary,
            ),
            mock.patch.object(
                process_session.ProcessSession,
                "terminate",
                side_effect=cleanup,
            ),
        ):
            with self.assertRaises(PrimaryFailure) as caught:
                self.start_sleep(registry, key)
        self.assertIs(caught.exception, primary)
        self.assertIs(caught.exception.__cause__, cleanup)
        session = registry.get(key)
        self.assertIsNotNone(session)
        self.assertTrue(process_supervision.identity_can_run(session.root_identity))
        self.cleanup(registry, key)

    def test_popen_init_baseexception_retains_partial_authority(self):
        registry = process_session.ProcessSessionRegistry()
        primary = PrimaryFailure("popen init interrupted")
        cleanup = CleanupFailure("termination proof failed")
        real_popen = subprocess.Popen

        class RaisingPopen(real_popen):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                raise primary

        key = "popen-failure:one"
        with (
            mock.patch.object(process_session.subprocess, "Popen", RaisingPopen),
            mock.patch.object(
                process_session.ProcessSession,
                "terminate",
                side_effect=cleanup,
            ),
        ):
            with self.assertRaises(PrimaryFailure) as caught:
                self.start_sleep(registry, key)
        self.assertIs(caught.exception, primary)
        self.assertIs(caught.exception.__cause__, cleanup)
        self.assertIsNotNone(registry.get(key))
        self.cleanup(registry, key)

    def test_stop_signal_failure_with_live_survivor_retains_authority(self):
        registry = process_session.ProcessSessionRegistry()
        key = "signal-failure:one"
        session = self.start_sleep(registry, key)
        failure = CleanupFailure("pidfd signal failed")
        with mock.patch.object(session, "terminate", side_effect=failure):
            with self.assertRaises(CleanupFailure) as caught:
                registry.stop(key)
        self.assertIs(caught.exception, failure)
        self.assertIs(registry.get(key), session)
        self.assertTrue(process_supervision.identity_can_run(session.root_identity))
        self.cleanup(registry, key)

    def test_stop_retry_proof_pops_authority_but_preserves_first_error(self):
        registry = process_session.ProcessSessionRegistry()
        failure = CleanupFailure("transient signal driver failure")

        class RetrySession:
            def __init__(self):
                self.calls = 0
                self.closed = False

            def terminate(self, **_options):
                self.calls += 1
                if self.calls == 1:
                    raise failure

            def close(self):
                self.closed = True

        session = RetrySession()
        registry._sessions["retry-proof:one"] = session
        with self.assertRaises(CleanupFailure) as caught:
            registry.stop("retry-proof:one")
        self.assertIs(caught.exception, failure)
        self.assertEqual(session.calls, 2)
        self.assertTrue(session.closed)
        self.assertIsNone(registry.get("retry-proof:one"))

    def test_shutdown_is_atomic_with_popen_to_registration(self):
        registry = process_session.ProcessSessionRegistry()
        entered = threading.Event()
        release = threading.Event()
        real_popen = subprocess.Popen

        class BarrierPopen(real_popen):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                entered.set()
                release.wait(timeout=5)

        errors = []
        with mock.patch.object(process_session.subprocess, "Popen", BarrierPopen):
            starter = threading.Thread(
                target=lambda: self._capture(
                    errors,
                    lambda: self.start_sleep(registry, "atomic:one"),
                )
            )
            stopper = threading.Thread(
                target=lambda: self._capture(
                    errors,
                    lambda: registry.shutdown("atomic:"),
                )
            )
            starter.start()
            self.assertTrue(entered.wait(timeout=5))
            stopper.start()
            time.sleep(0.05)
            self.assertTrue(stopper.is_alive())
            release.set()
            starter.join(timeout=10)
            stopper.join(timeout=10)
        self.assertFalse(starter.is_alive())
        self.assertFalse(stopper.is_alive())
        self.assertFalse(errors)
        self.assertEqual(registry.snapshot("atomic:"), {})

    def test_shutdown_attempts_every_session_and_retains_only_unproven_authority(self):
        registry = process_session.ProcessSessionRegistry()
        failure = CleanupFailure("first session cannot prove termination")

        class FakeSession:
            def __init__(self, error=None):
                self.error = error
                self.terminate_calls = 0
                self.closed = False

            def terminate(self, **_options):
                self.terminate_calls += 1
                if self.error is not None:
                    raise self.error

            def close(self):
                self.closed = True

        unproven = FakeSession(failure)
        healthy = FakeSession()
        registry._sessions = {
            "all-attempt:unproven": unproven,
            "all-attempt:healthy": healthy,
        }
        with self.assertRaises(CleanupFailure) as caught:
            registry.shutdown("all-attempt:")
        self.assertIs(caught.exception, failure)
        self.assertEqual(unproven.terminate_calls, 2)
        self.assertEqual(healthy.terminate_calls, 1)
        self.assertFalse(unproven.closed)
        self.assertTrue(healthy.closed)
        self.assertIs(registry.get("all-attempt:unproven"), unproven)
        self.assertIsNone(registry.get("all-attempt:healthy"))

    def test_process_survives_the_short_lived_calling_thread(self):
        registry = process_session.ProcessSessionRegistry()
        sessions = []
        errors = []
        caller = threading.Thread(
            target=lambda: self._capture(
                errors,
                lambda: sessions.append(
                    self.start_sleep(registry, "caller-thread:one")
                ),
            )
        )
        caller.start()
        caller.join(timeout=5)
        self.assertFalse(caller.is_alive())
        self.assertFalse(errors)
        self.assertEqual(len(sessions), 1)
        time.sleep(0.2)
        self.assertIsNone(sessions[0].poll())
        registry.stop("caller-thread:one")

    @staticmethod
    def _capture(errors, callback):
        try:
            callback()
        except BaseException as exc:
            errors.append(exc)

    def test_late_fork_into_new_session_is_gone_before_stop_returns(self):
        registry = process_session.ProcessSessionRegistry()
        with tempfile.TemporaryDirectory() as temporary:
            child_pid_path = Path(temporary) / "late.pid"
            child_code = (
                "import signal,time;"
                "signal.signal(signal.SIGTERM,signal.SIG_IGN);"
                "time.sleep(60)"
            )
            primary_code = (
                "import os,pathlib,signal,subprocess,sys,time\n"
                f"child_code={child_code!r}\n"
                f"pid_path={str(child_pid_path)!r}\n"
                "def stop(*_args):\n"
                " child=subprocess.Popen([sys.executable,'-c',child_code],"
                "start_new_session=True)\n"
                " pathlib.Path(pid_path).write_text(str(child.pid))\n"
                " os._exit(0)\n"
                "signal.signal(signal.SIGTERM,stop)\n"
                "time.sleep(60)\n"
            )
            key = "late-fork:one"
            registry.start(
                key,
                [sys.executable, "-c", primary_code],
                cwd=temporary,
                env=process_session.minimal_host_environment(),
                max_output_bytes=1024,
            )
            time.sleep(0.1)
            registry.stop(key, terminate_grace=0.5, kill_grace=3.0)
            self.assertTrue(child_pid_path.is_file())
            child_identity = process_supervision.process_identity(
                int(child_pid_path.read_text())
            )
            self.assertFalse(process_supervision.identity_can_run(child_identity))
            self.assertIsNone(registry.get(key))

    def test_persist_failure_does_not_stop_stream_drain(self):
        registry = process_session.ProcessSessionRegistry()
        with tempfile.TemporaryDirectory() as temporary:
            log_path = Path(temporary) / "tail.log"
            real_replace = os.replace

            def fail_log_replace(source, target):
                if Path(target) == log_path:
                    raise OSError("diagnostic disk failure")
                return real_replace(source, target)

            with mock.patch.object(
                process_supervision.os,
                "replace",
                side_effect=fail_log_replace,
            ):
                session = registry.start(
                    "persist-failure:one",
                    [
                        sys.executable,
                        "-c",
                        "import sys;sys.stdout.write('x'*4096+'END')",
                    ],
                    cwd=temporary,
                    env=process_session.minimal_host_environment(),
                    max_output_bytes=1024,
                    stdout_path=log_path,
                    merge_stderr=True,
                )
                self.assertEqual(session.wait(timeout=5), 0)
                registry.finish("persist-failure:one", session)
            self.assertTrue(session.stdout_text().endswith("END"))
            self.assertIsNotNone(session.stdout_tail.persist_error)

    def test_minimal_environment_rejects_loader_and_secret_fields(self):
        self.assertEqual(
            process_session.minimal_host_environment()["PYTHONNOUSERSITE"],
            "1",
        )
        with self.assertRaisesRegex(ValueError, "Unsafe"):
            process_session.minimal_host_environment(extra={"LD_PRELOAD": "x"})
        with self.assertRaisesRegex(ValueError, "Unsafe"):
            process_session.minimal_host_environment(extra={"ENGINE_TOKEN": "x"})

    def test_supervisor_fd_configuration_is_fstat_bound_and_cannot_change_active(self):
        registry = process_session.ProcessSessionRegistry()
        with tempfile.TemporaryDirectory() as temporary:
            lock_path = Path(temporary) / "lease.lock"
            handle = lock_path.open("a+", encoding="utf-8")
            descriptor = handle.fileno()
            registry.configure_inherited_supervisor_fds((descriptor,))
            inherited = registry.inherited_supervisor_fds()
            self.assertEqual(len(inherited), 1)
            self.assertGreaterEqual(inherited[0], 3)
            self.assertNotEqual(inherited[0], descriptor)
            self.assertEqual(
                os.fstat(inherited[0]).st_ino,
                os.fstat(descriptor).st_ino,
            )
            session = self.start_sleep(registry, "configured:one")
            with self.assertRaisesRegex(RuntimeError, "cannot change"):
                registry.configure_inherited_supervisor_fds((descriptor,))
            with self.assertRaisesRegex(RuntimeError, "cannot change"):
                registry.clear_inherited_supervisor_fds()
            registry.stop("configured:one")
            registry.clear_inherited_supervisor_fds()
            handle.close()
            with self.assertRaises(OSError):
                registry.configure_inherited_supervisor_fds((descriptor,))
            self.assertIsNotNone(session.root_identity)

    def test_outer_inherits_configured_fd_but_primary_closes_it(self):
        registry = process_session.ProcessSessionRegistry()
        with tempfile.TemporaryDirectory() as temporary:
            lock_path = Path(temporary) / "lease.lock"
            result_path = Path(temporary) / "primary-fd.json"
            release_path = Path(temporary) / "release"
            handle = lock_path.open("a+", encoding="utf-8")
            source_descriptor = handle.fileno()
            expected = os.fstat(source_descriptor)
            registry.configure_inherited_supervisor_fds((source_descriptor,))
            descriptor = registry.inherited_supervisor_fds()[0]
            code = (
                "import json,os,pathlib,sys,time\n"
                "fd=int(sys.argv[1]); dev=int(sys.argv[2]); ino=int(sys.argv[3])\n"
                "try:\n"
                " value=os.fstat(fd); inherited=(value.st_dev,value.st_ino)==(dev,ino)\n"
                "except OSError:\n"
                " inherited=False\n"
                "pathlib.Path(sys.argv[4]).write_text(json.dumps(inherited))\n"
                "deadline=time.monotonic()+5\n"
                "while not pathlib.Path(sys.argv[5]).exists() and time.monotonic()<deadline:\n"
                " time.sleep(0.01)\n"
            )
            key = "configured:primary-isolation"
            session = registry.start(
                key,
                [
                    sys.executable,
                    "-c",
                    code,
                    str(descriptor),
                    str(expected.st_dev),
                    str(expected.st_ino),
                    str(result_path),
                    str(release_path),
                ],
                cwd=temporary,
                env=process_session.minimal_host_environment(home=temporary),
                max_output_bytes=1024,
            )
            deadline = time.monotonic() + 5
            while not result_path.is_file() and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertTrue(result_path.is_file())
            outer_stat = os.stat(
                f"/proc/{session.process.pid}/fd/{descriptor}"
            )
            self.assertEqual(
                (outer_stat.st_dev, outer_stat.st_ino),
                (expected.st_dev, expected.st_ino),
            )
            self.assertEqual(result_path.read_text(encoding="utf-8"), "false")
            release_path.touch()
            self.assertEqual(session.wait(timeout=5), 0)
            registry.finish(key, session)
            registry.clear_inherited_supervisor_fds()
            handle.close()

    def test_owned_duplicate_survives_same_inode_same_number_source_reuse(self):
        registry = process_session.ProcessSessionRegistry()
        with tempfile.TemporaryDirectory() as temporary:
            lock_path = Path(temporary) / "lease.lock"
            first = lock_path.open("a+", encoding="utf-8")
            fcntl.flock(first.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            descriptor = first.fileno()
            registry.configure_inherited_supervisor_fds((descriptor,))
            owned_descriptor = registry.inherited_supervisor_fds()[0]
            expected_inode = os.fstat(owned_descriptor).st_ino
            first.close()
            replacement = lock_path.open("a+", encoding="utf-8")
            self.assertEqual(replacement.fileno(), descriptor)
            self.assertEqual(os.fstat(replacement.fileno()).st_ino, expected_inode)
            with self.assertRaises(BlockingIOError):
                fcntl.flock(
                    replacement.fileno(),
                    fcntl.LOCK_EX | fcntl.LOCK_NB,
                )
            session = registry.start(
                "configured:reused",
                [sys.executable, "-c", "pass"],
                cwd=temporary,
                env=process_session.minimal_host_environment(home=temporary),
                max_output_bytes=1024,
            )
            self.assertEqual(session.wait(timeout=5), 0)
            registry.finish("configured:reused", session)
            self.assertEqual(registry.snapshot(), {})
            with self.assertRaises(BlockingIOError):
                fcntl.flock(
                    replacement.fileno(),
                    fcntl.LOCK_EX | fcntl.LOCK_NB,
                )
            registry.clear_inherited_supervisor_fds()
            fcntl.flock(
                replacement.fileno(),
                fcntl.LOCK_EX | fcntl.LOCK_NB,
            )
            replacement.close()

    def test_kernel_child_proof_blocks_exit_when_proc_discovery_is_empty(self):
        with tempfile.TemporaryDirectory() as temporary:
            child_ready = Path(temporary) / "child-ready"
            child_code = (
                "import pathlib,sys,time;"
                "pathlib.Path(sys.argv[1]).write_text('ready');"
                "time.sleep(0.6)"
            )
            primary_code = (
                "import subprocess,sys;"
                f"code={child_code!r};"
                "subprocess.Popen([sys.executable,'-c',code,sys.argv[1]],"
                "start_new_session=True,close_fds=True)"
            )
            supervisor_code = (
                "import os,sys;"
                "from engine.runtime import process_session,process_supervision;"
                "process_supervision.discover_process_tree=lambda _pid:{};"
                f"command={[sys.executable, '-c', primary_code, str(child_ready)]!r};"
                "raise SystemExit(process_session._run_supervisor(os.getppid(),command))"
            )
            supervisor = subprocess.Popen(
                [sys.executable, "-c", supervisor_code],
                cwd=Path(__file__).resolve().parents[3],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                close_fds=True,
            )
            try:
                deadline = time.monotonic() + 5
                while not child_ready.is_file() and time.monotonic() < deadline:
                    time.sleep(0.01)
                self.assertTrue(child_ready.is_file())
                time.sleep(0.1)
                self.assertIsNone(supervisor.poll())
                self.assertEqual(
                    supervisor.wait(timeout=5),
                    process_supervision.SUPERVISOR_FORCED_DESCENDANT_EXIT_CODE,
                )
            finally:
                if supervisor.poll() is None:
                    supervisor.terminate()
                    supervisor.wait(timeout=5)
                supervisor.stdout.close()
                supervisor.stderr.close()


if __name__ == "__main__":
    unittest.main()
