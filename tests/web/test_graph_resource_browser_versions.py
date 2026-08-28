#!/usr/bin/env python3
"""Static contracts for Graph resource Browser and Detail version ownership."""

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]
APP_SOURCE = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
BROWSER_SOURCE = (ROOT / "web_src" / "trade_resource_browser.jsx").read_text(
    encoding="utf-8"
)
GRAPH_SOURCE = (ROOT / "web" / "module_graph_litegraph.js").read_text(
    encoding="utf-8"
)
CSS_SOURCE = (ROOT / "web" / "styles.css").read_text(encoding="utf-8")


class GraphResourceBrowserVersionTests(unittest.TestCase):
    def test_browser_opens_the_latest_projected_version_key(self):
        repository_open = APP_SOURCE.split(
            "async function openRepositoryItem(repository, itemId, openContext = {}) {", 1
        )[1].split("function openBacktestResult", 1)[0]
        self.assertIn(
            "openEnvironmentBlueprint(item.versionKey || item.sourceItemId || item.itemId",
            repository_open,
        )
        self.assertIn(
            "openAnalysisBlueprint(item.versionKey || item.sourceItemId || item.itemId",
            repository_open,
        )

    def test_version_history_is_described_as_detail_not_browser_entries(self):
        self.assertNotIn("archived Analysis Version(s)", APP_SOURCE)
        self.assertNotIn("archived Environment Version(s)", APP_SOURCE)
        self.assertIn("Latest Version shown · Open one to edit or switch Version", APP_SOURCE)
        self.assertIn("versions: analysisVersions", APP_SOURCE)
        self.assertIn("versions: environmentVersions", APP_SOURCE)

    def test_environment_and_analysis_have_explicit_rename_entry(self):
        self.assertIn('["environments", "analyses"].includes(repository)', BROWSER_SOURCE)
        self.assertIn('repository === "environments" && action === "rename"', APP_SOURCE)
        self.assertIn('repository === "analyses" && action === "rename"', APP_SOURCE)
        self.assertIn("data-graph-rename", GRAPH_SOURCE)

    def test_module_picker_is_owned_by_the_right_inspector(self):
        toolbar = GRAPH_SOURCE.split('<div class="alpha-litegraph-toolbar">', 1)[1].split(
            '<div class="alpha-litegraph-body">', 1
        )[0]
        inspector = GRAPH_SOURCE.split('<aside class="alpha-litegraph-inspector">', 1)[1].split(
            "</aside>", 1
        )[0]
        self.assertNotIn("data-graph-module", toolbar)
        self.assertIn('class="alpha-litegraph-node-add"', inspector)
        self.assertIn("data-graph-module", inspector)
        self.assertIn("data-graph-add-module", inspector)

    def test_canvas_selection_drives_a_scrollable_detail_inspector(self):
        self.assertIn("canvas.onSelectionChange =", GRAPH_SOURCE)
        self.assertIn("data-graph-selected-detail", GRAPH_SOURCE)
        self.assertIn("overflow-y: auto;", CSS_SOURCE)
        self.assertIn(".alpha-litegraph-inspector-list-item.active", CSS_SOURCE)

    def test_graph_arrange_uses_dependency_layers_and_invalidates_legacy_layout(self):
        self.assertIn('const positionPrefix = "trade.module-graph.positions.v2:"', GRAPH_SOURCE)
        self.assertIn("Object.values(graph.links || {}).filter(Boolean)", GRAPH_SOURCE)
        self.assertIn("depth.get(source.id) + 1", GRAPH_SOURCE)
        arrange = GRAPH_SOURCE.split("function arrange() {", 1)[1].split(
            "function copySelection()", 1
        )[0]
        self.assertIn("layeredLayout();", arrange)
        self.assertNotIn("scheduleEmit", arrange)

    def test_graph_fit_is_implemented_locally_and_used_by_the_toolbar(self):
        self.assertIn("function fitGraph() {", GRAPH_SOURCE)
        self.assertIn('addEventListener("click", fitGraph)', GRAPH_SOURCE)
        self.assertNotIn("canvas.fit?.", GRAPH_SOURCE)


if __name__ == "__main__":
    unittest.main()
