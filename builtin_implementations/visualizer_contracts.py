#!/usr/bin/env python3
"""Product-owned executable contract catalog for Result visualizers."""

from copy import deepcopy

from engine.contracts.json_schema import normalize_config_schema


__all__ = ("visualizer_definition_map", "visualizer_definitions")


_BASIC_WORKFLOW_PROTOCOL_ID = "trade.basic-workflow"


COLOR_SCHEMA = {
    "type": "string",
    "pattern": "^#[0-9a-fA-F]{6}$",
}

TIMESTAMP_SCHEMA = {"type": "string"}

OFFSET_BARS_SCHEMA = {
    "type": "integer",
    "minimum": -9007199254740991,
    "maximum": 9007199254740991,
    "title": "Bar Offset",
    "description": (
        "Shift values by an exact number of positions in the bound time series; "
        "values whose target position is outside that explicit series are omitted."
    ),
    "default": 0,
}

TIMED_OHLC_SCHEMA = {
    "type": "object",
    "properties": {
        "eventTime": TIMESTAMP_SCHEMA,
        "open": {"type": "number"},
        "high": {"type": "number"},
        "low": {"type": "number"},
        "close": {"type": "number"},
        "complete": {"type": "boolean"},
    },
    "required": ["eventTime", "open", "high", "low", "close"],
    "additionalProperties": False,
}


def _definition(
    identifier,
    label,
    input_ports,
    properties,
    required,
    *,
    protocol_id,
    provides=(),
    requires=(),
    interactions=(),
):
    params_schema = normalize_config_schema({
        "type": "object",
        "properties": deepcopy(properties),
        "required": list(required),
        "additionalProperties": False,
    })
    definition = {
        "id": identifier,
        "label": label,
        "inputPorts": deepcopy(input_ports),
        "paramsSchema": params_schema,
        "renderer": {
            "id": identifier,
            "apiVersion": 1,
        },
        "capabilities": {
            "provides": sorted(deepcopy(provides), key=lambda item: item["name"]),
            "requires": sorted(deepcopy(requires), key=lambda item: item["name"]),
            "interactions": sorted(interactions),
        },
    }
    if protocol_id is not None:
        definition["protocolId"] = protocol_id
    return definition


def _target_property():
    return {
        "type": "string",
        "minLength": 1,
        "title": "Target Visualizer",
    }


def _point_schema():
    return {
        "type": "object",
        "properties": {
            "time": {"type": "string", "minLength": 1},
            "price": {"type": "number"},
        },
        "required": ["time", "price"],
        "additionalProperties": False,
    }


def _price_coordinate_provider():
    return {
        "name": "series",
        "kind": "series.price-coordinate",
        "attributes": {
            "price-scale": "priceScaleId",
            "time-domain": "timeDomainId",
        },
    }


def _price_coordinate_target(*, match_time_domain):
    return {
        "name": "target",
        "kind": "series.price-coordinate",
        "bindingParam": "targetVisualizerId",
        "matches": (
            {"time-domain": "timeDomainId"}
            if match_time_domain
            else {}
        ),
    }


