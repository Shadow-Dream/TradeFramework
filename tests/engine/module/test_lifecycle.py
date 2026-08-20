#!/usr/bin/env python3

import base64
import stat
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from engine.service import module_workspaces
from engine.service import control_api as control
from engine.archive import version as version_archive
from engine.authority import module_definition as module_definition_authority
from engine.repository import control_state
from engine.repository import module_definitions
from engine.service import module_publication


RUNNER = b"#!/usr/bin/env python3\nprint('module source')\n"


class ModuleLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.config = {
            "controlRoot": str(root / "control"),
            "releaseRoot": str(root / "releases"),
            "liveRoot": str(root / "live"),
        }

    def tearDown(self):
        self.temp.cleanup()

    def payload(self, module_id="editable", *, kind="Signal", description="first"):
        return {
            "kind": kind,
            "moduleId": module_id,
            "name": module_id,
            "description": description,
            "activationMode": "ProcessRunner",
            "parameters": {
                "command": "{{moduleRoot}}/runner.py",
                "arguments": [],
                "workingDirectory": "{{moduleRoot}}",
            },
            "configSchema": {"type": "object", "additionalProperties": False},
            "ports": {"inputs": {}, "outputs": {}},
            "files": [{
                "path": "runner.py",
                "contentBase64": base64.b64encode(RUNNER).decode(),
                "executable": True,
            }],
        }

    def save(self, payload, *, engine_owned=False):
        repository = module_definitions.module_repository_for_kind(payload["kind"])
        return module_publication.publish_module(
            self.config,
            payload,
            repository=repository,
            engine_owned=engine_owned,
        )["definition"]

    def test_versions_are_system_assigned_deduplicated_and_never_decrease(self):
        first = self.save(self.payload())
        unchanged = self.save(self.payload())
        second = self.save(self.payload(description="second"))
        reverted = self.save(self.payload(description="first"))
        self.assertEqual(
            (first["version"], unchanged["version"], second["version"], reverted["version"]),
            ("1", "1", "2", "3"),
        )
        with self.assertRaisesRegex(ValueError, "unsupported field"):
            self.save({**self.payload("manual-version"), "version": "9"})

    def test_unchanged_publication_verifies_each_existing_archive_once(self):
        self.save(self.payload("single-verification"))
        with mock.patch.object(
            version_archive,
            "verify_archive",
            wraps=version_archive.verify_archive,
        ) as verify_archive:
            unchanged = self.save(self.payload("single-verification"))
        self.assertEqual(unchanged["version"], "1")
        self.assertEqual(verify_archive.call_count, 1)

    def test_raw_authority_rejects_identity_before_archive_reads(self):
        definition = self.save(self.payload("authority-preflight"))
        cases = (
            {**definition, "version": "01"},
            {
                **definition,
                "archive": {
                    **definition["archive"],
                    "resourceId": "Signal/different",
                },
            },
            {
                **definition,
                "archive": {
                    **definition["archive"],
                    "resourceType": "pipeline",
                },
            },
        )
        for forged in cases:
            with (
                self.subTest(forged=forged),
                mock.patch.object(version_archive, "verify_archive") as verify_archive,
                self.assertRaises(ValueError),
            ):
                module_definition_authority.verify_module_definition_authority(
                    forged
                )
            verify_archive.assert_not_called()

    def test_resource_ids_are_single_segments_but_bundle_paths_may_be_nested(self):
        for invalid in ("a/", "a/.", "."):
            with self.subTest(resource="pipeline", identity=invalid), self.assertRaisesRegex(
                ValueError, "one canonical filesystem-safe path segment"
            ):
                control.normalize_pipeline_draft({
                    "pipelineId": invalid,
                    "name": "Invalid identity",
                    "config": {
                        "observationInput": {"whitelist": [], "blacklist": []}
                    },
                    "instances": {},
                    "stages": {},
                    "signalGraph": {"nodes": [], "inputs": {}, "outputs": {}},
                })
            with self.subTest(resource="module", identity=invalid), self.assertRaisesRegex(
                ValueError, "one canonical filesystem-safe path segment"
            ):
                self.save(self.payload(invalid))

        nested = module_publication.decode_bundle_files([{
            "path": "src/package/runner.py",
            "contentBase64": base64.b64encode(RUNNER).decode(),
            "executable": True,
        }])
        self.assertEqual(nested[0][0].as_posix(), "src/package/runner.py")

    def test_bundle_rejects_engine_owned_root_module_json(self):
        with self.assertRaisesRegex(ValueError, "Engine-owned root module.json"):
            module_publication.decode_bundle_files([{
                "path": "module.json",
                "contentBase64": base64.b64encode(b"{}\n").decode(),
                "executable": False,
            }])
        nested = module_publication.decode_bundle_files([{
            "path": "src/package/module.json",
            "contentBase64": base64.b64encode(b"{}\n").decode(),
            "executable": False,
        }])
        self.assertEqual(nested[0][0].as_posix(), "src/package/module.json")

    def test_process_runner_bundle_paths_must_be_textually_canonical(self):
        cases = (
            ("command", "{{moduleRoot}}/./runner.py"),
            ("command", "{{moduleRoot}}/src//runner.py"),
            ("workingDirectory", "{{moduleRoot}}/src/./package"),
            ("argument", "--source={{moduleRoot}}/src//package"),
        )
        for field, value in cases:
            payload = self.payload(f"canonical-{field}")
            payload["files"].extend((
                {
                    "path": "src/runner.py",
                    "contentBase64": base64.b64encode(RUNNER).decode(),
                    "executable": True,
                },
                {
                    "path": "src/package/value.txt",
                    "contentBase64": base64.b64encode(b"value\n").decode(),
                    "executable": False,
                },
            ))
            if field == "argument":
                payload["parameters"]["arguments"] = [value]
            else:
                payload["parameters"][field] = value
            with self.subTest(field=field, value=value), self.assertRaisesRegex(
                ValueError, "non-canonical bundle path"
            ):
                module_publication.publish_module(self.config, payload)

    def test_module_root_token_may_not_escape_bundle_path_validation(self):
        payload = self.payload("duplicate-root-token")
        payload["parameters"]["arguments"] = [
            "{{moduleRoot}}/runner.py/{{moduleRoot}}"
        ]
        with self.assertRaisesRegex(ValueError, "exactly one Module root token"):
            module_publication.publish_module(self.config, payload)

        with self.assertRaisesRegex(ValueError, "reserved Module root token"):
            module_publication.decode_bundle_files([{
                "path": "{{moduleRoot}}/runner.py",
                "contentBase64": base64.b64encode(RUNNER).decode(),
                "executable": True,
            }])

    def test_workspace_ids_are_collision_resistant_after_readable_normalization(self):
        self.assertNotEqual(
            module_workspaces.workspace_id("Signal", "foo.bar", "1"),
            module_workspaces.workspace_id("Signal", "foo+bar", "1"),
        )
        shared_prefix = "same-prefix-" + "x" * 200
        self.assertNotEqual(
            module_workspaces.workspace_id("Signal", shared_prefix + "a", "1"),
            module_workspaces.workspace_id("Signal", shared_prefix + "b", "1"),
        )

    def test_archive_is_read_only_complete_and_verified_before_listing(self):
        definition = self.save(self.payload("verified"))
        root = Path(definition["archive"]["root"])
        self.assertTrue((root / version_archive.MANIFEST_NAME).is_file())
        self.assertTrue((root / version_archive.RECORD_NAME).is_file())
        for path in [root, *root.rglob("*")]:
            self.assertFalse(path.stat().st_mode & 0o222)
        version_archive.verify_record(definition)
        self.assertIn(
            "Signal/verified/1",
            module_definitions.load_pipeline_definitions(self.config),
        )

    def test_index_record_tampering_is_rejected(self):
        definition = self.save(self.payload("indexed"))
        state = control_state.load_state(self.config, "modules.json", {})
        state["Signal/indexed/1"] = {**definition, "name": "forged"}
        control_state.save_state(self.config, "modules.json", state)
        with self.assertRaisesRegex(ValueError, "index record does not match"):
            module_definitions.load_pipeline_definitions(self.config)

    def test_version_history_gaps_are_rejected(self):
        self.save(self.payload("history"))
        self.save(self.payload("history", description="second"))
        state = control_state.load_state(self.config, "modules.json", {})
        state.pop("Signal/history/1")
        control_state.save_state(self.config, "modules.json", state)
        with self.assertRaisesRegex(ValueError, "complete and monotonic"):
            module_definitions.load_pipeline_definitions(self.config)

    def test_archive_file_tampering_is_rejected(self):
        definition = self.save(self.payload("tampered"))
        runner = Path(definition["archive"]["root"]) / "runner.py"
        runner.chmod(runner.stat().st_mode | stat.S_IWUSR)
        runner.write_text("changed\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "verification failed"):
            version_archive.verify_record(definition)

    def test_edit_workspace_is_a_draft_copy_not_a_new_archive(self):
        definition = self.save(self.payload("workspace"))
        opened = module_workspaces.open_edit_workspace(
            self.config, "Signal", "workspace", definition["version"]
        )
        workspace = Path(opened["workspacePath"])
        draft = control_state.load_json_file(workspace / "module-draft.json", {})
        self.assertNotIn("version", draft)
        self.assertNotIn("archive", draft)
        self.assertTrue(workspace.stat().st_mode & stat.S_IWUSR)
        (workspace / "runner.py").write_text("draft change\n", encoding="utf-8")
        self.assertEqual(
            (Path(definition["archive"]["root"]) / "runner.py").read_bytes(),
            RUNNER,
        )

    def test_open_workspace_verifies_source_archive_once(self):
        definition = self.save(self.payload("workspace-single-verification"))
        with mock.patch.object(
            version_archive,
            "verify_archive",
            wraps=version_archive.verify_archive,
        ) as verify_archive:
            module_workspaces.open_edit_workspace(
                self.config,
                "Signal",
                "workspace-single-verification",
                definition["version"],
            )
        self.assertEqual(verify_archive.call_count, 1)

    def test_existing_workspace_with_mismatched_marker_is_rejected_without_repair(self):
        definition = self.save(self.payload("workspace-marker"))
        opened = module_workspaces.open_edit_workspace(
            self.config, "Signal", "workspace-marker", definition["version"]
        )
        workspace = Path(opened["workspacePath"])
        marker_path = workspace / ".module-workspace.json"
        draft_path = workspace / "module-draft.json"
        control_state.atomic_write_json(marker_path, {
            "schemaVersion": 1,
            "workspaceId": opened["workspaceId"],
            "sourceModuleKey": "Signal/different/1",
            "createdAt": "2026-01-01T00:00:00Z",
        })
        marker_before = marker_path.read_bytes()
        draft_before = draft_path.read_bytes()

        with self.assertRaisesRegex(ValueError, "identity metadata is invalid"):
            module_workspaces.open_edit_workspace(
                self.config, "Signal", "workspace-marker", definition["version"]
            )

        self.assertEqual(marker_path.read_bytes(), marker_before)
        self.assertEqual(draft_path.read_bytes(), draft_before)

    def test_workspace_publish_uses_the_common_archive_and_preserves_relative_files(self):
        first = self.save(self.payload("workspace-publish"))
        opened = module_workspaces.open_edit_workspace(
            self.config, "Signal", "workspace-publish", first["version"]
        )
        workspace = Path(opened["workspacePath"])
        (workspace / "src" / "package").mkdir(parents=True)
        (workspace / "src" / "package" / "helper.py").write_text("VALUE = 2\n", encoding="utf-8")
        (workspace / "src" / "package" / "module.json").write_text("{}\n", encoding="utf-8")
        (workspace / "runner.py").write_text("print('version two')\n", encoding="utf-8")

        result = module_workspaces.publish_edit_workspace(
            self.config, "pipeline", "Signal", "workspace-publish", first["version"]
        )
        second = result["definition"]
        archive = Path(second["archive"]["root"])
        self.assertEqual(second["version"], "2")
        self.assertTrue((archive / "src" / "package" / "helper.py").is_file())
        self.assertTrue((archive / "src" / "package" / "module.json").is_file())
        self.assertFalse((archive / ".module-workspace.json").exists())
        self.assertFalse((archive / "module-draft.json").exists())
        self.assertEqual(second["parameters"]["workingDirectory"], str(archive))
        self.assertEqual(second["parameters"]["arguments"], [])
        self.assertEqual(second["parameters"]["command"], str(archive / "runner.py"))
        unchanged = module_workspaces.publish_edit_workspace(
            self.config, "pipeline", "Signal", "workspace-publish", first["version"]
        )
        self.assertTrue(unchanged["unchanged"])
        self.assertEqual(unchanged["definition"]["version"], "2")

    def test_workspace_publish_requires_jupyter_termination_proof(self):
        first = self.save(self.payload("publish-stop-proof"))
        module_workspaces.open_edit_workspace(
            self.config, "Signal", "publish-stop-proof", first["version"]
        )
        with (
            mock.patch.object(
                module_workspaces.jupyter_workspaces,
                "stop_workspace_server",
                side_effect=RuntimeError("termination is unproven"),
            ),
            mock.patch.object(
                module_publication, "publish_module"
            ) as add_module,
        ):
            with self.assertRaisesRegex(RuntimeError, "unproven"):
                module_workspaces.publish_edit_workspace(
                    self.config,
                    "pipeline",
                    "Signal",
                    "publish-stop-proof",
                    first["version"],
                )
        add_module.assert_not_called()

    def test_jupyter_stop_wait_does_not_hold_the_control_transaction(self):
        first = self.save(self.payload("stop-lock-order"))
        module_workspaces.open_edit_workspace(
            self.config, "Signal", "stop-lock-order", first["version"]
        )
        stop_entered = threading.Event()
        release_stop = threading.Event()
        control_acquired = threading.Event()
        errors = []

        def blocking_stop(*_args):
            stop_entered.set()
            release_stop.wait(timeout=5)

        def publish():
            try:
                module_workspaces.publish_edit_workspace(
                    self.config,
                    "pipeline",
                    "Signal",
                    "stop-lock-order",
                    first["version"],
                )
            except BaseException as exc:
                errors.append(exc)

        def unrelated_control_transaction():
            with control_state.control_state_lock(self.config):
                control_acquired.set()

        with mock.patch.object(
            module_workspaces.jupyter_workspaces,
            "stop_workspace_server",
            side_effect=blocking_stop,
        ):
            publisher = threading.Thread(target=publish)
            publisher.start()
            self.assertTrue(stop_entered.wait(timeout=5))
            unrelated = threading.Thread(target=unrelated_control_transaction)
            unrelated.start()
            self.assertTrue(control_acquired.wait(timeout=1))
            unrelated.join(timeout=1)
            release_stop.set()
            publisher.join(timeout=10)
        self.assertFalse(publisher.is_alive())
        self.assertFalse(unrelated.is_alive())
        self.assertFalse(errors)

    def test_repository_editors_use_the_same_archive_and_separate_repositories(self):
        analyzer = self.save(self.payload("analyzer", kind="Analyzer"))
        environment = self.save(self.payload("environment", kind="Environment"))
        analysis_workspace = module_workspaces.open_repository_edit_workspace(
            self.config, "analysis", "Analyzer", "analyzer", analyzer["version"]
        )
        environment_workspace = module_workspaces.open_repository_edit_workspace(
            self.config, "environment", "Environment", "environment", environment["version"]
        )
        self.assertNotEqual(analysis_workspace["workspacePath"], environment_workspace["workspacePath"])
        self.assertIn(
            "Analyzer/analyzer/1",
            module_definitions.load_analysis_definitions(self.config),
        )
        self.assertIn(
            "Environment/environment/1",
            module_definitions.load_environment_definitions(self.config),
        )

    def test_engine_owned_module_identity_cannot_be_overwritten(self):
        self.save(self.payload("owned"), engine_owned=True)
        with self.assertRaisesRegex(ValueError, "ownership cannot change"):
            self.save(self.payload("owned", description="user change"))

    def test_user_owned_module_identity_cannot_be_taken_over_by_engine(self):
        first = self.save(self.payload("user-owned"))
        with self.assertRaisesRegex(ValueError, "ownership cannot change"):
            self.save(
                self.payload("user-owned", description="engine takeover"),
                engine_owned=True,
            )
        definitions = module_definitions.load_pipeline_definitions(self.config)
        self.assertEqual(
            [record["version"] for record in definitions.values()
             if record["moduleId"] == "user-owned"],
            [first["version"]],
        )


if __name__ == "__main__":
    unittest.main()
