"""Product-owned Pipeline BuiltIn Module declarations."""

from __future__ import annotations

from builtin_implementations.basic_workflow_contracts import (
    APPROVED_INTENT_SCHEMA,
    PRICE_SCHEMA,
    REQUESTED_INTENT_SCHEMA,
    SIGNAL_SCORE_SCHEMA,
    UNIVERSE_SELECTION_SCHEMA,
)
from engine.contracts.data_model import normalize_data_key_schema


def _module_source(kind, module_id, description, config_schema=None, ports=None):
    return {
        "kind": kind,
        "moduleId": module_id,
        "name": module_id,
        "configSchema": config_schema or {},
        "ports": ports or {},
        "description": description,
    }


def _object_schema(properties):
    return {
        "type": "object",
        "properties": properties,
        "additionalProperties": False,
    }


def _data_port(schema, *, required=True):
    return {"schema": normalize_data_key_schema(schema), "required": required}


def _factor_module(module_id, description, inputs, outputs, config_schema=None):
    return _module_source(
        "Signal",
        module_id,
        description,
        config_schema=_object_schema(config_schema or {}),
        ports={"inputs": inputs, "outputs": outputs},
    )


BUILTIN_PIPELINE_MODULES = (
    _module_source(
        "Universe",
        "basic-price-map-universe",
        "Selects every instrument visible in one explicitly bound price period.",
        config_schema={
            "type": "object",
            "properties": {"decisionPeriod": {"type": "string"}},
            "required": ["decisionPeriod"],
            "additionalProperties": False,
        },
        ports={
            "inputs": {"price": _data_port(PRICE_SCHEMA)},
            "outputs": {"selection": _data_port(UNIVERSE_SELECTION_SCHEMA)},
        },
    ),
    _module_source(
        "Signal",
        "basic-neutral-score-map",
        "Basic Workflow v2 no-intent Signal Graph placeholder.",
        config_schema=_object_schema({}),
        ports={
            "inputs": {"selection": _data_port(UNIVERSE_SELECTION_SCHEMA)},
            "outputs": {"scores": _data_port(SIGNAL_SCORE_SCHEMA)},
        },
    ),
    _module_source(
        "Target",
        "basic-score-map-position-target",
        "Maps per-instrument scores in [-1, 1] to absolute position intents.",
        config_schema=_object_schema(
            {
                "maximumAbsolutePosition": {
                    "type": "number",
                    "default": 1.0,
                    "minimum": 0,
                }
            }
        ),
        ports={
            "inputs": {
                "selection": _data_port(UNIVERSE_SELECTION_SCHEMA),
                "scores": _data_port(SIGNAL_SCORE_SCHEMA),
            },
            "outputs": {"intent": _data_port(REQUESTED_INTENT_SCHEMA)},
        },
    ),
    _module_source(
        "Constraint",
        "basic-absolute-position-map-constraint",
        "Applies an absolute position bound to each requested instrument target.",
        config_schema=_object_schema(
            {
                "maximumAbsolutePosition": {
                    "type": "number",
                    "default": 1.0,
                    "minimum": 0,
                }
            }
        ),
        ports={
            "inputs": {"intent": _data_port(REQUESTED_INTENT_SCHEMA)},
            "outputs": {"approved": _data_port(APPROVED_INTENT_SCHEMA)},
        },
    ),
    _factor_module(
        "sma-indicator",
        "Built-in SMA factor node. Config controls period; inputs/outputs bind graph wire ids.",
        {"value": _data_port({"type": "number"})},
        {"sma": _data_port({"type": ["number", "null"]})},
        {"period": {"type": "integer", "default": 20, "minimum": 1}},
    ),
    _factor_module(
        "ema-indicator",
        "Built-in EMA factor node. Config controls period; inputs/outputs bind graph wire ids.",
        {"value": _data_port({"type": "number"})},
        {"ema": _data_port({"type": ["number", "null"]})},
        {"period": {"type": "integer", "default": 20, "minimum": 1}},
    ),
    _factor_module(
        "wma-indicator",
        "Built-in weighted moving average factor node.",
        {"value": _data_port({"type": "number"})},
        {"wma": _data_port({"type": ["number", "null"]})},
        {"period": {"type": "integer", "default": 20, "minimum": 1}},
    ),
    _factor_module(
        "vwma-indicator",
        "Built-in volume-weighted moving average factor node.",
        {
            "price": _data_port({"type": "number"}),
            "volume": _data_port({"type": "number"}),
        },
        {"vwma": _data_port({"type": ["number", "null"]})},
        {"period": {"type": "integer", "default": 20, "minimum": 1}},
    ),
    _factor_module(
        "rsi-indicator",
        "Built-in RSI factor node.",
        {"price": _data_port({"type": "number"})},
        {"rsi": _data_port({"type": ["number", "null"]})},
        {"period": {"type": "integer", "default": 14, "minimum": 1}},
    ),
    _factor_module(
        "macd-indicator",
        "Built-in MACD factor node with macd, signal, and histogram outputs.",
        {"price": _data_port({"type": "number"})},
        {
            "macd": _data_port({"type": ["number", "null"]}),
            "signal": _data_port({"type": ["number", "null"]}, required=False),
            "histogram": _data_port(
                {"type": ["number", "null"]}, required=False
            ),
        },
        {
            "fastPeriod": {"type": "integer", "default": 12, "minimum": 1},
            "slowPeriod": {"type": "integer", "default": 26, "minimum": 1},
            "signalPeriod": {"type": "integer", "default": 9, "minimum": 1},
        },
    ),
    _factor_module(
        "bollinger-bands-indicator",
        "Built-in Bollinger Bands factor node.",
        {"price": _data_port({"type": "number"})},
        {
            "middle": _data_port({"type": ["number", "null"]}),
            "upper": _data_port({"type": ["number", "null"]}, required=False),
            "lower": _data_port({"type": ["number", "null"]}, required=False),
            "bandwidth": _data_port(
                {"type": ["number", "null"]}, required=False
            ),
            "percentB": _data_port(
                {"type": ["number", "null"]}, required=False
            ),
        },
        {
            "period": {"type": "integer", "default": 20, "minimum": 1},
            "k": {"type": "number", "default": 2},
        },
    ),
    _factor_module(
        "atr-indicator",
        "Built-in ATR factor node.",
        {
            "high": _data_port({"type": "number"}),
            "low": _data_port({"type": "number"}),
            "close": _data_port({"type": "number"}),
        },
        {"atr": _data_port({"type": ["number", "null"]})},
        {"period": {"type": "integer", "default": 14, "minimum": 1}},
    ),
    _factor_module(
        "stochastic-indicator",
        "Built-in stochastic oscillator factor node.",
        {
            "high": _data_port({"type": "number"}),
            "low": _data_port({"type": "number"}),
            "close": _data_port({"type": "number"}),
        },
        {
            "k": _data_port({"type": ["number", "null"]}),
            "d": _data_port({"type": ["number", "null"]}, required=False),
        },
        {
            "period": {"type": "integer", "default": 14, "minimum": 1},
            "dPeriod": {"type": "integer", "default": 3, "minimum": 1},
        },
    ),
    _factor_module(
        "obv-indicator",
        "Built-in OBV factor node.",
        {
            "close": _data_port({"type": "number"}),
            "volume": _data_port({"type": "number"}),
        },
        {"obv": _data_port({"type": ["number", "null"]})},
        {},
    ),
    _factor_module(
        "roc-indicator",
        "Built-in rate-of-change factor node.",
        {"price": _data_port({"type": "number"})},
        {"roc": _data_port({"type": ["number", "null"]})},
        {"period": {"type": "integer", "default": 12, "minimum": 1}},
    ),
    _factor_module(
        "cross-over-gate",
        "Built-in logic node that emits rise/fall/flat when two numeric inputs cross.",
        {
            "fast": _data_port({"type": ["number", "null"]}),
            "slow": _data_port({"type": ["number", "null"]}),
        },
        {
            "direction": _data_port(
                {
                    "type": ["string", "null"],
                    "enum": ["rise", "fall", "flat", None],
                }
            )
        },
        {},
    ),
    _factor_module(
        "direction-to-action",
        "Converts an explicitly bound direction signal into the Pipeline action object.",
        {
            "direction": _data_port(
                {
                    "type": ["string", "null"],
                    "enum": ["rise", "fall", "flat", None],
                }
            )
        },
        {
            "action": _data_port(
                {
                    "type": "object",
                    "properties": {
                        "type": {
                            "type": "string",
                            "enum": ["enter", "exit", "hold"],
                        },
                        "direction": {
                            "type": "string",
                            "enum": ["rise", "fall", "flat"],
                        },
                        "reason": {"type": "string"},
                    },
                    "required": ["type", "direction", "reason"],
                    "additionalProperties": False,
                }
            )
        },
        {},
    ),
)


__all__ = ("BUILTIN_PIPELINE_MODULES",)
