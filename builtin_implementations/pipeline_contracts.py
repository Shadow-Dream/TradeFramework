"""Product-owned Pipeline BuiltIn Module declarations."""

from __future__ import annotations

from builtin_implementations.basic_workflow_contracts import (
    APPROVED_INTENT_SCHEMA,
    PRICE_SCHEMA,
    OHLCV_PRICE_SCHEMA,
    REQUESTED_INTENT_SCHEMA,
    SIGNAL_SCORE_SCHEMA,
    UNIVERSE_SELECTION_SCHEMA,
)
from application_protocols.basic_workflow.manifest import PROTOCOL_ID
from engine.contracts.data_model import normalize_data_key_schema


DISPLAY_NAMES = {
    "basic-price-map-universe": "Price Map",
    "basic-ohlcv-price-map-universe": "OHLCV Price Map",
    "basic-price-close-selector": "Price Close",
    "basic-price-bar-selector": "Price OHLCV Bar",
    "basic-neutral-score-map": "Neutral Score",
    "basic-score-map-position-target": "Score Position",
    "basic-absolute-position-map-constraint": "Position Limit",
    "sma-indicator": "SMA",
    "ema-indicator": "EMA",
    "wma-indicator": "WMA",
    "vwma-indicator": "VWMA",
    "rsi-indicator": "RSI",
    "macd-indicator": "MACD",
    "bollinger-bands-indicator": "Bollinger Bands",
    "atr-indicator": "ATR",
    "stochastic-indicator": "Stochastic",
    "obv-indicator": "OBV",
    "roc-indicator": "ROC",
    "cci-indicator": "CCI",
    "williams-r-indicator": "Williams %R",
    "dmi-indicator": "Directional Movement Index",
    "supertrend-indicator": "Supertrend",
    "mfi-indicator": "Money Flow Index",
    "parabolic-sar-indicator": "Parabolic SAR",
    "volume-indicator": "Volume",
    "anchored-vwap-indicator": "Anchored VWAP",
    "ichimoku-indicator": "Ichimoku Cloud",
    "cross-over-gate": "Cross-over",
    "direction-to-action": "Direction Action",
}


