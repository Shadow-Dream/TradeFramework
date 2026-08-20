#!/usr/bin/env python3
"""JSON Schema and materialized configuration tests."""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from engine.authority.module_definition import verify_module_definition_authority
from engine.authority.module_material import directory_tree_fingerprint
from engine.contracts.json_schema import (
    normalize_config_schema,
    normalize_module_config_schema,
    validate_config,
)
from engine.runtime.module_implementation import materialize_verified_module_definition
from engine.runtime.module_implementation import materialized_module_definition_material


class ConfigurationAndSnapshotTests(unittest.TestCase):
    def test_configuration_uses_full_json_schema_validation(self):
        schema = normalize_config_schema({
            "type": "object",
            "properties": {"period": {"type": "integer", "minimum": 1}},
            "required": ["period"],
            "additionalProperties": False,
        })
        validate_config({"period": 2}, schema)
        with self.assertRaisesRegex(ValueError, "less than the minimum"):
            validate_config({"period": 0}, schema)
        with self.assertRaisesRegex(ValueError, "Additional properties"):
            validate_config({"period": 2, "hidden": True}, schema)

    def test_module_configuration_has_one_exact_json_object_domain(self):
        self.assertEqual(
            normalize_module_config_schema({}),
            {
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
        )
        for schema in (
            {"type": "string"},
            {"type": ["object", "null"]},
            {"const": {}},
            {"oneOf": [{"type": "object"}]},
        ):
            with self.subTest(schema=schema), self.assertRaisesRegex(
                ValueError, "root type must be 'object'"
            ):
                normalize_module_config_schema(schema)

        class DictSubclass(dict):
            pass

        class IntSubclass(int):
            pass

        schema = normalize_module_config_schema({
            "type": "object",
            "properties": {"period": {"type": "integer"}},
            "additionalProperties": False,
        })
        shared = []
        for value in (
            DictSubclass(),
            {"period": IntSubclass(2)},
            {"left": shared, "right": shared},
            {"period": object()},
        ):
            with self.subTest(value=type(value).__name__), self.assertRaisesRegex(
                ValueError, "finite exact JSON"
            ):
                validate_config(value, schema)

    def test_module_configuration_schema_is_constructively_satisfiable(self):
        accepted = ({
            "type": "object",
            "properties": {
                "items": {
                    "type": "array",
                    "items": {"type": ["number", "null"]},
                    "minItems": 2,
                },
                "choice": {"type": "string", "enum": ["a", "b"]},
            },
            "required": ["items", "choice"],
            "additionalProperties": False,
        })
        self.assertEqual(normalize_module_config_schema(accepted), accepted)

        for schema in (
            {
                "type": "object",
                "properties": {
                    "value": {
                        "type": "number",
                        "minimum": 100,
                        "exclusiveMinimum": 0,
                    },
                },
                "required": ["value"],
            },
            {
                "type": "object",
                "required": ["dynamic"],
                "additionalProperties": True,
            },
            {
                "type": "object",
                "properties": {
                    "dynamic": True,
                    "items": {
                        "type": "array",
                        "items": True,
                        "minItems": 1,
                    },
                },
                "required": ["dynamic", "items"],
            },
            {
                "type": "object",
                "properties": {
                    "value": {
                        "type": ["string", "null"],
                        "minLength": 10**30,
                    },
                    "items": {
                        "type": ["array", "null"],
                        "minItems": 10**30,
                    },
                    "choice": {
                        "type": ["object", "null"],
                        "properties": {
                            "large": {
                                "type": "string",
                                "minLength": 99_999,
                            },
                        },
                        "required": ["large"],
                        "maxProperties": 0,
                    },
                },
                "required": ["value", "items", "choice"],
            },
        ):
            with self.subTest(schema=schema):
                self.assertEqual(normalize_module_config_schema(schema), schema)

        rejected = (
            {"type": "object", "not": {}},
            {
                "type": "object",
                "required": ["missing"],
                "additionalProperties": False,
            },
            {
                "type": "object",
                "properties": {
                    "count": {"type": "integer", "minimum": 1, "maximum": 0},
                },
                "required": ["count"],
            },
            {"type": "object", "minProperties": 2, "maxProperties": 1},
            {
                "type": "object",
                "properties": {"count": {"type": "integer", "default": "bad"}},
            },
        )
        for schema in rejected:
            with self.subTest(schema=schema), self.assertRaises(ValueError):
                normalize_module_config_schema(schema)

        nested = {"type": "integer"}
        for _index in range(15):
            nested = {"type": "array", "items": nested, "minItems": 1}
        normalize_module_config_schema({
            "type": "object",
            "properties": {"nested": nested},
            "required": ["nested"],
        })
        with self.assertRaisesRegex(ValueError, "finite JSON value|witness beyond"):
            normalize_module_config_schema({
                "type": "object",
                "properties": {
                    "large": {
                        "type": "array",
                        "items": {
                            "type": "array",
                            "items": {"type": "integer"},
                            "minItems": 1000,
                        },
                        "minItems": 1000,
                    },
                },
                "required": ["large"],
            })

    def test_module_asset_snapshot_is_content_addressed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            source.mkdir()
            runner = source / "runner=name.py"
            runner.write_text("VALUE = 1\n", encoding="utf-8")
            runner.chmod(0o555)
            definition = {
                "kind": "Signal",
                "moduleId": "asset-snapshot",
                "name": "Asset Snapshot",
                "activationMode": "ProcessRunner",
                "parameters": {
                    "command": str(runner),
                    "arguments": [],
                    "workingDirectory": str(source),
                },
                "configSchema": {
                    "type": "object",
                    "additionalProperties": False,
                },
                "ports": {"inputs": {}, "outputs": {}},
                "description": "Content-addressed Module asset fixture.",
                "version": "1",
                "builtin": False,
                "archive": {
                    "resourceType": "module",
                    "resourceId": "Signal/asset-snapshot",
                    "root": str(source),
                },
            }
            with mock.patch(
                "engine.archive.version.verify_record"
            ):
                authority = verify_module_definition_authority(definition)
            materialized = materialize_verified_module_definition(
                authority, root / "assets", "modules"
            )
            frozen = materialized_module_definition_material(
                materialized,
                authority,
            )
            frozen_root = Path(frozen["parameters"]["workingDirectory"])
            expected_fingerprint = directory_tree_fingerprint(frozen_root)

            runner.chmod(0o755)
            runner.write_text("VALUE = 2\n", encoding="utf-8")

            self.assertEqual(
                expected_fingerprint,
                directory_tree_fingerprint(frozen_root),
            )
            self.assertEqual(
                (frozen_root / "runner=name.py").read_text(encoding="utf-8"),
                "VALUE = 1\n",
            )
            self.assertEqual(
                frozen["parameters"]["command"],
                str(frozen_root / "runner=name.py"),
            )

    def test_process_materialization_rebases_every_archive_reference(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            assets = source / "assets"
            assets.mkdir(parents=True)
            runner = source / "runner=name.py"
            runner.write_text("#!/usr/bin/env python3\n", encoding="utf-8")
            runner.chmod(0o555)
            data = assets / "data.json"
            data.write_text("{}\n", encoding="utf-8")
            data.chmod(0o444)
            definition = {
                "kind": "Signal",
                "moduleId": "complete-process-rebase",
                "name": "Complete Process Rebase",
                "activationMode": "ProcessRunner",
                "parameters": {
                    "command": str(runner),
                    "arguments": [
                        f"--source={data}",
                        str(data),
                        "literal-value",
                    ],
                    "workingDirectory": str(assets),
                },
                "configSchema": {"type": "object", "additionalProperties": False},
                "ports": {"inputs": {}, "outputs": {}},
                "description": "Complete ProcessRunner rebase fixture.",
                "version": "1",
                "builtin": False,
                "archive": {
                    "resourceType": "module",
                    "resourceId": "Signal/complete-process-rebase",
                    "root": str(source),
                },
            }
            with mock.patch("engine.archive.version.verify_record"):
                authority = verify_module_definition_authority(definition)
            materialized = materialize_verified_module_definition(
                authority, root / "execution", "modules"
            )
            frozen = materialized_module_definition_material(materialized, authority)
            isolated_root = Path(frozen["archive"]["root"])

            self.assertTrue(isolated_root.is_relative_to((root / "execution").resolve()))
            self.assertNotEqual(isolated_root, source.resolve())
            self.assertEqual(
                frozen["parameters"]["command"],
                str(isolated_root / "runner=name.py"),
            )
            self.assertEqual(
                frozen["parameters"]["workingDirectory"],
                str(isolated_root / "assets"),
            )
            self.assertEqual(
                frozen["parameters"]["arguments"],
                [
                    f"--source={isolated_root / 'assets' / 'data.json'}",
                    str(isolated_root / "assets" / "data.json"),
                    "literal-value",
                ],
            )
            serialized = repr(frozen)
            self.assertNotIn(str(source.resolve()), serialized)

    def test_process_materialization_rejects_symlinked_archive_assets(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            source.mkdir()
            external = root / "external.py"
            external.write_text("#!/usr/bin/env python3\n", encoding="utf-8")
            external.chmod(0o555)
            linked = source / "runner.py"
            linked.symlink_to(external)
            definition = {
                "kind": "Signal",
                "moduleId": "symlink-process-rebase",
                "name": "Symlink Process Rebase",
                "activationMode": "ProcessRunner",
                "parameters": {
                    "command": str(linked),
                    "arguments": [],
                    "workingDirectory": str(source),
                },
                "configSchema": {"type": "object", "additionalProperties": False},
                "ports": {"inputs": {}, "outputs": {}},
                "description": "Symlink ProcessRunner boundary fixture.",
                "version": "1",
                "builtin": False,
                "archive": {
                    "resourceType": "module",
                    "resourceId": "Signal/symlink-process-rebase",
                    "root": str(source),
                },
            }
            with mock.patch("engine.archive.version.verify_record"):
                authority = verify_module_definition_authority(definition)
            with self.assertRaisesRegex(ValueError, "outside|symbolic link"):
                materialize_verified_module_definition(
                    authority, root / "execution", "modules"
                )


if __name__ == "__main__":
    unittest.main()
