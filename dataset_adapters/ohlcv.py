"""Strict OHLCV CSV and bar-availability adapter for record Datasets."""

from __future__ import annotations

import csv
import copy
import io
import math
from collections.abc import Mapping
from datetime import datetime, timedelta, timezone

from engine.contracts import dataset as dataset_contracts
from engine.contracts import strict_json
from engine.contracts.data_model import (
    infer_schema,
    schema_types,
    validate_normalized_json_value,
)
from engine.core import resource_ids
from engine.repository import dataset_publication
from engine.repository import dataset_staging


def _date(value):
    value = str(value or "").strip()
    if len(value) == 8 and value.isdigit():
        return f"{value[:4]}-{value[4:6]}-{value[6:]}"
    return value


def parse_csv(csv_text):
    if not isinstance(csv_text, str) or not csv_text.strip():
        raise ValueError("OHLCV CSV must be non-empty.")
    reader = csv.DictReader(io.StringIO(csv_text))
    if not reader.fieldnames:
        raise ValueError("OHLCV CSV requires a header row.")
    normalized_headers = [str(name or "").strip().casefold() for name in reader.fieldnames]
    if any(not name for name in normalized_headers):
        raise ValueError("OHLCV CSV header names must be non-empty.")
    duplicates = sorted({
        name for name in normalized_headers if normalized_headers.count(name) > 1
    })
    if duplicates:
        raise ValueError(
            "OHLCV CSV contains duplicate normalized column(s): "
            + ", ".join(duplicates)
        )
    aliases = dict(zip(normalized_headers, reader.fieldnames))
    canonical_aliases = {
        "date": ("date", "datetime", "time", "timestamp"),
        "open": ("open",),
        "high": ("high",),
        "low": ("low",),
        "close": ("close", "adj close", "adj_close"),
        "volume": ("volume",),
    }
    resolved = {}
    for field_name, names in canonical_aliases.items():
        matches = [aliases[name] for name in names if name in aliases]
        if len(matches) > 1:
            raise ValueError(
                f"OHLCV CSV has multiple columns for canonical field '{field_name}'."
            )
        resolved[field_name] = matches[0] if matches else None
    missing = sorted(name for name in ("date", "open", "high", "low", "close") if resolved[name] is None)
    if missing:
        raise ValueError("OHLCV CSV is missing required column(s): " + ", ".join(missing))
    consumed = {name for name in resolved.values() if name is not None}

    def extra_value(value):
        text = str(value or "").strip()
        if not text:
            return None
        if text.lower() in {"true", "false"}:
            return text.lower() == "true"
        if len(text) > 1 and text[0] == "0" and text[1].isdigit():
            return text
        try:
            number = float(text)
        except ValueError:
            return text
        return int(number) if number.is_integer() and not any(char in text.lower() for char in (".", "e")) else number

    rows = []
    for row_number, row in enumerate(reader, start=2):
        date = _date(row.get(resolved["date"]))
        if not date:
            raise ValueError(f"OHLCV CSV row {row_number} has an empty date.")
        try:
            item = {
                "date": date,
                "open": float(row.get(resolved["open"], "")),
                "high": float(row.get(resolved["high"], "")),
                "low": float(row.get(resolved["low"], "")),
                "close": float(row.get(resolved["close"], "")),
                "volume": float(row.get(resolved["volume"], "0") or 0),
            }
        except (TypeError, ValueError) as exc:
            raise ValueError(f"OHLCV CSV row {row_number} contains an invalid numeric value.") from exc
        for original_name, raw_value in row.items():
            field_name = str(original_name or "").strip()
            if not field_name:
                raise ValueError(f"OHLCV CSV row {row_number} contains an unnamed field.")
            if original_name not in consumed:
                item[field_name] = extra_value(raw_value)
        rows.append(item)
    if not rows:
        raise ValueError("OHLCV CSV contains no data rows.")
    return rows


def _rows_to_csv(rows):
    output = io.StringIO()
    canonical = ["date", "open", "high", "low", "close", "volume"]
    extras = sorted({str(key) for row in rows for key in row if key not in canonical})
    writer = csv.DictWriter(output, fieldnames=canonical + extras)
    writer.writeheader()
    for row in rows:
        writer.writerow({
            key: (
                strict_json.dumps(value, sort_keys=True, separators=(",", ":"))
                if isinstance(value, (dict, list))
                else value
            )
            for key, value in row.items()
        })
    return output.getvalue()


