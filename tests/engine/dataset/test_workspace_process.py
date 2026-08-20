"""Dataset Workspace process isolation and build publication tests."""

import json
import os
import sqlite3
import stat
import subprocess
import sys
import threading
import time
from pathlib import Path
from unittest import mock

from engine.contracts import dataset_workspace as workspace_contracts
from engine.repository import dataset_recipes
from engine.repository import dataset_build_jobs
from engine.repository import dataset_workspaces as workspace_repository
from engine.runtime import dataset_build as build_runtime
from engine.service import dataset_builds
from engine.control import database as engine_database
from engine.repository import datasets
from engine.runtime import process_session
from engine.runtime import process_supervision
from tests.support.dataset_workspace import DatasetWorkspaceTestCase


class DatasetWorkspaceProcessTests(DatasetWorkspaceTestCase):
    @staticmethod
    def _successful_fake_execution(
        execution_workspace, _script_path, _arguments, _bindings, _timeout_seconds
    ):
        Path(execution_workspace, "output.txt").write_text("ok", encoding="utf-8")
        return "stdout", ""

    def test_committed_build_cleanup_failure_returns_success_and_retries(self):
        workspace = self.create_workspace("committed-cleanup-pending")
        recipe = self.archive_script(
            "from pathlib import Path\nPath('output.txt').write_text('ok')\n"
        )

        with (
            mock.patch.object(
                dataset_builds,
                "execute_in_sandbox",
                side_effect=self._successful_fake_execution,
            ),
            mock.patch.object(
                dataset_builds,
                "discard_execution_workspace",
                return_value=False,
            ),
            self.assertLogs(
                "engine.service.dataset_builds", level="WARNING"
            ) as logs,
        ):
            result = dataset_builds.submit_build(self.config, {
                "workspaceId": workspace["workspaceId"],
                "outputDatasetId": "committed-cleanup-pending-output",
                "recipeId": recipe["recipeId"],
                "recipeVersion": recipe["version"],
            })

        self.assertEqual(result["job"]["status"], "completed")
        self.assertEqual(result["dataset"]["status"], "active")
        self.assertTrue(any("cleanup pending" in line for line in logs.output))
        scratch = (
            dataset_build_jobs.job_root(self.config)
            / result["job"]["jobId"]
        )
        self.assertTrue((scratch / "workspace").is_dir())

        dataset_builds.reconcile_terminal_build_workspaces(self.config)
        self.assertFalse(scratch.exists())

    def test_dataset_process_logs_are_bounded_and_preserve_the_tail(self):
        workspace = Path(self.temp.name) / "bounded-log-workspace"
        workspace.mkdir()
        return_code, stdout, stderr = build_runtime._run_bounded_process(
            [
                sys.executable,
                "-c",
                (
                    "import sys; "
                    f"sys.stdout.write('o' * {build_runtime.MAX_LOG_CHARS + 50_000} + 'OUT-END'); "
                    f"sys.stderr.write('e' * {build_runtime.MAX_LOG_CHARS + 50_000} + 'ERR-END')"
                ),
            ],
            workspace,
            5,
        )
        self.assertEqual(return_code, 0)
        self.assertLessEqual(len(stdout), build_runtime.MAX_LOG_CHARS)
        self.assertLessEqual(len(stderr), build_runtime.MAX_LOG_CHARS)
        self.assertTrue(stdout.endswith("OUT-END"))
        self.assertTrue(stderr.endswith("ERR-END"))

    def test_dataset_host_process_environment_does_not_inherit_engine_secrets(self):
        workspace = Path(self.temp.name) / "environment-workspace"
        workspace.mkdir()
        output = workspace / "environment.json"
        script = (
            "import json,os,pathlib;"
            f"pathlib.Path({str(output)!r}).write_text(json.dumps(dict(os.environ)))"
        )
        with mock.patch.dict(
            os.environ,
            {
                "ENGINE_DEPLOYMENT_SECRET": "must-not-leak",
                "LD_LIBRARY_PATH": "/must/not/leak",
            },
        ):
            return_code, _stdout, _stderr = build_runtime._run_bounded_process(
                [sys.executable, "-c", script], workspace, 5
            )
        self.assertEqual(return_code, 0)
        environment = json.loads(output.read_text(encoding="utf-8"))
        self.assertNotIn("ENGINE_DEPLOYMENT_SECRET", environment)
        self.assertNotIn("LD_LIBRARY_PATH", environment)

    def test_dataset_process_timeout_kills_term_ignoring_descendant_tree(self):
        workspace = Path(self.temp.name) / "process-tree-workspace"
        workspace.mkdir()
        child_pid_file = workspace / "child.pid"
        child_code = (
            "import signal,time; "
            "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
            "time.sleep(60)"
        )
        parent_code = (
            "import pathlib,subprocess,sys,time; "
            f"child=subprocess.Popen([sys.executable, '-c', {child_code!r}], start_new_session=True); "
            f"pathlib.Path({str(child_pid_file)!r}).write_text(str(child.pid)); "
            "time.sleep(60)"
        )
        started = time.monotonic()
        with self.assertRaises(subprocess.TimeoutExpired):
            build_runtime._run_bounded_process(
                [sys.executable, "-c", parent_code], workspace, 1
            )
        self.assertLess(time.monotonic() - started, 3)
        child_pid = int(child_pid_file.read_text(encoding="utf-8"))
        cleanup_deadline = time.monotonic() + 2
        child_identity = process_supervision.process_identity(child_pid)
        while process_supervision.identity_can_run(child_identity) and time.monotonic() < cleanup_deadline:
            time.sleep(0.02)
        try:
            self.assertFalse(process_supervision.identity_can_run(child_identity))
        finally:
            if process_supervision.identity_can_run(child_identity):
                os.kill(child_pid, 9)

    def test_timeout_late_setsid_writer_is_gone_before_terminalization(self):
        workspace = self.create_workspace("late-writer")
        recipe = self.archive_script("from pathlib import Path\nPath('unused').touch()\n")
        completion_calls = []
        publish_calls = []
        terminal_checks = []
        real_mark_failed = dataset_builds._mark_build_failed
        real_discard = dataset_builds.discard_execution_workspace

        def execute_late_writer(
            execution_workspace,
            _script_path,
            _arguments,
            _bindings,
            _timeout_seconds,
        ):
            child_code = (
                "import signal,time;"
                "signal.signal(signal.SIGTERM,signal.SIG_IGN);"
                "time.sleep(60)"
            )
            primary_code = (
                "import os,pathlib,signal,subprocess,sys,time\n"
                f"child_code={child_code!r}\n"
                "def stop(*_args):\n"
                " child=subprocess.Popen([sys.executable,'-c',child_code],"
                "start_new_session=True)\n"
                " pathlib.Path('late-host.pid').write_text(str(child.pid))\n"
                " os._exit(0)\n"
                "signal.signal(signal.SIGTERM,stop)\n"
                "time.sleep(60)\n"
            )
            return build_runtime._run_bounded_process(
                [sys.executable, "-c", primary_code],
                execution_workspace,
                0.3,
            )

        def assert_writer_gone(config, job_id, *args):
            evidence = dataset_build_jobs.job_root(config) / job_id / "workspace" / "late-host.pid"
            self.assertTrue(evidence.is_file())
            identity = process_supervision.process_identity(
                int(evidence.read_text(encoding="utf-8"))
            )
            self.assertFalse(process_supervision.identity_can_run(identity))
            terminal_checks.append(job_id)
            return real_mark_failed(config, job_id, *args)

        def discard_after_proof(config, job_id, execution_workspace):
            self.assertTrue(terminal_checks)
            return real_discard(config, job_id, execution_workspace)

        with (
            mock.patch.object(
                dataset_builds,
                "execute_in_sandbox",
                side_effect=execute_late_writer,
            ),
            mock.patch.object(
                dataset_builds,
                "write_build_completion_evidence",
                side_effect=lambda *args: completion_calls.append(args),
            ),
            mock.patch.object(
                dataset_builds,
                "publish_dataset",
                side_effect=lambda *args: publish_calls.append(args),
            ),
            mock.patch.object(
                dataset_builds,
                "_mark_build_failed",
                side_effect=assert_writer_gone,
            ),
            mock.patch.object(
                dataset_builds,
                "discard_execution_workspace",
                side_effect=discard_after_proof,
            ),
        ):
            with self.assertRaises(subprocess.TimeoutExpired):
                dataset_builds.submit_build(
                    self.config,
                    {
                        "workspaceId": workspace["workspaceId"],
                        "outputDatasetId": "late-writer-output",
                        "recipeId": recipe["recipeId"],
                        "recipeVersion": recipe["version"],
                        "timeoutSeconds": 1,
                    },
                )
        self.assertEqual(completion_calls, [])
        self.assertEqual(publish_calls, [])
        self.assertEqual(len(terminal_checks), 1)

    def test_unproven_process_authority_preserves_running_job_and_scratch(self):
        workspace = self.create_workspace("unproven-writer")
        recipe = self.archive_script("from pathlib import Path\nPath('unused').touch()\n")
        key = "dataset:test-unproven-writer"

        def retain_writer(
            execution_workspace,
            _script_path,
            _arguments,
            _bindings,
            _timeout_seconds,
        ):
            process_session.PROCESS_SESSIONS.start(
                key,
                [sys.executable, "-c", "import time;time.sleep(60)"],
                cwd=execution_workspace,
                env=process_session.minimal_host_environment(),
                max_output_bytes=1024,
                metadata={
                    "executionWorkspace": str(Path(execution_workspace).resolve())
                },
            )
            raise RuntimeError("termination remains unproven")

        with (
            mock.patch.object(
                dataset_builds,
                "execute_in_sandbox",
                side_effect=retain_writer,
            ),
            mock.patch.object(dataset_builds, "_mark_build_failed") as mark_failed,
            mock.patch.object(
                dataset_builds, "discard_execution_workspace"
            ) as discard,
            mock.patch.object(
                dataset_builds, "write_build_completion_evidence"
            ) as completion,
            mock.patch.object(dataset_builds, "publish_dataset") as publish,
        ):
            with self.assertRaisesRegex(RuntimeError, "unproven"):
                dataset_builds.submit_build(
                    self.config,
                    {
                        "workspaceId": workspace["workspaceId"],
                        "outputDatasetId": "unproven-output",
                        "recipeId": recipe["recipeId"],
                        "recipeVersion": recipe["version"],
                    },
                )
        try:
            jobs = dataset_build_jobs.list_build_jobs(self.config)
            self.assertEqual(len(jobs), 1)
            self.assertEqual(jobs[0]["status"], "running")
            scratch = dataset_build_jobs.job_root(self.config) / jobs[0]["jobId"]
            self.assertTrue((scratch / "workspace").is_dir())
            mark_failed.assert_not_called()
            discard.assert_not_called()
            completion.assert_not_called()
            publish.assert_not_called()
        finally:
            process_session.PROCESS_SESSIONS.stop(key)

    def test_durable_submission_with_lost_commit_ack_uses_exact_receipt(self):
        workspace = self.create_workspace("late-submission-ack")
        recipe = self.archive_script("raise AssertionError('execution is injected')\n")
        original_connect = engine_database.connect_database
        ack_lost = []
        scratch_seen_after_ack = []

        class ConnectionProxy:
            def __init__(self, connection):
                self.connection = connection
                self.receipt_inserted = False
            def __enter__(self): return self
            def __exit__(self, kind, value, traceback):
                return self.connection.__exit__(kind, value, traceback)
            def __getattr__(self, name): return getattr(self.connection, name)
            def execute(self, sql, parameters=()):
                if "INSERT INTO dataset_build_submission_receipts" in sql:
                    self.receipt_inserted = True
                return self.connection.execute(sql, parameters)
            def commit(self):
                self.connection.commit()
                if self.receipt_inserted and not ack_lost:
                    ack_lost.append(True)
                    raise RuntimeError("driver lost durable submission ACK")

        def execute_after_reconciliation(
            execution_workspace, *execution_arguments
        ):
            self.assertTrue(Path(execution_workspace).is_dir())
            scratch_seen_after_ack.append(True)
            return self._successful_fake_execution(
                execution_workspace, *execution_arguments
            )

        with (
            mock.patch.object(
                engine_database, "connect_database",
                side_effect=lambda config: ConnectionProxy(original_connect(config)),
            ),
            mock.patch.object(
                dataset_builds, "execute_in_sandbox",
                side_effect=execute_after_reconciliation,
            ),
        ):
            result = dataset_builds.submit_build(self.config, {
                "workspaceId": workspace["workspaceId"],
                "outputDatasetId": "late-submission-output",
                "recipeId": recipe["recipeId"],
                "recipeVersion": recipe["version"],
                "jobId": "late-submission-job",
            })

        self.assertTrue(ack_lost)
        self.assertTrue(scratch_seen_after_ack)
        self.assertEqual(result["job"]["status"], "completed")
        with original_connect(self.config) as connection:
            receipt = connection.execute(
                "SELECT job_id, submission_digest FROM dataset_build_submission_receipts"
            ).fetchone()
        self.assertEqual(receipt["job_id"], "late-submission-job")
        self.assertRegex(receipt["submission_digest"], r"^sha256:[0-9a-f]{64}$")
        self.assertFalse(
            (dataset_build_jobs.job_root(self.config) / "late-submission-job").exists()
        )

    def test_concurrent_workspace_submission_has_one_authoritative_winner(self):
        workspace = self.create_workspace("concurrent-submit")
        recipe = self.archive_script("raise AssertionError('execution is injected')\n")
        rendezvous = threading.Barrier(2)
        real_commit = dataset_builds.build_jobs.commit_build_submission

        def concurrent_commit(*args, **kwargs):
            rendezvous.wait(timeout=5)
            return real_commit(*args, **kwargs)

        outcomes = []

        def submit(index):
            try:
                result = dataset_builds.submit_build(self.config, {
                    "workspaceId": workspace["workspaceId"],
                    "outputDatasetId": f"concurrent-output-{index}",
                    "recipeId": recipe["recipeId"],
                    "recipeVersion": recipe["version"],
                    "jobId": f"concurrent-job-{index}",
                })
                outcomes.append(("ok", result["job"]["jobId"]))
            except BaseException as exc:
                outcomes.append(("error", str(exc)))

        with (
            mock.patch.object(
                dataset_builds.build_jobs, "commit_build_submission",
                side_effect=concurrent_commit,
            ),
            mock.patch.object(
                dataset_builds, "execute_in_sandbox",
                side_effect=self._successful_fake_execution,
            ),
        ):
            threads = [threading.Thread(target=submit, args=(index,)) for index in (1, 2)]
            for thread in threads: thread.start()
            for thread in threads: thread.join(timeout=10)

        self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertEqual([kind for kind, _ in outcomes].count("ok"), 1)
        self.assertEqual([kind for kind, _ in outcomes].count("error"), 1)
        jobs = dataset_build_jobs.list_build_jobs(self.config)
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["status"], "completed")
        authoritative = workspace_repository.get_workspace(
            self.config, workspace["workspaceId"]
        )
        self.assertEqual(authoritative["status"], "published")
        self.assertEqual(authoritative["submittedJobId"], jobs[0]["jobId"])

    def test_process_cleanup_preserves_primary_error_identity(self):
        primary = RuntimeError("supervision-primary")
        cleanup = ValueError("workspace-cleanup-secondary")
        with (
            mock.patch.object(dataset_builds, "submit_build", side_effect=primary),
            mock.patch.object(dataset_builds, "delete_workspace", side_effect=cleanup),
        ):
            with self.assertRaisesRegex(RuntimeError, "supervision-primary") as raised:
                dataset_builds.process_recipe(self.config, {
                    "recipeId": "unused", "recipeVersion": "1",
                    "datasetIds": ["hourly-source"],
                })
        self.assertIs(raised.exception, primary)
        self.assertIs(raised.exception.__cause__, cleanup)

    def test_dataset_process_enforces_workspace_aggregate_limits_during_execution(self):
        workspace = Path(self.temp.name) / "quota-workspace"
        workspace.mkdir()
        script = (
            "from pathlib import Path\n"
            "import time\n"
            "for index in range(3):\n"
            "    Path(f'output-{index}.txt').write_text('x')\n"
            "time.sleep(60)\n"
        )
        started = time.monotonic()
        with mock.patch.object(build_runtime, "MAX_EXECUTION_WORKSPACE_ENTRIES", 2):
            with self.assertRaisesRegex(RuntimeError, "filesystem entries"):
                build_runtime._run_bounded_process(
                    [sys.executable, "-c", script], workspace, 5
                )
        self.assertLess(time.monotonic() - started, 3)

    def test_dataset_output_is_rechecked_against_byte_limit_before_publication(self):
        workspace = Path(self.temp.name) / "prepublish-quota-workspace"
        workspace.mkdir()
        (workspace / "output.bin").write_bytes(b"x" * 64)
        with mock.patch.object(build_runtime, "MAX_EXECUTION_WORKSPACE_BYTES", 32):
            with self.assertRaisesRegex(RuntimeError, "bytes"):
                dataset_builds.output_files(workspace, [])

    def test_submitted_script_runs_in_clean_workspace_and_publishes_lineage(self):
        workspace = self.create_workspace()
        development_path = Path(workspace["workspacePath"])
        self.assertTrue((development_path / "dataset1").is_symlink())
        (development_path / "debug-only.txt").write_text("must not publish", encoding="utf-8")
        script = """
import argparse
import csv
import json
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument('--multiplier', type=float, default=1)
args = parser.parse_args()
with Path('dataset1/bars.csv').open() as handle:
    rows = list(csv.DictReader(handle))
closes = [float(row['close']) * args.multiplier for row in rows]
Path('features.json').write_text(json.dumps({'close': closes}))
"""
        recipe = self.archive_script(script)

        result = dataset_builds.submit_build(self.config, {
            "workspaceId": workspace["workspaceId"],
            "outputDatasetId": "derived-features",
            "outputDatasetName": "Derived features",
            "recipeId": recipe["recipeId"],
            "recipeVersion": recipe["version"],
            "arguments": ["--multiplier", "2"],
        })

        self.assertEqual(result["job"]["status"], "completed")
        self.assertFalse(
            (dataset_build_jobs.job_root(self.config) / result["job"]["jobId"]).exists()
        )
        derived = result["dataset"]
        self.assertEqual(derived["status"], "active")
        self.assertEqual({item["datasetId"] for item in derived["upstream"]}, {"hourly-source", "daily-source"})
        version = datasets.ensure_dataset_version(self.config, "derived-features")
        container = Path(version["storage"]["uri"])
        self.assertEqual(json.loads((container / "features.json").read_text())["close"], [20.0, 24.0])
        self.assertFalse((container / "debug-only.txt").exists())
        self.assertFalse((container / "_build").exists())
        self.assertFalse((container / ".trade-engine").exists())
        with engine_database.connect_database(self.config) as conn, self.assertRaises(
            sqlite3.IntegrityError
        ):
            conn.execute(
                "UPDATE dataset_build_jobs SET status = 'failed' WHERE job_id = ?",
                (result["job"]["jobId"],),
            )
        self.assertFalse(stat.S_IMODE(container.stat().st_mode) & stat.S_IWUSR)
        manifest = json.loads((container / "_dataset.json").read_text())
        self.assertEqual(manifest["build"]["invocation"], {
            "type": "python-script", "arguments": ["--multiplier", "2"],
        })
        self.assertNotIn("entrypoint", manifest["build"])

        upstream = datasets.get_dataset(self.config, "hourly-source")
        self.assertEqual([item["datasetId"] for item in upstream["downstream"]], ["derived-features"])

    def test_sandbox_denies_writes_outside_execution_workspace(self):
        workspace = self.create_workspace("sandbox-check")
        outside = Path(self.temp.name) / "forbidden.txt"
        script = """
import argparse
from pathlib import Path
parser = argparse.ArgumentParser()
parser.add_argument('--outside', required=True)
args = parser.parse_args()
with open(args.outside, 'w') as handle:
    handle.write('forbidden')
Path('out.txt').write_text('should not reach')
"""
        recipe = self.archive_script(script)

        with self.assertRaisesRegex(RuntimeError, "Read-only file system"):
            dataset_builds.submit_build(self.config, {
                "workspaceId": workspace["workspaceId"],
                "outputDatasetId": "sandbox-output",
                "recipeId": recipe["recipeId"],
                "recipeVersion": recipe["version"],
                "arguments": ["--outside", str(outside)],
            })

        self.assertFalse(outside.exists())
        self.assertEqual(dataset_build_jobs.list_build_jobs(self.config)[0]["status"], "failed")

        source_workspace = self.create_workspace("source-write-check")
        source_csv = Path(source_workspace["sources"][0]["storageRoot"]) / "bars.csv"
        before = source_csv.read_bytes()
        source_write_recipe = self.archive_script(
            "from pathlib import Path\n"
            "with Path('dataset1/bars.csv').open('a') as handle:\n"
            "    handle.write('forbidden')\n"
        )
        with self.assertRaisesRegex(RuntimeError, "Read-only file system"):
            dataset_builds.submit_build(self.config, {
                "workspaceId": source_workspace["workspaceId"],
                "outputDatasetId": "source-write-output",
                "recipeId": source_write_recipe["recipeId"],
                "recipeVersion": source_write_recipe["version"],
            })
        self.assertEqual(source_csv.read_bytes(), before)

    def test_saved_script_receives_only_call_time_arguments(self):
        recipe = dataset_recipes.save_recipe(self.config, {
            "recipeId": "write-value",
            "scriptText": (
                "import argparse\n"
                "from pathlib import Path\n"
                "parser = argparse.ArgumentParser()\n"
                "parser.add_argument('--value', required=True)\n"
                "args = parser.parse_args()\n"
                "Path('value.txt').write_text(args.value)\n"
            ),
        })
        workspace = self.create_workspace("recipe-check")

        result = dataset_builds.submit_build(self.config, {
            "workspaceId": workspace["workspaceId"],
            "outputDatasetId": "recipe-output",
            "recipeId": recipe["recipeId"],
            "recipeVersion": recipe["version"],
            "arguments": '--value "hello world"',
        })

        version = datasets.ensure_dataset_version(self.config, result["dataset"]["datasetId"])
        self.assertEqual((Path(version["storage"]["uri"]) / "value.txt").read_text(), "hello world")
        self.assertNotIn("parameterSchema", recipe)
        self.assertNotIn("entrypoint", recipe)

    def test_script_arguments_are_tokenized_without_semantic_rewriting(self):
        self.assertEqual(
            workspace_contracts.normalize_script_arguments(
                '--name sample positional --arbitrary=value "two words"'
            ),
            ["--name", "sample", "positional", "--arbitrary=value", "two words"],
        )
        self.assertEqual(
            workspace_contracts.normalize_script_arguments(["--script-owned", "raw value"]),
            ["--script-owned", "raw value"],
        )
        with self.assertRaisesRegex(ValueError, "invalid"):
            workspace_contracts.normalize_script_arguments('"unterminated')

    def test_resource_ids_are_generated_from_names_without_using_names_as_identity(self):
        first = workspace_repository.create_workspace(self.config, {
            "name": "Repeated Workspace Name",
            "sources": [{"datasetId": "hourly-source", "alias": "dataset1"}],
        })
        second = workspace_repository.create_workspace(self.config, {
            "name": "Repeated Workspace Name",
            "sources": [{"datasetId": "daily-source", "alias": "dataset1"}],
        })
        self.assertRegex(first["workspaceId"], r"^ws_[0-9A-HJKMNP-TV-Z]{26}$")
        self.assertNotEqual(first["workspaceId"], second["workspaceId"])

        recipe = dataset_recipes.save_recipe(self.config, {
            "name": "Readable Script",
            "scriptText": "from pathlib import Path\nPath('out.txt').write_text('ok')\n",
        })
        self.assertRegex(recipe["recipeId"], r"^script_[0-9A-HJKMNP-TV-Z]{26}$")
        self.assertEqual(recipe["version"], "1")

        result = dataset_builds.submit_build(self.config, {
            "workspaceId": first["workspaceId"],
            "outputDatasetName": "Readable Output",
            "recipeId": recipe["recipeId"],
            "recipeVersion": recipe["version"],
        })
        self.assertRegex(result["dataset"]["datasetId"], r"^ds_[0-9A-HJKMNP-TV-Z]{26}$")
        self.assertRegex(result["job"]["jobId"], r"^job_[0-9A-HJKMNP-TV-Z]{26}$")
        self.assertEqual(result["dataset"]["name"], "Readable Output")

    def test_build_timeout_and_process_dataset_ids_are_strict(self):
        workspace = self.create_workspace("strict-build-request")
        recipe = self.archive_script(
            "from pathlib import Path\nPath('out.txt').write_text('ok')\n"
        )
        base = {
            "workspaceId": workspace["workspaceId"],
            "outputDatasetId": "strict-output",
            "recipeId": recipe["recipeId"],
            "recipeVersion": recipe["version"],
        }
        for value in (True, 0, 3601, "10", None):
            with self.subTest(timeout=value):
                with self.assertRaisesRegex(ValueError, "timeoutSeconds"):
                    dataset_builds.submit_build(
                        self.config, {**base, "timeoutSeconds": value}
                    )

        process_base = {
            "recipeId": recipe["recipeId"],
            "recipeVersion": recipe["version"],
        }
        for dataset_ids, message in (
            (["hourly-source", "hourly-source"], "duplicate"),
            (["hourly-source", ""], "non-empty"),
            (["hourly-source", 3], "non-empty"),
        ):
            with self.subTest(dataset_ids=dataset_ids):
                with self.assertRaisesRegex(ValueError, message):
                    dataset_builds.process_recipe(
                        self.config, {**process_base, "datasetIds": dataset_ids}
                    )

    def test_workspace_python_script_is_listed_snapshotted_and_built(self):
        workspace = self.create_workspace("jupyter-script")
        root = Path(workspace["workspacePath"])
        scripts = root / "scripts"
        scripts.mkdir()
        script_path = scripts / "prepare.py"
        script_path.write_text(
            "import argparse\n"
            "from pathlib import Path\n"
            "parser = argparse.ArgumentParser()\n"
            "parser.add_argument('--value', default='ok')\n"
            "args = parser.parse_args()\n"
            "Path('result.txt').write_text(args.value)\n",
            encoding="utf-8",
        )

        listed = workspace_repository.list_workspace_scripts(self.config, workspace["workspaceId"])
        self.assertEqual([item["path"] for item in listed], ["scripts/prepare.py"])
        recipe = self.archive_script(
            workspace_repository.read_workspace_script(workspace, "scripts/prepare.py")
        )

        result = dataset_builds.submit_build(self.config, {
            "workspaceId": workspace["workspaceId"],
            "outputDatasetId": "jupyter-script-output",
            "recipeId": recipe["recipeId"],
            "recipeVersion": recipe["version"],
            "arguments": ["--value", "from-jupyter"],
        })

        version = datasets.ensure_dataset_version(self.config, result["dataset"]["datasetId"])
        container = Path(version["storage"]["uri"])
        self.assertEqual((container / "result.txt").read_text(), "from-jupyter")
        self.assertFalse((container / "_build").exists())
        self.assertFalse((container / "scripts" / "prepare.py").exists())

    def test_build_rejects_unarchived_script_sources(self):
        workspace = self.create_workspace("unarchived-script")
        base = {
            "workspaceId": workspace["workspaceId"],
            "outputDatasetId": "unarchived-output",
        }
        with self.assertRaisesRegex(ValueError, "unsupported field.*scriptText"):
            dataset_builds.submit_build(self.config, {
                **base,
                "scriptText": "print('not archived')\n",
            })
        with self.assertRaisesRegex(ValueError, "unsupported field.*scriptPath"):
            dataset_builds.submit_build(self.config, {
                **base,
                "scriptPath": "prepare.py",
            })

    def test_workspace_script_rejects_escape_symlink_and_source_dataset(self):
        workspace = self.create_workspace("unsafe-script")
        root = Path(workspace["workspacePath"])
        outside = Path(self.temp.name) / "outside.py"
        outside.write_text("print('outside')\n", encoding="utf-8")
        (root / "linked.py").symlink_to(outside)

        with self.assertRaisesRegex(ValueError, "relative .py"):
            workspace_repository.read_workspace_script(workspace, "../outside.py")
        with self.assertRaisesRegex(ValueError, "symbolic link"):
            workspace_repository.read_workspace_script(workspace, "linked.py")
        with self.assertRaisesRegex(ValueError, "Source Dataset"):
            workspace_repository.read_workspace_script(workspace, "dataset1/not-a-script.py")

    def test_process_recipe_uses_hidden_workspace_and_publishes_dataset(self):
        recipe = dataset_recipes.save_recipe(self.config, {
            "recipeId": "combine",
            "scriptText": (
                "import argparse\n"
                "from pathlib import Path\n"
                "parser = argparse.ArgumentParser()\n"
                "parser.add_argument('--label', required=True)\n"
                "args = parser.parse_args()\n"
                "Path('result.txt').write_text(args.label)\n"
            ),
        })

        result = dataset_builds.process_recipe(self.config, {
            "recipeId": recipe["recipeId"],
            "recipeVersion": recipe["version"],
            "datasetIds": ["hourly-source", "daily-source"],
            "arguments": ["--label", "processed"],
            "outputDatasetId": "processed-output",
        })

        self.assertEqual(workspace_repository.list_workspaces(self.config), [])
        with engine_database.connect_database(self.config) as connection:
            internal_count = connection.execute(
                "SELECT COUNT(*) AS count FROM dataset_workspaces WHERE internal = 1"
            ).fetchone()["count"]
        self.assertEqual(internal_count, 0)
        version = datasets.ensure_dataset_version(self.config, "processed-output")
        self.assertEqual((Path(version["storage"]["uri"]) / "result.txt").read_text(), "processed")
        self.assertEqual(
            {item["datasetId"] for item in result["dataset"]["upstream"]},
            {"hourly-source", "daily-source"},
        )