def _module_source(
    kind,
    module_id,
    description,
    config_schema=None,
    ports=None,
    *,
    protocol_id=None,
):
    return {
        "kind": kind,
        "moduleId": module_id,
        "name": DISPLAY_NAMES[module_id],
        "configSchema": config_schema or {},
        "ports": ports or {},
        "description": description,
        **({"protocolId": protocol_id} if protocol_id is not None else {}),
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
        protocol_id=PROTOCOL_ID,
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
        "Universe",
        "basic-ohlcv-price-map-universe",
        "Selects every instrument visible in one explicitly bound OHLCV price period.",
        protocol_id=PROTOCOL_ID,
        config_schema={
            "type": "object",
            "properties": {"decisionPeriod": {"type": "string"}},
            "required": ["decisionPeriod"],
            "additionalProperties": False,
        },
        ports={
            "inputs": {"price": _data_port(OHLCV_PRICE_SCHEMA)},
            "outputs": {"selection": _data_port(UNIVERSE_SELECTION_SCHEMA)},
        },
    ),
    _module_source(
        "Signal",
        "basic-price-close-selector",
        "Selects one guaranteed close value from a Basic Workflow price map.",
        protocol_id=PROTOCOL_ID,
        config_schema={
            "type": "object",
            "properties": {
                "decisionPeriod": {"type": "string"},
                "instrumentId": {"type": "string"},
            },
            "required": ["decisionPeriod", "instrumentId"],
            "additionalProperties": False,
        },
        ports={
            "inputs": {"price": _data_port(PRICE_SCHEMA)},
            "outputs": {"close": _data_port({"type": "number"})},
        },
    ),
    _module_source(
        "Signal", "basic-price-bar-selector",
        "Selects exact OHLCV fields and event-time calendar reset signals.",
        protocol_id=PROTOCOL_ID,
        config_schema={"type": "object", "properties": {"decisionPeriod": {"type": "string"}, "instrumentId": {"type": "string"}}, "required": ["decisionPeriod", "instrumentId"], "additionalProperties": False},
        ports={"inputs": {"price": _data_port(OHLCV_PRICE_SCHEMA)}, "outputs": {"eventTime": _data_port({"type": "string"}), "open": _data_port({"type": "number"}), "high": _data_port({"type": "number"}), "low": _data_port({"type": "number"}), "close": _data_port({"type": "number"}), "hlc3": _data_port({"type": "number"}), "volume": _data_port({"type": "number"}), "resetDataset": _data_port({"type": "boolean"}), "resetWeek": _data_port({"type": "boolean"}), "resetMonth": _data_port({"type": "boolean"}), "resetYear": _data_port({"type": "boolean"})}},
    ),
    _module_source(
        "Signal",
        "basic-neutral-score-map",
        "Basic Workflow v2 no-intent Signal Graph placeholder.",
        protocol_id=PROTOCOL_ID,
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
        protocol_id=PROTOCOL_ID,
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
        protocol_id=PROTOCOL_ID,
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
            "smoothKPeriod": {"type": "integer", "default": 3, "minimum": 1},
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
        {"period": {"type": "integer", "default": 9, "minimum": 1}},
    ),
    _factor_module(
        "cci-indicator",
        "Built-in Commodity Channel Index over an explicitly bound source series.",
        {"source": _data_port({"type": "number"})},
        {"cci": _data_port({"type": ["number", "null"]})},
        {"period": {"type": "integer", "default": 20, "minimum": 1}},
    ),
    _factor_module(
        "williams-r-indicator",
        "Built-in Williams Percent Range oscillator.",
        {
            "high": _data_port({"type": "number"}),
            "low": _data_port({"type": "number"}),
            "close": _data_port({"type": "number"}),
        },
        {"williamsR": _data_port({"type": ["number", "null"]})},
        {"period": {"type": "integer", "default": 14, "minimum": 1}},
    ),
    _factor_module(
        "dmi-indicator",
        "Built-in Wilder DMI with +DI, -DI, and ADX outputs.",
        {
            "high": _data_port({"type": "number"}),
            "low": _data_port({"type": "number"}),
            "close": _data_port({"type": "number"}),
        },
        {
            "plusDI": _data_port({"type": ["number", "null"]}, required=False),
            "minusDI": _data_port({"type": ["number", "null"]}, required=False),
            "adx": _data_port({"type": ["number", "null"]}),
        },
        {
            "diPeriod": {"type": "integer", "default": 14, "minimum": 1},
            "adxSmoothing": {"type": "integer", "default": 14, "minimum": 1},
        },
    ),
    _factor_module("supertrend-indicator", "TradingView-style ATR Supertrend; direction and current unshifted line.",
        {"high": _data_port({"type":"number"}), "low": _data_port({"type":"number"}), "close": _data_port({"type":"number"})},
        {"supertrend": _data_port({"type":["number","null"]}), "direction": _data_port({"type":["string","null"],"enum":["up","down",None]}, required=False)},
        {"atrPeriod":{"type":"integer","default":10,"minimum":1},"multiplier":{"type":"number","default":3,"exclusiveMinimum":0}}),
    _factor_module("mfi-indicator", "Money Flow Index from typical-price money flow.",
        {"high": _data_port({"type":"number"}), "low": _data_port({"type":"number"}), "close": _data_port({"type":"number"}), "volume": _data_port({"type":"number"})},
        {"mfi": _data_port({"type":["number","null"]})}, {"period":{"type":"integer","default":14,"minimum":1}}),
    _factor_module("parabolic-sar-indicator", "Parabolic SAR trend state.",
        {"high": _data_port({"type":"number"}), "low": _data_port({"type":"number"}), "close": _data_port({"type":"number"})},
        {"sar": _data_port({"type":["number","null"]}), "direction": _data_port({"type":["string","null"],"enum":["up","down",None]}, required=False)},
        {"start":{"type":"number","default":.02,"minimum":0},"increment":{"type":"number","default":.02,"exclusiveMinimum":0},"maximum":{"type":"number","default":.2,"exclusiveMinimum":0}}),
    _factor_module("volume-indicator", "Pass-through volume indicator.", {"volume": _data_port({"type":"number"})}, {"volume": _data_port({"type":["number","null"]})}, {}),
    _factor_module("anchored-vwap-indicator", "Cumulative source-volume VWAP reset explicitly by input.",
        {"source": _data_port({"type":"number"}), "volume": _data_port({"type":"number"}), "reset": _data_port({"type":"boolean"})},
        {"vwap": _data_port({"type":["number","null"]})},
        {"anchor": {"type": "string", "enum": ["dataset", "week", "month", "year"], "default": "month"}}),
    _factor_module("ichimoku-indicator", "Current unshifted Ichimoku calculations; plot displacement is an explicit Visualizer parameter.",
        {"high": _data_port({"type":"number"}), "low": _data_port({"type":"number"}), "close": _data_port({"type":"number"})},
        {"conversion": _data_port({"type":["number","null"]}), "base": _data_port({"type":["number","null"]}, required=False), "spanA": _data_port({"type":["number","null"]}, required=False), "spanB": _data_port({"type":["number","null"]}, required=False), "lagging": _data_port({"type":["number","null"]}, required=False)},
        {"conversionPeriod":{"type":"integer","default":9,"minimum":1},"basePeriod":{"type":"integer","default":26,"minimum":1},"spanBPeriod":{"type":"integer","default":52,"minimum":1}}),
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
