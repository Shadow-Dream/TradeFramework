"""Direct Backtest execution with Engine-owned scratch and catalog recovery."""

from __future__ import annotations

import tempfile
from pathlib import Path

from engine.archive import backtest_result as backtest_result_archive
from engine.core import resource_ids
from engine.repository import backtest_results as result_repository
from engine.service import backtest_results as backtest_result_service
from engine.worker import backtest_execution as backtest_worker


def _recover_published_result(config, request, backtest_id):
    return backtest_result_service.recover_backtest_result_catalog(
        config,
        backtest_id,
        request,
    )


def run_backtest(
    config,
    request,
    *,
    backtest_id=None,
    progress_callback=None,
    execution_root=None,
):
    """Run one frozen Backtest and return its fully recovered public metadata."""

    if not isinstance(request, dict) or "executionSnapshot" not in request:
        raise ValueError(
            "Backtest execution requires an explicitly frozen executionSnapshot."
        )
    if not isinstance(request["executionSnapshot"], dict) or not request[
        "executionSnapshot"
    ]:
        raise ValueError(
            "Backtest executionSnapshot must be a non-empty Engine-owned object."
        )
    if execution_root is None:
        execution_parent = Path(config["controlRoot"]) / "backtest-runs"
        execution_parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix="direct-",
            dir=execution_parent,
        ) as owned_execution_root:
            return run_backtest(
                config,
                request,
                backtest_id=backtest_id,
                progress_callback=progress_callback,
                execution_root=Path(owned_execution_root),
            )
    if backtest_id is None:
        backtest_id = resource_ids.new_resource_id("backtest")
    if (
        not isinstance(backtest_id, str)
        or not backtest_id.startswith("bt_")
        or not resource_ids.is_resource_id(backtest_id)
    ):
        raise ValueError("Backtest ID must be an Engine-issued Backtest resource ID.")

    result_directory = backtest_result_archive.archive_directory(
        config["releaseRoot"],
        backtest_id,
        label="Backtest Result directory",
    )
    destination_existed = result_directory.exists() or result_directory.is_symlink()
    try:
        evidence = backtest_worker.execute_backtest(
            config,
            request,
            backtest_id=backtest_id,
            progress_callback=progress_callback,
            execution_root=execution_root,
        )
    except backtest_worker.BacktestResultPublicationUncertain as primary_error:
        primary_traceback = primary_error.__traceback__
        try:
            recovered = (
                _recover_published_result(config, request, backtest_id)
                if not destination_existed
                and (result_directory.exists() or result_directory.is_symlink())
                else None
            )
        except Exception as recovery_error:
            raise primary_error.with_traceback(primary_traceback) from recovery_error
        if recovered is not None:
            return recovered
        raise primary_error.with_traceback(primary_traceback)

    backtest_worker.require_backtest_execution_evidence(
        evidence,
        backtest_id=backtest_id,
    )
    recovered = _recover_published_result(config, request, backtest_id)
    if recovered is None:
        raise RuntimeError(
            "Backtest worker returned without publishing its sealed Result."
        )
    catalog_state = result_repository.catalog_commit_state(
        config,
        backtest_id,
        content_digest=evidence["contentDigest"],
        result_size=evidence["resultSize"],
    )
    if (
        recovered.get("backtestId") != evidence["backtestId"]
        or not isinstance(recovered.get("metrics"), dict)
        or recovered["metrics"].get("cycleCount") != evidence["cycleCount"]
        or catalog_state != "committed"
    ):
        raise ValueError(
            "Recovered Backtest metadata does not match worker execution evidence."
        )
    return recovered


__all__ = ("run_backtest",)
