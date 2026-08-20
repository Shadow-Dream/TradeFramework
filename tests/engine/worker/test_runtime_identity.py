#!/usr/bin/env python3

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from engine.core import runtime_identity
from engine.runtime import process_session

class RuntimeIdentityTests(unittest.TestCase):
    def test_declared_release_site_packages_is_the_only_distribution_root(self):
        with tempfile.TemporaryDirectory() as temporary:
            release_site = Path(temporary) / "runtime/lib/python3.10/site-packages"
            release_site.mkdir(parents=True)
            with (
                mock.patch.dict(
                    os.environ,
                    {"TRADE_ENGINE_RUNTIME_SITE_PACKAGES": str(release_site)},
                    clear=False,
                ),
                mock.patch.object(
                    sys, "path", [str(release_site), "/ambient/dist-packages"]
                ),
                mock.patch(
                    "engine.core.runtime_identity.site.getsitepackages"
                ) as ambient_site,
            ):
                roots = runtime_identity._python_distribution_paths()
        self.assertEqual(roots, (str(release_site.resolve()),))
        ambient_site.assert_not_called()

    def test_declared_release_site_packages_must_be_importable(self):
        with tempfile.TemporaryDirectory() as temporary:
            release_site = Path(temporary) / "site-packages"
            release_site.mkdir()
            with (
                mock.patch.dict(
                    os.environ,
                    {"TRADE_ENGINE_RUNTIME_SITE_PACKAGES": str(release_site)},
                    clear=False,
                ),
                mock.patch.object(sys, "path", ["/ambient/dist-packages"]),
            ):
                with self.assertRaisesRegex(RuntimeError, "not importable"):
                    runtime_identity._python_distribution_paths()

    def test_distribution_roots_preserve_precedence_deduplicate_and_exclude_user_site(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            site_root = root / "site"
            purelib_root = root / "purelib"
            user_root = root / "user-site"
            with (
                mock.patch(
                    "engine.core.runtime_identity.site.getsitepackages",
                    return_value=[site_root, purelib_root, site_root],
                ),
                mock.patch(
                    "engine.core.runtime_identity.site.getusersitepackages",
                    return_value=str(user_root),
                ) as user_site,
                mock.patch(
                    "engine.core.runtime_identity.sysconfig.get_paths",
                    return_value={
                        "purelib": str(purelib_root),
                        "platlib": str(site_root),
                    },
                ),
            ):
                roots = runtime_identity._python_distribution_paths()
        self.assertEqual(
            roots,
            (str(site_root.absolute()), str(purelib_root.absolute())),
        )
        self.assertNotIn(str(user_root.absolute()), roots)
        user_site.assert_not_called()

    def test_digest_hashes_root_contents_but_ignores_ambient_files_and_caches(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            installation = root / "installation"
            source = installation / "runtime_probe" / "core.py"
            source.parent.mkdir(parents=True)
            source.write_text("VALUE = 1\n", encoding="utf-8")
            ambient = root / "ambient.py"
            ambient.write_text("VALUE = 1\n", encoding="utf-8")
            with mock.patch.object(
                runtime_identity,
                "_python_distribution_paths",
                return_value=(str(installation),),
            ):
                runtime_identity.python_environment_digest.cache_clear()
                first = runtime_identity.python_environment_digest()
                ambient.write_text("VALUE = 2\n", encoding="utf-8")
                cache = source.parent / "__pycache__" / "core.pyc"
                cache.parent.mkdir()
                cache.write_bytes(b"runtime cache noise")
                runtime_identity.python_environment_digest.cache_clear()
                unchanged = runtime_identity.python_environment_digest()
                source.write_text("VALUE = 2\n", encoding="utf-8")
                runtime_identity.python_environment_digest.cache_clear()
                second = runtime_identity.python_environment_digest()
            runtime_identity.python_environment_digest.cache_clear()
            self.assertEqual(first, unchanged)
            self.assertNotEqual(first, second)

    def test_digest_follows_a_bounded_external_directory_symlink(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            installation = root / "installation"
            external = root / "external"
            installation.mkdir()
            external.mkdir()
            source = external / "linked_module.py"
            source.write_text("VALUE = 1\n", encoding="utf-8")
            (installation / "linked").symlink_to(external, target_is_directory=True)
            with mock.patch.object(
                runtime_identity,
                "_python_distribution_paths",
                return_value=(str(installation),),
            ):
                runtime_identity.python_environment_digest.cache_clear()
                first = runtime_identity.python_environment_digest()
                source.write_text("VALUE = 2\n", encoding="utf-8")
                runtime_identity.python_environment_digest.cache_clear()
                second = runtime_identity.python_environment_digest()
            runtime_identity.python_environment_digest.cache_clear()
        self.assertNotEqual(first, second)

    def test_digest_resolves_only_symbolic_links_without_losing_external_scope(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            installation = root / "installation"
            external = root / "external"
            installation.mkdir()
            external.mkdir()
            (installation / "ordinary.py").write_text(
                "VALUE = 1\n",
                encoding="utf-8",
            )
            (external / "linked.py").write_text(
                "VALUE = 2\n",
                encoding="utf-8",
            )
            (installation / "linked").symlink_to(
                external,
                target_is_directory=True,
            )
            real_resolve = Path.resolve
            resolved = []

            def record_resolve(path, *args, **kwargs):
                resolved.append(Path(path))
                return real_resolve(path, *args, **kwargs)

            with (
                mock.patch.object(
                    runtime_identity,
                    "_python_distribution_paths",
                    return_value=(str(installation),),
                ),
                mock.patch.object(Path, "resolve", record_resolve),
            ):
                runtime_identity.python_environment_digest.cache_clear()
                runtime_identity.python_environment_digest()
            runtime_identity.python_environment_digest.cache_clear()

        resolved_names = [path.name for path in resolved]
        self.assertIn("linked", resolved_names)
        self.assertNotIn("ordinary.py", resolved_names)
        self.assertNotIn("linked.py", resolved_names)

    def test_external_scope_budget_cannot_be_escaped_by_links_back_inside(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            installation = root / "installation"
            external = root / "external"
            installation.mkdir()
            external.mkdir()
            internal = installation / "internal.py"
            internal.write_text("VALUE = 1\n", encoding="utf-8")
            (installation / "linked").symlink_to(external, target_is_directory=True)
            for name in ("one.py", "two.py", "three.py"):
                (external / name).symlink_to(internal)
            with (
                mock.patch.object(
                    runtime_identity,
                    "_python_distribution_paths",
                    return_value=(str(installation),),
                ),
                mock.patch.object(
                    runtime_identity,
                    "_MAX_EXTERNAL_INSTALLATION_NODES",
                    2,
                ),
                self.assertRaisesRegex(RuntimeError, "unbounded external"),
            ):
                runtime_identity.python_environment_digest.cache_clear()
                runtime_identity.python_environment_digest()
            runtime_identity.python_environment_digest.cache_clear()

    def test_digest_rejects_an_external_plain_pth_import_root(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            installation = root / "installation"
            external = root / "external"
            installation.mkdir()
            external.mkdir()
            (installation / "external.pth").write_text(
                str(external) + "\n",
                encoding="utf-8",
            )
            with mock.patch.object(
                runtime_identity,
                "_python_distribution_paths",
                return_value=(str(installation),),
            ), self.assertRaisesRegex(RuntimeError, "external import root"):
                runtime_identity.python_environment_digest.cache_clear()
                runtime_identity.python_environment_digest()
            runtime_identity.python_environment_digest.cache_clear()

    def test_digest_rejects_oversized_path_metadata_before_traversal(self):
        with tempfile.TemporaryDirectory() as temporary:
            installation = Path(temporary) / "installation"
            installation.mkdir()
            (installation / "oversized.pth").write_bytes(b"x" * 17)
            with (
                mock.patch.object(
                    runtime_identity,
                    "_python_distribution_paths",
                    return_value=(str(installation),),
                ),
                mock.patch.object(
                    runtime_identity,
                    "_MAX_INSTALLATION_METADATA_BYTES",
                    16,
                ),
                self.assertRaisesRegex(RuntimeError, "size limit"),
            ):
                runtime_identity.python_environment_digest.cache_clear()
                runtime_identity.python_environment_digest()
            runtime_identity.python_environment_digest.cache_clear()

    def test_full_identity_ignores_ambient_pythonpath_distribution(self):
        project_root = Path(__file__).resolve().parents[3]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            metadata = root / "ambient_noise-1.0.dist-info"
            metadata.mkdir()
            (metadata / "METADATA").write_text(
                "Metadata-Version: 2.1\nName: ambient-noise\nVersion: 1.0\n",
                encoding="utf-8",
            )
            (root / "ambient_noise.py").write_text(
                "VALUE = 'must not affect Backtest identity'\n",
                encoding="utf-8",
            )
            code = (
                "from engine.core.runtime_identity import engine_runtime_identity;"
                "import json;"
                "print(json.dumps(engine_runtime_identity(),sort_keys=True))"
            )
            parent_environment = dict(os.environ)
            parent_environment["PYTHONPATH"] = str(root)
            parent = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    code,
                ],
                cwd=project_root,
                env=parent_environment,
                check=True,
                capture_output=True,
                text=True,
            )
            child = subprocess.run(
                [sys.executable, "-c", code],
                cwd=project_root,
                env=process_session.minimal_host_environment(home=root / "worker"),
                check=True,
                capture_output=True,
                text=True,
            )
        self.assertEqual(
            json.loads(parent.stdout),
            json.loads(child.stdout),
        )
