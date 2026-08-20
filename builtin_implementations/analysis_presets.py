"""Product-owned BuiltIn Analysis Graph presets."""

from __future__ import annotations

import copy

from engine.contracts.graph_cycle import CURRENT_PIPELINE_SOURCE
from engine.repository.module_versions import latest_archived_module_versions


NEUTRAL_ANALYSIS_ID = "neutral-analysis"
PERFORMANCE_ANALYSIS_ID = "standard-performance-analysis"
BASIC_WORKFLOW_ANALYSIS_ID = "basic-workflow-performance-analysis"


def _instance(instance_id, module_id, version, *, config=None, inputs=None, outputs=None):
    return {
        "instanceId": instance_id,
        "kind": "Analyzer",
        "moduleId": module_id,
        "version": str(version),
        "config": copy.deepcopy(config or {}),
        "inputs": copy.deepcopy(inputs or {}),
        "outputs": copy.deepcopy(outputs or {}),
    }


def _standard_performance_graph(module_definitions, module_definition_evidence):
    performance_version = latest_archived_module_versions(
        module_definitions,
        module_definition_evidence,
        "Analyzer",
        {"performance-metrics-analyzer"},
    )["performance-metrics-analyzer"]
    instances = {
        "performance": _instance(
            "performance",
            "performance-metrics-analyzer",
            performance_version,
            config={"riskFreeRate": 0.0},
            inputs={"equity": "wire.equity", "time": "wire.time"},
            outputs={"performance": "wire.performance"},
        ),
    }
    return {
        "instances": instances,
        "graph": {
            "nodes": ["performance"],
            "inputs": {
                "equity-input": {
                    "dataKey": "broker.account.equity",
                    "wire": "wire.equity",
                    "source": CURRENT_PIPELINE_SOURCE,
                },
                "time-input": {"dataKey": "decisionTime", "wire": "wire.time"},
            },
            "outputs": {
                "performance-output": {
                    "dataKey": "analysis.performance",
                    "wire": "wire.performance",
                },
            },
        },
    }


def _basic_workflow_performance_graph(
    module_definitions, module_definition_evidence
):
    graph = _standard_performance_graph(
        module_definitions, module_definition_evidence
    )
    graph["graph"]["inputs"]["equity-input"]["dataKey"] = (
        "portfolio.account.equity"
    )
    return graph


def builtin_analysis_definitions(
    module_definitions, module_definition_evidence
):
    performance = _standard_performance_graph(
        module_definitions, module_definition_evidence
    )
    basic_workflow_performance = _basic_workflow_performance_graph(
        module_definitions, module_definition_evidence
    )
    return {
        NEUTRAL_ANALYSIS_ID: {
            "schemaVersion": 1,
            "analysisId": NEUTRAL_ANALYSIS_ID,
            "name": "Neutral Analysis",
            "description": "Runs no Analyzer Modules and emits no Result fields.",
            "instances": {},
            "graph": {"nodes": [], "inputs": {}, "outputs": {}},
        },
        PERFORMANCE_ANALYSIS_ID: {
            "schemaVersion": 1,
            "analysisId": PERFORMANCE_ANALYSIS_ID,
            "name": "Standard Performance Analysis",
            "description": (
                "Annualized return, volatility, Sharpe ratio and drawdown from "
                "completed Pipeline account equity."
            ),
            "instances": performance["instances"],
            "graph": performance["graph"],
        },
        BASIC_WORKFLOW_ANALYSIS_ID: {
            "schemaVersion": 1,
            "analysisId": BASIC_WORKFLOW_ANALYSIS_ID,
            "name": "Basic Workflow Performance Analysis",
            "description": (
                "Basic Workflow v1 performance metrics from portfolio account "
                "equity and the causal cycle decision time."
            ),
            "instances": basic_workflow_performance["instances"],
            "graph": basic_workflow_performance["graph"],
        },
    }


__all__ = (
    "NEUTRAL_ANALYSIS_ID",
    "PERFORMANCE_ANALYSIS_ID",
    "BASIC_WORKFLOW_ANALYSIS_ID",
    "builtin_analysis_definitions",
)
