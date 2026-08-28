"""Basic Workflow v3 OHLCV CSV-directory-to-price-map Sampler source."""

from __future__ import annotations


SOURCE = r'''import csv
import json
import math
import re
from datetime import datetime, timezone


_CSV_FIELDS = ("time", "eventTime", "open", "close", "high", "low", "volume")
_INDEX_FILE = "basic_workflow.json"
_SEGMENT = re.compile(r"^[A-Za-z0-9_-]+$")


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Basic Workflow v3 JSON contains duplicate field: " + key)
        result[key] = value
    return result


def _constant(value):
    raise ValueError("Basic Workflow v3 JSON contains invalid number: " + value)


def _float(value):
    result = float(value)
    if not math.isfinite(result):
        raise ValueError("Basic Workflow v3 JSON contains a non-finite number.")
    return result


def _instant(value, label):
    if type(value) is not str or not value:
        raise ValueError(label + " must be a non-empty absolute ISO-8601 timestamp.")
    text = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        result = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(label + " must be an absolute ISO-8601 timestamp.") from exc
    if result.tzinfo is None:
        raise ValueError(label + " must include an absolute timezone.")
    return result.astimezone(timezone.utc)


def _canonical(value):
    return value.isoformat().replace("+00:00", "Z")


def _number(value, label, allow_zero=False):
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(label + " must be numeric.") from exc
    invalid = result < 0 if allow_zero else result <= 0
    if not math.isfinite(result) or invalid:
        qualifier = "non-negative" if allow_zero else "positive"
        raise ValueError(label + " must be finite and " + qualifier + ".")
    return result


def _descriptor(dataset):
    capability = dataset.capabilities.get("basicWorkflow")
    if type(capability) is not dict or set(capability) != {"protocol", "descriptor"}:
        raise ValueError("Dataset requires the Basic Workflow v3 capability.")
    if capability["protocol"] != "trade.app.basic-workflow-dataset/v3":
        raise ValueError("Dataset Basic Workflow v3 capability protocol is incompatible.")
    descriptor = capability["descriptor"]
    fields = {
        "protocolId", "protocolVersion", "profile", "cashUnit", "quantityUnit",
        "executionConvention", "valuationConvention",
    }
    if type(descriptor) is not dict or set(descriptor) != fields:
        raise ValueError("Dataset Basic Workflow v3 descriptor has an invalid schema.")
    expected = {
        "protocolId": "trade.basic-workflow",
        "protocolVersion": "3.0.0",
        "profile": "single-instrument-ohlcv-bar-position",
        "executionConvention": "prior-approved-intent-next-bar-open",
        "valuationConvention": "current-bar-close",
    }
    if any(descriptor.get(field) != value for field, value in expected.items()):
        raise ValueError("Dataset Basic Workflow v3 descriptor is incompatible.")
    for field in ("cashUnit", "quantityUnit"):
        if type(descriptor[field]) is not str or not descriptor[field]:
            raise ValueError("Dataset Basic Workflow v3 units are invalid.")
    return descriptor


def _index(dataset):
    with dataset.path(_INDEX_FILE).open("r", encoding="utf-8") as handle:
        value = json.load(
            handle,
            object_pairs_hook=_pairs,
            parse_constant=_constant,
            parse_float=_float,
        )
    if type(value) is not dict or set(value) != {
        "protocolId", "protocolVersion", "profile", "files"
    }:
        raise ValueError("Basic Workflow v3 Dataset index has an invalid schema.")
    if (
        value["protocolId"] != "trade.basic-workflow"
        or value["protocolVersion"] != "3.0.0"
        or value["profile"] != "single-instrument-ohlcv-bar-position"
    ):
        raise ValueError("Basic Workflow v3 Dataset index identity is incompatible.")
    files = value["files"]
    if type(files) is not dict or not files:
        raise ValueError("Basic Workflow v3 index files must be a non-empty object.")
    instruments_seen = set()
    for period, instruments in files.items():
        if type(period) is not str or not _SEGMENT.fullmatch(period):
            raise ValueError("Basic Workflow v3 index period is invalid.")
        if type(instruments) is not dict or not instruments:
            raise ValueError("Basic Workflow v3 index period cannot be empty.")
        for instrument, path in instruments.items():
            if type(instrument) is not str or not _SEGMENT.fullmatch(instrument):
                raise ValueError("Basic Workflow v3 index instrument is invalid.")
            if path != period + "/" + instrument + ".csv":
                raise ValueError("Basic Workflow v3 index path is not canonical.")
            instruments_seen.add(instrument)
    if len(instruments_seen) != 1:
        raise ValueError("Basic Workflow v3 index requires exactly one instrumentId.")
    return files


def _series(dataset, relative_path):
    values = []
    previous_available = None
    previous_event = None
    with dataset.path(relative_path).open("r", encoding="utf-8", newline="") as handle:
        reader = csv.reader(handle, strict=True)
        try:
            header = next(reader)
        except StopIteration as exc:
            raise ValueError(relative_path + " must contain a CSV header.") from exc
        if tuple(header) != _CSV_FIELDS:
            raise ValueError(relative_path + " has an incompatible v3 CSV header.")
        for row_number, row in enumerate(reader, start=2):
            if len(row) != len(_CSV_FIELDS):
                raise ValueError(relative_path + " row has an incompatible shape.")
            available = _instant(row[0], relative_path + ".time")
            event = _instant(row[1], relative_path + ".eventTime")
            if available < event:
                raise ValueError(relative_path + " availability precedes eventTime.")
            if previous_available is not None and available <= previous_available:
                raise ValueError(relative_path + " times must be strictly increasing.")
            if previous_event is not None and event <= previous_event:
                raise ValueError(relative_path + " eventTimes must be strictly increasing.")
            bar = {
                "eventTime": _canonical(event),
                "open": _number(row[2], relative_path + ".open"),
                "close": _number(row[3], relative_path + ".close"),
                "high": _number(row[4], relative_path + ".high"),
                "low": _number(row[5], relative_path + ".low"),
                "volume": _number(row[6], relative_path + ".volume", allow_zero=True),
            }
            if bar["low"] > min(bar["open"], bar["close"]):
                raise ValueError(relative_path + " violates the OHLC lower bound.")
            if max(bar["open"], bar["close"]) > bar["high"]:
                raise ValueError(relative_path + " violates the OHLC upper bound.")
            values.append((available, bar, row_number))
            previous_available = available
            previous_event = event
    if not values:
        raise ValueError(relative_path + " must contain at least one bar.")
    return values


def sample_basic_ohlcv_map(dataset, parameters):
    _descriptor(dataset)
    files = _index(dataset)
    if type(parameters) is not dict or not set(parameters).issubset({"decisionPeriod"}):
        raise ValueError("Basic Workflow v3 Sampler parameters allow decisionPeriod only.")
    decision_period = parameters.get("decisionPeriod")
    if decision_period is not None and (
        type(decision_period) is not str or not _SEGMENT.fullmatch(decision_period)
    ):
        raise ValueError("Basic Workflow v3 decisionPeriod is invalid.")
    if decision_period is not None and decision_period not in files:
        raise ValueError("Basic Workflow v3 decisionPeriod is absent from the Dataset.")

    series = {
        period: {
            instrument: _series(dataset, path)
            for instrument, path in sorted(instruments.items())
        }
        for period, instruments in sorted(files.items())
    }
    if decision_period is None:
        if len(series) != 1:
            raise ValueError(
                "Basic Workflow v3 decisionPeriod is required for a multi-period Dataset."
            )
        decision_period = next(iter(series))
    timeline = sorted({
        available
        for instrument_series in series[decision_period].values()
        for available, _bar, _row in instrument_series
    })
    cursors = {
        (period, instrument): -1
        for period, instruments in series.items()
        for instrument in instruments
    }
    for sequence, decision_time in enumerate(timeline):
        price = {}
        provenance = {
            "time": {
                "decisionPeriod": decision_period,
                "availableAt": _canonical(decision_time),
                "protocolVersion": "3.0.0",
            }
        }
        for period, instruments in series.items():
            period_values = {}
            for instrument, rows in instruments.items():
                key = (period, instrument)
                cursor = cursors[key]
                while cursor + 1 < len(rows) and rows[cursor + 1][0] <= decision_time:
                    cursor += 1
                cursors[key] = cursor
                if cursor < 0:
                    continue
                available, bar, row_number = rows[cursor]
                period_values[instrument] = dict(bar)
                provenance["price." + period + "." + instrument] = {
                    "sourcePath": files[period][instrument],
                    "sourceRow": row_number,
                    "eventTime": bar["eventTime"],
                    "availableAt": _canonical(available),
                    "decisionTime": _canonical(decision_time),
                    "sourceFields": list(_CSV_FIELDS),
                    "protocolVersion": "3.0.0",
                }
            if period_values:
                price[period] = period_values
        text = _canonical(decision_time)
        yield {
            "decisionTime": text,
            "sequence": sequence,
            "cycleId": "basic-workflow-v3:" + dataset.version_id + ":" + str(sequence),
            "data": {"time": text, "price": price},
            "provenance": provenance,
        }
'''


ENTRY_POINT = "sample_basic_ohlcv_map"


__all__ = ("ENTRY_POINT", "SOURCE")