VISUALIZER_DEFINITIONS = [
    _definition(
        "ohlc.candles",
        "Candles",
        {"dataKey": {"schema": TIMED_OHLC_SCHEMA}},
        {
            "dataKey": {"type": "string", "minLength": 1, "title": "Data"},
            "timeDomainId": {"type": "string", "minLength": 1, "title": "Time Domain"},
            "priceScaleId": {"type": "string", "minLength": 1, "title": "Price Scale"},
            "upColor": {**COLOR_SCHEMA, "title": "Up Color", "default": "#089981"},
            "downColor": {**COLOR_SCHEMA, "title": "Down Color", "default": "#f23645"},
        },
        ["dataKey", "timeDomainId", "priceScaleId"],
        protocol_id=_BASIC_WORKFLOW_PROTOCOL_ID,
        provides=(_price_coordinate_provider(),),
    ),
    _definition(
        "series.line",
        "Line",
        {
            "dataKey": {"schema": {"type": ["number", "null"]}},
            "timeKey": {"schema": TIMESTAMP_SCHEMA},
        },
        {
            "dataKey": {"type": "string", "minLength": 1, "title": "Data"},
            "timeKey": {"type": "string", "minLength": 1, "title": "Time"},
            "timeDomainId": {"type": "string", "minLength": 1, "title": "Time Domain"},
            "priceScaleId": {"type": "string", "minLength": 1, "title": "Price Scale"},
            "color": {**COLOR_SCHEMA, "title": "Color", "default": "#2563eb"},
            "lineWidth": {"type": "number", "minimum": 1, "title": "Width", "default": 2},
            "offsetBars": deepcopy(OFFSET_BARS_SCHEMA),
        },
        ["dataKey", "timeKey", "timeDomainId", "priceScaleId"],
        protocol_id=_BASIC_WORKFLOW_PROTOCOL_ID,
        provides=(_price_coordinate_provider(),),
    ),
    _definition(
        "series.scatter",
        "Scatter",
        {
            "dataKey": {"schema": {"type": ["number", "null"]}},
            "timeKey": {"schema": TIMESTAMP_SCHEMA},
        },
        {
            "dataKey": {"type": "string", "minLength": 1, "title": "Data"},
            "timeKey": {"type": "string", "minLength": 1, "title": "Time"},
            "timeDomainId": {"type": "string", "minLength": 1, "title": "Time Domain"},
            "priceScaleId": {"type": "string", "minLength": 1, "title": "Price Scale"},
            "color": {**COLOR_SCHEMA, "title": "Color", "default": "#2563eb"},
            "pointRadius": {"type": "number", "minimum": 1, "title": "Radius", "default": 3},
        },
        ["dataKey", "timeKey", "timeDomainId", "priceScaleId"],
        protocol_id=_BASIC_WORKFLOW_PROTOCOL_ID,
        provides=(_price_coordinate_provider(),),
    ),
    _definition(
        "series.histogram",
        "Histogram",
        {
            "dataKey": {"schema": {"type": ["number", "null"]}},
            "timeKey": {"schema": TIMESTAMP_SCHEMA},
        },
        {
            "dataKey": {"type": "string", "minLength": 1, "title": "Data"},
            "timeKey": {"type": "string", "minLength": 1, "title": "Time"},
            "timeDomainId": {"type": "string", "minLength": 1, "title": "Time Domain"},
            "priceScaleId": {"type": "string", "minLength": 1, "title": "Price Scale"},
            "color": {**COLOR_SCHEMA, "title": "Color", "default": "#64748b"},
            "positiveColor": {**COLOR_SCHEMA, "title": "Positive", "default": "#089981"},
            "negativeColor": {**COLOR_SCHEMA, "title": "Negative", "default": "#f23645"},
            "offsetBars": deepcopy(OFFSET_BARS_SCHEMA),
        },
        ["dataKey", "timeKey", "timeDomainId", "priceScaleId"],
        protocol_id=_BASIC_WORKFLOW_PROTOCOL_ID,
        provides=(_price_coordinate_provider(),),
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
            "timeKey": {"schema": TIMESTAMP_SCHEMA},
        },
        {
            "dataKey": {"type": "string", "minLength": 1, "title": "Marker Data"},
            "timeKey": {"type": "string", "minLength": 1, "title": "Time"},
            "timeDomainId": {"type": "string", "minLength": 1, "title": "Time Domain"},
            "targetVisualizerId": _target_property(),
        },
        ["dataKey", "timeKey", "timeDomainId", "targetVisualizerId"],
        protocol_id=_BASIC_WORKFLOW_PROTOCOL_ID,
        requires=(_price_coordinate_target(match_time_domain=True),),
    ),
    _definition(
        "overlay.priceLine",
        "Price Line",
        {
            "dataKey": {"schema": {"type": "number"}},
            "timeKey": {"schema": TIMESTAMP_SCHEMA},
        },
        {
            "dataKey": {"type": "string", "minLength": 1, "title": "Data"},
            "timeKey": {"type": "string", "minLength": 1, "title": "Time"},
            "timeDomainId": {"type": "string", "minLength": 1, "title": "Time Domain"},
            "targetVisualizerId": _target_property(),
            "reducer": {
                "type": "string",
                "enum": ["latest"],
                "title": "Reducer",
            },
            "color": {**COLOR_SCHEMA, "title": "Color", "default": "#475569"},
            "lineWidth": {"type": "number", "minimum": 1, "title": "Width", "default": 1},
        },
        ["dataKey", "timeKey", "timeDomainId", "targetVisualizerId", "reducer"],
        protocol_id=_BASIC_WORKFLOW_PROTOCOL_ID,
        requires=(_price_coordinate_target(match_time_domain=True),),
    ),
    _definition(
        "drawing.horizontalLine",
        "Horizontal Line",
        {},
        {
            "targetVisualizerId": _target_property(),
            "price": {"type": "number", "title": "Price"},
            "color": {**COLOR_SCHEMA, "title": "Color", "default": "#475569"},
            "lineWidth": {"type": "number", "minimum": 1, "title": "Width", "default": 1},
        },
        ["targetVisualizerId", "price"],
        protocol_id=_BASIC_WORKFLOW_PROTOCOL_ID,
        requires=(_price_coordinate_target(match_time_domain=False),),
        interactions=("chart.pointer",),
    ),
    _definition(
        "drawing.trendLine",
        "Trend Line",
        {},
        {
            "targetVisualizerId": _target_property(),
            "points": {
                "type": "array",
                "items": _point_schema(),
                "minItems": 2,
                "maxItems": 2,
                "title": "Points",
            },
            "color": {**COLOR_SCHEMA, "title": "Color", "default": "#475569"},
            "lineWidth": {"type": "number", "minimum": 1, "title": "Width", "default": 1},
        },
        ["targetVisualizerId", "points"],
        protocol_id=_BASIC_WORKFLOW_PROTOCOL_ID,
        requires=(_price_coordinate_target(match_time_domain=False),),
        interactions=("chart.pointer",),
    ),
    _definition(
        "drawing.rectangle",
        "Rectangle",
        {},
        {
            "targetVisualizerId": _target_property(),
            "corners": {
                "type": "array",
                "items": _point_schema(),
                "minItems": 2,
                "maxItems": 2,
                "title": "Corners",
            },
            "color": {**COLOR_SCHEMA, "title": "Border Color", "default": "#475569"},
            "lineWidth": {
                "type": "number",
                "minimum": 1,
                "maximum": 12,
                "title": "Border Width",
                "default": 1,
            },
            "fillColor": {**COLOR_SCHEMA, "title": "Fill Color", "default": "#2563eb"},
            "fillOpacity": {
                "type": "number",
                "minimum": 0,
                "maximum": 1,
                "title": "Fill Opacity",
                "default": 0.12,
            },
        },
        ["targetVisualizerId", "corners"],
        protocol_id=_BASIC_WORKFLOW_PROTOCOL_ID,
        requires=(_price_coordinate_target(match_time_domain=False),),
        interactions=("chart.pointer",),
    ),
    _definition(
        "drawing.brush",
        "Brush",
        {},
        {
            "targetVisualizerId": _target_property(),
            "points": {
                "type": "array",
                "items": _point_schema(),
                "minItems": 2,
                "maxItems": 8192,
                "title": "Points",
            },
            "color": {**COLOR_SCHEMA, "title": "Color", "default": "#475569"},
            "lineWidth": {
                "type": "number",
                "minimum": 1,
                "maximum": 64,
                "title": "Width",
                "default": 2,
            },
        },
        ["targetVisualizerId", "points"],
        protocol_id=_BASIC_WORKFLOW_PROTOCOL_ID,
        requires=(_price_coordinate_target(match_time_domain=False),),
        interactions=("chart.pointer",),
    ),
    _definition(
        "drawing.text",
        "Text",
        {},
        {
            "targetVisualizerId": _target_property(),
            "anchor": {**_point_schema(), "title": "Anchor"},
            "text": {
                "type": "string",
                "minLength": 1,
                "maxLength": 4096,
                "title": "Text",
            },
            "color": {**COLOR_SCHEMA, "title": "Color", "default": "#172026"},
            "fontSize": {
                "type": "number",
                "minimum": 8,
                "maximum": 96,
                "title": "Font Size",
                "default": 14,
            },
        },
        ["targetVisualizerId", "anchor", "text"],
        protocol_id=_BASIC_WORKFLOW_PROTOCOL_ID,
        requires=(_price_coordinate_target(match_time_domain=False),),
        interactions=("chart.pointer", "chart.text-input"),
    ),
]


def _ui_params(definition):
    required = set(definition["paramsSchema"].get("required") or [])
    visualizer_refs = {
        capability["bindingParam"]
        for capability in definition["capabilities"]["requires"]
    }
    result = []
    for name, schema in definition["paramsSchema"]["properties"].items():
        field = {
            "name": name,
            "label": schema.get("title") or name,
            "type": (
                "dataKey"
                if name in definition["inputPorts"]
                else "visualizerRef"
                if name in visualizer_refs
                else schema.get("type", "string")
            ),
            "required": name in required,
        }
        if "default" in schema:
            field["default"] = deepcopy(schema["default"])
        if "minimum" in schema:
            field["min"] = schema["minimum"]
        if "maximum" in schema:
            field["max"] = schema["maximum"]
        if "enum" in schema:
            field["options"] = deepcopy(schema["enum"])
        result.append(field)
    return result


def visualizer_definitions():
    result = deepcopy(VISUALIZER_DEFINITIONS)
    for definition in result:
        definition["params"] = _ui_params(definition)
    return result


def visualizer_definition_map():
    return {item["id"]: item for item in visualizer_definitions()}
