"""Fail-closed conformance for Basic Workflow v2 CSV directories."""

from __future__ import annotations

import csv
import math
import re
from datetime import datetime, timezone
from pathlib import Path

from builtin_implementations.basic_workflow_contracts import CSV_FIELDS


PROTOCOL_ID = "trade.basic-workflow"
PROTOCOL_VERSION = "2.0.0"
PROFILE_ID = "multi-instrument-bar-position"
CAPABILITY_PROTOCOL = "trade.app.basic-workflow-dataset/v2"
INDEX_FILE = "basic_workflow.json"

_PATH_SEGMENT = re.compile(r"^[A-Za-z0-9_-]+$")


def _instant(value, label):
    if type(value) is not str or not value:
        raise ValueError(f"{label} must be a non-empty absolute ISO-8601 timestamp.")
    text = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(f"{label} must be an absolute ISO-8601 timestamp.") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{label} must include an absolute timezone.")
    return parsed.astimezone(timezone.utc)


def _number(value, label):
    if type(value) is not str or not value:
        raise ValueError(f"{label} must be a finite positive number.")
    try:
        parsed = float(value)
    except ValueError as exc:
        raise ValueError(f"{label} must be a finite positive number.") from exc
    if not math.isfinite(parsed) or parsed <= 0:
        raise ValueError(f"{label} must be a finite positive number.")
    return parsed


def _segment(value, label):
    if type(value) is not str or not _PATH_SEGMENT.fullmatch(value):
        raise ValueError(
            f"{label} must contain only ASCII letters, digits, underscore or hyphen."
        )
    return value


def require_basic_workflow_descriptor(descriptor):
    if type(descriptor) is not dict:
        raise ValueError("Dataset basicWorkflow descriptor must be an object.")
    required = {
        "protocolId",
        "protocolVersion",
        "profile",
        "cashUnit",
        "quantityUnit",
        "executionConvention",
        "valuationConvention",
    }
    if set(descriptor) != required:
        raise ValueError("Dataset basicWorkflow descriptor fields are not canonical.")
    expected = {
        "protocolId": PROTOCOL_ID,
        "protocolVersion": PROTOCOL_VERSION,
        "profile": PROFILE_ID,
        "executionConvention": "prior-approved-intent-next-bar-open",
        "valuationConvention": "current-bar-close",
    }
    for field, value in expected.items():
        if descriptor[field] != value:
            raise ValueError(f"Dataset basicWorkflow {field} is incompatible.")
    for field in ("cashUnit", "quantityUnit"):
        value = descriptor[field]
        if type(value) is not str or not value or value.strip() != value:
            raise ValueError(f"Dataset basicWorkflow {field} must be a non-empty string.")
    return dict(descriptor)


def require_basic_workflow_capability(capabilities):
    if type(capabilities) is not dict:
        raise ValueError("Dataset capabilities must be an object.")
    capability = capabilities.get("basicWorkflow")
    if type(capability) is not dict or set(capability) != {"protocol", "descriptor"}:
        raise ValueError("Dataset must declare the basicWorkflow capability.")
    if capability["protocol"] != CAPABILITY_PROTOCOL:
        raise ValueError("Dataset basicWorkflow capability protocol is unsupported.")
    return require_basic_workflow_descriptor(capability["descriptor"])


def _csv_report(path, label):
    previous = None
    first = None
    last = None
    count = 0
    try:
        handle = path.open("r", encoding="utf-8", newline="")
    except UnicodeError as exc:
        raise ValueError(f"{label} must be UTF-8 CSV.") from exc
    with handle:
        reader = csv.reader(handle, strict=True)
        try:
            header = next(reader)
        except StopIteration as exc:
            raise ValueError(f"{label} must contain the canonical CSV header.") from exc
        except csv.Error as exc:
            raise ValueError(f"{label} is not valid CSV.") from exc
        if tuple(header) != CSV_FIELDS:
            raise ValueError(
                f"{label} header must be exactly {','.join(CSV_FIELDS)}."
            )
        try:
            for row_number, row in enumerate(reader, start=2):
                row_label = f"{label}:{row_number}"
                if len(row) != len(CSV_FIELDS):
                    raise ValueError(f"{row_label} must contain exactly five columns.")
                current = _instant(row[0], f"{row_label}.time")
                if previous is not None and current <= previous:
                    raise ValueError(f"{label} times must be strictly increasing.")
                open_value = _number(row[1], f"{row_label}.open")
                close_value = _number(row[2], f"{row_label}.close")
                high_value = _number(row[3], f"{row_label}.high")
                low_value = _number(row[4], f"{row_label}.low")
                if low_value > min(open_value, close_value):
                    raise ValueError(f"{row_label} violates the OHLC lower bound.")
                if max(open_value, close_value) > high_value:
                    raise ValueError(f"{row_label} violates the OHLC upper bound.")
                previous = current
                first = current if first is None else first
                last = current
                count += 1
        except csv.Error as exc:
            raise ValueError(f"{label} is not valid CSV.") from exc
    if count == 0:
        raise ValueError(f"{label} must contain at least one bar.")
    return count, first, last


def validate_dataset_directory(source_root, descriptor):
    """Validate one canonical period/instrument CSV tree and report its shape."""

    require_basic_workflow_descriptor(descriptor)
    root = Path(source_root)
    if root.is_symlink() or not root.is_dir():
        raise ValueError("Basic Workflow source_root must be a non-symlink directory.")
    periods = {}
    file_count = 0
    row_count = 0
    first = None
    last = None
    entries = sorted(root.iterdir(), key=lambda item: item.name)
    if not entries:
        raise ValueError("Basic Workflow Dataset directory cannot be empty.")
    for period_path in entries:
        period = _segment(period_path.name, "Basic Workflow period")
        if period_path.is_symlink() or not period_path.is_dir():
            raise ValueError(
                "Basic Workflow Dataset root may contain only period directories."
            )
        instruments = []
        period_rows = 0
        children = sorted(period_path.iterdir(), key=lambda item: item.name)
        if not children:
            raise ValueError(f"Basic Workflow period '{period}' cannot be empty.")
        for csv_path in children:
            if csv_path.is_symlink() or not csv_path.is_file() or csv_path.suffix != ".csv":
                raise ValueError(
                    f"Basic Workflow period '{period}' may contain only regular .csv files."
                )
            instrument = _segment(csv_path.stem, "Basic Workflow instrumentId")
            count, file_first, file_last = _csv_report(
                csv_path,
                f"{period}/{csv_path.name}",
            )
            instruments.append(instrument)
            file_count += 1
            row_count += count
            period_rows += count
            first = file_first if first is None else min(first, file_first)
            last = file_last if last is None else max(last, file_last)
        periods[period] = {
            "instruments": instruments,
            "rowCount": period_rows,
        }
    canonical = lambda value: value.isoformat().replace("+00:00", "Z")
    return {
        "protocolId": PROTOCOL_ID,
        "protocolVersion": PROTOCOL_VERSION,
        "profile": PROFILE_ID,
        "periods": periods,
        "fileCount": file_count,
        "rowCount": row_count,
        "firstTime": canonical(first),
        "lastTime": canonical(last),
    }


__all__ = (
    "CAPABILITY_PROTOCOL",
    "INDEX_FILE",
    "PROFILE_ID",
    "PROTOCOL_ID",
    "PROTOCOL_VERSION",
    "require_basic_workflow_capability",
    "require_basic_workflow_descriptor",
    "validate_dataset_directory",
)
