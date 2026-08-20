"""Exclusive control-store ownership tests."""

import json
import http.client
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from engine.core.build_identity import build_id, source_manifest
from engine.control.owner import (
    assert_control_access,
    claim_control_owner,
    delegated_control_child_environment,
)
from engine.runtime import process_session
from engine.runtime import process_supervision


PROJECT_ROOT = Path(__file__).resolve().parents[3]


class EngineOwnerTests(unittest.TestCase):
    def test_build_identity_covers_runtime_packages_without_a_file_allowlist(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "strategy_devkit").mkdir()
            (root / "builtin_implementations").mkdir()
            (root / "dataset_adapters").mkdir()
            (root / "engine").mkdir()
            (root / "engine.py").write_text("VALUE = 1\n", encoding="utf-8")
            sdk_contract = root / "strategy_devkit" / "module_contract.py"
            sdk_contract.write_text("VALUE = 1\n", encoding="utf-8")
            packaged_runtime = root / "engine" / "runtime.py"
            packaged_runtime.write_text("VALUE = 1\n", encoding="utf-8")
            dataset_adapter = root / "dataset_adapters" / "records.py"
            dataset_adapter.write_text("VALUE = 1\n", encoding="utf-8")
            test_source = root / "test_engine.py"
            test_source.write_text("VALUE = 1\n", encoding="utf-8")

            original = build_id(root)
            sdk_contract.write_text("VALUE = 2\n", encoding="utf-8")
            self.assertNotEqual(build_id(root), original)

            updated = build_id(root)
            packaged_runtime.write_text("VALUE = 2\n", encoding="utf-8")
            self.assertNotEqual(build_id(root), updated)

            updated = build_id(root)
            dataset_adapter.write_text("VALUE = 2\n", encoding="utf-8")
            self.assertNotEqual(build_id(root), updated)

            updated = build_id(root)
            test_source.write_text("VALUE = 2\n", encoding="utf-8")
            self.assertNotEqual(build_id(root), updated)

            manifest = source_manifest(root)
            self.assertEqual(
                [entry["path"] for entry in manifest["files"]],
                [
                    "engine.py",
                    "test_engine.py",
                    "dataset_adapters/records.py",
                    "engine/runtime.py",
                    "strategy_devkit/module_contract.py",
                ],
            )
            self.assertEqual(manifest["fileCount"], 5)
            self.assertEqual(manifest["engineBuildId"], build_id(root))
            self.assertRegex(manifest["sha256"], r"^sha256:[0-9a-f]{64}$")

    def test_running_service_excludes_direct_control_access_from_other_processes(self):
        with tempfile.TemporaryDirectory() as temporary:
            config = {"controlRoot": str(Path(temporary) / "control")}
            lease = claim_control_owner(config)
            try:
                assert_control_access(config)
                command = (
                    "import json,sys; "
                    "from engine.control.owner import assert_control_access; "
                    "assert_control_access(json.loads(sys.argv[1]))"
                )
                denied = subprocess.run(
                    [sys.executable, "-c", command, json.dumps(config)],
                    cwd=PROJECT_ROOT,
                    text=True,
                    capture_output=True,
                    check=False,
                )
                self.assertNotEqual(denied.returncode, 0)
                self.assertIn("owned by the running Engine service", denied.stderr)
            finally:
                lease.close()

            allowed = subprocess.run(
                [sys.executable, "-c", command, json.dumps(config)],
                cwd=PROJECT_ROOT,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(allowed.returncode, 0, allowed.stderr)

    def test_owner_can_delegate_only_to_its_direct_disposable_child(self):
        with tempfile.TemporaryDirectory() as temporary:
            config = {"controlRoot": str(Path(temporary) / "control")}
            lease = claim_control_owner(config)
            try:
                command = (
                    "import json,sys; "
                    "from engine.control.owner import assert_control_access; "
                    "assert_control_access(json.loads(sys.argv[1]))"
                )
                delegated = subprocess.run(
                    [sys.executable, "-c", command, json.dumps(config)],
                    cwd=PROJECT_ROOT,
                    env=delegated_control_child_environment(config),
                    text=True,
                    capture_output=True,
                    check=False,
                )
                self.assertEqual(delegated.returncode, 0, delegated.stderr)

                nested_command = (
                    "import json,subprocess,sys; "
                    "code='from engine.control.owner import assert_control_access; "
                    "import json,sys; assert_control_access(json.loads(sys.argv[1]))'; "
                    "result=subprocess.run([sys.executable,'-c',code,sys.argv[1]]); "
                    "raise SystemExit(0 if result.returncode != 0 else 1)"
                )
                nested = subprocess.run(
                    [sys.executable, "-c", nested_command, json.dumps(config)],
                    cwd=PROJECT_ROOT,
                    env=delegated_control_child_environment(config),
                    text=True,
                    capture_output=True,
                    check=False,
                )
                self.assertEqual(nested.returncode, 0, nested.stderr)
            finally:
                lease.close()

    def test_outer_supervisor_copy_blocks_claim_after_explicit_parent_close(self):
        with tempfile.TemporaryDirectory() as temporary:
            config = {"controlRoot": str(Path(temporary) / "control")}
            registry = process_session.ProcessSessionRegistry()
            lease = claim_control_owner(config)
            session = None
            replacement = None
            try:
                descriptors = lease.child_pass_fds()
                registry.configure_inherited_supervisor_fds(descriptors)
                session = registry.start(
                    "owner-barrier:explicit-close",
                    [sys.executable, "-c", "import time;time.sleep(60)"],
                    cwd=temporary,
                    env=process_session.minimal_host_environment(home=temporary),
                    max_output_bytes=1024,
                )
                with mock.patch("engine.control.owner.fcntl.flock") as flock:
                    lease.close()
                flock.assert_not_called()
                with self.assertRaisesRegex(RuntimeError, "already owns"):
                    claim_control_owner(config)
                (Path(config["controlRoot"]) / ".engine-owner.json").write_text(
                    json.dumps({
                        "schemaVersion": 1,
                        "pid": os.getpid(),
                        "buildId": "stale-reused-pid",
                        "token": "not-this-process-active-token",
                    }),
                    encoding="utf-8",
                )
                with self.assertRaisesRegex(RuntimeError, "owned by"):
                    assert_control_access(config)
                with self.assertRaisesRegex(RuntimeError, "active TradeEngine owner"):
                    delegated_control_child_environment(config)
                with self.assertRaisesRegex(RuntimeError, "closed"):
                    lease.child_pass_fds()
                registry.stop(
                    "owner-barrier:explicit-close",
                    terminate_grace=0.2,
                    kill_grace=2.0,
                )
                session = None
                registry.clear_inherited_supervisor_fds()
                replacement = claim_control_owner(config)
            finally:
                if session is not None and registry.get(
                    "owner-barrier:explicit-close"
                ) is not None:
                    registry.stop(
                        "owner-barrier:explicit-close",
                        terminate_grace=0.2,
                        kill_grace=2.0,
                    )
                if not registry.snapshot():
                    registry.clear_inherited_supervisor_fds()
                if replacement is not None:
                    replacement.close()
                if not lease.closed:
                    lease.close()

    def test_sigkill_owner_keeps_lock_until_late_setsid_writer_is_quiescent(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = {"controlRoot": str(root / "control")}
            driver_ready = root / "driver-ready.json"
            primary_ready = root / "primary-ready"
            late_pid_path = root / "late-writer.pid"
            marker_path = root / "writer.marker"
            primary_code = r'''
import pathlib
import signal
import subprocess
import sys
import time

marker = pathlib.Path(sys.argv[1])
late_pid = pathlib.Path(sys.argv[2])
ready = pathlib.Path(sys.argv[3])
spawned = False

def stop(*_args):
    global spawned
    if not spawned:
        spawned = True
        marker.write_text("p", encoding="ascii")
        child_code = (
            "import pathlib,signal,sys,time;"
            "signal.signal(signal.SIGTERM,signal.SIG_IGN);"
            "path=pathlib.Path(sys.argv[1]);"
            "path.write_text('x',encoding='ascii');"
            "\nwhile True:\n"
            " path.open('a',encoding='ascii').write('x')\n"
            " time.sleep(0.01)\n"
        )
        child = subprocess.Popen(
            [sys.executable, "-c", child_code, str(marker)],
            start_new_session=True,
            close_fds=True,
        )
        late_pid.write_text(str(child.pid), encoding="ascii")
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    while True:
        time.sleep(0.05)

signal.signal(signal.SIGTERM, stop)
ready.write_text("ready", encoding="ascii")
while True:
    time.sleep(0.05)
'''
            driver_code = r'''
import json
import pathlib
import sys
import time
from engine.control.owner import claim_control_owner
from engine.runtime.process_session import (
    ProcessSessionRegistry,
    minimal_host_environment,
)

config = json.loads(sys.argv[1])
driver_ready = pathlib.Path(sys.argv[2])
primary_ready = pathlib.Path(sys.argv[3])
primary_code = sys.argv[4]
lease = claim_control_owner(config)
registry = ProcessSessionRegistry()
registry.configure_inherited_supervisor_fds(lease.child_pass_fds())
session = registry.start(
    "owner-barrier:crash",
    [sys.executable, "-c", primary_code, sys.argv[5], sys.argv[6], sys.argv[3]],
    cwd=sys.argv[7],
    env=minimal_host_environment(home=sys.argv[7]),
    max_output_bytes=4096,
)
deadline = time.monotonic() + 5
while not primary_ready.is_file() and time.monotonic() < deadline:
    time.sleep(0.01)
if not primary_ready.is_file():
    raise RuntimeError("primary did not become ready")
driver_ready.write_text(
    json.dumps({"outerPid": session.process.pid}),
    encoding="utf-8",
)
while True:
    time.sleep(60)
'''
            owner = subprocess.Popen(
                [
                    sys.executable,
                    "-c",
                    driver_code,
                    json.dumps(config),
                    str(driver_ready),
                    str(primary_ready),
                    primary_code,
                    str(marker_path),
                    str(late_pid_path),
                    str(PROJECT_ROOT),
                ],
                cwd=PROJECT_ROOT,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                close_fds=True,
            )
            outer_identity = None
            outer_stopped = False
            acquired = None
            try:
                deadline = time.monotonic() + 10
                while not driver_ready.is_file() and time.monotonic() < deadline:
                    if owner.poll() is not None:
                        _stdout, stderr = owner.communicate()
                        self.fail(f"owner driver exited early: {stderr}")
                    time.sleep(0.01)
                self.assertTrue(driver_ready.is_file())
                outer_pid = json.loads(
                    driver_ready.read_text(encoding="utf-8")
                )["outerPid"]
                outer_identity = process_supervision.process_identity(outer_pid)
                self.assertTrue(
                    process_supervision.identity_can_run(outer_identity)
                )

                # Freeze the old outer authority across the owner crash so
                # both post-SIGKILL claim checks are deterministic even on a
                # heavily loaded test host.  SIGCONT then lets the pending
                # PDEATHSIG drive the real late-fork cleanup path.
                os.kill(outer_pid, signal.SIGSTOP)
                outer_stopped = True
                os.kill(owner.pid, signal.SIGKILL)
                with self.assertRaisesRegex(RuntimeError, "already owns"):
                    claim_control_owner(config)
                os.kill(outer_pid, signal.SIGCONT)
                outer_stopped = False
                owner.wait(timeout=5)
                with self.assertRaisesRegex(RuntimeError, "already owns"):
                    claim_control_owner(config)

                deadline = time.monotonic() + 5
                while not late_pid_path.is_file() and time.monotonic() < deadline:
                    time.sleep(0.01)
                self.assertTrue(late_pid_path.is_file())
                deadline = time.monotonic() + 10
                while acquired is None and time.monotonic() < deadline:
                    try:
                        acquired = claim_control_owner(config)
                    except RuntimeError:
                        time.sleep(0.02)
                self.assertIsNotNone(acquired)
                self.assertFalse(
                    process_supervision.identity_can_run(outer_identity)
                )
                self.assertTrue(marker_path.is_file())
                marker_size = marker_path.stat().st_size
                time.sleep(0.2)
                self.assertEqual(marker_path.stat().st_size, marker_size)
                marker_path.unlink()
                time.sleep(0.1)
                self.assertFalse(marker_path.exists())
            finally:
                if acquired is not None:
                    acquired.close()
                if owner.poll() is None:
                    os.kill(owner.pid, signal.SIGKILL)
                    owner.wait(timeout=5)
                owner.communicate(timeout=1)
                if (
                    outer_identity is not None
                    and process_supervision.identity_can_run(outer_identity)
                ):
                    if outer_stopped:
                        try:
                            os.kill(outer_identity[0], signal.SIGCONT)
                        except ProcessLookupError:
                            pass
                    try:
                        os.kill(outer_identity[0], signal.SIGTERM)
                    except ProcessLookupError:
                        pass
                    deadline = time.monotonic() + 5
                    while (
                        process_supervision.identity_can_run(outer_identity)
                        and time.monotonic() < deadline
                    ):
                        time.sleep(0.02)

    def test_service_shutdown_joins_slow_handler_before_owner_release(self):
        import engine_service

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = {"controlRoot": str(root / "control")}
            marker = root / "handler-write"
            entered = threading.Event()
            release = threading.Event()

            class SlowHandler(BaseHTTPRequestHandler):
                def do_GET(self):
                    entered.set()
                    release.wait(timeout=10)
                    marker.write_text("complete", encoding="utf-8")
                    self.send_response(200)
                    self.end_headers()

                def log_message(self, _format, *_args):
                    return

            lease = claim_control_owner(config)
            process_session.PROCESS_SESSIONS.configure_inherited_supervisor_fds(
                lease.child_pass_fds()
            )
            server = engine_service.EngineThreadingHTTPServer(
                ("127.0.0.1", 0), SlowHandler
            )
            server_thread = threading.Thread(target=server.serve_forever)
            request_errors = []

            def request():
                connection = http.client.HTTPConnection(
                    "127.0.0.1", server.server_address[1], timeout=10
                )
                try:
                    connection.request("GET", "/slow")
                    response = connection.getresponse()
                    response.read()
                except BaseException as exc:
                    request_errors.append(exc)
                finally:
                    connection.close()

            server_thread.start()
            request_thread = threading.Thread(target=request)
            request_thread.start()
            self.assertTrue(entered.wait(5))
            shutdown_errors = []

            def shutdown():
                try:
                    engine_service._shutdown_engine_service(
                        server,
                        None,
                        lease,
                        registry_configured=True,
                    )
                except BaseException as exc:
                    shutdown_errors.append(exc)

            replacement = None
            with (
                mock.patch.object(
                    engine_service.result_runtime,
                    "shutdown_result_runtimes",
                ),
                mock.patch.object(
                    engine_service.dataset_build_runtime,
                    "shutdown_build_processes",
                ),
                mock.patch.object(
                    engine_service.jupyter_workspaces,
                    "shutdown_managed_process",
                ),
            ):
                shutdown_thread = threading.Thread(target=shutdown)
                shutdown_thread.start()
                deadline = time.monotonic() + 5
                while (
                    not engine_service.EngineServiceHandler.stopping.is_set()
                    and time.monotonic() < deadline
                ):
                    time.sleep(0.01)
                self.assertTrue(shutdown_thread.is_alive())
                with self.assertRaisesRegex(RuntimeError, "already owns"):
                    claim_control_owner(config)
                release.set()
                shutdown_thread.join(timeout=10)
                self.assertFalse(shutdown_thread.is_alive())
                self.assertEqual(shutdown_errors, [])
                replacement = claim_control_owner(config)
            request_thread.join(timeout=5)
            server_thread.join(timeout=5)
            self.assertFalse(request_thread.is_alive())
            self.assertFalse(server_thread.is_alive())
            self.assertEqual(request_errors, [])
            self.assertTrue(marker.is_file())
            if replacement is not None:
                replacement.close()
            engine_service.EngineServiceHandler.stopping.clear()

    def test_post_claim_startup_failure_clears_registry_and_owner_lease(self):
        import engine_service

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = {
                "liveRoot": str(root / "live"),
                "releaseRoot": str(root / "releases"),
                "controlRoot": str(root / "control"),
                "allowInsecureAuth": True,
            }
            config_path = root / "config.json"
            config_path.write_text(json.dumps(config), encoding="utf-8")
            failure = RuntimeError("startup reconciliation failed")
            args = SimpleNamespace(
                config=str(config_path),
                host="127.0.0.1",
                port=0,
                public_url="http://127.0.0.1",
            )
            with (
                mock.patch.object(
                    engine_service.version_archive,
                    "reconcile_staging_directories",
                    side_effect=failure,
                ),
                self.assertRaises(RuntimeError) as raised,
            ):
                engine_service._run_engine_service(args)
            self.assertIs(raised.exception, failure)
            self.assertEqual(
                process_session.PROCESS_SESSIONS.inherited_supervisor_fds(),
                (),
            )
            replacement = claim_control_owner(config)
            replacement.close()
            engine_service.EngineServiceHandler.stopping.clear()

    def test_failed_handler_join_retains_owner_until_shutdown_retry(self):
        import engine_service

        class FakeServer:
            engine_serve_started = False

            def __init__(self):
                self.error = RuntimeError("handler join remains unproven")

            def server_close(self):
                if self.error is not None:
                    raise self.error

        with tempfile.TemporaryDirectory() as temporary:
            config = {"controlRoot": str(Path(temporary) / "control")}
            lease = claim_control_owner(config)
            process_session.PROCESS_SESSIONS.configure_inherited_supervisor_fds(
                lease.child_pass_fds()
            )
            server = FakeServer()
            with (
                mock.patch.object(
                    engine_service.result_runtime,
                    "shutdown_result_runtimes",
                ),
                mock.patch.object(
                    engine_service.dataset_build_runtime,
                    "shutdown_build_processes",
                ),
                mock.patch.object(
                    engine_service.jupyter_workspaces,
                    "shutdown_managed_process",
                ),
            ):
                with self.assertRaisesRegex(RuntimeError, "join remains unproven"):
                    engine_service._shutdown_engine_service(
                        server,
                        None,
                        lease,
                        registry_configured=True,
                    )
                self.assertFalse(lease.closed)
                self.assertNotEqual(
                    process_session.PROCESS_SESSIONS.inherited_supervisor_fds(),
                    (),
                )
                with self.assertRaisesRegex(RuntimeError, "already owns"):
                    claim_control_owner(config)
                server.error = None
                engine_service._shutdown_engine_service(
                    server,
                    None,
                    lease,
                    registry_configured=True,
                )
            self.assertTrue(lease.closed)
            self.assertEqual(
                process_session.PROCESS_SESSIONS.inherited_supervisor_fds(),
                (),
            )
            replacement = claim_control_owner(config)
            replacement.close()
            with engine_service._RETAINED_SERVICE_LIFECYCLES_LOCK:
                self.assertEqual(
                    engine_service._RETAINED_SERVICE_LIFECYCLES,
                    {},
                )
            engine_service.EngineServiceHandler.stopping.clear()

    def test_failed_manager_shutdown_retains_owner_until_shutdown_retry(self):
        import engine_service

        class FakeManager:
            def __init__(self):
                self.error = RuntimeError("manager quiescence remains unproven")

            def shutdown(self):
                if self.error is not None:
                    raise self.error

        with tempfile.TemporaryDirectory() as temporary:
            config = {"controlRoot": str(Path(temporary) / "control")}
            lease = claim_control_owner(config)
            process_session.PROCESS_SESSIONS.configure_inherited_supervisor_fds(
                lease.child_pass_fds()
            )
            manager = FakeManager()
            with (
                mock.patch.object(
                    engine_service.result_runtime,
                    "shutdown_result_runtimes",
                ),
                mock.patch.object(
                    engine_service.dataset_build_runtime,
                    "shutdown_build_processes",
                ),
                mock.patch.object(
                    engine_service.jupyter_workspaces,
                    "shutdown_managed_process",
                ),
            ):
                with self.assertRaisesRegex(RuntimeError, "remains unproven"):
                    engine_service._shutdown_engine_service(
                        None,
                        manager,
                        lease,
                        registry_configured=True,
                    )
                self.assertFalse(lease.closed)
                self.assertEqual(
                    process_session.PROCESS_SESSIONS.snapshot(),
                    {},
                )
                self.assertNotEqual(
                    process_session.PROCESS_SESSIONS.inherited_supervisor_fds(),
                    (),
                )
                with self.assertRaisesRegex(RuntimeError, "already owns"):
                    claim_control_owner(config)
                manager.error = None
                engine_service._shutdown_engine_service(
                    None,
                    manager,
                    lease,
                    registry_configured=True,
                )
            self.assertTrue(lease.closed)
            self.assertEqual(
                process_session.PROCESS_SESSIONS.inherited_supervisor_fds(),
                (),
            )
            replacement = claim_control_owner(config)
            replacement.close()
            with engine_service._RETAINED_SERVICE_LIFECYCLES_LOCK:
                self.assertEqual(
                    engine_service._RETAINED_SERVICE_LIFECYCLES,
                    {},
                )
            engine_service.EngineServiceHandler.stopping.clear()


if __name__ == "__main__":
    unittest.main()