def _normalize_rows(rows):
    if not isinstance(rows, list) or not rows:
        raise ValueError("OHLCV adapter requires at least one row.")
    normalized = []
    for index, raw in enumerate(rows):
        if not isinstance(raw, Mapping):
            raise ValueError(f"OHLCV row {index + 1} must be an object.")
        if any(not isinstance(key, str) or not key for key in raw):
            raise ValueError(f"OHLCV row {index + 1} contains an invalid field name.")
        missing = sorted(field for field in ("date", "open", "high", "low", "close") if field not in raw)
        if missing:
            raise ValueError(
                f"OHLCV row {index + 1} is missing required field(s): " + ", ".join(missing)
            )
        row = dict(raw)
        row["date"] = _date(row["date"])
        if not row["date"]:
            raise ValueError(f"OHLCV row {index + 1} has an empty date.")
        for field in ("open", "high", "low", "close", "volume"):
            raw_value = row.get(field, 0.0)
            if isinstance(raw_value, bool):
                raise ValueError(f"OHLCV row {index + 1} field '{field}' must be numeric.")
            try:
                value = float(raw_value)
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    f"OHLCV row {index + 1} field '{field}' must be numeric."
                ) from exc
            if not math.isfinite(value):
                raise ValueError(f"OHLCV row {index + 1} field '{field}' must be finite.")
            row[field] = value
        for field, value in row.items():
            if field in {"date", "open", "high", "low", "close", "volume"}:
                continue
            validate_normalized_json_value(
                value, {}, path=f"OHLCV row {index + 1}.{field}"
            )
        normalized.append(row)
    return normalized


def _instant(value):
    text = str(value)
    if len(text) == 10:
        text += "T00:00:00Z"
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(f"OHLCV timestamp is invalid: {value!r}.") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _record_schema(rows):
    fields = {}
    for field_name in sorted({str(key) for row in rows for key in row}):
        observed = {
            tuple(sorted(schema_types(infer_schema(row.get(field_name)))))
            for row in rows
            if row.get(field_name) is not None
        }
        if observed and observed <= {("integer",), ("number",)}:
            field_schema = {"type": "number"}
        elif len(observed) == 1:
            types = list(next(iter(observed)))
            field_schema = {"type": types[0] if len(types) == 1 else types}
        else:
            raise ValueError(f"OHLCV field '{field_name}' has no single concrete JSON type.")
        if any(row.get(field_name) is None for row in rows):
            field_schema["type"] = sorted(set(schema_types(field_schema)) | {"null"})
        fields[field_name] = field_schema
    return {"fields": fields, "entityKeys": [], "sortKeys": []}


