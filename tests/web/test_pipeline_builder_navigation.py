#!/usr/bin/env python3
"""Static contracts for the Pipeline Browser-to-Builder boundary."""

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]
APP_SOURCE = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
HTML_SOURCE = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
BROWSER_SOURCE = (ROOT / "web_src" / "trade_resource_browser.jsx").read_text(
    encoding="utf-8"
)
ENGINE_SOURCE = (ROOT / "engine_service.py").read_text(encoding="utf-8")


class PipelineBuilderNavigationTests(unittest.TestCase):
    def test_pipeline_browser_and_builder_are_distinct_pages(self):
        self.assertIn('id="pipelineRepositoryPage"', HTML_SOURCE)
        self.assertIn(
            'id="pipelineBuilderPage" class="pipeline-page pipeline-subcontent blueprint-route-content" hidden',
            HTML_SOURCE,
        )
        self.assertIn('id="backToPipelineListBtn"', HTML_SOURCE)
        self.assertIn('"/pipeline/builder",', ENGINE_SOURCE)
        self.assertIn('if (path === "/pipeline/builder")', APP_SOURCE)
        self.assertIn('currentPipelinePage = "browser";', APP_SOURCE)
        self.assertIn('currentPipelinePage = "builder";', APP_SOURCE)

    def test_browser_route_does_not_initialize_the_builder(self):
        loader = APP_SOURCE.split("async function loadPipeline(force = false) {", 1)[1].split(
            "async function loadResults", 1
        )[0]
        browser_boundary, builder_boundary = loader.split(
            'const selectedPipelineId = requestedPipelineId || pipelineEditorState.pipelineId;', 1
        )
        self.assertIn('loadedViews.add("pipeline-browser")', browser_boundary)
        self.assertIn('renderEmbeddedRepositoryBrowser("pipelines")', browser_boundary)
        self.assertIn("return;", browser_boundary)
        self.assertNotIn('getJson("/api/modules?limit=500")', browser_boundary)
        self.assertNotIn("loadPipelineSelection", browser_boundary)
        self.assertIn('getJson("/api/modules?limit=500")', builder_boundary)
        self.assertIn("loadPipelineSelection", builder_boundary)

    def test_open_and_double_click_enter_the_builder(self):
        repository_open = APP_SOURCE.split(
            'async function openRepositoryItem(repository, itemId, openContext = {}) {', 1
        )[1].split("function openBacktestResult", 1)[0]
        self.assertIn(
            'await openPipelineBuilder(item.pipelineId || item.itemId);',
            repository_open,
        )
        self.assertIn("onDoubleClickCapture={handleResourceDoubleClick}", BROWSER_SOURCE)
        double_click = BROWSER_SOURCE.split("function handleResourceDoubleClick(event) {", 1)[
            1
        ].split("async function pipelineContextAction", 1)[0]
        self.assertIn("openEntry(entry);", double_click)
        context_open = BROWSER_SOURCE.split(
            "async function pipelineContextAction(action) {", 1
        )[1].split("async function moduleContextAction", 1)[0]
        self.assertIn('action === "open"', context_open)
        self.assertIn("await openEntry(entry);", context_open)

    def test_back_navigation_returns_to_the_browser(self):
        close = APP_SOURCE.split("function closePipelineBuilder({ replace = false } = {}) {", 1)[
            1
        ].split("function switchView", 1)[0]
        self.assertIn('currentPipelinePage = "browser";', close)
        self.assertIn('"/pipeline",', close)
        self.assertIn(
            '$("backToPipelineListBtn")?.addEventListener("click"', APP_SOURCE
        )
        self.assertNotIn("/pipeline?pipelineId=", APP_SOURCE)


if __name__ == "__main__":
    unittest.main()
