"""Adversarial Dataset Build path and submission recovery tests."""

import stat
from pathlib import Path
from unittest import mock

from engine.control import database as engine_database
from engine.core import clock as engine_clock
from engine.repository import dataset_build_jobs
from engine.repository import dataset_build_paths
from engine.repository import dataset_workspaces as workspace_repository
from engine.service import dataset_builds
from tests.support.dataset_workspace import DatasetWorkspaceTestCase


class DatasetWorkspaceRecoveryTests(DatasetWorkspaceTestCase):
    def _create_running_job_with_completion(self, job_id):
        workspace = self.create_workspace(f"{job_id}-workspace")
        script_text = (
            "from pathlib import Path\nPath('output.txt').write_text('ok')\n"
        )
        recipe = self.archive_script(script_text)
        bindings = workspace_repository.verify_workspace_bindings(
            self.config, workspace["sources"]
        )
        root = dataset_build_paths.dataset_build_job_directory(
            self.config, job_id, create=True
        )
        execution_workspace = root / "workspace"
        workspace_repository.materialize_source_links(
            execution_workspace, bindings
        )
        engine_directory = execution_workspace / ".trade-engine"
        engine_directory.mkdir()
        script_path = engine_directory / "submitted.py"
        script_path.write_text(script_text, encoding="utf-8")
        submitted_at = engine_clock.utc_now()
        submission = {
            "jobId": job_id,
            "workspaceId": workspace["workspaceId"],
            "outputDatasetId": f"{job_id}-output",
            "outputDatasetName": f"{job_id}-output",
            "executionWorkspacePath": str(execution_workspace),
            "recipeId": recipe["recipeId"],
            "recipeVersion": recipe["version"],
            "scriptPath": str(script_path),
            "scriptDigest": recipe["scriptDigest"],
            "arguments": [],
            "sources": bindings,
            "submittedAt": submitted_at,
        }
        dataset_build_jobs.commit_build_submission(
            self.config, submission, f"nonce-{job_id}"
        )
        dataset_build_jobs.mark_build_running(self.config, job_id)
        output = execution_workspace / "output.txt"
        output.write_text("external-content", encoding="utf-8")
        dataset_builds.write_build_completion_evidence(
            dataset_build_jobs.get_build_job(self.config, job_id),
            execution_workspace,
            [],
            "stdout",
            "",
        )
        output.chmod(0o666)
        return root, output

    def test_recovery_rejects_symlinked_job_identity_without_external_io(self):
        job_id = "recovery-symlink-job"
        root, output = self._create_running_job_with_completion(job_id)
        outside_root = Path(self.temp.name) / "outside-running-job"
        root.rename(outside_root)
        root.symlink_to(outside_root, target_is_directory=True)
        external_output = outside_root / "workspace" / output.name
        before = external_output.read_bytes()
        before_mode = stat.S_IMODE(external_output.stat().st_mode)

        with self.assertRaisesRegex(ValueError, "symbolic link"):
            dataset_builds.reconcile_interrupted_builds(self.config)

        self.assertEqual(external_output.read_bytes(), before)
        self.assertEqual(stat.S_IMODE(external_output.stat().st_mode), before_mode)
        self.assertEqual(
            dataset_build_jobs.get_build_job(self.config, job_id)["status"],
            "running",
        )

    def test_precommit_cleanup_failure_is_explicit_and_startup_reclaims_orphan(self):
        workspace = self.create_workspace("precommit-cleanup")
        recipe = self.archive_script(
            "from pathlib import Path\nPath('output.txt').write_text('ok')\n"
        )
        primary = RuntimeError("submission-precommit-primary")
        job_id = "precommit-cleanup-job"
        with (
            mock.patch.object(
                dataset_builds.build_jobs,
                "commit_build_submission",
                side_effect=primary,
            ),
            mock.patch.object(
                dataset_builds.build_jobs,
                "build_submission_commit_state",
                return_value="absent",
            ),
            mock.patch.object(
                dataset_builds,
                "discard_execution_workspace",
                return_value=False,
            ),
        ):
            with self.assertRaisesRegex(
                RuntimeError, "submission-precommit-primary"
            ) as raised:
                dataset_builds.submit_build(self.config, {
                    "workspaceId": workspace["workspaceId"],
                    "outputDatasetId": "precommit-cleanup-output",
                    "recipeId": recipe["recipeId"],
                    "recipeVersion": recipe["version"],
                    "jobId": job_id,
                })

        self.assertIs(raised.exception, primary)
        self.assertRegex(str(raised.exception.__cause__), "cleanup remains incomplete")
        orphan = dataset_build_paths.dataset_build_job_root(self.config) / job_id
        self.assertTrue(orphan.is_dir())
        with engine_database.connect_database(self.config) as connection:
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM dataset_build_jobs WHERE job_id = ?",
                    (job_id,),
                ).fetchone()[0],
                0,
            )

        dataset_builds.reconcile_build_job_directories(self.config)
        self.assertFalse(orphan.exists())
