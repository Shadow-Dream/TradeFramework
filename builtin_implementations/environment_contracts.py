#!/usr/bin/env python3
"""Product-owned Environment BuiltIn Module declarations."""

from __future__ import annotations

import copy
from builtin_implementations.basic_workflow_contracts import (
    APPROVED_INTENT_SCHEMA,
    EXECUTION_ORDERS_SCHEMA,
    PRICE_SCHEMA,
    PORTFOLIO_ACCOUNT_SCHEMA,
)
from engine.contracts.data_model import normalize_data_key_schema


NUMBER = {"type": "number"}
INTEGER = {"type": "integer"}
BOOLEAN = {"type": "boolean"}
STRING = {"type": "string"}
NULLABLE_NUMBER = {"type": ["number", "null"]}
NULLABLE_STRING = {"type": ["string", "null"]}
NULLABLE_OBJECT = {"type": ["object", "null"]}

ACCOUNT_SCHEMA = {
    "type": "object",
    "properties": {
        "cash": NUMBER,
        "position": NUMBER,
        "markedValue": NUMBER,
        "equity": NUMBER,
        "marginInterest": NUMBER,
    },
    "required": ["cash", "position", "markedValue", "equity", "marginInterest"],
    "additionalProperties": False,
}

ORDER_SCHEMA = {
    "type": "object",
    "properties": {
        "status": STRING,
        "requestedTarget": NUMBER,
        "approvedTarget": NUMBER,
        "requestedQuantity": NUMBER,
        "filledQuantity": NUMBER,
        "sampleValue": NUMBER,
        "fillValue": NULLABLE_NUMBER,
        "notional": NUMBER,
        "fee": NUMBER,
    },
    "required": [
        "status", "requestedTarget", "approvedTarget", "requestedQuantity",
        "filledQuantity", "sampleValue", "fillValue", "notional", "fee",
    ],
    "additionalProperties": False,
}

SETTLEMENT_SCHEMA = {
    "type": ["object", "null"],
    "properties": {"cashDelta": NUMBER, "positionDelta": NUMBER},
    "required": ["cashDelta", "positionDelta"],
    "additionalProperties": False,
}

BENCHMARK_SCHEMA = {
    "type": "object",
    "properties": {"value": NUMBER, "return": NUMBER},
    "required": ["value", "return"],
    "additionalProperties": False,
}


def _port(schema, *, required=True):
    if not isinstance(required, bool):
        raise ValueError("Environment Module port required must be a boolean.")
    return {"schema": normalize_data_key_schema(schema), "required": required}


def _config(properties=None):
    return {
        "type": "object",
        "properties": copy.deepcopy(properties or {}),
        "additionalProperties": False,
    }


def _definition(
    module_id,
    name,
    _category,
    description,
    *,
    inputs=None,
    outputs=None,
    config=None,
):
    result = {
        "kind": "Environment",
        "moduleId": module_id,
        "name": name,
        "description": description,
        "configSchema": _config(config),
        "ports": {
            "inputs": copy.deepcopy(inputs or {}),
            "outputs": copy.deepcopy(outputs or {}),
        },
    }
    return result


