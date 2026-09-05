"""Compile and persist Visualizations over first-class Sample Results."""

from __future__ import annotations

from builtin_implementations.visualizer_contracts import visualizer_definition_map
from engine.compiler import visualization as visualization_compiler
from engine.contracts import sample_visualization as contracts
from engine.core import clock as engine_clock
from engine.core import resource_ids
from engine.repository import module_definitions
from engine.repository import sample_results
from engine.repository import sample_visualizations as repository


SampleVisualizationRevisionConflict = repository.SampleVisualizationRevisionConflict


def current_visualization_id(sample_result_id):
    suffix = sample_result_id.removeprefix("sha256:")
    return resource_ids.normalize_resource_id(f"sample-{suffix}-current")


def save_sample_visualization(config, request):
    contracts.require_save_request(request)
    view = sample_results.sample_result_view(config, request["sampleResultId"])
    if request["spec"]["datasetId"] != view["datasetId"]:
        raise ValueError("Sample Visualization datasetId does not match its Sample Result.")
    temporary_modules = list(request["spec"].get("temporaryModules") or [])
    for pane in request["spec"]["panes"]:
        temporary_modules.extend(pane.get("temporaryModules") or [])
    definitions, _evidence = module_definitions.load_definition_versions(
        config,
        [
            (module.get("kind"), module.get("moduleId"), module.get("version"))
            for module in temporary_modules
        ],
    )
    visualization_compiler.compile_visualization_contracts(
        view["dataKeys"],
        request["spec"],
        definitions,
        visualizer_definition_map(),
    )
    record = {
        "visualizationId": resource_ids.normalize_resource_id(
            request["visualizationId"].strip()
        ),
        "sampleResultId": request["sampleResultId"],
        "name": request["name"].strip(),
        "createdAt": engine_clock.utc_now(),
        "revision": request["expectedRevision"] + 1,
        "spec": request["spec"],
    }
    return {
        "accepted": True,
        "visualization": repository.save_sample_visualization(
            config,
            record,
            expected_revision=request["expectedRevision"],
        ),
    }


def list_sample_visualizations(config, sample_result_id):
    sample_results.sample_result_view(config, sample_result_id)
    return repository.list_sample_visualizations(config, sample_result_id)


__all__ = (
    "SampleVisualizationRevisionConflict",
    "current_visualization_id",
    "list_sample_visualizations",
    "save_sample_visualization",
)
