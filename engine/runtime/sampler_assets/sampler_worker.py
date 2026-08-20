#!/usr/bin/env python3
"""JSON-lines worker for one immutable Python Script Sampler version."""

from __future__ import annotations

import json
import sys
import traceback
import math
from collections.abc import Iterable, Mapping

from sampler_sdk import Dataset


PROTOCOL_STDOUT = sys.stdout
sys.stdout = sys.stderr


def _is_json(value):
    """Allocation-free success proof for the JSON-lines encoder boundary."""
    if value is None or isinstance(value, (str, bool)):
        return True
    if isinstance(value, int) and not isinstance(value, bool):
        return True
    if isinstance(value, float):
        return math.isfinite(value)
    if type(value) is list:
        return all(_is_json(item) for item in value)
    if type(value) is dict:
        return all(
            isinstance(key, str) and _is_json(item)
            for key, item in value.items()
        )
    return False


def _raise_invalid_json(value, path="value"):
    if value is None or isinstance(value, (str, bool)):
        return
    if isinstance(value, int) and not isinstance(value, bool):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{path} must contain only finite JSON numbers.")
        return
    if type(value) is list:
        for index, item in enumerate(value):
            _raise_invalid_json(item, f"{path}[{index}]")
        return
    if type(value) is dict:
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError(f"{path} object keys must be strings.")
            _raise_invalid_json(item, f"{path}.{key}")
        return
    raise ValueError(f"{path} contains a non-JSON value.")


def _validate_json(value, path="value"):
    if _is_json(value):
        return
    _raise_invalid_json(value, path)


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON object key: {key}")
        result[key] = value
    return result


def _reject_constant(value):
    raise ValueError(f"Invalid JSON constant: {value}")


def _finite_float(value):
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"JSON number is outside the finite range: {value}")
    return result


def _encode(value):
    _validate_json(value)
    return json.dumps(value, separators=(",", ":"), allow_nan=False)


def _decode(value):
    return json.loads(
        value,
        parse_constant=_reject_constant,
        parse_float=_finite_float,
        object_pairs_hook=_pairs,
    )


_PROTOCOL_ENCODER = _encode
_PROTOCOL_DECODER = _decode


def emit(payload):
    PROTOCOL_STDOUT.write(_PROTOCOL_ENCODER(payload) + "\n")
    PROTOCOL_STDOUT.flush()


def require_fields(value, *, allowed, required, label):
    if type(value) is not dict:
        raise ValueError(f"{label} must be an object.")
    unknown = sorted(set(value) - set(allowed))
    if unknown:
        raise ValueError(f"{label} contains unsupported field(s): " + ", ".join(unknown))
    missing = sorted(set(required) - set(value))
    if missing:
        raise ValueError(f"{label} is missing required field(s): " + ", ".join(missing))


def main():
    raw = sys.stdin.readline()
    if not raw:
        raise ValueError("Sampler worker requires an initialization request.")
    request = _PROTOCOL_DECODER(raw)
    require_fields(
        request,
        allowed={"source", "entryPoint", "dataset", "parameters"},
        required={"source", "entryPoint", "dataset", "parameters"},
        label="Sampler worker initialization request",
    )
    source = request["source"]
    entry_point = request["entryPoint"]
    if not isinstance(source, str) or not source.strip():
        raise ValueError("Sampler worker source must be a non-empty string.")
    if not isinstance(entry_point, str) or not entry_point.strip():
        raise ValueError("Sampler worker entryPoint must be a non-empty string.")
    entry_point = entry_point.strip()
    namespace = {"__name__": "trade_user_sampler", "__file__": "<sampler>"}
    exec(compile(source, "<sampler>", "exec"), namespace, namespace)
    callback = namespace.get(entry_point)
    if not callable(callback):
        raise ValueError(f"Sampler entry point is not callable: {entry_point}")
    if type(request["dataset"]) is not dict or type(request["parameters"]) is not dict:
        raise ValueError("Sampler worker dataset and parameters must be objects.")
    values = callback(Dataset(request["dataset"]), request["parameters"])
    if isinstance(values, Mapping) or not isinstance(values, Iterable):
        raise ValueError("Sampler entry point must return an iterable of sample objects.")
    for sequence, value in enumerate(values):
        require_fields(
            value,
            allowed={"decisionTime", "data", "provenance", "sequence", "cycleId"},
            required={"decisionTime", "data"},
            label=f"Sampler item {sequence}",
        )
        decision_time = value["decisionTime"]
        data = value["data"]
        provenance = value.get("provenance", {})
        item_sequence = value.get("sequence", sequence)
        if not isinstance(decision_time, str) or not decision_time.strip():
            raise ValueError(f"Sampler item {sequence} decisionTime must be a non-empty string.")
        if type(data) is not dict:
            raise ValueError(f"Sampler item {sequence} data must be an object.")
        if type(provenance) is not dict:
            raise ValueError(f"Sampler item {sequence} provenance must be an object.")
        if any(
            not isinstance(key, str)
            or not key
            or type(item) is not dict
            for key, item in provenance.items()
        ):
            raise ValueError(
                f"Sampler item {sequence} provenance must map non-empty string paths to objects."
            )
        if (
            isinstance(item_sequence, bool)
            or not isinstance(item_sequence, int)
            or item_sequence < 0
        ):
            raise ValueError(f"Sampler item {sequence} sequence must be a non-negative integer.")
        if item_sequence != sequence:
            raise ValueError(
                f"Sampler item {sequence} sequence must equal its zero-based emission order."
            )
        payload = {
            "decisionTime": decision_time,
            "data": dict(data),
            "provenance": {key: dict(item) for key, item in provenance.items()},
            "sequence": item_sequence,
        }
        if "cycleId" in value:
            cycle_id = value["cycleId"]
            if not isinstance(cycle_id, str) or not cycle_id.strip():
                raise ValueError(
                    f"Sampler item {sequence} cycleId must be a non-empty string when present."
                )
            payload["cycleId"] = cycle_id
        emit({"type": "sample", "sample": payload})
    emit({"type": "complete"})


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        emit({"type": "error", "error": f"{exc.__class__.__name__}: {exc}"})
        traceback.print_exc(file=sys.stderr)
        raise SystemExit(1)
