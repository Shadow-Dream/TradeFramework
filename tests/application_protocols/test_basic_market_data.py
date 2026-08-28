"""Fixtures for the Basic Workflow official NASDAQ snapshot provider."""

from __future__ import annotations

import hashlib
import json
import unittest
from datetime import date, timedelta
from urllib.parse import parse_qs, urlparse
from unittest import mock

from application_protocols.basic_workflow import market_data as market_data_module
from application_protocols.basic_workflow.market_data import (
    DEFAULT_PROVIDER_ID,
    EODHD_ADJUSTMENT_POLICY,
    EODHD_DEMO_PROVIDER_ID,
    EODHD_LOOKBACK_DAYS,
    EODHD_REVISION_POLICY,
    NASDAQ_HISTORICAL_URL,
    NASDAQ_LISTED_URL,
    NASDAQ_OTHER_LISTED_URL,
    EodhdDemoSnapshotProvider,
    NasdaqSnapshotProvider,
    _eodhd_bars,
)


NASDAQ_LISTED = """\
Symbol|Security Name|Market Category|Test Issue|Financial Status|Round Lot Size|ETF|NextShares
AAPL|Apple Inc. - Common Stock|Q|N|N|100|N|N
QQQT|Test Security|Q|Y|N|100|N|N
File Creation Time: 0826202621:31|||||||
"""

OTHER_LISTED = """\
ACT Symbol|Security Name|Exchange|CQS Symbol|ETF|Round Lot Size|Test Issue|NASDAQ Symbol
SPY|SPDR S&P 500 ETF Trust|P|SPY|Y|100|N|SPY
File Creation Time: 0826202621:31|||||||
"""


def _generated_eodhd_rows():
    rows = []
    current = date(2026, 7, 30)
    end = date(2026, 8, 26)
    while current <= end:
        if current.weekday() < 5:
            offset = len(rows)
            close = 100.5 + offset
            rows.append({
                "date": current.isoformat(),
                "open": 100.0 + offset,
                "high": 102.0 + offset,
                "low": 99.0 + offset,
                "close": close,
                "adjusted_close": close,
                "volume": 1_000_000 + offset * 1000,
            })
        current += timedelta(days=1)
    if len(rows) != 20:
        raise AssertionError("The generated EODHD fixture must contain twenty rows.")
    return rows


class NasdaqSnapshotProviderTests(unittest.TestCase):
    def test_catalog_and_daily_bars_are_normalized_as_explicit_snapshots(self):
        text_calls = []
        json_calls = []

        def fetch_text(url, *, limit):
            text_calls.append((url, limit))
            return {
                NASDAQ_LISTED_URL: NASDAQ_LISTED,
                NASDAQ_OTHER_LISTED_URL: OTHER_LISTED,
            }[url]

        def fetch_json(url, *, limit):
            json_calls.append((url, limit))
            return {
                "data": {
                    "totalRecords": 2,
                    "tradesTable": {
                        "rows": [
                            {
                                "date": "07/07/2026",
                                "open": "$210.00",
                                "high": "$214.00",
                                "low": "$209.00",
                                "close": "$213.00",
                            },
                            {
                                "date": "01/05/2026",
                                "open": "$200.00",
                                "high": "$203.00",
                                "low": "$199.00",
                                "close": "$202.00",
                            },
                        ]
                    },
                }
            }

        provider = NasdaqSnapshotProvider(
            fetch_text=fetch_text,
            fetch_json=fetch_json,
            today=date(2026, 8, 26),
        )
        catalog = provider.sync_catalog()
        self.assertEqual(provider.provider_id, DEFAULT_PROVIDER_ID)
        self.assertEqual(
            [instrument["instrumentId"] for instrument in catalog["instruments"]],
            ["US-AAPL", "US-SPY"],
        )
        self.assertEqual(catalog["instruments"][1]["assetType"], "etf")
        self.assertEqual(catalog["instruments"][1]["exchange"], "NYSE Arca")
        self.assertEqual(
            [call[0] for call in text_calls],
            [NASDAQ_LISTED_URL, NASDAQ_OTHER_LISTED_URL],
        )

        snapshot = provider.download_bars(catalog["instruments"][0], "day")
        self.assertEqual(snapshot["providerId"], DEFAULT_PROVIDER_ID)
        self.assertEqual(
            [bar["time"] for bar in snapshot["bars"]],
            ["2026-01-05T21:00:00Z", "2026-07-07T20:00:00Z"],
        )
        self.assertEqual(snapshot["bars"][0]["close"], 202.0)
        self.assertEqual(len(json_calls), 1)
        parsed = urlparse(json_calls[0][0])
        self.assertEqual(
            parsed.path,
            urlparse(NASDAQ_HISTORICAL_URL.format(symbol="AAPL")).path,
        )
        query = parse_qs(parsed.query)
        self.assertEqual(query["assetclass"], ["stocks"])
        self.assertEqual(query["fromdate"], ["2016-08-26"])
        self.assertEqual(query["todate"], ["2026-08-26"])
        self.assertNotIn("apikey", query)

    def test_invalid_nasdaq_ohlc_fails_instead_of_becoming_a_dataset(self):
        provider = NasdaqSnapshotProvider(
            fetch_json=lambda _url, *, limit: {
                "data": {
                    "totalRecords": 1,
                    "tradesTable": {
                        "rows": [
                            {
                                "date": "2026-01-05",
                                "open": "$200",
                                "high": "$199",
                                "low": "$198",
                                "close": "$201",
                            }
                        ]
                    },
                }
            },
            today=date(2026, 8, 26),
        )
        with self.assertRaisesRegex(ValueError, "OHLC upper bound"):
            provider.download_bars(
                {
                    "instrumentId": "US-AAPL",
                    "symbol": "AAPL",
                    "name": "Apple Inc.",
                    "exchange": "NASDAQ",
                    "currency": "USD",
                    "assetType": "stock",
                    "availablePeriods": ["day"],
                },
                "day",
            )


