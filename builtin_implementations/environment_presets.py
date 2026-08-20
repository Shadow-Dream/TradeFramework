"""Product-owned BuiltIn Environment Graph presets."""

from __future__ import annotations

import copy

from engine.repository.module_versions import latest_archived_module_versions


NEUTRAL_ENVIRONMENT_ID = "neutral-backtest-environment"
PAPER_ENVIRONMENT_ID = "standard-paper-environment"
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


def _standard_paper_graph(module_definitions, module_definition_evidence):
    versions = latest_archived_module_versions(
        module_definitions, module_definition_evidence, "Environment", {
        "numeric-target-submit-rule", "target-delta-execution-rule",
        "constant-bps-slippage-model", "proportional-fill-model",
        "fixed-plus-bps-fee-model", "immediate-settlement-model",
        "paper-account-ledger", "paper-order-summary",
    })
    instances = {
        "target": _instance(
            "target", "numeric-target-submit-rule", versions["numeric-target-submit-rule"],
            inputs={"intent": "wire.intent"},
            outputs={"target": "wire.target"},
        ),
        "execution": _instance(
            "execution", "target-delta-execution-rule",
            versions["target-delta-execution-rule"],
            config={"maximumOrderQuantity": 1000000.0, "initialPosition": 0.0},
            inputs={"target": "wire.target", "accountPosition": "wire.account_position"},
            outputs={
                "requestedQuantity": "wire.requested_quantity",
                "approvedQuantity": "wire.approved_quantity",
                "approvedTarget": "wire.approved_target",
                "status": "wire.execution_status",
            },
        ),
        "slippage": _instance(
            "slippage", "constant-bps-slippage-model",
            versions["constant-bps-slippage-model"],
            config={"slippageBps": 0.0},
            inputs={
                "approvedQuantity": "wire.approved_quantity",
                "executionValue": "wire.execution_value",
            },
            outputs={"fillValue": "wire.fill_value"},
        ),
        "fill": _instance(
            "fill", "proportional-fill-model", versions["proportional-fill-model"],
            config={"fillRatio": 1.0},
            inputs={
                "approvedQuantity": "wire.approved_quantity",
                "fillValue": "wire.fill_value",
            },
            outputs={
                "filledQuantity": "wire.filled_quantity",
                "notional": "wire.notional",
                "status": "wire.fill_status",
            },
        ),
        "fee": _instance(
            "fee", "fixed-plus-bps-fee-model", versions["fixed-plus-bps-fee-model"],
            config={"fixedFee": 0.0, "feeBps": 0.0},
            inputs={
                "filledQuantity": "wire.filled_quantity",
                "notional": "wire.notional",
            },
            outputs={"fee": "wire.fee"},
        ),
        "settlement": _instance(
            "settlement", "immediate-settlement-model",
            versions["immediate-settlement-model"],
            inputs={
                "notional": "wire.notional",
                "filledQuantity": "wire.filled_quantity",
                "fee": "wire.fee",
            },
            outputs={"settlement": "wire.settlement"},
        ),
        "account": _instance(
            "account", "paper-account-ledger", versions["paper-account-ledger"],
            config={"initialCash": 100000.0, "initialPosition": 0.0},
            inputs={
                "previousAccount": "wire.previous_account",
                "executionValue": "wire.execution_value",
                "settlement": "wire.settlement",
            },
            outputs={"account": "wire.account"},
        ),
        "order": _instance(
            "order", "paper-order-summary", versions["paper-order-summary"],
            inputs={
                "requestedTarget": "wire.target",
                "approvedTarget": "wire.approved_target",
                "requestedQuantity": "wire.requested_quantity",
                "filledQuantity": "wire.filled_quantity",
                "executionValue": "wire.execution_value",
                "fillValue": "wire.fill_value",
                "notional": "wire.notional",
                "fee": "wire.fee",
                "status": "wire.fill_status",
            },
            outputs={"order": "wire.order"},
        ),
    }
    nodes = [
        "target", "execution", "slippage", "fill", "fee", "settlement", "account", "order",
    ]
    return {
        "instances": instances,
        "graph": {
            "nodes": nodes,
            "inputs": {
                "sample-value": {
                    "dataKey": "market.execution_value",
                    "wire": "wire.execution_value",
                },
                "last-intent": {
                    "dataKey": "last.policy.target_position",
                    "wire": "wire.intent",
                },
                "last-position": {
                    "dataKey": "last.broker.account.position",
                    "wire": "wire.account_position",
                },
                "last-account": {
                    "dataKey": "last.broker.account",
                    "wire": "wire.previous_account",
                },
            },
            "outputs": {
                "execution-value-output": {
                    "dataKey": "market.execution_value",
                    "wire": "wire.execution_value",
                },
                "account-output": {"dataKey": "broker.account", "wire": "wire.account"},
                "order-output": {"dataKey": "broker.order", "wire": "wire.order"},
            },
        },
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
                "executionPeriod": "day",
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
    paper = _standard_paper_graph(
        module_definitions, module_definition_evidence
    )
    basic_workflow = _basic_workflow_graph(
        module_definitions, module_definition_evidence
    )
    return {
        NEUTRAL_ENVIRONMENT_ID: {
            "schemaVersion": 2,
            "environmentId": NEUTRAL_ENVIRONMENT_ID,
            "name": "Neutral Environment",
            "description": (
                "Produces an empty Observation. Add explicit Environment Graph "
                "outputs, then select them with Pipeline observationInput."
            ),
            "instances": {},
            "graph": {"nodes": [], "inputs": {}, "outputs": {}},
        },
        PAPER_ENVIRONMENT_ID: {
            "schemaVersion": 2,
            "environmentId": PAPER_ENVIRONMENT_ID,
            "name": "Standard Paper Environment",
            "description": (
                "Editable typed graph preset for target execution, fill, fee, "
                "settlement and account state."
            ),
            "instances": paper["instances"],
            "graph": paper["graph"],
        },
        BASIC_WORKFLOW_ENVIRONMENT_ID: {
            "schemaVersion": 2,
            "environmentId": BASIC_WORKFLOW_ENVIRONMENT_ID,
            "name": "Basic Multi-Asset Paper Environment",
            "description": (
                "Basic Workflow v2 prior-approved-intent execution at bar open "
                "with stateful multi-asset close valuation."
            ),
            "instances": basic_workflow["instances"],
            "graph": basic_workflow["graph"],
        },
    }


__all__ = (
    "NEUTRAL_ENVIRONMENT_ID",
    "PAPER_ENVIRONMENT_ID",
    "BASIC_WORKFLOW_ENVIRONMENT_ID",
    "builtin_environment_definitions",
)
