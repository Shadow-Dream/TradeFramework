"""Product-owned BuiltIn Analysis Graph presets.

Analysis is intentionally neutral by default.  Application protocols may
publish analyzers for their own declared result contracts, but BuiltIns do not
bind Analyzer inputs to Pipeline-private DataKeys.
"""

from __future__ import annotations

from application_protocols.basic_workflow.manifest import PROTOCOL_ID

NEUTRAL_ANALYSIS_ID = "neutral-analysis"
BASIC_WORKFLOW_ANALYSIS_ID = "basic-workflow-analysis"


def builtin_analysis_definitions(
    _module_definitions, _module_definition_evidence
):
    return {
        NEUTRAL_ANALYSIS_ID: {
            "schemaVersion": 1,
            "analysisId": NEUTRAL_ANALYSIS_ID,
            "name": "Basic",
            "description": "Generic Analysis Graph. Add Analyzer Modules explicitly; no metrics are assumed.",
            "instances": {},
            "graph": {"nodes": [], "inputs": {}, "outputs": {}},
        },
        BASIC_WORKFLOW_ANALYSIS_ID: {
            "schemaVersion": 1,
            "analysisId": BASIC_WORKFLOW_ANALYSIS_ID,
            "name": "Basic Workflow",
            "protocolId": PROTOCOL_ID,
            "description": (
                "Basic Workflow Analysis Graph with no Analyzer Modules selected."
            ),
            "instances": {},
            "graph": {"nodes": [], "inputs": {}, "outputs": {}},
        },
    }


__all__ = (
    "BASIC_WORKFLOW_ANALYSIS_ID",
    "NEUTRAL_ANALYSIS_ID",
    "builtin_analysis_definitions",
)
