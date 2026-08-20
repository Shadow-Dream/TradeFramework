"""Service-facing Result slice and archive-validation operations."""

from __future__ import annotations

import copy
import tempfile
from pathlib import Path

from engine.control import database as engine_database
from engine.repository import backtest_results as result_repository
from engine.runtime.result_projection import write_verified_result_projection
from engine.runtime.result_runtime import write_result_projection_in_runtime


def write_backtest_result_slice(
    config,
    backtest_id,
    paths,
    temporary_modules,
    destination_path,
    *,
    module_definitions_loader=None,
):
    """Write one bounded-memory Result projection for HTTP or local consumers."""
    if not isinstance(temporary_modules, list):
        raise ValueError("Result slice temporaryModules must be an array.")
    with engine_database.connect_database(config) as conn:
        row = conn.execute(
            "SELECT status FROM backtests WHERE backtest_id = ?", (backtest_id,)
        ).fetchone()
    if not row:
        raise ValueError(f"Unknown backtest: {backtest_id}")
    if row["status"] == "archived":
        raise ValueError(
            f"Backtest '{backtest_id}' is archived and its Result is not loaded."
        )
    evidence = result_repository.load_result_archive_evidence(
        config, backtest_id, verify_digest=False
    )
    if temporary_modules:
        if not callable(module_definitions_loader):
            raise ValueError(
                "Temporary visualization requires the Archived Module repository."
            )
        module_definitions = module_definitions_loader()
        return write_result_projection_in_runtime(
            evidence,
            paths,
            temporary_modules,
            module_definitions,
            destination_path,
        )
    return write_verified_result_projection(
        evidence, paths, destination_path
    )


def validate_backtest_result_archive(config, backtest_id):
    """Strictly validate a sealed Result without materializing its cycles."""
    evidence = result_repository.load_result_archive_evidence(
        config, backtest_id, verify_digest=False
    )
    with tempfile.TemporaryDirectory(
        prefix="trade-result-validation-"
    ) as root:
        write_verified_result_projection(
            evidence,
            ["metrics"],
            Path(root) / "validated.json",
        )
    return {
        "metrics": copy.deepcopy(evidence["metrics"]),
        "completedAt": evidence["manifest"]["catalog"]["completedAt"],
    }


__all__ = ("validate_backtest_result_archive", "write_backtest_result_slice")
