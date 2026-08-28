"""Application resource boundary checks independent of Engine execution semantics."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from builtin_implementations.analysis_presets import (
    BASIC_WORKFLOW_ANALYSIS_ID,
    NEUTRAL_ANALYSIS_ID,
    builtin_analysis_definitions,
)
from application_protocols.basic_workflow.manifest import PROTOCOL_ID
from builtin_implementations.environment_presets import (
    BASIC_WORKFLOW_ENVIRONMENT_ID,
    builtin_environment_definitions,
)


class ResourceBoundaryTests(unittest.TestCase):
    def test_builtin_analyzer_presets_never_read_pipeline_data(self):
        definitions = builtin_analysis_definitions({}, {})
        self.assertEqual(
            set(definitions),
            {BASIC_WORKFLOW_ANALYSIS_ID, NEUTRAL_ANALYSIS_ID},
        )
        self.assertEqual(definitions[NEUTRAL_ANALYSIS_ID]["name"], "Basic")
        self.assertNotIn("protocolId", definitions[NEUTRAL_ANALYSIS_ID])
        self.assertEqual(
            definitions[NEUTRAL_ANALYSIS_ID]["graph"],
            {"nodes": [], "inputs": {}, "outputs": {}},
        )
        self.assertEqual(
            definitions[BASIC_WORKFLOW_ANALYSIS_ID]["protocolId"],
            PROTOCOL_ID,
        )
        self.assertEqual(
            definitions[BASIC_WORKFLOW_ANALYSIS_ID]["graph"],
            {"nodes": [], "inputs": {}, "outputs": {}},
        )

    def test_builtin_environments_use_only_declared_protocol_boundaries(self):
        with patch(
            "builtin_implementations.environment_presets.latest_archived_module_versions",
            return_value={"basic-multi-asset-bar-account": "1"},
        ):
            basic = builtin_environment_definitions({}, {})
        self.assertEqual(
            set(basic),
            {BASIC_WORKFLOW_ENVIRONMENT_ID},
        )
        graph = basic[BASIC_WORKFLOW_ENVIRONMENT_ID]["graph"]
        self.assertEqual(
            basic[BASIC_WORKFLOW_ENVIRONMENT_ID]["protocolId"],
            PROTOCOL_ID,
        )
        input_keys = {value["dataKey"] for value in graph["inputs"].values()}
        output_keys = {value["dataKey"] for value in graph["outputs"].values()}
        self.assertEqual(input_keys, {"time", "price", "last.intent.approved"})
        self.assertFalse(any(key.startswith("last.pipeline.") for key in input_keys))
        self.assertFalse(any(key.startswith("pipeline.") for key in output_keys))


if __name__ == "__main__":
    unittest.main()
