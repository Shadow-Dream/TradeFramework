"""Saved Visualization repository and transaction tests."""

import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from engine.contracts import strict_json
from engine.contracts import visualization as visualization_contracts
from engine.control import database as engine_database
from engine.repository import visualizations as visualization_repository


class VisualizationRepositoryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.config = {
            "controlRoot": str(root / "control"),
            "releaseRoot": str(root / "releases"),
            "liveRoot": str(root / "live"),
        }
        engine_database.prepare_database(self.config)
        self.initial_spec = visualization_contracts.default_spec("prices", "UTC")
        with engine_database.connect_database(self.config) as connection:
            connection.execute(
                """
                INSERT INTO datasets
                (dataset_id, name, source_json, created_at, metadata_json)
                VALUES (?, ?, ?, ?, ?)
                """,
                ("prices", "Prices", "{}", "2026-08-11T00:00:00Z", "{}"),
            )
            connection.execute(
                """
                INSERT INTO backtests
                (backtest_id, pipeline_id, dataset_id, name, status, runner,
                 created_at, completed_at, request_json, metrics_json,
                 visualization_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "bt_01K00000000000000000000000",
                    "pipeline",
                    "prices",
                    "Backtest",
                    "completed",
                    "test",
                    "2026-08-11T00:00:00Z",
                    "2026-08-11T00:00:01Z",
                    "{}",
                    "{}",
                    strict_json.dumps(self.initial_spec, sort_keys=True),
                ),
            )
            connection.commit()
        self.record = {
            "visualizationId": "backtest-current",
            "backtestId": "bt_01K00000000000000000000000",
            "name": "Current",
            "createdAt": "2026-08-11T12:00:00Z",
            "revision": 1,
            "spec": {
                **self.initial_spec,
                "panes": [{
                    "id": "empty",
                    "title": "Empty",
                    "role": "financial",
                    "view": {
                        "start": None,
                        "end": None,
                        "logScale": False,
                        "controlsCollapsed": False,
                    },
                    "visualizers": [],
                    "temporaryModules": [],
                }],
            },
        }

    def tearDown(self):
        self.temporary.cleanup()

    def test_save_updates_current_spec_and_upserts_one_strict_record(self):
        saved = visualization_repository.save_visualization(
            self.config,
            self.record,
            expected_revision=0,
        )
        self.assertEqual(saved, self.record)
        self.assertEqual(
            visualization_repository.get_visualization(
                self.config,
                self.record["visualizationId"],
            ),
            self.record,
        )
        with engine_database.connect_database(self.config) as connection:
            current = strict_json.loads(connection.execute(
                "SELECT visualization_json FROM backtests WHERE backtest_id = ?",
                (self.record["backtestId"],),
            ).fetchone()["visualization_json"])
        self.assertEqual(current, self.record["spec"])

        replacement = {
            **self.record,
            "name": "Renamed",
            "createdAt": "2026-08-11T13:00:00Z",
            "revision": 2,
        }
        visualization_repository.save_visualization(
            self.config, replacement, expected_revision=1
        )
        self.assertEqual(
            visualization_repository.list_visualizations(
                self.config,
                self.record["backtestId"],
            ),
            [replacement],
        )

    def test_list_filters_and_orders_newest_first(self):
        older = {
            **self.record,
            "visualizationId": "older",
            "createdAt": "2026-08-11T10:00:00Z",
        }
        newer = {
            **self.record,
            "visualizationId": "newer",
            "createdAt": "2026-08-11T11:00:00Z",
        }
        visualization_repository.save_visualization(
            self.config, older, expected_revision=0
        )
        visualization_repository.save_visualization(
            self.config, newer, expected_revision=0
        )
        self.assertEqual(
            [item["visualizationId"] for item in
             visualization_repository.list_visualizations(self.config)],
            ["newer", "older"],
        )
        self.assertEqual(
            len(visualization_repository.list_visualizations(
                self.config,
                self.record["backtestId"],
            )),
            2,
        )
        self.assertEqual(
            visualization_repository.list_visualizations(
                self.config,
                "bt_01K11111111111111111111111",
            ),
            [],
        )

    def test_second_statement_failure_rolls_back_backtest_update(self):
        connection = engine_database.connect_database(self.config)
        try:
            connection.execute(
                """
                CREATE TEMP TRIGGER reject_visualization_insert
                BEFORE INSERT ON visualizations
                BEGIN
                    SELECT RAISE(ABORT, 'forced visualization insert failure');
                END
                """
            )
            connection.commit()
            with mock.patch.object(
                visualization_repository.engine_database,
                "connect_database",
                return_value=connection,
            ):
                with self.assertRaisesRegex(
                    Exception,
                    "forced visualization insert failure",
                ):
                    visualization_repository.save_visualization(
                        self.config,
                        self.record,
                        expected_revision=0,
                    )
            current = strict_json.loads(connection.execute(
                "SELECT visualization_json FROM backtests WHERE backtest_id = ?",
                (self.record["backtestId"],),
            ).fetchone()["visualization_json"])
            count = connection.execute(
                "SELECT COUNT(*) AS count FROM visualizations"
            ).fetchone()["count"]
            self.assertEqual(current, self.initial_spec)
            self.assertEqual(count, 0)
        finally:
            connection.close()

    def test_row_decode_rejects_extra_fields_and_invalid_stored_spec(self):
        row = {
            "visualization_id": "saved",
            "backtest_id": self.record["backtestId"],
            "name": "Saved",
            "created_at": "2026-08-11T12:00:00Z",
            "revision": 1,
            "spec_json": strict_json.dumps(self.initial_spec),
        }
        with self.assertRaisesRegex(ValueError, "legacy"):
            visualization_repository.decode_visualization_row(
                {**row, "legacy": True}
            )
        with self.assertRaisesRegex(ValueError, "must be an object"):
            visualization_repository.decode_visualization_row(
                {**row, "spec_json": "[]"}
            )

    def test_stale_revision_cannot_replace_record_or_backtest_spec(self):
        visualization_repository.save_visualization(
            self.config, self.record, expected_revision=0
        )
        winner = {
            **self.record,
            "name": "Winner",
            "revision": 2,
            "spec": {**self.record["spec"], "timeZone": "America/New_York"},
        }
        visualization_repository.save_visualization(
            self.config, winner, expected_revision=1
        )
        stale = {
            **self.record,
            "name": "Stale",
            "revision": 2,
            "spec": {**self.record["spec"], "timeZone": "Europe/London"},
        }
        with self.assertRaises(
            visualization_repository.VisualizationRevisionConflict
        ) as raised:
            visualization_repository.save_visualization(
                self.config, stale, expected_revision=1
            )
        self.assertEqual(raised.exception.current, winner)
        self.assertEqual(
            visualization_repository.get_visualization(
                self.config, self.record["visualizationId"]
            ),
            winner,
        )
        with engine_database.connect_database(self.config) as connection:
            current = strict_json.loads(connection.execute(
                "SELECT visualization_json FROM backtests WHERE backtest_id = ?",
                (self.record["backtestId"],),
            ).fetchone()["visualization_json"])
        self.assertEqual(current, winner["spec"])

    def test_schema_19_records_migrate_to_revision_one_without_replacement(self):
        database_path = Path(self.config["controlRoot"]) / "engine-data.db"
        with sqlite3.connect(database_path) as connection:
            connection.execute("DROP TABLE visualizations")
            connection.execute(
                engine_database._LEGACY_VISUALIZATIONS_TABLE_SQL
            )
            connection.execute(
                """
                INSERT INTO visualizations
                (visualization_id, backtest_id, name, created_at, spec_json)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    self.record["visualizationId"],
                    self.record["backtestId"],
                    self.record["name"],
                    self.record["createdAt"],
                    strict_json.dumps(self.record["spec"], sort_keys=True),
                ),
            )
            connection.execute("PRAGMA user_version = 19")
            connection.commit()

        engine_database.prepare_database(self.config)

        self.assertEqual(
            visualization_repository.get_visualization(
                self.config, self.record["visualizationId"]
            ),
            self.record,
        )
        self.assertFalse(
            (
                Path(self.config["controlRoot"])
                / "_incompatible_archives"
                / "database"
            ).exists()
        )


if __name__ == "__main__":
    unittest.main()