ENVIRONMENT_MODULES = [
    _definition(
        "basic-multi-asset-bar-account",
        "Basic Multi-Asset Bar Account",
        "Account",
        "Executes prior approved intents at bar open and marks a stateful account at close.",
        inputs={
            "time": _port(STRING),
            "price": _port(PRICE_SCHEMA),
            "previousApprovedIntent": _port(
                APPROVED_INTENT_SCHEMA,
                required=False,
            ),
        },
        outputs={
            "account": _port(PORTFOLIO_ACCOUNT_SCHEMA),
            "orders": _port(EXECUTION_ORDERS_SCHEMA),
        },
        config={
            "executionPeriod": {
                "type": "string",
            },
            "initialCash": {"type": "number", "default": 100000.0},
            "fixedFee": {"type": "number", "default": 0.0, "minimum": 0},
            "feeBps": {"type": "number", "default": 0.0, "minimum": 0},
        },
    ),
    _definition(
        "numeric-target-submit-rule", "Numeric Target Input", "Order",
        "Converts an optional previous-cycle numeric intent into a target.",
        inputs={"intent": _port(NULLABLE_NUMBER, required=False)},
        outputs={"target": _port(NULLABLE_NUMBER)},
    ),
    _definition(
        "lot-size-order-update-rule", "Lot Size Rounding", "Order",
        "Rounds a requested target to a configurable lot size.",
        inputs={"target": _port(NULLABLE_NUMBER)},
        outputs={"target": _port(NULLABLE_NUMBER)},
        config={"lotSize": {"type": "number", "default": 1.0, "exclusiveMinimum": 0}},
    ),
    _definition(
        "configurable-shortable-provider", "Shortable Check", "Risk",
        "Allows or rejects negative target positions.",
        inputs={"target": _port(NULLABLE_NUMBER)},
        outputs={"target": _port(NULLABLE_NUMBER)},
        config={"allowShort": {"type": "boolean", "default": True}},
    ),
    _definition(
        "maximum-leverage-rule", "Maximum Leverage", "Risk",
        "Caps an absolute position target using marked equity and execution value.",
        inputs={
            "target": _port(NULLABLE_NUMBER),
            "accountEquity": _port(NUMBER),
            "executionValue": _port(NUMBER),
        },
        outputs={"target": _port(NULLABLE_NUMBER)},
        config={"maximumLeverage": {"type": "number", "default": 1.0, "minimum": 0}},
    ),
    _definition(
        "cash-equity-buying-power-model", "Cash Buying Power", "Risk",
        "Caps a target using cash-equity buying power.",
        inputs={
            "target": _port(NULLABLE_NUMBER),
            "accountEquity": _port(NUMBER),
            "executionValue": _port(NUMBER),
        },
        outputs={"target": _port(NULLABLE_NUMBER)},
    ),
    _definition(
        "target-delta-execution-rule", "Target Delta Execution", "Execution",
        "Converts a target position into an approved position delta.",
        inputs={
            "target": _port(NULLABLE_NUMBER, required=False),
            "accountPosition": _port(NUMBER, required=False),
        },
        outputs={
            "requestedQuantity": _port(NUMBER),
            "approvedQuantity": _port(NUMBER),
            "approvedTarget": _port(NUMBER),
            "status": _port(STRING),
        },
        config={
            "maximumOrderQuantity": {"type": "number", "default": 1000000.0, "minimum": 0},
            "initialPosition": {"type": "number", "default": 0.0},
        },
    ),
    _definition(
        "constant-bps-slippage-model", "Constant BPS Slippage", "Execution",
        "Adjusts the sampled execution value by configurable basis points.",
        inputs={"approvedQuantity": _port(NUMBER), "executionValue": _port(NUMBER)},
        outputs={"fillValue": _port(NULLABLE_NUMBER)},
        config={"slippageBps": {"type": "number", "default": 0.0}},
    ),
    _definition(
        "proportional-fill-model", "Proportional Fill", "Execution",
        "Fills an approved quantity by a configurable ratio.",
        inputs={"approvedQuantity": _port(NUMBER), "fillValue": _port(NULLABLE_NUMBER)},
        outputs={
            "filledQuantity": _port(NUMBER),
            "notional": _port(NUMBER),
            "status": _port(STRING),
        },
        config={"fillRatio": {"type": "number", "default": 1.0, "minimum": 0, "maximum": 1}},
    ),
    _definition(
        "fixed-plus-bps-fee-model", "Fixed Plus BPS Fee", "Fee",
        "Charges a fixed amount plus basis points of absolute notional.",
        inputs={"filledQuantity": _port(NUMBER), "notional": _port(NUMBER)},
        outputs={"fee": _port(NUMBER)},
        config={
            "fixedFee": {"type": "number", "default": 0.0, "minimum": 0},
            "feeBps": {"type": "number", "default": 0.0, "minimum": 0},
        },
    ),
    _definition(
        "immediate-settlement-model", "Immediate Settlement", "Settlement",
        "Produces a cash and position settlement instruction from a fill.",
        inputs={
            "notional": _port(NUMBER),
            "filledQuantity": _port(NUMBER),
            "fee": _port(NUMBER, required=False),
        },
        outputs={"settlement": _port(SETTLEMENT_SCHEMA)},
    ),
    _definition(
        "negative-cash-interest-model", "Negative Cash Interest", "Settlement",
        "Charges per-cycle interest when cash is negative.",
        inputs={"accountCash": _port(NUMBER)},
        outputs={"marginInterest": _port(NUMBER)},
        config={"marginInterestPerCycle": {"type": "number", "default": 0.0, "minimum": 0}},
    ),
    _definition(
        "datakey-benchmark-provider", "Benchmark Return", "Benchmark",
        "Publishes benchmark value and cumulative return from an explicitly connected input.",
        inputs={"value": _port(NUMBER)},
        outputs={"benchmark": _port(BENCHMARK_SCHEMA)},
    ),
    _definition(
        "paper-account-ledger", "Paper Account Ledger", "Account",
        "Applies settlement and margin interest to the previous-cycle account.",
        inputs={
            "previousAccount": _port(ACCOUNT_SCHEMA, required=False),
            "executionValue": _port(NUMBER),
            "settlement": _port(SETTLEMENT_SCHEMA, required=False),
            "marginInterest": _port(NUMBER, required=False),
        },
        outputs={"account": _port(ACCOUNT_SCHEMA)},
        config={
            "initialCash": {"type": "number", "default": 100000.0},
            "initialPosition": {"type": "number", "default": 0.0},
        },
    ),
    _definition(
        "paper-order-summary", "Paper Order Summary", "Order",
        "Builds a typed order record from ordinary Environment Graph wires.",
        inputs={
            "requestedTarget": _port(NULLABLE_NUMBER, required=False),
            "approvedTarget": _port(NUMBER),
            "requestedQuantity": _port(NUMBER),
            "filledQuantity": _port(NUMBER),
            "executionValue": _port(NUMBER),
            "fillValue": _port(NULLABLE_NUMBER),
            "notional": _port(NUMBER),
            "fee": _port(NUMBER, required=False),
            "status": _port(STRING),
        },
        outputs={"order": _port(ORDER_SCHEMA)},
    ),
]


def environment_module_definitions():
    return {
        item["moduleId"]: copy.deepcopy(item)
        for item in ENVIRONMENT_MODULES
    }
