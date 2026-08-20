import json
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from engine.control import database
from engine.core import clock
from engine.control.owner import claim_control_owner


class EngineDatabaseTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.config = {"controlRoot": str(self.root / "control")}

    def database_path(self):
        return Path(self.config["controlRoot"]) / "engine-data.db"

    def incompatible_database_archives(self):
        root = (
            Path(self.config["controlRoot"])
            / "_incompatible_archives"
            / "database"
        )
        return tuple(sorted(root.iterdir())) if root.exists() else ()

    def tearDown(self):
        self.temporary.cleanup()

    def downgrade_to_schema_18(self):
        with sqlite3.connect(self.database_path()) as connection:
            connection.execute("DROP INDEX backtest_jobs_backtest_identity")
            connection.execute(
                "DROP TRIGGER backtest_job_backtest_identity_required"
            )
            connection.execute("PRAGMA user_version = 18")
            connection.commit()

    def test_non_owner_cannot_replace_an_incompatible_live_database(self):
        database.prepare_database(self.config)
        path = self.database_path()
        with sqlite3.connect(path) as connection:
            connection.execute("CREATE TABLE legacy_marker (value TEXT NOT NULL)")
            connection.execute("INSERT INTO legacy_marker VALUES ('preserve')")
            connection.execute("PRAGMA user_version = 16")
            connection.commit()

        lease = claim_control_owner(self.config)
        try:
            script = (
                "import json,sys; "
                "from engine.control import database; "
                "database.prepare_database(json.loads(sys.argv[1]))"
            )
            completed = subprocess.run(
                [sys.executable, "-c", script, json.dumps(self.config)],
                cwd=Path(__file__).resolve().parents[3],
                text=True,
                capture_output=True,
            )
        finally:
            lease.close()

        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("owned by the running Engine service", completed.stderr)
        with sqlite3.connect(path) as connection:
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 16)
            self.assertEqual(
                connection.execute("SELECT value FROM legacy_marker").fetchone()[0],
                "preserve",
            )
        archive_root = self.root / "control" / "_incompatible_archives" / "database"
        self.assertFalse(archive_root.exists())

    def test_archive_timestamp_uses_the_engine_clock_authority_once(self):
        database.prepare_database(self.config)
        path = self.database_path()
        with sqlite3.connect(path) as connection:
            connection.execute("PRAGMA user_version = 16")
            connection.commit()

        fixed = "2042-03-04T05:06:07.123456Z"
        with mock.patch.object(clock, "utc_now", return_value=fixed) as now:
            database.prepare_database(self.config)

        now.assert_called_once_with()
        archive = (
            self.root
            / "control"
            / "_incompatible_archives"
            / "database"
            / "20420304T050607123456Z"
        )
        evidence = json.loads((archive / "archive.json").read_text(encoding="utf-8"))
        self.assertEqual(evidence["archivedAt"], fixed)

    def test_wal_mode_is_part_of_the_physical_database_contract(self):
        database.prepare_database(self.config)
        path = self.database_path()
        with sqlite3.connect(path) as connection:
            self.assertEqual(
                str(connection.execute("PRAGMA journal_mode=DELETE").fetchone()[0]).casefold(),
                "delete",
            )

        with self.assertRaisesRegex(RuntimeError, "WAL journal mode"):
            database.connect_database(self.config)

        database.prepare_database(self.config)
        archive_root = self.root / "control" / "_incompatible_archives" / "database"
        archives = tuple(archive_root.iterdir())
        self.assertEqual(len(archives), 1)
        evidence = json.loads(
            (archives[0] / "archive.json").read_text(encoding="utf-8")
        )
        self.assertEqual(evidence["foundJournalMode"], "delete")
        self.assertEqual(evidence["requiredJournalMode"], "wal")
        with database.connect_database(self.config) as connection:
            self.assertEqual(
                str(connection.execute("PRAGMA journal_mode").fetchone()[0]).casefold(),
                "wal",
            )

    def test_schema_18_is_archived_without_migration_and_replaced_fresh(self):
        database.prepare_database(self.config)
        with sqlite3.connect(self.database_path()) as connection:
            connection.execute(
                """
                INSERT INTO backtest_jobs
                (job_id, status, phase, pipeline_id, dataset_id, request_json,
                 submitted_at, backtest_id, snapshot_hash)
                VALUES ('job-preserved', 'queued', 'queued', 'pipeline',
                        'dataset', '{}', '2026-08-10T00:00:00Z',
                        'backtest-preserved', 'digest')
                """
            )
            connection.commit()
        self.downgrade_to_schema_18()

        database.prepare_database(self.config)

        with database.connect_database(self.config) as connection:
            self.assertEqual(
                connection.execute("PRAGMA user_version").fetchone()[0],
                database.DATABASE_SCHEMA_VERSION,
            )
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM backtest_jobs WHERE job_id = ?",
                    ("job-preserved",),
                ).fetchone()[0], 0,
            )
        archives = self.incompatible_database_archives()
        self.assertEqual(len(archives), 1)
        evidence = json.loads(
            (archives[0] / "archive.json").read_text(encoding="utf-8")
        )
        self.assertEqual(evidence["foundSchemaVersion"], 18)
        self.assertEqual(
            evidence["requiredSchemaVersion"], database.DATABASE_SCHEMA_VERSION
        )
        with sqlite3.connect(archives[0] / "engine-data.db") as archived:
            self.assertEqual(archived.execute("PRAGMA user_version").fetchone()[0], 18)
            self.assertEqual(
                archived.execute(
                    "SELECT backtest_id FROM backtest_jobs WHERE job_id = ?",
                    ("job-preserved",),
                ).fetchone()[0],
                "backtest-preserved",
            )
            indexes = {
                row[1] for row in archived.execute("PRAGMA index_list(backtest_jobs)")
            }
            self.assertNotIn("backtest_jobs_backtest_identity", indexes)

    def test_schema_18_duplicate_data_is_archived_without_upgrade_interpretation(self):
        database.prepare_database(self.config)
        self.downgrade_to_schema_18()
        with sqlite3.connect(self.database_path()) as connection:
            connection.executemany(
                """
                INSERT INTO backtest_jobs
                (job_id, status, phase, pipeline_id, dataset_id, request_json,
                 submitted_at, started_at, backtest_id, snapshot_hash)
                VALUES (?, 'running', 'running', 'pipeline', 'dataset', '{}',
                        ?, '2026-08-10T00:00:01Z', 'backtest-duplicate', 'digest')
                """,
                (
                    ("job-duplicate-a", "2026-08-10T00:00:00Z"),
                    ("job-duplicate-b", "2026-08-10T00:00:01Z"),
                ),
            )
            connection.commit()

        database.prepare_database(self.config)

        with database.connect_database(self.config) as connection:
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM backtest_jobs "
                    "WHERE backtest_id = 'backtest-duplicate'"
                ).fetchone()[0], 0,
            )
        archives = self.incompatible_database_archives()
        self.assertEqual(len(archives), 1)
        with sqlite3.connect(archives[0] / "engine-data.db") as archived:
            self.assertEqual(archived.execute("PRAGMA user_version").fetchone()[0], 18)
            self.assertEqual(
                archived.execute(
                    "SELECT COUNT(*) FROM backtest_jobs "
                    "WHERE backtest_id = 'backtest-duplicate'"
                ).fetchone()[0], 2,
            )

    def test_schema_18_invalid_data_is_archived_without_upgrade_interpretation(self):
        database.prepare_database(self.config)
        self.downgrade_to_schema_18()
        with sqlite3.connect(self.database_path()) as connection:
            connection.execute(
                """
                INSERT INTO backtest_jobs
                (job_id, status, phase, pipeline_id, dataset_id, request_json,
                 submitted_at, backtest_id, snapshot_hash)
                VALUES ('job-empty-binding', 'queued', 'queued', 'pipeline',
                        'dataset', '{}', '2026-08-10T00:00:00Z', '', 'digest')
                """
            )
            connection.commit()

        database.prepare_database(self.config)

        with database.connect_database(self.config) as connection:
            self.assertEqual(
                connection.execute("PRAGMA user_version").fetchone()[0],
                database.DATABASE_SCHEMA_VERSION,
            )
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM backtest_jobs WHERE job_id = ?",
                    ("job-empty-binding",),
                ).fetchone()[0], 0,
            )
        archives = self.incompatible_database_archives()
        self.assertEqual(len(archives), 1)
        with sqlite3.connect(archives[0] / "engine-data.db") as archived:
            self.assertEqual(archived.execute("PRAGMA user_version").fetchone()[0], 18)
            self.assertEqual(
                archived.execute(
                    "SELECT status FROM backtest_jobs WHERE job_id = ?",
                    ("job-empty-binding",),
                ).fetchone()[0],
                "queued",
            )

    def test_concurrent_schema_18_preparation_archives_once_and_replaces_fresh(self):
        database.prepare_database(self.config)
        with sqlite3.connect(self.database_path()) as connection:
            connection.execute(
                """
                INSERT INTO datasets
                (dataset_id, name, source_json, created_at, metadata_json,
                 status, archived_at, archive_reason)
                VALUES ('migration-marker', 'Migration marker', '{}',
                        '2026-08-10T00:00:00Z', '{}', 'active', '', '')
                """
            )
            connection.commit()
        self.downgrade_to_schema_18()
        ready = self.root / "first-prepare-ready"
        release = self.root / "release-first-prepare"
        repository_root = Path(__file__).resolve().parents[3]
        first_script = """
import json
import sys
import time
from pathlib import Path
from engine.control import database

config = json.loads(sys.argv[1])
ready = Path(sys.argv[2])
release = Path(sys.argv[3])
original = database._prepare_database_locked

def prepare_locked(current_config):
    ready.write_text('ready', encoding='utf-8')
    deadline = time.monotonic() + 10
    while not release.exists():
        if time.monotonic() >= deadline:
            raise RuntimeError('Timed out waiting to release database preparation.')
        time.sleep(0.01)
    return original(current_config)

database._prepare_database_locked = prepare_locked
database.prepare_database(config)
"""
        second_script = (
            "import json,sys; from engine.control import database; "
            "database.prepare_database(json.loads(sys.argv[1]))"
        )
        first = subprocess.Popen(
            [
                sys.executable,
                "-c",
                first_script,
                json.dumps(self.config),
                str(ready),
                str(release),
            ],
            cwd=repository_root,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        second = None
        try:
            deadline = time.monotonic() + 10
            while not ready.exists() and first.poll() is None:
                if time.monotonic() >= deadline:
                    self.fail("First database preparation did not reach schema inspection.")
                time.sleep(0.01)
            self.assertIsNone(first.poll())
            second = subprocess.Popen(
                [
                    sys.executable,
                    "-c",
                    second_script,
                    json.dumps(self.config),
                ],
                cwd=repository_root,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            time.sleep(0.2)
            self.assertIsNone(
                second.poll(),
                "Concurrent preparation bypassed the database replacement lock.",
            )
            release.write_text("release", encoding="utf-8")
            first_stdout, first_stderr = first.communicate(timeout=10)
            second_stdout, second_stderr = second.communicate(timeout=10)
            self.assertEqual(
                first.returncode,
                0,
                first_stdout + first_stderr,
            )
            self.assertEqual(
                second.returncode,
                0,
                second_stdout + second_stderr,
            )
        finally:
            release.touch(exist_ok=True)
            for process in (first, second):
                if process is not None and process.poll() is None:
                    process.kill()
                    process.communicate()

        with database.connect_database(self.config) as connection:
            self.assertEqual(
                connection.execute("PRAGMA user_version").fetchone()[0],
                database.DATABASE_SCHEMA_VERSION,
            )
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM datasets "
                    "WHERE dataset_id = 'migration-marker'"
                ).fetchone()[0], 0,
            )
        archives = self.incompatible_database_archives()
        self.assertEqual(len(archives), 1)
        with sqlite3.connect(archives[0] / "engine-data.db") as archived:
            self.assertEqual(archived.execute("PRAGMA user_version").fetchone()[0], 18)
            self.assertEqual(
                archived.execute(
                    "SELECT COUNT(*) FROM datasets "
                    "WHERE dataset_id = 'migration-marker'"
                ).fetchone()[0], 1,
            )


if __name__ == "__main__":
    unittest.main()
