"""Shared Dataset Workspace test fixture."""

import tempfile
import unittest
from pathlib import Path

from dataset_adapters import ohlcv
from engine.control import database as engine_database
from engine.repository import dataset_recipes
from engine.repository import dataset_workspaces


class DatasetWorkspaceTestCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.config = {
            "controlRoot": str(root / "control"),
            "releaseRoot": str(root / "releases"),
        }
        engine_database.prepare_database(self.config)
        self.recipe_sequence = 0
        self.rows = [
            {"date": "2026-01-01", "open": 10, "high": 11, "low": 9, "close": 10, "volume": 100},
            {"date": "2026-01-02", "open": 10, "high": 13, "low": 10, "close": 12, "volume": 120},
        ]
        for dataset_id, symbol in (("hourly-source", "SPY"), ("daily-source", "QQQ")):
            ohlcv.register_dataset(
                self.config,
                dataset_id=dataset_id,
                name=dataset_id,
                symbol=symbol,
                source="test",
                interval="d",
                rows=self.rows,
                availability_policy="bar_end_utc",
            )

    def tearDown(self):
        self.temp.cleanup()

    def create_workspace(self, workspace_id="multi-source"):
        return dataset_workspaces.create_workspace(self.config, {
            "workspaceId": workspace_id,
            "name": "Multi source",
            "sources": [
                {"datasetId": "hourly-source", "alias": "dataset1"},
                {"datasetId": "daily-source", "alias": "dataset2"},
            ],
        })

    def archive_script(self, script_text):
        self.recipe_sequence += 1
        return dataset_recipes.save_recipe(self.config, {
            "recipeId": f"test-script-{self.recipe_sequence}",
            "name": f"Test Script {self.recipe_sequence}",
            "scriptText": script_text,
        })
