#!/usr/bin/env python3

from __future__ import annotations

import csv
import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path

from dataset_adapters.basic_workflow_v3_conformance import (
    validate_dataset_directory,
)
from scripts.prepare_us_hourly import (
    Session,
    _zip_member,
    aggregate_minute_bars,
    scan_source,
    write_hourly_csv,
)


UTC = timezone.utc


def _session(label, opened, closed):
    opened = opened[:-1] + "+00:00" if opened.endswith("Z") else opened
    closed = closed[:-1] + "+00:00" if closed.endswith("Z") else closed
    return Session(
        label=date.fromisoformat(label),
        open=datetime.fromisoformat(opened).astimezone(UTC),
        close=datetime.fromisoformat(closed).astimezone(UTC),
    )


def _descriptor():
    return {
        "protocolId": "trade.basic-workflow",
        "protocolVersion": "3.0.0",
        "profile": "single-instrument-ohlcv-bar-position",
        "cashUnit": "USD",
        "quantityUnit": "share",
        "executionConvention": "prior-approved-intent-next-bar-open",
        "valuationConvention": "current-bar-close",
    }


class PrepareUsHourlyTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def _csv(self, rows, name="source.csv"):
        path = self.root / name
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle, lineterminator="\n")
            writer.writerow(("timestamp", "open", "high", "low", "close", "volume"))
            writer.writerows(rows)
        return path

    def test_normal_session_is_aligned_to_0930_and_is_v3_conformant(self):
        path = self._csv([
            ("2023-01-03 04:00:00", 1, 1, 1, 1, 1),
            ("2023-01-03 09:30:00", 100, 102, 99, 101, 10),
            ("2023-01-03 10:29:00", 101, 104, 100, 103, 20),
            ("2023-01-03 10:30:00", 103, 105, 102, 104, 30),
            ("2023-01-03 11:30:00", 104, 106, 103, 105, 40),
            ("2023-01-03 12:30:00", 105, 107, 104, 106, 50),
            ("2023-01-03 13:30:00", 106, 108, 105, 107, 60),
            ("2023-01-03 14:30:00", 107, 109, 106, 108, 70),
            ("2023-01-03 15:30:00", 108, 110, 107, 109, 80),
            ("2023-01-03 15:59:00", 109, 112, 108, 111, 90),
            ("2023-01-03 16:00:00", 111, 111, 111, 111, 1),
        ])
        sessions = [_session(
            "2023-01-03",
            "2023-01-03T14:30:00+00:00",
            "2023-01-03T21:00:00+00:00",
        )]

        bars, report = aggregate_minute_bars(
            path, None, sessions, source_time_zone="America/New_York"
        )

        self.assertEqual(len(bars), 7)
        self.assertEqual(bars[0]["eventTime"].isoformat(), "2023-01-03T15:30:00+00:00")
        self.assertEqual(bars[0]["open"], 100)
        self.assertEqual(bars[0]["close"], 103)
        self.assertEqual(bars[0]["high"], 104)
        self.assertEqual(bars[0]["low"], 99)
        self.assertEqual(bars[0]["volume"], 30)
        self.assertEqual(bars[-1]["eventTime"].isoformat(), "2023-01-03T21:00:00+00:00")
        self.assertEqual(report["outsideRegularSessionRows"], 2)
        self.assertEqual(report["missingBuckets"], [])

        dataset = self.root / "dataset"
        write_hourly_csv(dataset / "hour" / "US-AAPL.csv", bars)
        conformance = validate_dataset_directory(dataset, _descriptor())
        self.assertEqual(conformance["rowCount"], 7)

    def test_early_close_has_four_buckets_and_closes_last_at_1300(self):
        path = self._csv([
            ("2023-11-24 09:30:00", 100, 101, 99, 100, 10),
            ("2023-11-24 10:30:00", 100, 102, 100, 101, 20),
            ("2023-11-24 11:30:00", 101, 103, 101, 102, 30),
            ("2023-11-24 12:30:00", 102, 104, 102, 103, 40),
            ("2023-11-24 12:59:00", 103, 105, 102, 104, 50),
        ])
        sessions = [_session(
            "2023-11-24",
            "2023-11-24T14:30:00+00:00",
            "2023-11-24T18:00:00+00:00",
        )]

        bars, report = aggregate_minute_bars(
            path, None, sessions, source_time_zone="America/New_York"
        )

        self.assertEqual(len(bars), 4)
        self.assertEqual(bars[-1]["eventTime"].isoformat(), "2023-11-24T18:00:00+00:00")
        self.assertEqual(report["earlyCloseSessions"], ["2023-11-24"])

    def test_source_timezone_observes_dst_without_manual_offsets(self):
        path = self._csv([
            ("2022-11-04 09:30:00", 100, 101, 99, 100, 1),
            ("2022-11-04 10:30:00", 100, 101, 99, 100, 1),
            ("2022-11-04 11:30:00", 100, 101, 99, 100, 1),
            ("2022-11-04 12:30:00", 100, 101, 99, 100, 1),
            ("2022-11-04 13:30:00", 100, 101, 99, 100, 1),
            ("2022-11-04 14:30:00", 100, 101, 99, 100, 1),
            ("2022-11-04 15:30:00", 100, 101, 99, 100, 1),
            ("2022-11-07 09:30:00", 100, 101, 99, 100, 1),
            ("2022-11-07 10:30:00", 100, 101, 99, 100, 1),
            ("2022-11-07 11:30:00", 100, 101, 99, 100, 1),
            ("2022-11-07 12:30:00", 100, 101, 99, 100, 1),
            ("2022-11-07 13:30:00", 100, 101, 99, 100, 1),
            ("2022-11-07 14:30:00", 100, 101, 99, 100, 1),
            ("2022-11-07 15:30:00", 100, 101, 99, 100, 1),
        ])
        sessions = [
            _session("2022-11-04", "2022-11-04T13:30:00Z", "2022-11-04T20:00:00Z"),
            _session("2022-11-07", "2022-11-07T14:30:00Z", "2022-11-07T21:00:00Z"),
        ]

        bars, _report = aggregate_minute_bars(
            path, None, sessions, source_time_zone="America/New_York"
        )

        self.assertEqual(bars[0]["eventTime"].isoformat(), "2022-11-04T14:30:00+00:00")
        self.assertEqual(bars[7]["eventTime"].isoformat(), "2022-11-07T15:30:00+00:00")

    def test_duplicate_bad_ohlc_and_missing_bucket_fail_closed(self):
        duplicate = self._csv([
            ("2023-01-03 09:30:00", 100, 101, 99, 100, 1),
            ("2023-01-03 09:30:00", 100, 101, 99, 100, 1),
        ], "duplicate.csv")
        with self.assertRaisesRegex(ValueError, "strictly increasing"):
            scan_source(duplicate, None)
        duplicate_session = [_session(
            "2023-01-03",
            "2023-01-03T14:30:00Z",
            "2023-01-03T21:00:00Z",
        )]
        with self.assertRaisesRegex(ValueError, "strictly increasing"):
            aggregate_minute_bars(
                duplicate,
                None,
                duplicate_session,
                source_time_zone="America/New_York",
            )

        bad = self._csv([
            ("2023-01-03 09:30:00", 100, 99, 98, 100, 1),
        ], "bad.csv")
        with self.assertRaisesRegex(ValueError, "upper bound"):
            scan_source(bad, None)

        sparse = self._csv([
            ("2023-01-03 09:30:00", 100, 101, 99, 100, 1),
        ], "sparse.csv")
        sessions = [_session(
            "2023-01-03",
            "2023-01-03T14:30:00Z",
            "2023-01-03T21:00:00Z",
        )]
        with self.assertRaisesRegex(ValueError, "every XNYS session/hour bucket"):
            aggregate_minute_bars(
                sparse, None, sessions, source_time_zone="America/New_York"
            )

    def test_zip_requires_an_exact_csv_member(self):
        import zipfile

        archive = self.root / "many.zip"
        with zipfile.ZipFile(archive, "w") as handle:
            handle.writestr("one.csv", "timestamp,open,high,low,close,volume\n")
            handle.writestr("two.csv", "timestamp,open,high,low,close,volume\n")
        with self.assertRaisesRegex(ValueError, "exactly one"):
            _zip_member(archive)
        self.assertEqual(_zip_member(archive, "two.csv"), "two.csv")


if __name__ == "__main__":
    unittest.main()
