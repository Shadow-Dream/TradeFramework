import fcntl
import json
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from engine.control import owner as control_owner
from engine.repository import control_state


class ControlStateRepositoryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.config = {"controlRoot": str(self.root / "control")}

    def tearDown(self):
        self.temporary.cleanup()

    def test_state_lock_is_reentrant_only_for_the_active_engine_root(self):
        other = {"controlRoot": str(self.root / "other-control")}
        with control_state.control_state_lock(self.config):
            with control_state.control_state_lock(self.config):
                control_state.save_state(self.config, "state.json", {"value": 1})
            with self.assertRaisesRegex(
                RuntimeError,
                "may not span different Engine roots",
            ):
                with control_state.control_state_lock(other):
                    self.fail("cross-root transaction unexpectedly entered")
        self.assertEqual(
            control_state.load_state(self.config, "state.json", {}),
            {"value": 1},
        )

    def test_state_lock_rechecks_owner_authority_after_winning_the_file_lock(self):
        checks = [None, RuntimeError("owner claimed during lock acquisition")]
        with (
            mock.patch.object(
                control_state,
                "assert_control_access",
                side_effect=checks,
            ) as access,
            self.assertRaisesRegex(
                RuntimeError,
                "owner claimed during lock acquisition",
            ),
        ):
            with control_state.control_state_lock(self.config):
                self.fail("transaction entered after its authority was revoked")

        self.assertEqual(access.call_count, 2)
        with control_state.control_state_lock(self.config):
            pass

    def test_state_name_rejects_every_escape_before_creating_the_control_root(self):
        invalid_names = (
            "",
            ".",
            "..",
            "../escaped.json",
            "nested/state.json",
            "nested\\state.json",
            str(self.root / "absolute.json"),
            "state.json\x00suffix",
        )
        for index, name in enumerate(invalid_names):
            control_root = self.root / f"invalid-control-{index}"
            config = {"controlRoot": str(control_root)}
            with self.subTest(name=name), self.assertRaisesRegex(
                ValueError,
                "one canonical filesystem-safe path segment",
            ):
                control_state.save_state(config, name, {"escaped": True})
            self.assertFalse(control_root.exists())
        self.assertFalse((self.root / "escaped.json").exists())
        self.assertFalse((self.root / "absolute.json").exists())

    def test_foreign_owner_rejection_precedes_an_invalid_state_name_without_changes(self):
        control_root = self.root / "foreign-owned-control"
        control_root.mkdir()
        owner_path = control_root / control_owner.OWNER_FILE
        owner_path.write_text(json.dumps({
            "schemaVersion": 1,
            "pid": os.getpid() + 100000,
            "buildId": "engine:test-owner",
            "token": "foreign-token",
        }), encoding="utf-8")
        (control_root / control_owner.LOCK_FILE).touch()
        before = {
            path.relative_to(control_root).as_posix(): path.read_bytes()
            for path in control_root.rglob("*")
            if path.is_file()
        }
        config = {"controlRoot": str(control_root)}

        with (control_root / control_owner.LOCK_FILE).open(
            "a+", encoding="utf-8"
        ) as foreign_lease:
            fcntl.flock(foreign_lease.fileno(), fcntl.LOCK_EX)
            with self.assertRaisesRegex(
                RuntimeError,
                "owned by the running Engine service",
            ):
                control_state.save_state(
                    config,
                    "../escaped.json",
                    {"escaped": True},
                )

        after = {
            path.relative_to(control_root).as_posix(): path.read_bytes()
            for path in control_root.rglob("*")
            if path.is_file()
        }
        self.assertEqual(after, before)
        self.assertFalse((self.root / "escaped.json").exists())

    def test_atomic_write_cleanup_does_not_replace_the_primary_error(self):
        target = self.root / "control" / "state.json"
        with (
            mock.patch.object(
                control_state.os,
                "replace",
                side_effect=OSError("primary replace failure"),
            ),
            mock.patch.object(
                control_state.os,
                "unlink",
                side_effect=OSError("secondary cleanup failure"),
            ),
            self.assertRaisesRegex(OSError, "primary replace failure"),
        ):
            control_state.atomic_write_json(target, {"value": 1})

    def test_history_append_is_one_durable_byte_write(self):
        writes = []
        sync_modes = []
        real_write = os.write
        real_fsync = os.fsync

        def write(descriptor, payload):
            writes.append(payload)
            return real_write(descriptor, payload)

        def fsync(descriptor):
            mode = os.fstat(descriptor).st_mode
            sync_modes.append("directory" if stat.S_ISDIR(mode) else "file")
            return real_fsync(descriptor)

        with (
            mock.patch.object(control_state.os, "write", side_effect=write),
            mock.patch.object(control_state.os, "fsync", side_effect=fsync),
        ):
            event = control_state.append_history_event(
                self.config,
                "test.appended",
                {"status": "ready"},
            )

        self.assertEqual(len(writes), 1)
        self.assertTrue(writes[0].endswith(b"\n"))
        self.assertEqual(sync_modes, ["file", "directory"])
        self.assertEqual(control_state.load_history_events(self.config), [event])

    def test_short_history_write_is_rolled_back_instead_of_corrupting_the_log(self):
        real_write = os.write

        def short_write(descriptor, payload):
            prefix = payload[: max(1, len(payload) // 2)]
            return real_write(descriptor, prefix)

        with (
            mock.patch.object(control_state.os, "write", side_effect=short_write),
            self.assertRaisesRegex(OSError, "append was incomplete"),
        ):
            control_state.append_history_event(
                self.config,
                "test.partial",
                {"status": "must-not-survive"},
            )

        self.assertEqual(control_state.load_history_events(self.config), [])

    def test_history_order_limit_and_sanitization_are_preserved(self):
        control_state.append_history_event(
            self.config,
            "dataset.created",
            {
                "secret": "discard",
                "dataset": {
                    "datasetId": "prices",
                    "latestVersionId": "2",
                    "name": "Prices",
                    "source": "upload",
                    "status": "ready",
                },
            },
        )
        second = control_state.append_history_event(
            self.config,
            "backtest.completed",
            {
                "backtest": {
                    "backtestId": "bt-1",
                    "pipelineId": "p-1",
                    "datasetId": "prices",
                    "status": "completed",
                    "runner": "worker",
                    "metrics": {"cycles": 3},
                },
                "secret": "discard",
            },
        )

        self.assertEqual(control_state.load_history_events(self.config, 1), [second])
        sanitized = control_state.load_sanitized_history_events(self.config, 0)
        self.assertEqual([event["type"] for event in sanitized], [
            "dataset.created",
            "backtest.completed",
        ])
        self.assertEqual(sanitized[0]["payload"], {
            "datasetId": "prices",
            "datasetVersionId": "2",
            "name": "Prices",
            "source": "upload",
            "status": "ready",
        })
        self.assertEqual(sanitized[1]["payload"], {
            "backtestId": "bt-1",
            "pipelineId": "p-1",
            "datasetId": "prices",
            "status": "completed",
            "runner": "worker",
            "metrics": {"cycles": 3},
        })


if __name__ == "__main__":
    unittest.main()
