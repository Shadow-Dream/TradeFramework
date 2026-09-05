"""Service-facing Result slice and archive-validation operations."""

from __future__ import annotations

import copy
import tempfile
from pathlib import Path

from engine.control import database as engine_database
from engine.repository import backtest_results as result_repository
from engine.runtime.projection_worker import write_result_projection_in_worker
from engine.runtime.result_projection import write_verified_result_projection
from engine.service import projection_cache
from engine.service import projection_prewarm


def write_backtest_result_slice_cached(
    config,
    backtest_id,
    paths,
    temporary_modules,
    destination_path,
    *,
    module_definitions_loader=None,
    projection_format="rows",
    window=None,
    priority="interactive",
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
    module_definitions = {}
    if temporary_modules:
        if not callable(module_definitions_loader):
            raise ValueError(
                "Temporary visualization requires the Archived Module repository."
            )
        available_definitions = module_definitions_loader()
        references = {
            (module.get("kind"), module.get("moduleId"), module.get("version"))
            for module in temporary_modules
        }
        module_definitions = {
            key: definition
            for key, definition in available_definitions.items()
            if (
                definition.get("kind"),
                definition.get("moduleId"),
                definition.get("version"),
            ) in references
        }
        matched = {
            (
                definition.get("kind"),
                definition.get("moduleId"),
                definition.get("version"),
            )
            for definition in module_definitions.values()
        }
        if matched != references:
            raise ValueError(
                "Temporary visualization requires every exact Archived Module Definition."
            )
    identity = projection_cache.projection_identity(
        kind="backtest",
        result_id=backtest_id,
        result_content_digest=evidence["contentDigest"],
        paths=paths,
        temporary_modules=temporary_modules,
        module_definitions=module_definitions,
        projection_format=projection_format,
        window=window,
    )
    cache = projection_cache.write_cached_projection(
        config,
        identity,
        destination_path,
        lambda staged: write_result_projection_in_worker(
            evidence,
            paths,
            temporary_modules,
            module_definitions,
            staged,
            projection_format=projection_format,
            window=window,
            priority=priority,
        ),
    )
    if cache["hit"]:
        projection_prewarm.prepare_projection_source(
            "backtest", evidence, interactive=priority == "interactive"
        )
    return {"path": destination_path, "cache": cache}


def write_backtest_result_slice(*args, **kwargs):
    return write_backtest_result_slice_cached(*args, **kwargs)["path"]


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


__all__ = (
    "validate_backtest_result_archive",
    "write_backtest_result_slice",
    "write_backtest_result_slice_cached",
)
