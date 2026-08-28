#!/usr/bin/env python3
"""Contracts for current-only operational resource and Module selectors."""

import json
from pathlib import Path
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[2]
APP_SOURCE = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
CHART_SOURCE = (ROOT / "web" / "chart.js").read_text(encoding="utf-8")
GRAPH_SOURCE = (ROOT / "web" / "module_graph_litegraph.js").read_text(encoding="utf-8")
HTML_SOURCE = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
CHART_HTML_SOURCE = (ROOT / "web" / "chart.html").read_text(encoding="utf-8")
HELPER_PATH = ROOT / "web" / "version_selection.js"


class CurrentVersionSelectionTests(unittest.TestCase):
    def run_helper(self, expression):
        script = (
            f"const api = require({json.dumps(str(HELPER_PATH))});"
            f"process.stdout.write(JSON.stringify({expression}));"
        )
        completed = subprocess.run(
            ["node", "-e", script],
            check=True,
            capture_output=True,
            text=True,
        )
        return json.loads(completed.stdout)

    def test_current_rows_prefers_explicit_current_then_latest_numeric_version(self):
        result = self.run_helper(
            "api.currentRows(["
            "{moduleId:'explicit',version:'2',current:false},"
            "{moduleId:'explicit',version:'1',current:true},"
            "{moduleId:'latest',version:'2'},"
            "{moduleId:'latest',version:'10'}"
            "], ['moduleId']).map(row => [row.moduleId,row.version])"
        )
        self.assertEqual(result, [["explicit", "1"], ["latest", "10"]])

    def test_data_repository_projects_sampler_and_script_histories_to_current(self):
        result = self.run_helper(
            "api.projectCurrentCatalog({repository:'data',items:["
            "{itemId:'s1v1',sourceRepository:'samplers',samplerId:'s1',version:'1'},"
            "{itemId:'s1v2',sourceRepository:'samplers',samplerId:'s1',version:'2'},"
            "{itemId:'r1v1',sourceRepository:'scripts',recipeId:'r1',version:'1'},"
            "{itemId:'r1v3',sourceRepository:'scripts',recipeId:'r1',version:'3'},"
            "{itemId:'dataset',sourceRepository:'datasets',datasetId:'d1'}"
            "]}).items.map(row => row.itemId)"
        )
        self.assertEqual(result, ["s1v2", "r1v3", "dataset"])

    def test_operational_selectors_use_current_projection(self):
        self.assertIn(
            'currentRows(state.samplers, ["samplerId"])', APP_SOURCE
        )
        self.assertIn(
            'currentRows(state.environments, ["environmentId"])', APP_SOURCE
        )
        self.assertIn(
            'currentRows(state.analyses, ["analysisId"])', APP_SOURCE
        )
        self.assertIn('version.current === true', APP_SOURCE)
        self.assertIn('currentRows(state.datasetRecipes, ["recipeId"])', APP_SOURCE)
        self.assertIn('function resultModuleDefinitions()', APP_SOURCE)
        self.assertIn('function resultModuleDefinitions()', CHART_SOURCE)
        self.assertIn('moduleChoices: modules', APP_SOURCE)
        self.assertIn(
            'aria-label="Search current ${escapeHtml(moduleKind)} Modules"',
            GRAPH_SOURCE,
        )
        self.assertIn("const availableModules = (Array.isArray(moduleChoices)", GRAPH_SOURCE)

    def test_only_explicit_history_controls_list_all_resource_versions(self):
        prohibited = (
            "Select an archived",
            "archived version(s)",
            "Select a Sampler Version",
            "Select an Environment Version",
            "Select an Analysis Version",
        )
        for source in (APP_SOURCE, CHART_SOURCE, GRAPH_SOURCE):
            for phrase in prohibited:
                self.assertNotIn(phrase, source)
        self.assertIn('id="pipelineVersionSelect"', HTML_SOURCE)
        self.assertIn('data-graph-version', GRAPH_SOURCE)
        self.assertIn('/version_selection.js', HTML_SOURCE)
        self.assertIn('/version_selection.js', CHART_HTML_SOURCE)


if __name__ == "__main__":
    unittest.main()
