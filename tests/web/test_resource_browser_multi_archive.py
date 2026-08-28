import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
BROWSER_SOURCE = (ROOT / "web_src" / "trade_resource_browser.jsx").read_text(
    encoding="utf-8"
)
APP_SOURCE = (ROOT / "web" / "app.js").read_text(encoding="utf-8")


class ResourceBrowserMultiArchiveTests(unittest.TestCase):
    def test_ctrl_or_command_click_uses_checkbox_selection_update(self):
        self.assertIn("function handleResourceClickCapture(event)", BROWSER_SOURCE)
        self.assertIn("event.ctrlKey || event.metaKey", BROWSER_SOURCE)
        self.assertIn("checkbox.click();", BROWSER_SOURCE)
        self.assertIn("onClickCapture={handleResourceClickCapture}", BROWSER_SOURCE)

    def test_backtest_context_menu_preserves_and_archives_selection(self):
        backtest_branch = BROWSER_SOURCE.split(
            'if (repository === "backtest") {', 1
        )[1].split('if (repository !== "pipelines")', 1)[0]
        self.assertIn("entries: selectedResourceEntries()", backtest_branch)
        self.assertNotIn("setSelection([clickedEntry])", backtest_branch)
        self.assertIn("Archive ${archivable.length} Backtests", BROWSER_SOURCE)

    def test_multi_selection_inspector_exposes_batch_archive(self):
        self.assertIn("tradeBatch: true", BROWSER_SOURCE)
        self.assertIn("tradeRecords: archivable.map", BROWSER_SOURCE)
        self.assertIn("Archive {archivable.length}", BROWSER_SOURCE)

    def test_backtest_batch_action_archives_every_selected_id_then_refreshes(self):
        branch = APP_SOURCE.split(
            'if (repository === "backtest" && action === "archive") {', 1
        )[1].split('if (sourceRepository === "workspaces"', 1)[0]
        self.assertIn("Array.isArray(item?.items)", branch)
        self.assertIn("for (const backtest of backtests)", branch)
        self.assertIn("await loadBacktests(true)", branch)
        self.assertIn("failures.length", branch)


if __name__ == "__main__":
    unittest.main()