def _available_at(row, interval, policy):
    explicit_fields = [field for field in ("availableAt", "available_at") if field in row]
    if len(explicit_fields) == 2 and row[explicit_fields[0]] != row[explicit_fields[1]]:
        raise ValueError("OHLCV row has conflicting availableAt and available_at values.")
    if explicit_fields:
        explicit = row[explicit_fields[0]]
        if not isinstance(explicit, str) or not explicit.strip():
            raise ValueError("OHLCV explicit availability time must be a non-empty string.")
        return explicit.strip()
    event_time = str(row["date"])
    if policy == "timestamp_is_available_at":
        return event_time
    if policy != "bar_end_utc":
        raise ValueError("OHLCV adapter requires an explicit availability policy.")
    text = event_time[:-1] + "+00:00" if event_time.endswith("Z") else event_time
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    durations = {
        "1m": timedelta(minutes=1), "2m": timedelta(minutes=2),
        "5m": timedelta(minutes=5), "10m": timedelta(minutes=10),
        "15m": timedelta(minutes=15), "h": timedelta(hours=1),
        "1h": timedelta(hours=1), "d": timedelta(days=1),
        "1d": timedelta(days=1), "w": timedelta(days=7),
        "1w": timedelta(days=7),
    }
    interval_key = str(interval).lower()
    if interval_key in {"m", "1mo", "month", "monthly"}:
        year = parsed.year + (parsed.month == 12)
        available = parsed.replace(year=year, month=1 if parsed.month == 12 else parsed.month + 1, day=1)
    else:
        duration = durations.get(interval_key)
        if duration is None:
            raise ValueError(f"OHLCV adapter has no bar-end rule for interval '{interval}'.")
        available = parsed + duration
    return available.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def register_dataset(
    config, *, dataset_id, name, symbol, source, interval, rows,
    availability_policy, metadata=None, display_time_zone="UTC",
    application_capabilities=None,
):
    rows = _normalize_rows(rows)
    dataset_id = resource_ids.normalize_resource_id(dataset_id)
    for label, value in (("symbol", symbol), ("source", source), ("interval", interval)):
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"OHLCV adapter {label} must be a non-empty string.")
    if not isinstance(availability_policy, str) or not availability_policy:
        raise ValueError("OHLCV adapter availability_policy must be a non-empty string.")
    if metadata is None:
        metadata = {}
    elif not isinstance(metadata, Mapping):
        raise ValueError("OHLCV adapter metadata must be an object.")
    else:
        metadata = dict(metadata)
    adapter_metadata = {
        "adapter": "ohlcv",
        "symbol": symbol.strip(),
        "interval": interval.strip(),
        "availabilityPolicy": availability_policy,
    }
    conflicts = sorted(
        field for field, value in adapter_metadata.items()
        if field in metadata and metadata[field] != value
    )
    if conflicts:
        raise ValueError(
            "OHLCV metadata conflicts with adapter field(s): " + ", ".join(conflicts)
        )
    metadata.update(adapter_metadata)
    records = []
    record_rows = []
    previous_available = None
    for sequence, row in enumerate(rows):
        available_at = _available_at(row, interval, availability_policy)
        current_available = _instant(available_at)
        if previous_available is not None and current_available < previous_available:
            raise ValueError(
                "OHLCV records must already be ordered by non-decreasing availability time."
            )
        previous_available = current_available
        values = {
            **row,
            "symbol": symbol.strip(),
            "available_at": available_at,
        }
        record_rows.append(values)
        records.append({
            "sequence": sequence,
            "eventTime": row["date"],
            "availableAt": available_at,
            "values": values,
        })
    staging = dataset_staging.create_dataset_staging(config, dataset_id)
    try:
        (staging.path / "bars.csv").write_text(
            _rows_to_csv(rows), encoding="utf-8"
        )
        (staging.path / "records.jsonl").write_text(
            "".join(
                strict_json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n"
                for record in records
            ),
            encoding="utf-8",
        )
        capabilities = {
            dataset_contracts.RECORDS_CAPABILITY: {
                "protocol": dataset_contracts.RECORDS_PROTOCOL,
                "descriptor": {
                    "path": "records.jsonl",
                    "recordCount": len(records),
                    "eventTimeField": "eventTime",
                    "availableTimeField": "availableAt",
                    "valueSchema": {
                        **_record_schema(record_rows),
                        "entityKeys": ["symbol"],
                        "sortKeys": ["date"],
                    },
                },
            },
            dataset_contracts.VISUALIZATION_CAPABILITY: {
                "protocol": dataset_contracts.VISUALIZATION_PROTOCOL,
                "descriptor": {"timeZone": display_time_zone},
            },
        }
        if application_capabilities is not None:
            if not isinstance(application_capabilities, Mapping):
                raise ValueError("OHLCV application_capabilities must be an object.")
            conflicts = sorted(set(application_capabilities) & set(capabilities))
            if conflicts:
                raise ValueError(
                    "OHLCV application capabilities conflict with reserved capability names: "
                    + ", ".join(conflicts)
                )
            capabilities.update(copy.deepcopy(dict(application_capabilities)))
        return dataset_publication.publish_dataset_version(
            config,
            dataset={
                "datasetId": dataset_id,
                "name": str(name or dataset_id),
                "source": {
                    "type": "ohlcv-adapter",
                    "details": {"provider": source.strip()},
                },
                "metadata": metadata,
            },
            staging=staging,
            capabilities=capabilities,
            version_source={
                "type": "ohlcv-records",
                "details": {
                    "provider": source.strip(),
                    "symbol": symbol.strip(),
                    "interval": interval.strip(),
                    "availabilityPolicy": availability_policy,
                },
            },
        )
    finally:
        dataset_staging.discard_dataset_staging(staging)
