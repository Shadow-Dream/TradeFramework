#!/usr/bin/env python3

import json
import tempfile
import unittest
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from scripts.prepare_tlm01d02_evaluation_data import (
    DESCRIPTOR_BASE,
    _aggregate,
    _package,
    _write_csv,
)


def _bar(value: str, close: float) -> dict:
    return {
        "time": datetime.fromisoformat(value).astimezone(timezone.utc),
        "open": close - 1.0,
        "close": close,
        "high": close + 1.0,
        "low": close - 2.0,
        "volume": 100.0,
    }


class Tlm01d02EvaluationDataTests(unittest.TestCase):
    def test_history_stays_as_period_facts_instead_of_formal_cycles(self):
        historical_daily = [
            _bar("2024-08-19T21:00:00+00:00", 100.0),
            _bar("2024-08-20T21:00:00+00:00", 101.0),
        ]
        evaluation_daily = [_bar("2024-08-23T20:00:00+00:00", 102.0)]
        daily = [*historical_daily, *evaluation_daily]
        weekly = _aggregate(
            daily,
            lambda value: (value.isocalendar().year, value.isocalendar().week),
        )

        self.assertEqual(len(daily), 3)
        self.assertEqual(len(weekly), 1)
        self.assertEqual(weekly[0]["open"], historical_daily[0]["open"])
        self.assertEqual(weekly[0]["close"], evaluation_daily[0]["close"])
        self.assertEqual(weekly[0]["time"], evaluation_daily[0]["time"])

    def test_package_contains_only_basic_workflow_fact_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            instrument_id = "SPX"
            rows = [_bar("2024-08-23T15:30:00+00:00", 100.0)]
            for period in ("hour", "day", "week"):
                _write_csv(source / period / f"{instrument_id}.csv", rows)
            destination = root / "dataset.zip"
            descriptor = {**DESCRIPTOR_BASE, "quantityUnit": "index-unit"}

            _package(source, destination, descriptor, instrument_id)

            with zipfile.ZipFile(destination) as archive:
                names = set(archive.namelist())
                capabilities = json.loads(
                    archive.read("_trade_dataset_capabilities.json")
                )
            self.assertNotIn("bars.csv", names)
            self.assertEqual(
                names,
                {
                    "_trade_dataset_capabilities.json",
                    "basic_workflow.json",
                    "day/SPX.csv",
                    "hour/SPX.csv",
                    "week/SPX.csv",
                },
            )
            self.assertEqual(
                set(capabilities["capabilities"]), {"basicWorkflow", "visualization"}
            )
            self.assertNotIn("tlm01d02", json.dumps(capabilities).lower())


if __name__ == "__main__":
    unittest.main()
