"""Service composition for validating and saving Result Visualizations."""

from __future__ import annotations

from engine.compiler import visualization as visualization_compiler
from engine.contracts import visualization as visualization_contracts
from engine.core import clock as engine_clock
from engine.core import resource_ids
from engine.repository import backtest_results as result_repository
from engine.repository import module_definitions as module_repository
from engine.repository import visualizations as visualization_repository


def validate_visualization_contracts(
    config,
    result,
    spec,
    visualizer_definitions,
):
    """Compile a spec using the verified Pipeline Module repository."""

    if not isinstance(result, dict) or not isinstance(result.get("dataKeys"), dict):
        raise ValueError("Visualization Result dataKeys are required.")
    definitions = module_repository.load_pipeline_definitions(config)
    return visualization_compiler.compile_visualization_contracts(
        result["dataKeys"],
        spec,
        definitions,
        visualizer_definitions,
    )


def save_visualization(config, request, visualizer_definitions):
    """Validate all read contracts, then atomically persist one Visualization."""

    visualization_contracts.require_save_request(request)
    backtest = result_repository.get_backtest_meta(
        config,
        request["backtestId"],
    )
    spec = request["spec"]
    if spec["datasetId"] != backtest["datasetId"]:
        raise ValueError("Visualization datasetId does not match its Backtest.")
    validate_visualization_contracts(
        config,
        backtest,
        spec,
        visualizer_definitions,
    )

    record = {
        "visualizationId": resource_ids.normalize_resource_id(
            request["visualizationId"].strip()
        ),
        "backtestId": request["backtestId"],
        "name": request["name"].strip(),
        "createdAt": engine_clock.utc_now(),
        "spec": spec,
    }
    saved = visualization_repository.save_visualization(config, record)
    return {"accepted": True, "visualization": saved}


def get_visualization(config, visualization_id):
    return visualization_repository.get_visualization(config, visualization_id)


def list_visualizations(config, backtest_id=""):
    return visualization_repository.list_visualizations(config, backtest_id)


__all__ = (
    "get_visualization",
    "list_visualizations",
    "save_visualization",
    "validate_visualization_contracts",
)
