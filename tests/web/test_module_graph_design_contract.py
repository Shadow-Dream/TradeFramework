#!/usr/bin/env python3
"""Static UI contracts shared by every repository Module Graph surface."""

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]
APP_SOURCE = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
GRAPH_SOURCE = (ROOT / "web" / "module_graph_litegraph.js").read_text(
    encoding="utf-8"
)
HTML_SOURCE = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
CSS_SOURCE = (ROOT / "web" / "styles.css").read_text(encoding="utf-8")


class ModuleGraphDesignContractTests(unittest.TestCase):
    def test_all_three_resource_surfaces_mount_the_one_graph_renderer(self):
        self.assertEqual(APP_SOURCE.count("window.ModuleGraphLiteGraph?.mount({"), 2)
        self.assertIn("blueprintImpl?.mount({", APP_SOURCE)
        self.assertIn('const root = $("alphaGraphBuilder")', APP_SOURCE)
        self.assertIn('const root = $("environmentGraphBuilder")', APP_SOURCE)
        self.assertIn('const root = $("analysisGraphBuilder")', APP_SOURCE)
        self.assertEqual(HTML_SOURCE.count('/module_graph_litegraph.js'), 1)
        self.assertNotIn('/alpha_blueprint_litegraph.js', HTML_SOURCE)

    def test_confirmed_two_row_toolbar_and_right_sidebar_own_all_controls(self):
        toolbar = GRAPH_SOURCE.split(
            '<div class="alpha-litegraph-toolbar">', 1
        )[1].split('<div class="alpha-litegraph-body">', 1)[0]
        inspector = GRAPH_SOURCE.split(
            '<aside class="alpha-litegraph-inspector">', 1
        )[1].split("</aside>", 1)[0]
        self.assertIn("alpha-litegraph-toolbar-row-primary", toolbar)
        self.assertIn("alpha-litegraph-toolbar-row-secondary", toolbar)
        for selector in (
            "data-graph-arrange",
            "data-graph-fit",
            "data-graph-fullscreen",
            "data-graph-toggle-explorer",
            "data-graph-undo",
            "data-graph-redo",
            "data-graph-version",
        ):
            self.assertIn(selector, toolbar)
        for selector in (
            "data-graph-module",
            "data-graph-add-module",
            "data-graph-input",
            "data-graph-output",
        ):
            self.assertNotIn(selector, toolbar)
            self.assertIn(selector, inspector)

    def test_legacy_alpha_blueprint_x6_and_svg_renderer_are_absent(self):
        for source in (APP_SOURCE, GRAPH_SOURCE, HTML_SOURCE, CSS_SOURCE):
            self.assertNotIn("alpha-blueprint-", source)
        self.assertNotIn("/vendor/x6/", HTML_SOURCE)
        for marker in ("X6", "createElementNS", "<svg", "graph-edges"):
            self.assertNotIn(marker, GRAPH_SOURCE)

    def test_node_explorer_retains_search_filter_selection_and_detail(self):
        for contract in (
            'type="search" data-graph-module',
            'data-graph-explorer-mode="all"',
            'data-graph-explorer-mode="outputs"',
            'data-graph-explorer-mode="issues"',
            "data-graph-explorer-filter",
            'data-graph-explorer-action="select"',
            "data-graph-selected-detail",
            "canvas.onSelectionChange =",
            "selectNodes([...graph._nodes]);",
        ):
            self.assertIn(contract, GRAPH_SOURCE)

    def test_confirmed_port_connection_actions_are_present(self):
        for contract in (
            'data-graph-port-action="add-source"',
            '${source ? "Replace" : "Connect"}',
            'data-graph-port-action="pick-source"',
            '>Use Existing</button>',
            'data-graph-port-action="disconnect-input"',
            '>Disconnect</button>',
            'data-graph-port-action="add-downstream"',
            '>Add Downstream</button>',
            'data-graph-port-action="pick-target"',
            'data-graph-port-action="disconnect-link"',
        ):
            self.assertIn(contract, GRAPH_SOURCE)

    def test_fullscreen_sidebar_and_history_have_executable_handlers(self):
        for contract in (
            'root.classList.toggle("alpha-graph-fullscreen", graphFullscreen)',
            'root.classList.toggle("alpha-explorer-collapsed", explorerCollapsed)',
            '[data-graph-fullscreen]").addEventListener("click"',
            '[data-graph-toggle-explorer]").addEventListener("click"',
            '[data-graph-undo]").addEventListener("click", undo)',
            '[data-graph-redo]").addEventListener("click", redo)',
            "recordHistory(next);",
            "rebuild(entry.snapshot);",
        ):
            self.assertIn(contract, GRAPH_SOURCE)

    def test_layout_contract_is_360_sidebar_and_12_pixel_fullscreen_inset(self):
        self.assertIn("--alpha-inspector-width: 360px;", CSS_SOURCE)
        self.assertIn(
            "grid-template-columns: minmax(0, 1fr) var(--alpha-inspector-width);",
            CSS_SOURCE,
        )
        fullscreen = CSS_SOURCE.split(
            ".alpha-graph-builder.alpha-graph-fullscreen {", 1
        )[1].split("}", 1)[0]
        self.assertIn("inset: 12px;", fullscreen)
        self.assertIn("width: auto;", fullscreen)
        self.assertIn("height: auto;", fullscreen)
        self.assertIn(".alpha-explorer-collapsed .alpha-litegraph-body", CSS_SOURCE)

    def test_graph_snapshot_and_internal_links_are_preserved_by_history(self):
        snapshot = GRAPH_SOURCE.split("function snapshot() {", 1)[1].split(
            "function positionsSnapshot()", 1
        )[0]
        clipboard = GRAPH_SOURCE.split("function copySelection() {", 1)[1].split(
            "function pasteSelection()", 1
        )[0]
        self.assertIn("inputWire(node, slot)", snapshot)
        self.assertIn("inputWire(node, 0)", snapshot)
        self.assertIn("Object.values(graph.links || {})", clipboard)
        self.assertIn("origin_slot", clipboard)
        self.assertIn("target_slot", clipboard)


if __name__ == "__main__":
    unittest.main()
