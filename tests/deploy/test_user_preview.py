import sqlite3
import unittest
from pathlib import Path

from scripts.prepare_engine_preview import (
    PREVIEW_MINING_ROOT,
    PROJECT,
    preview_user_id,
    sync_preview_users,
)


class UserPreviewAccountSyncTests(unittest.TestCase):
    def test_engine_service_allows_netlink_for_private_sampler_loopback(self):
        unit = (
            Path(__file__).resolve().parents[2]
            / "deploy"
            / "user"
            / "trade-engine-preview.service"
        ).read_text(encoding="utf-8")
        self.assertIn(
            "RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6 AF_NETLINK",
            unit,
        )
        self.assertNotIn("--share-net", unit)
        self.assertIn(".runtime/preview/venv/bin/python -B", unit)

    def test_workspace_jupyter_version_is_pinned_for_the_prebuilt_extension(self):
        requirements = (
            Path(__file__).resolve().parents[2] / "requirements-workspace.txt"
        ).read_text(encoding="utf-8").splitlines()
        self.assertIn("jupyterlab==4.6.3", requirements)

    def test_hot_reload_prepares_only_engine_owned_resources(self):
        reload_script = (
            Path(__file__).resolve().parents[2] / "scripts" / "reload_agent_web.sh"
        ).read_text(encoding="utf-8")
        self.assertIn("prepare_engine_preview.py\"", reload_script)
        self.assertNotIn("preserve-existing-resources", reload_script)
        self.assertIn("build_jupyter_ui_sync.sh", reload_script)

    def test_preview_mining_evidence_is_outside_the_source_repository(self):
        self.assertNotEqual(PREVIEW_MINING_ROOT, PROJECT)
        self.assertNotIn(PROJECT, PREVIEW_MINING_ROOT.parents)

    def test_sync_updates_accounts_preserves_live_sessions_and_removes_stale_users(self):
        connection = sqlite3.connect(":memory:")
        connection.execute("PRAGMA foreign_keys=ON")
        connection.executescript(
            """
            CREATE TABLE users (
              user_id TEXT PRIMARY KEY, email TEXT NOT NULL UNIQUE,
              password_hash TEXT NOT NULL, role TEXT NOT NULL,
              status TEXT NOT NULL, created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );
            CREATE TABLE sessions (
              token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL,
              csrf_hash TEXT NOT NULL, created_at INTEGER NOT NULL,
              expires_at INTEGER NOT NULL, last_seen_at INTEGER NOT NULL,
              FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
            );
            """
        )
        kept_id = preview_user_id("production-user")
        connection.execute(
            "INSERT INTO users VALUES (?,?,?,?,?,?,?)",
            (kept_id, "old@example.com", "old-hash", "user", "active", "t0", "t0"),
        )
        connection.execute(
            "INSERT INTO users VALUES (?,?,?,?,?,?,?)",
            ("stale", "stale@example.com", "hash", "user", "active", "t0", "t0"),
        )
        connection.execute(
            "INSERT INTO sessions VALUES (?,?,?,?,?,?)",
            ("kept-session", kept_id, "csrf", 1, 999, 1),
        )
        connection.execute(
            "INSERT INTO sessions VALUES (?,?,?,?,?,?)",
            ("stale-session", "stale", "csrf", 1, 999, 1),
        )
        connection.commit()

        sync_preview_users(
            connection,
            [
                {
                    "user_id": "production-user",
                    "email": "new@example.com",
                    "password_hash": "new-hash",
                    "role": "admin",
                    "status": "active",
                    "created_at": "t0",
                    "updated_at": "t1",
                }
            ],
        )

        self.assertEqual(
            connection.execute(
                "SELECT email,password_hash,role FROM users WHERE user_id=?",
                (kept_id,),
            ).fetchone(),
            ("new@example.com", "new-hash", "admin"),
        )
        self.assertEqual(
            connection.execute("SELECT token_hash FROM sessions").fetchall(),
            [("kept-session",)],
        )


if __name__ == "__main__":
    unittest.main()
