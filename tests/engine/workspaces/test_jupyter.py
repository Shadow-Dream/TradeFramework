import http.client
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from email.message import Message
from types import SimpleNamespace
from pathlib import Path
from unittest import mock

from engine.contracts import workspace as workspace_contract
from engine.repository import workspace_files
from engine.repository import workspace_paths
from engine.runtime import jupyter_workspace
from engine.runtime import process_session
from engine.service import jupyter_proxy
from engine.service import jupyter_workspaces

class JupyterLogRotationTests(unittest.TestCase):
    def test_python_user_nested_symlink_is_rejected_without_external_write(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = {
                "controlRoot": str(root / "control"),
                "releaseRoot": str(root / "releases"),
            }
            instance = workspace_files.instance_runtime_root(config, "symlinked")
            python_user = instance / "python-user"
            python_user.mkdir()
            outside = root / "outside"
            outside.mkdir()
            (python_user / "lib").symlink_to(outside, target_is_directory=True)
            with self.assertRaisesRegex(ValueError, "real directories"):
                workspace_files.prepare_instance_storage(config, "symlinked")
            self.assertEqual(tuple(outside.iterdir()), ())

    def test_runtime_cleanup_rejects_symlinked_instances_without_external_delete(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = {
                "controlRoot": str(root / "control"),
                "releaseRoot": str(root / "releases"),
            }
            runtime = workspace_files.runtime_root(config)
            outside = root / "outside"
            victim = outside / workspace_contract.workspace_slug("victim", "dataset")
            victim.mkdir(parents=True)
            evidence = victim / "keep.txt"
            evidence.write_text("keep", encoding="utf-8")
            (runtime / "instances").symlink_to(outside, target_is_directory=True)
            with self.assertRaisesRegex(ValueError, "symbolic link"):
                workspace_files.discard_workspace_runtime(
                    config, "victim", "dataset"
                )
            self.assertEqual(evidence.read_text(encoding="utf-8"), "keep")

    def test_private_token_creation_is_atomic_across_callers(self):
        with tempfile.TemporaryDirectory() as temporary:
            config = {
                "controlRoot": str(Path(temporary) / "control"),
                "releaseRoot": str(Path(temporary) / "releases"),
            }
            barrier = threading.Barrier(12)
            tokens = []
            errors = []

            def create():
                try:
                    barrier.wait()
                    tokens.append(
                        workspace_files.load_or_create_token(config, "atomic")
                    )
                except BaseException as exc:
                    errors.append(exc)

            threads = [threading.Thread(target=create) for _ in range(12)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=10)
            self.assertFalse(errors)
            self.assertEqual(len(set(tokens)), 1)
            token = workspace_files.token_path(config, "atomic")
            self.assertEqual(token.stat().st_mode & 0o777, 0o600)

    def test_workspace_environment_does_not_inherit_engine_secrets(self):
        with tempfile.TemporaryDirectory() as temporary, mock.patch.dict(
            os.environ,
            {"ENGINE_DEPLOYMENT_SECRET": "must-not-cross-boundary"},
        ):
            config = {
                "controlRoot": str(Path(temporary) / "control"),
                "releaseRoot": str(Path(temporary) / "releases"),
            }
            storage = workspace_files.prepare_instance_storage(config, "isolated")
            environment = jupyter_workspace.process_environment(storage)
            self.assertNotIn("ENGINE_DEPLOYMENT_SECRET", environment)
            self.assertNotIn(str(Path.home()), environment["PYTHONPATH"])

    def test_prebuilt_ui_sync_extension_is_installed_into_private_jupyter_data(self):
        with tempfile.TemporaryDirectory() as temporary:
            config = {
                "controlRoot": str(Path(temporary) / "control"),
                "releaseRoot": str(Path(temporary) / "releases"),
            }
            storage = workspace_files.prepare_instance_storage(config, "ui-sync")
            target = workspace_files.install_ui_sync_labextension(storage)
            manifest = json.loads((target / "package.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["name"], "@trade-engine/jupyter-ui-sync")
            remote = manifest["jupyterlab"]["_build"]["load"]
            self.assertTrue((target / remote).is_file())
            stale = target / "stale.js"
            stale.write_text("stale", encoding="utf-8")
            self.assertEqual(workspace_files.install_ui_sync_labextension(storage), target)
            self.assertFalse(stale.exists())

    def test_status_uses_a_stable_process_registry_snapshot(self):
        with tempfile.TemporaryDirectory() as temporary:
            config = {
                "controlRoot": str(Path(temporary) / "control"),
                "releaseRoot": str(Path(temporary) / "releases"),
            }
            first = {"baseUrl": "/first/"}
            second = {"baseUrl": "/second/"}
            records = {
                "first": {"state": "running", "instance": first, "session": object()},
                "second": {"state": "running", "instance": second, "session": object()},
            }
            with jupyter_workspaces._state_lock:
                prior = jupyter_workspaces._instances
                jupyter_workspaces._instances = records

            def health(instance, _session):
                if instance is first:
                    with jupyter_workspaces._state_lock:
                        jupyter_workspaces._instances.pop("second", None)
                return True

            try:
                with mock.patch.object(
                    jupyter_workspace, "workspace_health", side_effect=health
                ):
                    state = jupyter_workspaces.status(config)
                self.assertEqual(state["activeWorkspaceServers"], 2)
            finally:
                with jupyter_workspaces._state_lock:
                    jupyter_workspaces._instances = prior

    def test_http_request_body_is_streamed_with_exact_framing(self):
        headers = Message()
        headers["Content-Length"] = str(2 * 1024 * 1024)
        payload = b"x" * (2 * 1024 * 1024)
        body, length = jupyter_proxy._request_body(
            SimpleNamespace(headers=headers, rfile=io.BytesIO(payload))
        )
        self.assertEqual(length, len(payload))
        self.assertIsInstance(body, jupyter_proxy._FixedLengthBody)
        self.assertEqual(len(body.read(64 * 1024)), 64 * 1024)
        self.assertGreater(body.remaining, 0)

        chunked = Message()
        chunked["Transfer-Encoding"] = "chunked"
        with self.assertRaisesRegex(ValueError, "Transfer-Encoding"):
            jupyter_proxy._request_body(
                SimpleNamespace(headers=chunked, rfile=io.BytesIO(b""))
            )

    def test_proxy_has_no_global_jupyter_fallback(self):
        with self.assertRaisesRegex(ValueError, "Unknown or expired"):
            jupyter_workspaces.resolve_proxy_instance("/jupyter/lab")

    def test_rotation_bounds_log_history_without_runtime_configuration(self):
        with tempfile.TemporaryDirectory() as temporary:
            log = Path(temporary) / "jupyter.log"
            log.write_bytes(b"x" * (workspace_files.MAX_JUPYTER_LOG_BYTES + 1))
            for index in range(1, workspace_files.JUPYTER_LOG_BACKUPS + 1):
                log.with_name(f"{log.name}.{index}").write_text(
                    str(index), encoding="utf-8"
                )

            workspace_files.rotate_jupyter_log(log)

            self.assertFalse(log.exists())
            self.assertEqual(
                log.with_name(f"{log.name}.1").stat().st_size,
                workspace_files.MAX_JUPYTER_LOG_BYTES + 1,
            )
            self.assertEqual(log.with_name(f"{log.name}.2").read_text(), "1")
            self.assertEqual(log.with_name(f"{log.name}.3").read_text(), "2")

    def test_live_process_output_is_continuously_bounded(self):
        with tempfile.TemporaryDirectory() as temporary:
            log = Path(temporary) / "jupyter.log"
            session = process_session.PROCESS_SESSIONS.start(
                "test-jupyter:bounded",
                [sys.executable, "-c", "import sys;sys.stdout.write('x'*4096)"],
                cwd=temporary,
                env=process_session.minimal_host_environment(),
                max_output_bytes=1024,
                stdout_path=log,
                merge_stderr=True,
            )
            self.assertEqual(session.wait(timeout=10), 0)
            process_session.PROCESS_SESSIONS.finish(
                "test-jupyter:bounded", session
            )
            self.assertLessEqual(log.stat().st_size, 1024)
            self.assertEqual(log.read_bytes(), b"x" * 1024)

    def test_stop_cancels_start_during_lock_free_health_wait(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = {
                "controlRoot": str(root / "control"),
                "releaseRoot": str(root / "releases"),
            }
            workspace = workspace_paths.dataset_workspace_root(config) / "barrier"
            workspace.mkdir()
            health_entered = threading.Event()
            release_health = threading.Event()
            errors = []

            def start_process(instance, _storage, _log, *, max_log_bytes):
                return process_session.PROCESS_SESSIONS.start(
                    f"jupyter:{instance['slug']}",
                    [sys.executable, "-c", "import time;time.sleep(60)"],
                    cwd=workspace,
                    env=process_session.minimal_host_environment(),
                    max_output_bytes=max_log_bytes,
                    merge_stderr=True,
                )

            def wait_for_health(_instance, _session, timeout=1.0):
                health_entered.set()
                release_health.wait(timeout=5)
                return True

            def start():
                try:
                    jupyter_workspaces.ensure_workspace_running(
                        config, "barrier", "dataset", timeout=5
                    )
                except BaseException as exc:
                    errors.append(exc)

            with (
                mock.patch.object(
                    jupyter_workspace,
                    "start_workspace_process",
                    side_effect=start_process,
                ),
                mock.patch.object(
                    jupyter_workspace,
                    "workspace_health",
                    side_effect=wait_for_health,
                ),
            ):
                starter = threading.Thread(target=start)
                starter.start()
                self.assertTrue(health_entered.wait(timeout=5))
                acquired = jupyter_workspaces._state_lock.acquire(timeout=1)
                self.assertTrue(acquired)
                if acquired:
                    jupyter_workspaces._state_lock.release()
                stopper = threading.Thread(
                    target=lambda: jupyter_workspaces.stop_workspace_server(
                        "barrier", "dataset"
                    )
                )
                stopper.start()
                stopper.join(timeout=5)
                self.assertFalse(stopper.is_alive())
                release_health.set()
                starter.join(timeout=5)
            self.assertFalse(starter.is_alive())
            self.assertTrue(errors)
            slug = workspace_contract.workspace_slug("barrier", "dataset")
            self.assertIsNone(
                process_session.PROCESS_SESSIONS.get(f"jupyter:{slug}")
            )
            with jupyter_workspaces._state_lock:
                self.assertNotIn(slug, jupyter_workspaces._instances)


@unittest.skipUnless(jupyter_workspace.is_installed() and shutil.which("bwrap"), "JupyterLab/bubblewrap unavailable")
class JupyterWorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.config = {
            "controlRoot": str(root / "control"),
            "releaseRoot": str(root / "releases"),
        }

    def tearDown(self):
        jupyter_workspaces.stop_managed_process()
        self.temp.cleanup()

    def test_managed_jupyter_starts_with_private_token_and_positive_mounts(self):
        workspace = workspace_paths.dataset_workspace_root(self.config) / "managed"
        workspace.mkdir(parents=True)
        instance = jupyter_workspaces.ensure_workspace_running(
            self.config, "managed", "dataset"
        )
        result = jupyter_workspaces.status(self.config)
        self.assertTrue(result["running"])
        self.assertEqual(result["authentication"], "jupyter-token")

        connection = http.client.HTTPConnection(instance["host"], instance["port"], timeout=5)
        connection.request(
            "GET",
            instance["baseUrl"] + "api/status",
            headers={"Authorization": f"token {instance['token']}"},
        )
        response = connection.getresponse()
        payload = json.loads(response.read())
        connection.close()

        self.assertEqual(response.status, 200)
        self.assertEqual(payload["kernels"], 0)
        storage = workspace_files.prepare_instance_storage(
            self.config, instance["slug"]
        )
        command = jupyter_workspace.build_workspace_command(instance, storage)
        self.assertNotIn(["--ro-bind", "/", "/"], [command[index:index + 3] for index in range(len(command) - 2)])
        self.assertIn("--unshare-pid", command)
        self.assertNotIn("--unshare-all", command)
        self.assertIn(jupyter_workspace.ISOLATED_WORKSPACE_ROOT, command)
        self.assertNotIn(instance["token"], " ".join(command))

    def test_workspace_sessions_are_isolated_and_pip_installs_to_writable_user_base(self):
        dataset_workspace = workspace_paths.dataset_workspace_root(self.config) / "research"
        module_workspace = workspace_paths.module_workspace_root(self.config) / "signal-one"
        sampler_workspace = workspace_paths.sampler_workspace_root(self.config) / "row-map-one"
        dataset_workspace.mkdir(parents=True)
        module_workspace.mkdir(parents=True)
        sampler_workspace.mkdir(parents=True)
        (sampler_workspace / ".sampler-workspace.json").write_text(
            "{}\n", encoding="utf-8"
        )
        (sampler_workspace / "sampler.json").write_text("{}\n", encoding="utf-8")
        (sampler_workspace / "SAMPLER_VERSION.md").write_text(
            "fixture\n", encoding="utf-8"
        )
        dataset_url = jupyter_workspaces.workspace_url(
            self.config, "https://trade.example", "research", "dataset"
        )
        module_url = jupyter_workspaces.workspace_url(
            self.config, "https://trade.example", "signal-one", "module"
        )
        sampler_url = jupyter_workspaces.workspace_url(
            self.config, "https://trade.example", "row-map-one", "sampler"
        )
        self.assertIn("/jupyter/w/dataset-research-", dataset_url)
        self.assertTrue(dataset_url.endswith("/lab?reset"))
        self.assertIn("/jupyter/w/module-signal-one-", module_url)
        self.assertTrue(module_url.endswith("/lab?reset"))
        self.assertIn("/jupyter/w/sampler-row-map-one-", sampler_url)
        self.assertTrue(sampler_url.endswith("/lab?reset"))
        self.assertNotIn("/tree/Datasets", dataset_url)
        self.assertNotIn("/tree/Modules", module_url)
        self.assertNotEqual(dataset_url, module_url)
        self.assertNotEqual(module_url, sampler_url)

        fixture = workspace_paths.dataset_workspace_root(self.config) / "install-fixture"
        package = fixture / "trade_workspace_install_probe"
        package.mkdir(parents=True)
        (fixture / "setup.py").write_text(
            "from setuptools import setup\nsetup(name='trade-workspace-install-probe', version='0.0.1', packages=['trade_workspace_install_probe'])\n",
            encoding="utf-8",
        )
        (package / "__init__.py").write_text("VALUE = 'installed'\n", encoding="utf-8")
        script = (
            "import subprocess,sys; "
            "subprocess.check_call([sys.executable,'-m','pip','install','--no-deps','/tmp/workspace']); "
            "import trade_workspace_install_probe as probe; "
            "assert probe.VALUE == 'installed'"
        )
        install_storage = workspace_files.prepare_instance_storage(
            self.config, "install-fixture"
        )
        completed = subprocess.run(
            [*jupyter_workspace.sandbox_prefix(fixture, install_storage), sys.executable, "-c", script],
            env=jupyter_workspace.process_environment(install_storage),
            text=True,
            capture_output=True,
            timeout=60,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertTrue(any(
            path.name == "trade_workspace_install_probe"
            for path in workspace_files.python_user_site(self.config, "install-fixture").iterdir()
        ))

        executable_root = workspace_paths.module_workspace_root(self.config) / "execute-probe"
        executable_root.mkdir(parents=True)
        executable = executable_root / "run.py"
        executable.write_text("#!/usr/bin/env python3\nprint('workspace-executable-ok')\n", encoding="utf-8")
        executable.chmod(0o755)
        execute_storage = workspace_files.prepare_instance_storage(
            self.config, "execute-probe"
        )
        executed = subprocess.run(
            [
                *jupyter_workspace.sandbox_prefix(executable_root, execute_storage),
                "/tmp/workspace/run.py",
            ],
            env=jupyter_workspace.process_environment(execute_storage),
            text=True,
            capture_output=True,
            timeout=20,
        )
        self.assertEqual(executed.returncode, 0, executed.stderr)
        self.assertEqual(executed.stdout.strip(), "workspace-executable-ok")

    def test_isolated_workspace_contents_cannot_browse_siblings_and_can_delete_owned_files(self):
        datasets = workspace_paths.dataset_workspace_root(self.config)
        current = datasets / "current"
        sibling = datasets / "sibling"
        current.mkdir(parents=True)
        sibling.mkdir()
        (current / "deletable.txt").write_text("delete me", encoding="utf-8")
        (sibling / "secret.txt").write_text("not visible", encoding="utf-8")
        source = Path(self.config["releaseRoot"]) / "_data" / "source" / "container"
        source.mkdir(parents=True)
        (source / "source.txt").write_text("selected", encoding="utf-8")
        (current / "dataset1").symlink_to(source, target_is_directory=True)
        control_secret = Path(self.config["controlRoot"]) / "secret.txt"
        control_secret.parent.mkdir(parents=True, exist_ok=True)
        control_secret.write_text("not visible", encoding="utf-8")

        instance = jupyter_workspaces.ensure_workspace_running(
            self.config,
            "current",
            "dataset",
            read_only_roots=[source],
        )
        token = instance["token"]
        connection = http.client.HTTPConnection(instance["host"], instance["port"], timeout=10)
        connection.request(
            "GET", instance["baseUrl"] + "api/contents",
            headers={"Authorization": f"token {token}"},
        )
        response = connection.getresponse()
        listing = json.loads(response.read())
        self.assertEqual(response.status, 200)
        self.assertEqual(
            {item["name"] for item in listing["content"]},
            {"dataset1", "deletable.txt"},
        )

        connection.request(
            "DELETE", instance["baseUrl"] + "api/contents/deletable.txt",
            headers={"Authorization": f"token {token}"},
        )
        response = connection.getresponse()
        delete_body = response.read().decode("utf-8", errors="replace")
        connection.close()
        self.assertIn(response.status, {204, 200}, delete_body)
        self.assertFalse((current / "deletable.txt").exists())
        self.assertTrue((sibling / "secret.txt").exists())
        isolation_storage = workspace_files.prepare_instance_storage(
            self.config, "isolation-probe"
        )
        isolated = subprocess.run(
            [
                *jupyter_workspace.sandbox_prefix(
                    current,
                    isolation_storage,
                    (source,),
                ),
                sys.executable,
                "-c",
                (
                    "from pathlib import Path; "
                    f"assert not Path({str(datasets)!r}).exists(); "
                    f"assert not Path({str(control_secret)!r}).exists(); "
                    "assert Path('/tmp/workspace/dataset1/source.txt').read_text() == 'selected'; "
                    "probe=Path('/tmp/workspace/probe.txt'); probe.write_text('ok'); probe.unlink()"
                ),
            ],
            env=jupyter_workspace.process_environment(isolation_storage),
            text=True,
            capture_output=True,
            timeout=20,
        )
        self.assertEqual(isolated.returncode, 0, isolated.stderr)

    def test_workspace_permission_repair_skips_read_only_source_symlink(self):
        workspace = workspace_paths.dataset_workspace_root(self.config) / "permissions"
        source = Path(self.temp.name) / "source"
        workspace.mkdir(parents=True)
        source.mkdir()
        source_file = source / "source.txt"
        source_file.write_text("immutable", encoding="utf-8")
        source_file.chmod(0o444)
        (workspace / "dataset1").symlink_to(source, target_is_directory=True)
        owned = workspace / "owned.txt"
        owned.write_text("writable", encoding="utf-8")
        owned.chmod(0o444)

        jupyter_workspaces.ensure_workspace_writable(workspace)

        owned.write_text("updated", encoding="utf-8")
        owned.unlink()
        self.assertFalse(owned.exists())
        self.assertEqual(source_file.stat().st_mode & 0o777, 0o444)


if __name__ == "__main__":
    unittest.main()
