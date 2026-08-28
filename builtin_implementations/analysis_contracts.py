#!/usr/bin/env python3
"""Product-owned Analysis BuiltIn Module declarations."""

from __future__ import annotations

import copy

from application_protocols.basic_workflow.manifest import PROTOCOL_ID
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
        "protocolId": PROTOCOL_ID,
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
]


def analysis_module_definitions():
    return {
        item["moduleId"]: copy.deepcopy(item)
        for item in ANALYSIS_MODULES
    }
