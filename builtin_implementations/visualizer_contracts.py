#!/usr/bin/env python3
"""Product-owned executable contract catalog for Result visualizers."""

from copy import deepcopy

from engine.contracts.json_schema import normalize_config_schema


__all__ = ("visualizer_definition_map", "visualizer_definitions")


COLOR_SCHEMA = {
    "type": "string",
    "pattern": "^#[0-9a-fA-F]{6}$",
}


def _definition(identifier, label, input_ports, properties, required):
    params_schema = normalize_config_schema({
        "type": "object",
        "properties": deepcopy(properties),
        "required": list(required),
        "additionalProperties": False,
    })
    return {
        "id": identifier,
        "label": label,
        "inputPorts": deepcopy(input_ports),
        "paramsSchema": params_schema,
    }


VISUALIZER_DEFINITIONS = [
    _definition(
        "ohlc.candles",
        "Candles",
        {"dataKey": {"schema": {
            "type": "object",
            "properties": {
                "time": {"type": "string"},
                "open": {"type": "number"},
                "high": {"type": "number"},
                "low": {"type": "number"},
                "close": {"type": "number"},
                "complete": {"type": "boolean"},
            },
            "required": ["time", "open", "high", "low", "close", "complete"],
            "additionalProperties": False,
        }}},
        {
            "dataKey": {"type": "string", "minLength": 1, "title": "Data"},
            "upColor": {**COLOR_SCHEMA, "title": "Up Color", "default": "#089981"},
            "downColor": {**COLOR_SCHEMA, "title": "Down Color", "default": "#f23645"},
        },
        ["dataKey"],
    ),
    _definition(
        "series.line",
        "Line",
        {"dataKey": {"schema": {"type": ["number", "null"]}}},
        {
            "dataKey": {"type": "string", "minLength": 1, "title": "Data"},
            "color": {**COLOR_SCHEMA, "title": "Color", "default": "#2563eb"},
            "lineWidth": {"type": "number", "minimum": 1, "title": "Width", "default": 2},
        },
        ["dataKey"],
    ),
    _definition(
        "series.scatter",
        "Scatter",
        {"dataKey": {"schema": {"type": ["number", "null"]}}},
        {
            "dataKey": {"type": "string", "minLength": 1, "title": "Data"},
            "color": {**COLOR_SCHEMA, "title": "Color", "default": "#2563eb"},
            "pointRadius": {"type": "number", "minimum": 1, "title": "Radius", "default": 3},
        },
        ["dataKey"],
    ),
    _definition(
        "series.histogram",
        "Histogram",
        {"dataKey": {"schema": {"type": ["number", "null"]}}},
        {
            "dataKey": {"type": "string", "minLength": 1, "title": "Data"},
            "color": {**COLOR_SCHEMA, "title": "Color", "default": "#64748b"},
            "positiveColor": {**COLOR_SCHEMA, "title": "Positive", "default": "#089981"},
            "negativeColor": {**COLOR_SCHEMA, "title": "Negative", "default": "#f23645"},
        },
        ["dataKey"],
    ),
    _definition(
        "overlay.markers",
        "Markers",
        {
            "dataKey": {"schema": {
                "type": ["object", "null"],
                "properties": {
                    "side": {"type": "string"},
                    "price": {"type": "number"},
                    "reason": {"type": ["string", "null"]},
                    "quantity": {"type": "number"},
                    "shape": {"type": "string"},
                    "position": {"type": "string"},
                    "color": {"type": "string"},
                },
                "additionalProperties": False,
            }},
            "targetDataKey": {"schema": {"type": "number"}},
        },
        {
            "dataKey": {"type": "string", "minLength": 1, "title": "Marker Data"},
            "targetDataKey": {"type": "string", "minLength": 1, "title": "Target Data"},
        },
        ["dataKey", "targetDataKey"],
    ),
    _definition(
        "overlay.priceLine",
        "Price Line",
        {"dataKey": {"schema": {"type": "number"}}},
        {
            "dataKey": {"type": "string", "minLength": 1, "title": "Data"},
            "color": {**COLOR_SCHEMA, "title": "Color", "default": "#475569"},
            "lineWidth": {"type": "number", "minimum": 1, "title": "Width", "default": 1},
        },
        ["dataKey"],
    ),
]


def _ui_params(definition):
    required = set(definition["paramsSchema"].get("required") or [])
    result = []
    for name, schema in definition["paramsSchema"]["properties"].items():
        field = {
            "name": name,
            "label": schema.get("title") or name,
            "type": "dataKey" if name in definition["inputPorts"] else schema.get("type", "string"),
            "required": name in required,
        }
        if "default" in schema:
            field["default"] = deepcopy(schema["default"])
        if "minimum" in schema:
            field["min"] = schema["minimum"]
        if "maximum" in schema:
            field["max"] = schema["maximum"]
        result.append(field)
    return result


def visualizer_definitions():
    result = deepcopy(VISUALIZER_DEFINITIONS)
    for definition in result:
        definition["params"] = _ui_params(definition)
    return result


def visualizer_definition_map():
    return {item["id"]: item for item in visualizer_definitions()}
