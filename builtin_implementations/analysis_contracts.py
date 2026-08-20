#!/usr/bin/env python3
"""Product-owned Analysis BuiltIn Module declarations."""

from __future__ import annotations

import copy

from engine.contracts.data_model import normalize_data_key_schema


def _port(schema, *, required=True):
    if not isinstance(required, bool):
        raise ValueError("Analysis Module port required must be a boolean.")
    return {"schema": normalize_data_key_schema(schema), "required": required}


def _definition(module_id, name, description, *, inputs=None, outputs=None, config=None):
    return {
        "kind": "Analyzer",
        "moduleId": module_id,
        "name": name,
        "description": description,
        "configSchema": {
            "type": "object",
            "properties": copy.deepcopy(config or {}),
            "additionalProperties": False,
        },
        "ports": {
            "inputs": copy.deepcopy(inputs or {}),
            "outputs": copy.deepcopy(outputs or {}),
        },
    }


ANALYSIS_MODULES = [
    _definition(
        "cycle-count-analyzer",
        "Cycle Count",
        "Counts observations processed by this Analyzer instance.",
        outputs={"count": _port({"type": "integer"})},
    ),
    _definition(
        "numeric-change-analyzer",
        "Numeric Change",
        "Computes current minus previous and the corresponding fractional return.",
        inputs={
            "current": _port({"type": "number"}),
            "previous": _port({"type": "number"}, required=False),
        },
        outputs={
            "change": _port({"type": ["number", "null"]}),
            "return": _port({"type": ["number", "null"]}),
        },
    ),
    _definition(
        "performance-metrics-analyzer",
        "Performance Metrics",
        "Tracks total and annualized return, annualized volatility, Sharpe ratio and maximum drawdown.",
        inputs={
            "equity": _port({"type": "number"}, required=False),
            "time": _port({"type": "string"}),
        },
        outputs={"performance": _port({
            "type": "object",
            "properties": {
                "observationCount": {"type": "integer"},
                "returnCount": {"type": "integer"},
                "startEquity": {"type": ["number", "null"]},
                "endEquity": {"type": ["number", "null"]},
                "totalReturn": {"type": ["number", "null"]},
                "annualizedReturn": {"type": ["number", "null"]},
                "annualizedVolatility": {"type": ["number", "null"]},
                "sharpeRatio": {"type": ["number", "null"]},
                "maxDrawdown": {"type": ["number", "null"]},
                "firstTime": {"type": ["string", "null"]},
                "lastTime": {"type": ["string", "null"]},
                "observationsPerYear": {"type": ["number", "null"]},
            },
            "required": [
                "observationCount", "returnCount", "startEquity", "endEquity",
                "totalReturn", "annualizedReturn", "annualizedVolatility",
                "sharpeRatio", "maxDrawdown", "firstTime", "lastTime",
                "observationsPerYear",
            ],
            "additionalProperties": False,
        })},
        config={
            "riskFreeRate": {"type": "number", "exclusiveMinimum": -1.0, "default": 0.0},
        },
    ),
]


def analysis_module_definitions():
    return {
        item["moduleId"]: copy.deepcopy(item)
        for item in ANALYSIS_MODULES
    }