class EodhdDemoSnapshotProviderTests(unittest.TestCase):
    def test_public_demo_fetch_is_bounded_causal_hashed_and_token_free_in_result(self):
        rows = _generated_eodhd_rows()
        raw = json.dumps(rows, separators=(",", ":")).encode("utf-8")
        calls = []

        def fetch_json(url, *, limit):
            calls.append((url, limit))
            return raw, rows

        provider = EodhdDemoSnapshotProvider(
            fetch_json=fetch_json,
            today=date(2026, 8, 26),
        )
        with mock.patch.object(
            market_data_module.engine_clock,
            "utc_now",
            return_value="2026-08-27T18:59:33Z",
        ):
            catalog = provider.sync_catalog()
            instrument = next(
                value
                for value in catalog["instruments"]
                if value["instrumentId"] == "US-TSLA"
            )
            result = provider.download_bars(instrument, "day")

        self.assertEqual(provider.provider_id, EODHD_DEMO_PROVIDER_ID)
        self.assertEqual(
            [item["symbol"] for item in catalog["instruments"]],
            ["AAPL", "AMZN", "TSLA", "VTI"],
        )
        self.assertEqual(len(result["bars"]), 20)
        self.assertEqual(result["bars"][0]["eventTime"], "2026-07-30T20:00:00Z")
        self.assertEqual(result["bars"][0]["time"], "2026-07-30T20:15:00Z")
        self.assertEqual(result["bars"][-1]["volume"], 1_019_000.0)
        self.assertEqual(
            result["rawSha256"],
            "sha256:" + hashlib.sha256(raw).hexdigest(),
        )
        self.assertEqual(result["adjustmentPolicy"], EODHD_ADJUSTMENT_POLICY)
        self.assertEqual(result["revisionPolicy"], EODHD_REVISION_POLICY)
        self.assertNotIn("api_token", json.dumps(result, sort_keys=True))
        self.assertEqual(len(calls), 1)
        parsed = urlparse(calls[0][0])
        query = parse_qs(parsed.query)
        self.assertEqual(query["from"], [
            (date(2026, 8, 26) - timedelta(days=EODHD_LOOKBACK_DAYS)).isoformat()
        ])
        self.assertEqual(query["to"], ["2026-08-26"])
        self.assertEqual(query["period"], ["d"])
        self.assertEqual(query["order"], ["a"])
        self.assertEqual(query["api_token"], ["demo"])

    def test_nonordinary_dates_duplicates_and_missing_volume_fail_closed(self):
        def row(day):
            return {
                "date": day,
                "open": 100,
                "high": 102,
                "low": 99,
                "close": 101,
                "adjusted_close": 101,
                "volume": 1000,
            }

        for day in ("2026-11-27", "2026-12-25"):
            with self.subTest(day=day), self.assertRaisesRegex(
                ValueError, "outside the supported ordinary"
            ):
                _eodhd_bars([row(day)])
        with self.assertRaisesRegex(ValueError, "strictly increasing"):
            _eodhd_bars([row("2026-08-03"), row("2026-08-03")])
        missing = row("2026-08-03")
        missing.pop("volume")
        with self.assertRaisesRegex(ValueError, "invalid OHLCV schema"):
            _eodhd_bars([missing])
        negative = row("2026-08-03")
        negative["volume"] = -1
        with self.assertRaisesRegex(ValueError, "non-negative"):
            _eodhd_bars([negative])

    def test_injected_download_errors_do_not_relay_the_tokenized_url(self):
        def fail(url, *, limit):
            raise RuntimeError(url)

        provider = EodhdDemoSnapshotProvider(
            fetch_json=fail,
            today=date(2026, 8, 26),
        )
        instrument = next(
            value
            for value in provider.sync_catalog()["instruments"]
            if value["instrumentId"] == "US-AAPL"
        )
        with self.assertRaisesRegex(ValueError, "download failed") as captured:
            provider.download_bars(instrument, "day")
        self.assertNotIn("api_token", str(captured.exception))


if __name__ == "__main__":
    unittest.main()
