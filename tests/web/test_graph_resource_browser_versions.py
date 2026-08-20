#!/usr/bin/env python3
"""Static contracts for Graph resource Browser and Detail version ownership."""

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]
APP_SOURCE = (ROOT / "web" / "app.js").read_text(encoding="utf-8")


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


if __name__ == "__main__":
    unittest.main()
