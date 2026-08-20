#!/usr/bin/env python3
"""Static contracts for typed and single-kind Module Browser folders."""

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]
APP_SOURCE = (ROOT / "web" / "app.js").read_text(encoding="utf-8")


class ModuleRepositoryFolderTests(unittest.TestCase):
    def test_only_pipeline_modules_are_constrained_to_type_roots(self):
        options = APP_SOURCE.split(
            "function repositoryFolderOptions(repository, item = null) {", 1
        )[1].split("function setRepositoryError", 1)[0]
        self.assertIn('if (repository === "modules" && item)', options)
        self.assertIn('const fixedPlacement = repository === "modules";', options)
        self.assertNotIn("MODULE_REPOSITORY_IDS.has(repository) && item", options)

    def test_single_kind_module_browsers_expose_the_repository_root(self):
        tree = APP_SOURCE.split("function renderRepositoryFolderTree() {", 1)[1].split(
            "function renderRepositoryCards", 1
        )[0]
        self.assertIn('if (repository !== "modules")', tree)
        dialog = APP_SOURCE.split("function openRepositoryFolderDialog(mode) {", 1)[
            1
        ].split("const EMBEDDED_REPOSITORIES", 1)[0]
        self.assertIn('repository === "modules" ?', dialog)


if __name__ == "__main__":
    unittest.main()
