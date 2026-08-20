"""Canonical manifest for the Basic Workflow application protocol."""

from __future__ import annotations

from . import schemas


PROTOCOL_ID = "trade.basic-workflow"
PROTOCOL_VERSION = "2.0.0"
PROFILE_ID = "multi-instrument-bar-position"

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


__all__ = ("MANIFEST", "PROFILE_ID", "PROTOCOL_ID", "PROTOCOL_VERSION")
