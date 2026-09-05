#!/usr/bin/env python3
"""Prepare causal XNYS regular-session hourly bars from one-minute OHLCV CSV."""

from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.metadata
import json
import math
import re
import shutil
import sys
import tempfile
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Iterator
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dataset_adapters.basic_workflow_v3_conformance import (
    validate_dataset_directory,
)


SCRIPT_VERSION = "1.0.0"
CSV_HEADER = ("timestamp", "open", "high", "low", "close", "volume")
OUTPUT_HEADER = ("time", "eventTime", "open", "close", "high", "low", "volume")
DEFAULT_SAMPLE_URL = (
    "https://frd001.s3-us-east-2.amazonaws.com/"
    "AAPL_1min_sample_firstratedata.zip"
)
MAX_DOWNLOAD_BYTES = 512 * 1024 * 1024
MAX_CSV_BYTES = 2 * 1024 * 1024 * 1024
_INSTRUMENT_ID = re.compile(r"^[A-Za-z0-9_-]+$")


@dataclass(frozen=True)
class Session:
    label: date
    open: datetime
    close: datetime


@dataclass(frozen=True)
class MinuteBar:
    timestamp: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal


def _canonical(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("Canonical timestamps must be timezone-aware.")
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _decimal(value: str, label: str, *, allow_zero: bool = False) -> Decimal:
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError(f"{label} must be numeric.") from exc
    invalid = parsed < 0 if allow_zero else parsed <= 0
    if not parsed.is_finite() or invalid:
        qualifier = "non-negative" if allow_zero else "positive"
        raise ValueError(f"{label} must be finite and {qualifier}.")
    return parsed


def _timestamp(value: str, label: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.strip())
    except ValueError as exc:
        raise ValueError(f"{label} must be an ISO-8601 local timestamp.") from exc
    if parsed.tzinfo is not None:
        raise ValueError(f"{label} must be timezone-naive provider-local time.")
    if parsed.second or parsed.microsecond:
        raise ValueError(f"{label} must be aligned to a whole minute.")
    return parsed


def _parse_row(row: list[str], row_number: int) -> MinuteBar:
    if len(row) != len(CSV_HEADER):
        raise ValueError(f"Source CSV row {row_number} must contain six columns.")
    timestamp = _timestamp(row[0], f"Source CSV row {row_number} timestamp")
    values = {
        field: _decimal(
            row[index],
            f"Source CSV row {row_number} {field}",
            allow_zero=field == "volume",
        )
        for index, field in enumerate(CSV_HEADER[1:], start=1)
    }
    if values["low"] > min(values["open"], values["close"]):
        raise ValueError(f"Source CSV row {row_number} violates the OHLC lower bound.")
    if max(values["open"], values["close"]) > values["high"]:
        raise ValueError(f"Source CSV row {row_number} violates the OHLC upper bound.")
    return MinuteBar(timestamp=timestamp, **values)


def _zip_member(path: Path, requested: str | None = None) -> str | None:
    if not zipfile.is_zipfile(path):
        if requested is not None:
            raise ValueError("--member is valid only for a ZIP source.")
        return None
    with zipfile.ZipFile(path) as archive:
        candidates = [
            item
            for item in archive.infolist()
            if not item.is_dir() and item.filename.lower().endswith(".csv")
        ]
        if requested is not None:
            candidates = [item for item in candidates if item.filename == requested]
        if len(candidates) != 1:
            names = ", ".join(item.filename for item in candidates[:8]) or "none"
            raise ValueError(
                "Source ZIP must resolve to exactly one CSV member; "
                f"matched {len(candidates)} ({names}). Use --member when needed."
            )
        if candidates[0].file_size > MAX_CSV_BYTES:
            raise ValueError("Source CSV member exceeds the safety limit.")
        return candidates[0].filename


def _rows(path: Path, member: str | None) -> Iterator[MinuteBar]:
    binary = None
    archive = None
    text = None
    try:
        if member is None:
            binary = path.open("rb")
        else:
            archive = zipfile.ZipFile(path)
            binary = archive.open(member, "r")
        import io

        text = io.TextIOWrapper(binary, encoding="utf-8-sig", newline="")
        reader = csv.reader(text, strict=True)
        first = next(reader, None)
        if first is None:
            raise ValueError("Source CSV is empty.")
        normalized = tuple(value.strip().lower() for value in first)
        row_number = 1
        if normalized != CSV_HEADER:
            yield _parse_row(first, row_number)
        for row_number, row in enumerate(reader, start=2):
            yield _parse_row(row, row_number)
    except (UnicodeDecodeError, csv.Error) as exc:
        raise ValueError("Source must be valid UTF-8 CSV.") from exc
    finally:
        if text is not None:
            text.close()
        elif binary is not None:
            binary.close()
        if archive is not None:
            archive.close()


def scan_source(path: Path, member: str | None) -> dict:
    first = None
    last = None
    previous = None
    count = 0
    for bar in _rows(path, member):
        if previous is not None and bar.timestamp <= previous:
            raise ValueError(
                "Source timestamps must be unique and strictly increasing; "
                f"found {bar.timestamp.isoformat(sep=' ')} after "
                f"{previous.isoformat(sep=' ')}."
            )
        first = bar.timestamp if first is None else first
        last = bar.timestamp
        previous = bar.timestamp
        count += 1
    if count == 0:
        raise ValueError("Source CSV contains no minute bars.")
    return {"rowCount": count, "firstTimestamp": first, "lastTimestamp": last}


def load_xnys_sessions(start: date, end: date) -> tuple[list[Session], dict]:
    try:
        import exchange_calendars as calendars
    except ImportError as exc:
        raise RuntimeError(
            "exchange_calendars is required; install requirements-workspace.txt."
        ) from exc
    version = importlib.metadata.version("exchange-calendars")
    calendar = calendars.get_calendar(
        "XNYS",
        start=start.isoformat(),
        end=end.isoformat(),
    )
    schedule = calendar.schedule.loc[start.isoformat() : end.isoformat()]
    sessions = []
    digest = hashlib.sha256()
    for label, row in schedule.iterrows():
        opened = row["open"].to_pydatetime().astimezone(timezone.utc)
        closed = row["close"].to_pydatetime().astimezone(timezone.utc)
        session = Session(label=label.date(), open=opened, close=closed)
        if session.close <= session.open:
            raise ValueError(f"XNYS session {session.label} has invalid bounds.")
        sessions.append(session)
        digest.update(
            f"{session.label.isoformat()},{_canonical(opened)},{_canonical(closed)}\n".encode()
        )
    if not sessions:
        raise ValueError("XNYS calendar contains no sessions in the source range.")
    return sessions, {
        "calendar": "XNYS",
        "package": "exchange_calendars",
        "packageVersion": version,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "sessionCount": len(sessions),
        "scheduleSha256": "sha256:" + digest.hexdigest(),
    }


def _bucket_keys(session: Session) -> list[tuple[date, int]]:
    seconds = (session.close - session.open).total_seconds()
    return [
        (session.label, index)
        for index in range(math.ceil(seconds / timedelta(hours=1).total_seconds()))
    ]


def aggregate_minute_bars(
    path: Path,
    member: str | None,
    sessions: list[Session],
    *,
    source_time_zone: str,
    availability_lag_seconds: int = 0,
) -> tuple[list[dict], dict]:
    if availability_lag_seconds < 0:
        raise ValueError("Availability lag must be non-negative.")
    try:
        local_zone = ZoneInfo(source_time_zone)
    except Exception as exc:
        raise ValueError(f"Unknown source timezone '{source_time_zone}'.") from exc
    by_date = {session.label: session for session in sessions}
    if len(by_date) != len(sessions):
        raise ValueError("Calendar session labels must be unique.")
    accumulators: dict[tuple[date, int], dict] = {}
    input_rows = 0
    rth_rows = 0
    outside_rows = 0
    session_rows = {session.label: 0 for session in sessions}
    previous_source_time = None
    for bar in _rows(path, member):
        if previous_source_time is not None and bar.timestamp <= previous_source_time:
            raise ValueError("Source timestamps must be unique and strictly increasing.")
        previous_source_time = bar.timestamp
        input_rows += 1
        session = by_date.get(bar.timestamp.date())
        if session is None:
            outside_rows += 1
            continue
        localized = bar.timestamp.replace(tzinfo=local_zone)
        instant = localized.astimezone(timezone.utc)
        if not (session.open <= instant and instant + timedelta(minutes=1) <= session.close):
            outside_rows += 1
            continue
        elapsed = (instant - session.open).total_seconds()
        if elapsed < 0 or elapsed % 60:
            raise ValueError(
                f"Source minute {bar.timestamp.isoformat(sep=' ')} is not aligned "
                "to the XNYS session."
            )
        bucket_index = int(elapsed // 3600)
        key = (session.label, bucket_index)
        bucket_start = session.open + timedelta(hours=bucket_index)
        bucket_end = min(bucket_start + timedelta(hours=1), session.close)
        values = accumulators.get(key)
        if values is None:
            values = {
                "start": bucket_start,
                "end": bucket_end,
                "open": bar.open,
                "close": bar.close,
                "high": bar.high,
                "low": bar.low,
                "volume": bar.volume,
                "sourceBars": 1,
            }
            accumulators[key] = values
        else:
            values["close"] = bar.close
            values["high"] = max(values["high"], bar.high)
            values["low"] = min(values["low"], bar.low)
            values["volume"] += bar.volume
            values["sourceBars"] += 1
        rth_rows += 1
        session_rows[session.label] += 1

    expected = [key for session in sessions for key in _bucket_keys(session)]
    missing_sessions = [label.isoformat() for label, count in session_rows.items() if not count]
    missing_buckets = [
        f"{label.isoformat()}#{index + 1}"
        for label, index in expected
        if (label, index) not in accumulators
    ]
    if missing_sessions or missing_buckets:
        examples = ", ".join((missing_sessions + missing_buckets)[:12])
        raise ValueError(
            "Source does not cover every XNYS session/hour bucket in its date range: "
            + examples
        )

    lag = timedelta(seconds=availability_lag_seconds)
    output = []
    source_bars_per_bucket = []
    for key in expected:
        values = accumulators[key]
        event_time = values["end"]
        output.append(
            {
                "time": event_time + lag,
                "eventTime": event_time,
                "open": values["open"],
                "close": values["close"],
                "high": values["high"],
                "low": values["low"],
                "volume": values["volume"],
            }
        )
        source_bars_per_bucket.append(values["sourceBars"])
    if not output or any(
        current["eventTime"] <= previous["eventTime"]
        or current["time"] <= previous["time"]
        for previous, current in zip(output, output[1:])
    ):
        raise ValueError("Derived hourly bars must be non-empty and strictly increasing.")
    early_closes = [
        session.label.isoformat()
        for session in sessions
        if (session.close - session.open) < timedelta(hours=6, minutes=30)
    ]
    expected_minutes = sum(
        int((session.close - session.open).total_seconds() // 60)
        for session in sessions
    )
    return output, {
        "inputRows": input_rows,
        "regularSessionRows": rth_rows,
        "outsideRegularSessionRows": outside_rows,
        "expectedRegularSessionMinutes": expected_minutes,
        "omittedZeroVolumeOrMissingMinutes": expected_minutes - rth_rows,
        "outputRows": len(output),
        "earlyCloseSessions": early_closes,
        "minimumSourceBarsPerBucket": min(source_bars_per_bucket),
        "maximumSourceBarsPerBucket": max(source_bars_per_bucket),
        "missingSessions": missing_sessions,
        "missingBuckets": missing_buckets,
    }


def _format_decimal(value: Decimal) -> str:
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def write_hourly_csv(path: Path, bars: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(OUTPUT_HEADER)
        for bar in bars:
            writer.writerow(
                (
                    _canonical(bar["time"]),
                    _canonical(bar["eventTime"]),
                    _format_decimal(bar["open"]),
                    _format_decimal(bar["close"]),
                    _format_decimal(bar["high"]),
                    _format_decimal(bar["low"]),
                    _format_decimal(bar["volume"]),
                )
            )


def _safe_url(value: str) -> tuple[str, str]:
    parsed = urllib.parse.urlsplit(value)
    if parsed.scheme != "https" or not parsed.netloc:
        raise ValueError("Remote source must use an absolute HTTPS URL.")
    public = urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))
    query_digest = "sha256:" + hashlib.sha256(parsed.query.encode()).hexdigest()
    return public, query_digest


def _download(url: str, destination: Path) -> dict:
    public, query_digest = _safe_url(url)
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/zip,text/csv,application/octet-stream",
            "User-Agent": "TradeEngine-hourly-dataset-preparation/1.0",
        },
    )
    digest = hashlib.sha256()
    size = 0
    with urllib.request.urlopen(request, timeout=60) as response:
        with destination.open("wb") as handle:
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > MAX_DOWNLOAD_BYTES:
                    raise ValueError("Remote source exceeds the download safety limit.")
                digest.update(chunk)
                handle.write(chunk)
    return {
        "type": "https",
        "locator": public,
        "querySha256": query_digest,
        "byteCount": size,
        "sha256": "sha256:" + digest.hexdigest(),
    }


