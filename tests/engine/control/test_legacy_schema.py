import tempfile
import unittest
from pathlib import Path

from engine.control import schema as control_schema
from engine.contracts import strict_json
from engine.service import control_api as control


class ControlSchemaBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.config = {
            "controlRoot": str(root / "control"),
            "releaseRoot": str(root / "release"),
            "liveRoot": str(root / "live"),
        }
        for field in ("controlRoot", "releaseRoot", "liveRoot"):
            Path(self.config[field]).mkdir(parents=True)

    def tearDown(self):
        self.temporary.cleanup()

    def test_previous_schema_is_archived_wholesale_before_new_state_is_used(self):
        marker = Path(self.config["controlRoot"]) / control_schema.MARKER_NAME
        marker.write_text(strict_json.dumps({
            "schemaVersion": control_schema.CONTROL_SCHEMA_VERSION - 1,
            "activatedAt": "2026-08-08T00:00:00Z",
            "previousArchive": None,
        }), encoding="utf-8")
        (Path(self.config["controlRoot"]) / "engine-data.db").write_bytes(b"old-db")
        (Path(self.config["releaseRoot"]) / "_samplers").mkdir()
        (Path(self.config["releaseRoot"]) / "_samplers" / "old.txt").write_text(
            "old", encoding="utf-8"
        )

        result = control_schema.prepare(self.config)

        self.assertTrue(result["changed"])
        self.assertEqual(result["schemaVersion"], control_schema.CONTROL_SCHEMA_VERSION)
        self.assertFalse((Path(self.config["controlRoot"]) / "engine-data.db").exists())
        self.assertFalse((Path(self.config["releaseRoot"]) / "_samplers").exists())
        archive = Path(result["previousArchive"])
        self.assertEqual((archive / "control" / "engine-data.db").read_bytes(), b"old-db")
        self.assertEqual((archive / "release" / "_samplers" / "old.txt").read_text(), "old")
        current = strict_json.loads(marker.read_text(encoding="utf-8"))
        self.assertEqual(current["schemaVersion"], control_schema.CONTROL_SCHEMA_VERSION)

    def test_malformed_current_version_marker_is_not_accepted(self):
        marker = Path(self.config["controlRoot"]) / control_schema.MARKER_NAME
        marker.write_text(
            '{"schemaVersion":%d,"schemaVersion":%d}' % (
                control_schema.CONTROL_SCHEMA_VERSION,
                control_schema.CONTROL_SCHEMA_VERSION,
            ),
            encoding="utf-8",
        )
        (Path(self.config["releaseRoot"]) / "resource.txt").write_text(
            "archive me", encoding="utf-8"
        )
        result = control_schema.prepare(self.config)
        self.assertTrue(result["changed"])
        self.assertIsNotNone(result["previousArchive"])

    def test_missing_control_root_is_rejected_without_creating_a_fallback(self):
        root = Path(self.temporary.name) / "default-layout"
        config_path = root / "config.json"
        root.mkdir()
        config_path.write_text(strict_json.dumps({
            "releaseRoot": str(root / "release"),
            "liveRoot": str(root / "live"),
        }), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "missing required field.*controlRoot"):
            control.load_config(config_path)
        self.assertFalse((root / "release").exists())
        self.assertFalse((root / "live").exists())

    def test_nested_symbolic_link_is_rejected_without_copying_external_content(self):
        external = Path(self.temporary.name) / "external.txt"
        external.write_text("outside", encoding="utf-8")
        resource = Path(self.config["releaseRoot"]) / "resource"
        resource.mkdir()
        link = resource / "linked.txt"
        try:
            link.symlink_to(external)
        except OSError as exc:
            self.skipTest(f"symbolic links unavailable: {exc}")

        with self.assertRaisesRegex(ValueError, "symbolic links"):
            control_schema.prepare(self.config)

        self.assertEqual(external.read_text(encoding="utf-8"), "outside")
        archive_root = (
            Path(self.config["controlRoot"])
            / control_schema.ARCHIVE_DIRECTORY
            / "control-schema"
        )
        self.assertFalse(any(
            path.name != ".staging" and path.is_dir()
            for path in archive_root.iterdir()
        ))

    def test_deeply_nested_configured_root_is_excluded_from_parent_archive(self):
        root = Path(self.temporary.name) / "nested-layout"
        config = {
            "releaseRoot": str(root / "release"),
            "controlRoot": str(root / "release" / "container" / "control"),
            "liveRoot": str(root / "live"),
        }
        for path in map(lambda field: Path(config[field]), config):
            path.mkdir(parents=True, exist_ok=True)
        release_container = Path(config["releaseRoot"]) / "container"
        (release_container / "release.txt").write_text("release", encoding="utf-8")
        (Path(config["controlRoot"]) / "control.txt").write_text("control", encoding="utf-8")

        result = control_schema.prepare(config)
        archive = Path(result["previousArchive"])

        self.assertEqual(
            (archive / "release" / "container" / "release.txt").read_text(),
            "release",
        )
        self.assertFalse((archive / "release" / "container" / "control").exists())
        self.assertEqual(
            (archive / "control" / "control.txt").read_text(),
            "control",
        )

    def test_configured_root_itself_may_not_be_a_symbolic_link(self):
        target = Path(self.temporary.name) / "linked-release-target"
        target.mkdir()
        link = Path(self.temporary.name) / "linked-release"
        try:
            link.symlink_to(target, target_is_directory=True)
        except OSError as exc:
            self.skipTest(f"symbolic links unavailable: {exc}")
        config = dict(self.config)
        config["releaseRoot"] = str(link)
        with self.assertRaisesRegex(ValueError, "root may not be a symbolic link"):
            control_schema.prepare(config)


if __name__ == "__main__":
    unittest.main()
