"""Basic Workflow v2 CSV-directory-to-price-map Sampler source."""

from __future__ import annotations


SOURCE = r'''import csv
import json
import math
import re
from datetime import datetime, timezone


_CSV_FIELDS = ("time", "open", "close", "high", "low")
_INDEX_FILE = "basic_workflow.json"
_SEGMENT = re.compile(r"^[A-Za-z0-9_-]+$")


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Basic Workflow JSON contains duplicate field: " + key)
        result[key] = value
    return result


def _constant(value):
    raise ValueError("Basic Workflow JSON contains invalid number: " + value)


def _float(value):
    result = float(value)
    if not math.isfinite(result):
        raise ValueError("Basic Workflow JSON contains a non-finite number.")
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


def _number(value, label):
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(label + " must be a finite positive number.") from exc
    if not math.isfinite(result) or result <= 0:
        raise ValueError(label + " must be a finite positive number.")
    return result


def _descriptor(dataset):
    capability = dataset.capabilities.get("basicWorkflow")
    if type(capability) is not dict or set(capability) != {"protocol", "descriptor"}:
        raise ValueError("Dataset requires the Basic Workflow capability.")
    if capability["protocol"] != "trade.app.basic-workflow-dataset/v2":
        raise ValueError("Dataset Basic Workflow capability protocol is incompatible.")
    descriptor = capability["descriptor"]
    fields = {
        "protocolId", "protocolVersion", "profile", "cashUnit", "quantityUnit",
        "executionConvention", "valuationConvention",
    }
    if type(descriptor) is not dict or set(descriptor) != fields:
        raise ValueError("Dataset Basic Workflow descriptor has an invalid schema.")
    if descriptor["protocolId"] != "trade.basic-workflow":
        raise ValueError("Dataset Basic Workflow protocol id is incompatible.")
    if descriptor["protocolVersion"] != "2.0.0":
        raise ValueError("Dataset Basic Workflow protocol version is incompatible.")
    if descriptor["profile"] != "multi-instrument-bar-position":
        raise ValueError("Dataset Basic Workflow profile is incompatible.")
    if descriptor["executionConvention"] != "prior-approved-intent-next-bar-open":
        raise ValueError("Dataset Basic Workflow execution convention is incompatible.")
    if descriptor["valuationConvention"] != "current-bar-close":
        raise ValueError("Dataset Basic Workflow valuation convention is incompatible.")
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
        raise ValueError("Basic Workflow Dataset index has an invalid schema.")
    if (
        value["protocolId"] != "trade.basic-workflow"
        or value["protocolVersion"] != "2.0.0"
        or value["profile"] != "multi-instrument-bar-position"
    ):
        raise ValueError("Basic Workflow Dataset index identity is incompatible.")
    files = value["files"]
    if type(files) is not dict or not files:
        raise ValueError("Basic Workflow Dataset index files must be a non-empty object.")
    for period, instruments in files.items():
        if type(period) is not str or not _SEGMENT.fullmatch(period):
            raise ValueError("Basic Workflow Dataset index period is invalid.")
        if type(instruments) is not dict or not instruments:
            raise ValueError("Basic Workflow Dataset index period cannot be empty.")
        for instrument, path in instruments.items():
            if type(instrument) is not str or not _SEGMENT.fullmatch(instrument):
                raise ValueError("Basic Workflow Dataset index instrument is invalid.")
            if path != period + "/" + instrument + ".csv":
                raise ValueError("Basic Workflow Dataset index path is not canonical.")
    return files


def _series(dataset, relative_path):
    values = []
    previous = None
    with dataset.path(relative_path).open("r", encoding="utf-8", newline="") as handle:
        reader = csv.reader(handle, strict=True)
        try:
            header = next(reader)
        except StopIteration as exc:
            raise ValueError(relative_path + " must contain a CSV header.") from exc
        if tuple(header) != _CSV_FIELDS:
            raise ValueError(relative_path + " has an incompatible CSV header.")
        for row_number, row in enumerate(reader, start=2):
            if len(row) != 5:
                raise ValueError(relative_path + " row has an incompatible shape.")
            current = _instant(row[0], relative_path + ".time")
            if previous is not None and current <= previous:
                raise ValueError(relative_path + " times must be strictly increasing.")
            bar = {
                "open": _number(row[1], relative_path + ".open"),
                "close": _number(row[2], relative_path + ".close"),
                "high": _number(row[3], relative_path + ".high"),
                "low": _number(row[4], relative_path + ".low"),
            }
            if bar["low"] > min(bar["open"], bar["close"]):
                raise ValueError(relative_path + " violates the OHLC lower bound.")
            if max(bar["open"], bar["close"]) > bar["high"]:
                raise ValueError(relative_path + " violates the OHLC upper bound.")
            values.append((current, bar, row_number))
            previous = current
    if not values:
        raise ValueError(relative_path + " must contain at least one bar.")
    return values


def sample_basic_price_map(dataset, parameters):
    _descriptor(dataset)
    if type(parameters) is not dict or set(parameters) != {"decisionPeriod"}:
        raise ValueError("Basic Workflow Sampler parameters require decisionPeriod only.")
    decision_period = parameters["decisionPeriod"]
    if type(decision_period) is not str or not _SEGMENT.fullmatch(decision_period):
        raise ValueError("Basic Workflow decisionPeriod is invalid.")
    files = _index(dataset)
    if decision_period not in files:
        raise ValueError("Basic Workflow decisionPeriod is absent from the Dataset.")

    series = {
        period: {
            instrument: _series(dataset, path)
            for instrument, path in sorted(instruments.items())
        }
        for period, instruments in sorted(files.items())
    }
    timeline = sorted({
        time
        for instrument_series in series[decision_period].values()
        for time, _bar, _row in instrument_series
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
                "time": _canonical(decision_time),
                "protocolVersion": "2.0.0",
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
                bar_time, bar, row_number = rows[cursor]
                period_values[instrument] = dict(bar)
                provenance["price." + period + "." + instrument] = {
                    "sourcePath": files[period][instrument],
                    "sourceRow": row_number,
                    "barTime": _canonical(bar_time),
                    "decisionTime": _canonical(decision_time),
                    "sourceFields": list(_CSV_FIELDS),
                    "protocolVersion": "2.0.0",
                }
            if period_values:
                price[period] = period_values
        text = _canonical(decision_time)
        yield {
            "decisionTime": text,
            "sequence": sequence,
            "cycleId": "basic-workflow:" + dataset.version_id + ":" + str(sequence),
            "data": {"time": text, "price": price},
            "provenance": provenance,
        }
'''


ENTRY_POINT = "sample_basic_price_map"


__all__ = ("ENTRY_POINT", "SOURCE")