def _materialize_source(source: str, raw_root: Path) -> tuple[Path, dict]:
    parsed = urllib.parse.urlsplit(source)
    suffix = Path(parsed.path).suffix.lower() if parsed.scheme else Path(source).suffix.lower()
    suffix = suffix if suffix in {".zip", ".csv", ".txt"} else ".dat"
    destination = raw_root / ("source" + suffix)
    if parsed.scheme:
        evidence = _download(source, destination)
    else:
        original = Path(source).expanduser().resolve()
        if not original.is_file() or original.is_symlink():
            raise ValueError("Local source must be a regular non-symlink file.")
        if original.stat().st_size > MAX_DOWNLOAD_BYTES:
            raise ValueError("Local source exceeds the copy safety limit.")
        shutil.copyfile(original, destination)
        evidence = {
            "type": "file",
            "locator": str(original),
            "byteCount": destination.stat().st_size,
            "sha256": _sha256(destination),
        }
    return destination, evidence


def prepare_workspace(args: argparse.Namespace) -> dict:
    output = Path(args.output).resolve()
    if output.exists():
        raise ValueError(f"Output already exists: {output}")
    if not _INSTRUMENT_ID.fullmatch(args.instrument_id):
        raise ValueError("instrumentId contains unsupported characters.")
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{output.name}.", dir=output.parent))
    try:
        raw_root = temporary / "raw"
        raw_root.mkdir()
        source_path, source_evidence = _materialize_source(args.source, raw_root)
        member = _zip_member(source_path, args.member)
        scan = scan_source(source_path, member)
        sessions, calendar_evidence = load_xnys_sessions(
            scan["firstTimestamp"].date(),
            scan["lastTimestamp"].date(),
        )
        bars, aggregation = aggregate_minute_bars(
            source_path,
            member,
            sessions,
            source_time_zone=args.source_time_zone,
            availability_lag_seconds=args.availability_lag_seconds,
        )
        dataset_root = temporary / "dataset"
        csv_path = dataset_root / "hour" / f"{args.instrument_id}.csv"
        write_hourly_csv(csv_path, bars)
        descriptor = {
            "protocolId": "trade.basic-workflow",
            "protocolVersion": "3.0.0",
            "profile": "single-instrument-ohlcv-bar-position",
            "cashUnit": "USD",
            "quantityUnit": "share",
            "executionConvention": "prior-approved-intent-next-bar-open",
            "valuationConvention": "current-bar-close",
        }
        conformance = validate_dataset_directory(dataset_root, descriptor)
        now = datetime.now(timezone.utc)
        evidence = {
            "schemaVersion": 1,
            "generatedAt": _canonical(now),
            "recipe": {
                "script": "scripts/prepare_us_hourly.py",
                "scriptVersion": SCRIPT_VERSION,
                "instrumentId": args.instrument_id,
                "ticker": args.ticker,
                "inputInterval": "1m",
                "inputTimestampSemantics": "interval-start",
                "sourceTimeZone": args.source_time_zone,
                "session": "XNYS-regular",
                "outputInterval": "1h-session-aligned",
                "eventTimeSemantics": "bar-end",
                "availabilityLagSeconds": args.availability_lag_seconds,
                "adjustmentPolicy": args.adjustment_policy,
                "missingValuePolicy": "no-interpolation-provider-omits-zero-volume-bars",
                "duplicatePolicy": "reject",
                "orderingPolicy": "reject-non-increasing",
            },
            "source": {
                **source_evidence,
                "rawPath": str(source_path.relative_to(temporary)),
                "csvMember": member,
                "rowCount": scan["rowCount"],
                "firstTimestamp": scan["firstTimestamp"].isoformat(sep=" "),
                "lastTimestamp": scan["lastTimestamp"].isoformat(sep=" "),
            },
            "calendar": calendar_evidence,
            "aggregation": aggregation,
            "output": {
                "datasetRoot": "dataset",
                "csvPath": str(csv_path.relative_to(temporary)),
                "csvSha256": _sha256(csv_path),
                "descriptor": descriptor,
                "conformance": conformance,
            },
        }
        (temporary / "evidence.json").write_text(
            json.dumps(evidence, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        temporary.rename(output)
        return evidence
    except BaseException:
        shutil.rmtree(temporary, ignore_errors=True)
        raise


def _arguments(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source",
        default=DEFAULT_SAMPLE_URL,
        help="Local CSV/ZIP path or HTTPS URL (defaults to the official AAPL sample).",
    )
    parser.add_argument("--member", help="Exact CSV member name for a multi-file ZIP.")
    parser.add_argument("--output", required=True, help="New Dataset workspace path.")
    parser.add_argument("--ticker", default="AAPL")
    parser.add_argument("--instrument-id", default="US-AAPL")
    parser.add_argument("--source-time-zone", default="America/New_York")
    parser.add_argument(
        "--adjustment-policy",
        required=True,
        choices=(
            "unadjusted",
            "split-adjusted",
            "split-dividend-adjusted",
            "provider-undocumented",
        ),
    )
    parser.add_argument("--availability-lag-seconds", type=int, default=0)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _arguments(argv)
    evidence = prepare_workspace(args)
    print(json.dumps({
        "workspace": str(Path(args.output).resolve()),
        "sourceSha256": evidence["source"]["sha256"],
        "outputSha256": evidence["output"]["csvSha256"],
        "inputRows": evidence["source"]["rowCount"],
        "outputRows": evidence["aggregation"]["outputRows"],
        "conformance": evidence["output"]["conformance"],
    }, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
