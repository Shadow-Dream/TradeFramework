"""Product-owned BuiltIn Environment Graph presets."""

from __future__ import annotations

import copy

from application_protocols.basic_workflow.manifest import PROTOCOL_ID
from engine.repository.module_versions import latest_archived_module_versions


NEUTRAL_ENVIRONMENT_ID = "neutral-backtest-environment"
BASIC_WORKFLOW_ENVIRONMENT_ID = "basic-multi-asset-paper-environment"


def _instance(instance_id, module_id, version, *, config=None, inputs=None, outputs=None):
    return {
        "instanceId": instance_id,
        "kind": "Environment",
        "moduleId": module_id,
        "version": str(version),
        "config": copy.deepcopy(config or {}),
        "inputs": copy.deepcopy(inputs or {}),
        "outputs": copy.deepcopy(outputs or {}),
    }


def _basic_workflow_graph(module_definitions, module_definition_evidence):
    module_id = "basic-multi-asset-bar-account"
    versions = latest_archived_module_versions(
        module_definitions,
        module_definition_evidence,
        "Environment",
        {module_id},
    )
    instances = {
        "account": _instance(
            "account",
            module_id,
            versions[module_id],
            config={
                "executionPeriod": "hour",
                "initialCash": 100000.0,
                "fixedFee": 0.0,
                "feeBps": 0.0,
            },
            inputs={
                "time": "wire.time",
                "price": "wire.price",
                "previousApprovedIntent": "wire.previous_approved_intent",
            },
            outputs={
                "account": "wire.account",
                "orders": "wire.orders",
            },
        )
    }
    return {
        "instances": instances,
        "graph": {
            "nodes": ["account"],
            "inputs": {
                "time-input": {"dataKey": "time", "wire": "wire.time"},
                "price-input": {"dataKey": "price", "wire": "wire.price"},
                "previous-approved-intent-input": {
                    "dataKey": "last.intent.approved",
                    "wire": "wire.previous_approved_intent",
                },
            },
            "outputs": {
                "time-output": {"dataKey": "time", "wire": "wire.time"},
                "price-output": {"dataKey": "price", "wire": "wire.price"},
                "account-output": {
                    "dataKey": "portfolio.account",
                    "wire": "wire.account",
                },
                "orders-output": {
                    "dataKey": "execution.orders",
                    "wire": "wire.orders",
                },
            },
        },
    }


def builtin_environment_definitions(
    module_definitions, module_definition_evidence
):
    basic_workflow = _basic_workflow_graph(
        module_definitions, module_definition_evidence
    )
    return {
        BASIC_WORKFLOW_ENVIRONMENT_ID: {
            "schemaVersion": 2,
            "environmentId": BASIC_WORKFLOW_ENVIRONMENT_ID,
            "name": "Basic",
            "protocolId": PROTOCOL_ID,
            "description": (
                "Basic Workflow v2 prior-approved-intent execution at bar open "
                "with stateful multi-asset close valuation."
            ),
            "instances": basic_workflow["instances"],
            "graph": basic_workflow["graph"],
        },
    }


__all__ = (
    # Retained only for test-local generic Engine fixtures; it is not a
    # product BuiltIn Environment.
    "NEUTRAL_ENVIRONMENT_ID",
    "BASIC_WORKFLOW_ENVIRONMENT_ID",
    "builtin_environment_definitions",
)
