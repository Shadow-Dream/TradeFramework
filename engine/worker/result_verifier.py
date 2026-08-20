"""Strictly verify one byte range of a physically framed Result archive."""

from __future__ import annotations

import os
import sys
import traceback
from pathlib import Path

from engine.contracts import result as result_contracts
from engine.contracts import strict_json
from engine.contracts.module import require_exact_fields
from engine.runtime import result_stream


_CYCLE_INDEX_TOKEN = "__ENGINE_RESULT_CYCLE_INDEX__"
_SPEC_FIELDS = frozenset({
    "schemaVersion",
    "shardIndex",
    "resultPath",
    "rangeStart",
    "rangeEnd",
    "finalRange",
    "dataKeys",
    "ledgerPath",
    "outcomePath",
})
_OUTCOME_FIELDS = frozenset({
    "schemaVersion",
    "status",
    "shardIndex",
    "lineCount",
    "validatedCount",
    "firstCycleId",
    "lastCycleId",
    "errorLocalIndex",
    "errorType",
    "errorMessage",
})


def _write_outcome(path, outcome):
    require_exact_fields(
        outcome,
        allowed=_OUTCOME_FIELDS,
        required=_OUTCOME_FIELDS,
        label="Result verifier outcome",
    )
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        strict_json.dumps(outcome, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _owned_output_path(spec_root, raw_path, label):
    if not isinstance(raw_path, str) or not raw_path:
        raise ValueError(f"Result verifier {label} is required.")
    candidate = Path(raw_path)
    if candidate.is_symlink():
        raise ValueError(f"Result verifier {label} may not be a symlink.")
    candidate = candidate.resolve()
    if candidate.parent != spec_root or candidate.exists():
        raise ValueError(f"Result verifier {label} is not a new owned path.")
    return candidate


def verify_specification(spec_path):
    spec_path = Path(spec_path)
    if spec_path.is_symlink() or not spec_path.is_file():
        raise ValueError("Result verifier specification path is invalid.")
    spec_path = spec_path.resolve()
    spec = strict_json.loads(spec_path.read_bytes())
    require_exact_fields(
        spec,
        allowed=_SPEC_FIELDS,
        required=_SPEC_FIELDS,
        label="Result verifier specification",
    )
    if spec["schemaVersion"] != 1:
        raise ValueError("Result verifier specification schemaVersion 1 is required.")
    shard_index = spec["shardIndex"]
    if (
        isinstance(shard_index, bool)
        or not isinstance(shard_index, int)
        or shard_index < 0
        or shard_index >= result_stream.MAX_RESULT_VERIFICATION_SHARDS
    ):
        raise ValueError("Result verifier shardIndex is invalid.")
    result_path = Path(spec["resultPath"])
    if result_path.is_symlink() or not result_path.is_file():
        raise ValueError("Result verifier archive path is invalid.")
    result_path = result_path.resolve()
    data_keys = spec["dataKeys"]
    if not isinstance(data_keys, dict):
        raise ValueError("Result verifier dataKeys must be an object.")
    validate_cycle_data = result_contracts.compile_cycle_validator(data_keys)
    ledger_path = _owned_output_path(
        spec_path.parent, spec["ledgerPath"], "ledgerPath"
    )
    outcome_path = _owned_output_path(
        spec_path.parent, spec["outcomePath"], "outcomePath"
    )
    line_count = result_stream.count_framed_cycle_lines(
        result_path,
        spec["rangeStart"],
        spec["rangeEnd"],
    )
    ledger = result_stream.ResultCycleIdentityLedger(ledger_path)
    validated_count = 0
    first_cycle_id = None
    last_cycle_id = None
    rejected = None
    try:
        try:
            for local_index, cycle in enumerate(
                result_stream.iter_framed_cycle_values(
                    result_path,
                    spec["rangeStart"],
                    spec["rangeEnd"],
                    final_range=spec["finalRange"],
                )
            ):
                ledger.select_cycle(local_index)
                result_contracts.require_cycle(
                    cycle,
                    _CYCLE_INDEX_TOKEN,
                    validate_cycle_data,
                    ledger,
                )
                cycle_id = cycle["cycleId"]
                if first_cycle_id is None:
                    first_cycle_id = cycle_id
                last_cycle_id = cycle_id
                validated_count += 1
        except ValueError as exc:
            rejected = {
                "errorLocalIndex": validated_count,
                "errorType": "ValueError",
                "errorMessage": str(exc),
            }
    finally:
        ledger.close()
    if rejected is None and validated_count != line_count:
        raise RuntimeError("Result verifier did not consume every physical cycle line.")
    outcome = {
        "schemaVersion": 1,
        "status": "rejected" if rejected is not None else "verified",
        "shardIndex": shard_index,
        "lineCount": line_count,
        "validatedCount": validated_count,
        "firstCycleId": first_cycle_id,
        "lastCycleId": last_cycle_id,
        "errorLocalIndex": None,
        "errorType": None,
        "errorMessage": None,
    }
    if rejected is not None:
        outcome.update(rejected)
    _write_outcome(outcome_path, outcome)
    return outcome


def main():
    if len(sys.argv) != 2:
        raise SystemExit(
            "usage: python -m engine.worker.result_verifier SPEC_PATH"
        )
    try:
        verify_specification(sys.argv[1])
        return 0
    except BaseException:
        traceback.print_exc(file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ("verify_specification",)
