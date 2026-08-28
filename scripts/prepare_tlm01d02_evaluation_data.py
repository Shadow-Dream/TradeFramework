#!/usr/bin/env python3
"""Prepare causal Basic Workflow v2 market-data bundles for TLM01D02 evaluation."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import shutil
import sys
import urllib.parse
import urllib.request
import zipfile
from collections import defaultdict
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dataset_adapters.basic_workflow_conformance import validate_dataset_directory


DEFAULT_START = "2024-08-23T00:00:00Z"
DEFAULT_END = "2026-08-21T00:00:00Z"
DEFAULT_HISTORY_START = "2022-05-01T00:00:00Z"
DEFAULT_OUTPUT = "artifacts/dataset-workspaces/tlm01d02-evaluation-20260821"
PROVIDER = "Yahoo Finance chart API"
TIME_ZONE = ZoneInfo("America/New_York")
DESCRIPTOR_BASE = {
    "protocolId": "trade.basic-workflow",
    "protocolVersion": "2.0.0",
    "profile": "multi-instrument-bar-position",
    "cashUnit": "USD",
    "executionConvention": "prior-approved-intent-next-bar-open",
    "valuationConvention": "current-bar-close",
}
INSTRUMENTS = {
    "SPX": {
        "symbol": "^GSPC",
        "name": "S&P 500 Index",
        "quantityUnit": "index-unit",
    },
    "NASDAQ_COMPOSITE": {
        "symbol": "^IXIC",
        "name": "Nasdaq Composite Index",
        "quantityUnit": "index-unit",
    },
    "NVDA": {
        "symbol": "NVDA",
        "name": "NVIDIA Corporation",
        "quantityUnit": "share",
    },
}


def _instant(value: str) -> datetime:
    parsed = datetime.fromisoformat(value[:-1] + "+00:00" if value.endswith("Z") else value)
    if parsed.tzinfo is None:
        raise ValueError("Timestamps must include an absolute timezone.")
    return parsed.astimezone(timezone.utc)


def _canonical(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _provider_url(symbol: str, start: datetime, end: datetime, interval: str) -> str:
    query = urllib.parse.urlencode(
        {
            "period1": int(start.timestamp()),
            "period2": int(end.timestamp()),
            "interval": interval,
            "events": "div,splits",
            "includeAdjustedClose": "true",
        }
    )
    encoded_symbol = urllib.parse.quote(symbol, safe="")
    return f"https://query1.finance.yahoo.com/v8/finance/chart/{encoded_symbol}?{query}"


def _download(url: str, destination: Path) -> None:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0 TradeEngine evaluation data preparation"},
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        payload = response.read()
    parsed = json.loads(payload)
    if parsed.get("chart", {}).get("error") is not None:
        raise ValueError(f"Provider returned an error: {parsed['chart']['error']}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(payload)


def _result(payload: dict) -> dict:
    chart = payload.get("chart")
    if not isinstance(chart, dict) or chart.get("error") is not None:
        raise ValueError("Yahoo chart response contains an error.")
    results = chart.get("result")
    if not isinstance(results, list) or len(results) != 1 or not isinstance(results[0], dict):
        raise ValueError("Yahoo chart response must contain one result.")
    return results[0]


def _sessions(result: dict) -> list[tuple[int, int]]:
    periods = result.get("meta", {}).get("tradingPeriods")
    if not isinstance(periods, list):
        raise ValueError("Provider response is missing regular trading periods.")
    sessions = []
    for group in periods:
        entries = group if isinstance(group, list) else [group]
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            start, end = entry.get("start"), entry.get("end")
            if type(start) is int and type(end) is int and start < end:
                sessions.append((start, end))
    if not sessions:
        raise ValueError("Provider response contains no regular sessions.")
    return sorted(set(sessions))


def _hour_bars(result: dict) -> tuple[list[dict], int, int]:
    timestamps = result.get("timestamp")
    indicators = result.get("indicators", {}).get("quote")
    if not isinstance(timestamps, list) or not isinstance(indicators, list) or len(indicators) != 1:
        raise ValueError("Provider response is missing hourly quote arrays.")
    quote = indicators[0]
    fields = ("open", "close", "high", "low", "volume")
    if any(not isinstance(quote.get(field), list) for field in fields):
        raise ValueError("Provider response is missing a required OHLC array.")
    if any(len(quote[field]) != len(timestamps) for field in fields):
        raise ValueError("Provider timestamp and OHLC array lengths differ.")
    sessions = _sessions(result)
    session_index = 0
    bars = []
    dropped = 0
    merged_close_snapshots = 0
    for index, raw_time in enumerate(timestamps):
        values = {field: quote[field][index] for field in fields}
        if any(value is None for value in values.values()):
            dropped += 1
            continue
        if type(raw_time) is not int or any(
            isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value)
            for value in values.values()
        ):
            raise ValueError("Provider returned an invalid timestamp or OHLC value.")
        while session_index < len(sessions) and sessions[session_index][1] < raw_time:
            session_index += 1
        if session_index >= len(sessions) or not (
            sessions[session_index][0] <= raw_time <= sessions[session_index][1]
        ):
            raise ValueError(f"Hourly bar {raw_time} is outside the declared regular session.")
        session_end = sessions[session_index][1]
        available = raw_time if raw_time == session_end else min(raw_time + 3600, session_end)
        bar = {field: float(values[field]) for field in fields}
        if min(bar[field] for field in ("open", "close", "high", "low")) <= 0:
            raise ValueError("Provider returned a non-positive OHLC value.")
        if bar["volume"] < 0 or bar["low"] > min(bar["open"], bar["close"]):
            raise ValueError("Provider returned an invalid OHLC lower bound.")
        if max(bar["open"], bar["close"]) > bar["high"]:
            raise ValueError("Provider returned an invalid OHLC upper bound.")
        available_time = datetime.fromtimestamp(available, timezone.utc)
        if bars and bars[-1]["time"] == available_time:
            if raw_time != session_end:
                raise ValueError("Provider returned duplicate non-closing hourly bars.")
            bars[-1]["close"] = bar["close"]
            bars[-1]["high"] = max(bars[-1]["high"], bar["high"])
            bars[-1]["low"] = min(bars[-1]["low"], bar["low"])
            bars[-1]["volume"] += bar["volume"]
            merged_close_snapshots += 1
            continue
        bars.append({"time": available_time, **bar})
    if not bars or any(bars[index]["time"] >= bars[index + 1]["time"] for index in range(len(bars) - 1)):
        raise ValueError("Causal hourly timestamps must be non-empty and strictly increasing.")
    return bars, dropped, merged_close_snapshots


def _daily_bars(result: dict) -> tuple[list[dict], int]:
    timestamps = result.get("timestamp")
    indicators = result.get("indicators", {}).get("quote")
    if not isinstance(timestamps, list) or not isinstance(indicators, list) or len(indicators) != 1:
        raise ValueError("Provider response is missing daily quote arrays.")
    quote = indicators[0]
    fields = ("open", "close", "high", "low", "volume")
    if any(not isinstance(quote.get(field), list) for field in fields):
        raise ValueError("Provider response is missing a required daily OHLC array.")
    if any(len(quote[field]) != len(timestamps) for field in fields):
        raise ValueError("Provider daily timestamp and OHLC array lengths differ.")
    bars = []
    dropped = 0
    for index, raw_time in enumerate(timestamps):
        values = {field: quote[field][index] for field in fields}
        if any(value is None for value in values.values()):
            dropped += 1
            continue
        if type(raw_time) is not int or any(
            isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value)
            for value in values.values()
        ):
            raise ValueError("Provider returned an invalid daily timestamp or OHLC value.")
        local_date = datetime.fromtimestamp(raw_time, timezone.utc).astimezone(TIME_ZONE).date()
        available = datetime.combine(local_date, time(17), tzinfo=TIME_ZONE).astimezone(timezone.utc)
        bar = {field: float(values[field]) for field in fields}
        if min(bar[field] for field in ("open", "close", "high", "low")) <= 0:
            raise ValueError("Provider returned a non-positive daily OHLC value.")
        if bar["volume"] < 0 or bar["low"] > min(bar["open"], bar["close"]):
            raise ValueError("Provider returned an invalid daily OHLC lower bound.")
        if max(bar["open"], bar["close"]) > bar["high"]:
            raise ValueError("Provider returned an invalid daily OHLC upper bound.")
        bars.append({"time": available, "sourceDate": local_date.isoformat(), **bar})
    if not bars or any(bars[index]["time"] >= bars[index + 1]["time"] for index in range(len(bars) - 1)):
        raise ValueError("Historical daily timestamps must be non-empty and strictly increasing.")
    return bars, dropped


def _aggregate(bars: list[dict], key_function) -> list[dict]:
    grouped = defaultdict(list)
    for bar in bars:
        grouped[key_function(bar["time"].astimezone(TIME_ZONE))].append(bar)
    result = []
    for key in sorted(grouped):
        rows = grouped[key]
        result.append(
            {
                "time": rows[-1]["time"],
                "open": rows[0]["open"],
                "close": rows[-1]["close"],
                "high": max(row["high"] for row in rows),
                "low": min(row["low"] for row in rows),
                "volume": sum(row["volume"] for row in rows),
            }
        )
    return result


def _format_number(value: float) -> str:
    return format(value, ".15g")


def _write_csv(path: Path, bars: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(("time", "open", "close", "high", "low"))
        for bar in bars:
            writer.writerow(
                (
                    _canonical(bar["time"]),
                    _format_number(bar["open"]),
                    _format_number(bar["close"]),
                    _format_number(bar["high"]),
                    _format_number(bar["low"]),
                )
            )


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _package(source_root: Path, destination: Path, descriptor: dict, instrument_id: str) -> None:
    files = {
        period: {instrument_id: f"{period}/{instrument_id}.csv"}
        for period in ("hour", "day", "week")
    }
    _write_json(
        source_root / "basic_workflow.json",
        {
            "protocolId": descriptor["protocolId"],
            "protocolVersion": descriptor["protocolVersion"],
            "profile": descriptor["profile"],
            "files": files,
        },
    )
    _write_json(
        source_root / "_trade_dataset_capabilities.json",
        {
            "schemaVersion": 1,
            "capabilities": {
                "basicWorkflow": {
                    "protocol": "trade.app.basic-workflow-dataset/v2",
                    "descriptor": descriptor,
                },
                "visualization": {
                    "protocol": "trade.dataset.visualization/v1",
                    "descriptor": {"timeZone": "America/New_York"},
                },
            },
        },
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED, allowZip64=True) as archive:
        for path in sorted(source_root.rglob("*")):
            if path.is_file():
                info = zipfile.ZipInfo(path.relative_to(source_root).as_posix())
                info.date_time = (1980, 1, 1, 0, 0, 0)
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o100644 << 16
                archive.writestr(info, path.read_bytes())


def prepare(
    output_root: Path,
    start: datetime,
    end: datetime,
    history_start: datetime,
    reuse_raw: bool,
) -> dict:
    if end <= start or end - start > timedelta(days=729):
        raise ValueError("The hourly request window must be positive and no longer than 729 days.")
    if history_start >= start:
        raise ValueError("History start must precede the evaluation window.")
    raw_root = output_root / "raw"
    normalized_root = output_root / "normalized"
    package_root = output_root / "packages"
    normalized_root.mkdir(parents=True, exist_ok=True)
    package_root.mkdir(parents=True, exist_ok=True)
    evidence = {
        "schemaVersion": 1,
        "preparedAt": _canonical(datetime.now(timezone.utc)),
        "requestedWindow": {"startInclusive": _canonical(start), "endExclusive": _canonical(end)},
        "historicalFactsWindow": {
            "startInclusive": _canonical(history_start),
            "endExclusive": _canonical(start),
            "providerInterval": "1d",
        },
        "provider": PROVIDER,
        "providerInterval": "1h",
        "availabilityPolicy": "min(providerBarStart+1h,regularSessionClose)",
        "aggregationPolicy": (
            "evaluation day bars are derived from closed hourly facts; weekly bars are "
            "derived from the combined historical and evaluation daily facts"
        ),
        "cyclePolicy": (
            "Dataset contains facts only; Sampler selects formal hourly cycles and passes "
            "pre-window facts through its explicit bootstrapHistory output"
        ),
        "nasdaqDefinition": "Nasdaq Composite Index (^IXIC), not Nasdaq-100 (^NDX)",
        "instruments": {},
    }
    window_label = (
        start.strftime("%Y%m%d")
        + "-"
        + (end - timedelta(seconds=1)).strftime("%Y%m%d")
    )
    for instrument_id, instrument in INSTRUMENTS.items():
        url = _provider_url(instrument["symbol"], start, end, "1h")
        history_url = _provider_url(instrument["symbol"], history_start, start, "1d")
        raw_path = raw_root / f"{instrument_id}.json"
        history_raw_path = raw_root / f"{instrument_id}_history_daily.json"
        legacy_raw_path = raw_root / f"{instrument_id}_warmup_daily.json"
        if not reuse_raw or not raw_path.is_file():
            _download(url, raw_path)
        if reuse_raw and not history_raw_path.is_file() and legacy_raw_path.is_file():
            shutil.copyfile(legacy_raw_path, history_raw_path)
        if not reuse_raw or not history_raw_path.is_file():
            _download(history_url, history_raw_path)
        payload = json.loads(raw_path.read_text(encoding="utf-8"))
        history_payload = json.loads(history_raw_path.read_text(encoding="utf-8"))
        provider_result = _result(payload)
        history_result = _result(history_payload)
        hour_bars, dropped, merged_close_snapshots = _hour_bars(provider_result)
        historical_daily_bars, history_dropped = _daily_bars(history_result)
        evaluation_daily_bars = _aggregate(
            hour_bars, lambda value: value.date().isoformat()
        )
        historical_daily_bars = [bar for bar in historical_daily_bars if bar["time"] < start]
        combined_daily_bars = sorted(
            [*historical_daily_bars, *evaluation_daily_bars], key=lambda bar: bar["time"]
        )
        bars = {"hour": hour_bars, "day": combined_daily_bars}
        bars["week"] = _aggregate(
            bars["day"], lambda value: (value.isocalendar().year, value.isocalendar().week)
        )
        source_root = normalized_root / instrument_id
        if source_root.exists():
            shutil.rmtree(source_root)
        for period, rows in bars.items():
            _write_csv(source_root / period / f"{instrument_id}.csv", rows)
        descriptor = {**DESCRIPTOR_BASE, "quantityUnit": instrument["quantityUnit"]}
        report = validate_dataset_directory(source_root, descriptor)
        package_path = package_root / f"tlm01d02-eval-{instrument_id.lower()}-{window_label}.zip"
        _package(source_root, package_path, descriptor, instrument_id)
        evidence["instruments"][instrument_id] = {
            "name": instrument["name"],
            "providerSymbol": instrument["symbol"],
            "providerUrl": url,
            "providerExchangeTimezone": provider_result.get("meta", {}).get("exchangeTimezoneName"),
            "rawSha256": _sha256(raw_path),
            "historyRawSha256": _sha256(history_raw_path),
            "historyProviderUrl": history_url,
            "events": provider_result.get("events", {}),
            "droppedIncompleteProviderRows": dropped,
            "droppedIncompleteHistoryProviderRows": history_dropped,
            "mergedSessionCloseSnapshots": merged_close_snapshots,
            "conformance": report,
            "periodRows": {period: len(rows) for period, rows in bars.items()},
            "applicationConsumption": {
                "formalCyclePeriod": "hour",
                "formalCycleRows": len(bars["hour"]),
                "historicalDailyFactRows": len(historical_daily_bars),
                "bootstrapSelectionOwner": "Sampler/Application Protocol",
                "strategySpecificRows": 0,
            },
            "package": package_path.relative_to(output_root).as_posix(),
            "packageSha256": _sha256(package_path),
        }
    manifest_path = output_root / "evidence.json"
    _write_json(manifest_path, evidence)
    return evidence


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", default=DEFAULT_OUTPUT)
    parser.add_argument("--start", default=DEFAULT_START)
    parser.add_argument("--end", default=DEFAULT_END)
    parser.add_argument("--history-start", default=DEFAULT_HISTORY_START)
    parser.add_argument("--reuse-raw", action="store_true")
    args = parser.parse_args()
    output_root = Path(args.output_root).expanduser().resolve()
    evidence = prepare(
        output_root,
        _instant(args.start),
        _instant(args.end),
        _instant(args.history_start),
        args.reuse_raw,
    )
    print(json.dumps({
        "outputRoot": str(output_root),
        "evidence": str(output_root / "evidence.json"),
        "periodRows": {
            key: value["periodRows"] for key, value in evidence["instruments"].items()
        },
    }, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
