"""Canonical manifest for the Basic Workflow application protocol."""

from __future__ import annotations

from . import schemas


PROTOCOL_ID = "trade.basic-workflow"
PROTOCOL_VERSION = "2.0.0"
PROFILE_ID = "multi-instrument-bar-position"
V3_CAPABILITY_PROTOCOL = "trade.app.basic-workflow-dataset/v3"
V3_PROTOCOL_VERSION = "3.0.0"
V3_PROFILE_ID = "single-instrument-ohlcv-bar-position"

MANIFEST = {
    "protocolId": PROTOCOL_ID,
    "protocolVersion": PROTOCOL_VERSION,
    "profile": PROFILE_ID,
    "cycleOrder": ["sampler", "environment", "pipeline", "analysis"],
    "dataset": {
        "layout": "<period>/<instrumentId>.csv",
        "csvFields": list(schemas.CSV_FIELDS),
    },
    "sampler": {"provides": ["time", "price"]},
    "environment": {
        "requires": ["time", "price", "last.intent.approved"],
        "provides": [
            "time",
            "price",
            "portfolio.account",
            "execution.orders",
        ],
    },
    "pipeline": {
        "requires": ["time", "price", "portfolio.account", "execution.orders"],
        "provides": [
            "universe.selected",
            "intent.requested",
            "intent.approved",
        ],
    },
    "analysis": {
        "inputs": "declared-by-selected-analysis-graph",
        "outputs": "declared-by-selected-analysis-graph",
    },
}

V3_MANIFEST = {
    **MANIFEST,
    "protocolVersion": V3_PROTOCOL_VERSION,
    "profile": V3_PROFILE_ID,
    "dataset": {
        "layout": "<period>/<instrumentId>.csv",
        "csvFields": list(schemas.CSV_FIELDS_V3),
        "capabilityProtocol": V3_CAPABILITY_PROTOCOL,
        "timeSemantics": {
            "time": "availableAt",
            "eventTime": "regularSessionClose",
        },
    },
}


__all__ = (
    "MANIFEST",
    "PROFILE_ID",
    "PROTOCOL_ID",
    "PROTOCOL_VERSION",
    "V3_CAPABILITY_PROTOCOL",
    "V3_MANIFEST",
    "V3_PROTOCOL_VERSION",
    "V3_PROFILE_ID",
)
