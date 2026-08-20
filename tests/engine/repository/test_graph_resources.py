import os
import stat
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from engine.repository import control_state
from engine.repository import graph_resources


class GraphResourceRepositoryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.config = {
            "controlRoot": str(self.root / "control"),
            "releaseRoot": str(self.root / "releases"),
        }

    def tearDown(self):
        self.temporary.cleanup()

    @staticmethod
    def _draft(identity):
        return {
            "schemaVersion": 1,
            "analysisId": identity,
            "name": identity,
            "description": "",
            "instances": {},
            "graph": {},
        }

    @staticmethod
    def _validate(candidate, _module_definitions):
        return candidate

    def test_concurrent_distinct_publications_preserve_both_indexes_and_archives(self):
        first_save_entered = threading.Event()
        release_first_save = threading.Event()
        second_read_entered = threading.Event()
        real_load = control_state.load_state
        real_save = control_state.save_state
        errors = []
        results = []

        def observed_load(config, name, default):
            result = real_load(config, name, default)
            if (
                name == "analyses.json"
                and threading.current_thread().name == "second-publisher"
            ):
                second_read_entered.set()
            return result

        def coordinated_save(config, name, payload):
            if name == "analyses.json" and not first_save_entered.is_set():
                first_save_entered.set()
                if not release_first_save.wait(timeout=5):
                    raise RuntimeError("Timed out coordinating the first index commit.")
            return real_save(config, name, payload)

        def publish(identity):
            try:
                results.append(graph_resources.archive_if_changed(
                    self.config,
                    "analysis",
                    self._draft(identity),
                    module_definitions={},
                    validate=self._validate,
                ))
            except BaseException as exc:
                errors.append(exc)

        with (
            mock.patch.object(
                control_state,
                "load_state",
                side_effect=observed_load,
            ),
            mock.patch.object(
                control_state,
                "save_state",
                side_effect=coordinated_save,
            ),
        ):
            first = threading.Thread(
                target=publish,
                args=("alpha",),
                name="first-publisher",
            )
            second = threading.Thread(
                target=publish,
                args=("beta",),
                name="second-publisher",
            )
            first.start()
            self.assertTrue(first_save_entered.wait(timeout=5))
            second.start()
            # Before the repository owned the complete transaction lock, this
            # read happened while the first publisher held a stale whole-file
            # snapshot, and the later replace silently lost one index entry.
            second_read_entered.wait(timeout=0.2)
            release_first_save.set()
            first.join(timeout=5)
            second.join(timeout=5)

        self.assertFalse(first.is_alive())
        self.assertFalse(second.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(len(results), 2)
        records = graph_resources.load_repository(self.config, "analysis")
        self.assertEqual(set(records), {"alpha/1", "beta/1"})
        self.assertTrue(
            (Path(self.config["releaseRoot"]) / "_analyses" / "alpha" / "1").is_dir()
        )
        self.assertTrue(
            (Path(self.config["releaseRoot"]) / "_analyses" / "beta" / "1").is_dir()
        )

    def test_index_directory_fsync_uncertainty_is_reconciled_by_exact_readback(self):
        real_fsync = os.fsync
        real_replace = os.replace
        state_replaced = False
        uncertainty_observed = False

        def replace(source, destination):
            nonlocal state_replaced
            result = real_replace(source, destination)
            if Path(destination).name == "analyses.json":
                state_replaced = True
            return result

        def fsync(descriptor):
            nonlocal state_replaced, uncertainty_observed
            if state_replaced and stat.S_ISDIR(os.fstat(descriptor).st_mode):
                state_replaced = False
                uncertainty_observed = True
                raise OSError("index directory fsync acknowledgement failed")
            return real_fsync(descriptor)

        with (
            mock.patch.object(control_state.os, "replace", side_effect=replace),
            mock.patch.object(control_state.os, "fsync", side_effect=fsync),
        ):
            result = graph_resources.archive_if_changed(
                self.config,
                "analysis",
                self._draft("uncertain"),
                module_definitions={},
                validate=self._validate,
            )

        self.assertTrue(uncertainty_observed)
        self.assertTrue(result["accepted"])
        self.assertEqual(result["resourceKey"], "uncertain/1")
        records = graph_resources.load_repository(self.config, "analysis")
        self.assertEqual(set(records), {"uncertain/1"})
        self.assertEqual(
            [event["type"] for event in control_state.load_history_events(self.config)],
            ["analysis.archived"],
        )

    def test_unchanged_version_and_builtin_ownership_are_repository_invariants(self):
        first = graph_resources.archive_if_changed(
            self.config,
            "analysis",
            self._draft("owned"),
            module_definitions={},
            validate=self._validate,
            engine_owned=True,
        )
        unchanged = graph_resources.archive_if_changed(
            self.config,
            "analysis",
            self._draft("owned"),
            module_definitions={},
            validate=self._validate,
            engine_owned=True,
        )
        with self.assertRaisesRegex(ValueError, "ownership cannot change"):
            graph_resources.archive_if_changed(
                self.config,
                "analysis",
                self._draft("owned"),
                module_definitions={},
                validate=self._validate,
                engine_owned=False,
            )
        changed = graph_resources.archive_if_changed(
            self.config,
            "analysis",
            {**self._draft("owned"), "description": "version two"},
            module_definitions={},
            validate=self._validate,
            engine_owned=True,
        )

        self.assertEqual(first["definition"]["version"], "1")
        self.assertTrue(unchanged["unchanged"])
        self.assertEqual(unchanged["definition"]["version"], "1")
        self.assertEqual(changed["definition"]["version"], "2")
        self.assertTrue(changed["definition"]["builtin"])
        self.assertEqual(
            set(graph_resources.load_repository(self.config, "analysis")),
            {"owned/1", "owned/2"},
        )

    def test_exact_version_path_segments_are_rejected_before_index_access(self):
        invalid_pairs = (
            ("../identity", "1"),
            ("identity/child", "1"),
            ("identity", "../1"),
            ("identity", "1/child"),
            ("identity", "1\x00suffix"),
        )
        with mock.patch.object(
            control_state,
            "load_state",
            side_effect=AssertionError("invalid path reached the index"),
        ):
            for identity, version in invalid_pairs:
                with self.subTest(identity=identity, version=version), self.assertRaisesRegex(
                    ValueError,
                    "one canonical filesystem-safe path segment",
                ):
                    graph_resources.load_version(
                        self.config,
                        "analysis",
                        identity,
                        version,
                    )

    def test_repository_rejects_builtin_ownership_changes_across_versions(self):
        records = {
            "owned/1": {
                "analysisId": "owned",
                "version": "1",
                "status": "archived",
                "builtin": False,
            },
            "owned/2": {
                "analysisId": "owned",
                "version": "2",
                "status": "archived",
                "builtin": True,
            },
        }
        with mock.patch.object(
            graph_resources.version_archive,
            "verify_record_location",
            side_effect=lambda record, **_kwargs: record,
        ), self.assertRaisesRegex(ValueError, "changes immutable field 'builtin'"):
            graph_resources.verify_repository(self.config, "analysis", records)

    def test_repository_rejects_a_non_object_index_record(self):
        with self.assertRaisesRegex(
            ValueError,
            "Archived version record must be an object",
        ):
            graph_resources.verify_repository(
                self.config,
                "analysis",
                {"broken/1": []},
            )


if __name__ == "__main__":
    unittest.main()
