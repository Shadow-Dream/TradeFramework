#!/usr/bin/env python3
"""Execute one frozen Backtest inside one disposable Python Runtime process."""

from __future__ import annotations

import os
import sys
import time
import traceback
from pathlib import Path

from engine.contracts import strict_json
from engine.contracts.module import require_exact_fields
from engine.core import resource_ids
from engine.worker import backtest_execution


_DURABLE_STATUS_INTERVAL_SECONDS = 1.0


def _write_status(path: Path, payload: dict) -> None:
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        strict_json.dumps(payload, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
    os.replace(temporary, path)


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit(
            "usage: python -m engine.worker.backtest_runtime SPEC_PATH"
        )
    spec_path = Path(sys.argv[1]).resolve()
    spec = strict_json.loads(spec_path.read_text(encoding="utf-8"))
    require_exact_fields(
        spec,
        allowed={
            "schemaVersion", "config", "request", "backtestId",
            "runtimeRoot", "statusPath",
        },
        required={
            "schemaVersion", "config", "request", "backtestId",
            "runtimeRoot", "statusPath",
        },
        label="Backtest Runtime specification",
    )
    if spec["schemaVersion"] != 1:
        raise ValueError("Backtest Runtime specification schemaVersion 1 is required.")
    if not isinstance(spec["config"], dict) or not isinstance(spec["request"], dict):
        raise ValueError("Backtest Runtime config and request must be objects.")
    if (
        not isinstance(spec["backtestId"], str)
        or not spec["backtestId"].startswith("bt_")
        or not resource_ids.is_resource_id(spec["backtestId"])
    ):
        raise ValueError(
            "Backtest Runtime backtestId must be an Engine-issued Backtest resource ID."
        )
    for field in ("runtimeRoot", "statusPath"):
        if not isinstance(spec[field], str) or not spec[field]:
            raise ValueError(f"Backtest Runtime {field} is required.")
    execution_root = spec_path.parent
    runtime_root = Path(spec["runtimeRoot"]).resolve()
    status_path = Path(spec["statusPath"]).resolve()
    if runtime_root != execution_root / "runtime" or status_path != execution_root / "runtime-status.json":
        raise ValueError("Backtest Runtime paths do not match their execution root.")
    sequence = 0
    last_write = {"at": 0.0, "completed": -1, "phase": ""}

    def progress(completed_cycles, total_cycles, phase="running"):
        nonlocal sequence
        if (
            isinstance(completed_cycles, bool)
            or not isinstance(completed_cycles, int)
            or completed_cycles < 0
            or isinstance(total_cycles, bool)
            or not isinstance(total_cycles, int)
            or total_cycles < 0
            or (total_cycles > 0 and completed_cycles > total_cycles)
        ):
            raise ValueError("Backtest progress cycle counts are invalid.")
        if not isinstance(phase, str) or not phase:
            raise ValueError("Backtest progress phase must be a non-empty string.")
        now = time.monotonic()
        force = (
            phase != last_write["phase"]
            or completed_cycles >= total_cycles > 0
            or now - last_write["at"] >= _DURABLE_STATUS_INTERVAL_SECONDS
        )
        if not force or (
            completed_cycles == last_write["completed"]
            and phase == last_write["phase"]
        ):
            return
        sequence += 1
        _write_status(status_path, {
            "schemaVersion": 1,
            "status": "running",
            "sequence": sequence,
            "phase": phase,
            "completedCycles": completed_cycles,
            "totalCycles": total_cycles,
        })
        last_write.update(at=now, completed=completed_cycles, phase=phase)

    try:
        evidence = backtest_execution.execute_backtest(
            spec["config"],
            spec["request"],
            backtest_id=spec["backtestId"],
            progress_callback=progress,
            execution_root=runtime_root,
        )
        backtest_execution.require_backtest_execution_evidence(
            evidence,
            backtest_id=spec["backtestId"],
        )
        cycle_count = evidence["cycleCount"]
        sequence += 1
        _write_status(status_path, {
            "schemaVersion": 1,
            "status": "completed",
            "sequence": sequence,
            "phase": "completed",
            "completedCycles": cycle_count,
            "totalCycles": cycle_count,
        })
        return 0
    except BaseException as exc:
        sequence += 1
        _write_status(status_path, {
            "schemaVersion": 1,
            "status": "failed",
            "sequence": sequence,
            "phase": "failed",
            "error": str(exc) or exc.__class__.__name__,
        })
        traceback.print_exc(file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
