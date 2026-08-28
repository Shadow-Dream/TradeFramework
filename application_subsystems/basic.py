"""HTTP/page adapter for the trusted Basic Workflow application."""

from __future__ import annotations

from application_protocols.basic_workflow import market_service
from application_protocols.basic_workflow.manifest import PROTOCOL_ID

from .registry import (
    SubsystemApiRoute,
    SubsystemDefinition,
    SubsystemEvent,
    SubsystemPage,
    SubsystemResponse,
)


def _market_state(context, payload):
    if payload is not None:
        raise ValueError("Basic market snapshot GET does not accept a body.")
    market_service.ensure_snapshot_worker(context.config)
    return SubsystemResponse(200, market_service.market_state(context.config))


def _sync_market(context, payload):
    result = market_service.sync_market(context.config, payload)
    market_service.ensure_snapshot_worker(context.config)
    return SubsystemResponse(
        200,
        result,
        (
            SubsystemEvent(
                "basic-market.snapshot.synced",
                {
                    "snapshotId": result["snapshot"]["snapshotId"],
                    "providerId": result["snapshot"]["providerId"],
                    "instrumentCount": result["snapshot"]["instrumentCount"],
                },
            ),
        ),
    )


def _set_watchlist(context, payload):
    result = market_service.set_watchlist(context.config, payload)
    market_service.ensure_snapshot_worker(context.config)
    return SubsystemResponse(
        200,
        result,
        (
            SubsystemEvent(
                "basic-market.watchlist.updated",
                {
                    "snapshotId": result["snapshotId"],
                    "instrumentCount": len(result["watchlist"]),
                },
            ),
        ),
    )


def _open_instrument(context, payload):
    result = market_service.open_instrument(
        context.config,
        payload,
        prepared_store=context.prepared_store,
        job_manager=context.job_manager,
        session_identity=context.session_identity,
        owner_identity=context.owner_identity or context.session_identity,
    )
    return SubsystemResponse(
        202,
        result,
        (
            SubsystemEvent(
                "basic-market.instrument.opened",
                {
                    "snapshotId": result["snapshotId"],
                    "instrumentId": result["instrument"]["instrumentId"],
                    "datasetVersionId": result["materialization"]["dataset"][
                        "datasetVersionId"
                    ],
                    "pipelineId": result["materialization"]["pipeline"][
                        "pipelineId"
                    ],
                    "jobId": result["job"]["jobId"],
                    "backtestId": result["job"]["backtestId"],
                },
            ),
        ),
    )


def _project_result(context, payload):
    result = market_service.project_result_cached(
        context.config,
        payload,
        session_identity=context.owner_identity or context.session_identity,
    )
    return SubsystemResponse(200, result)


def subsystem_definition():
    return SubsystemDefinition(
        subsystem_id="basic",
        protocol_id=PROTOCOL_ID,
        label="Basic",
        pages=(
            SubsystemPage(
                path="/basic-workflow",
                file="basic_workflow.html",
                catalog_entry=True,
            ),
            SubsystemPage(
                path="/basic-workflow/workspace",
                file="basic_workflow_workspace.html",
            ),
        ),
        api_routes=(
            SubsystemApiRoute(
                "GET",
                "/api/subsystems/basic/market",
                "Basic market snapshot request",
                _market_state,
            ),
            SubsystemApiRoute(
                "POST",
                "/api/subsystems/basic/market/sync",
                "Basic market sync request",
                _sync_market,
            ),
            SubsystemApiRoute(
                "POST",
                "/api/subsystems/basic/watchlist",
                "Basic market watchlist request",
                _set_watchlist,
            ),
            SubsystemApiRoute(
                "POST",
                "/api/subsystems/basic/instruments/open",
                "Basic market open instrument request",
                _open_instrument,
            ),
            SubsystemApiRoute(
                "POST",
                "/api/subsystems/basic/result-projections",
                "Basic cached Result projection request",
                _project_result,
            ),
        ),
    )


__all__ = ("subsystem_definition",)
