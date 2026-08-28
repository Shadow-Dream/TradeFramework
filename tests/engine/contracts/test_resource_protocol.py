"""Focused tests for passive protocol ownership metadata."""

from __future__ import annotations

import json
import unittest

from engine.contracts.backtest import execution_snapshot_protocol_id
from engine.contracts.protocol import common_protocol_id, normalize_protocol_id
from engine.repository.backtest_result_views import backtest_summary


PROTOCOL_ID = "trade.basic-workflow"


class ResourceProtocolContractTests(unittest.TestCase):
    def test_protocol_id_is_opaque_but_canonical(self):
        self.assertEqual(normalize_protocol_id(PROTOCOL_ID), PROTOCOL_ID)
        self.assertEqual(normalize_protocol_id("vendor.unknown"), "vendor.unknown")
        for value in (None, "", " padded "):
            with self.subTest(value=value), self.assertRaises(ValueError):
                normalize_protocol_id(value)

    def test_common_protocol_requires_every_record_to_declare_the_same_id(self):
        self.assertEqual(
            common_protocol_id([
                {"protocolId": PROTOCOL_ID},
                {"protocolId": PROTOCOL_ID},
            ]),
            PROTOCOL_ID,
        )
        self.assertIsNone(common_protocol_id([{"protocolId": PROTOCOL_ID}, {}]))
        self.assertIsNone(common_protocol_id([
            {"protocolId": PROTOCOL_ID},
            {"protocolId": "vendor.other"},
        ]))

    def test_backtest_projection_reads_only_frozen_top_level_resources(self):
        snapshot = {
            "datasetVersion": {"protocolId": PROTOCOL_ID},
            "samplerDefinition": {"protocolId": PROTOCOL_ID},
            "pipeline": {"definition": {"protocolId": PROTOCOL_ID}},
            "environmentDefinition": {"protocolId": PROTOCOL_ID},
            "analysisDefinition": {"protocolId": PROTOCOL_ID},
            "pipelineModuleDefinitions": {
                "unbound": {"moduleId": "generic-module"},
            },
        }
        self.assertEqual(execution_snapshot_protocol_id(snapshot), PROTOCOL_ID)
        snapshot["analysisDefinition"].pop("protocolId")
        self.assertIsNone(execution_snapshot_protocol_id(snapshot))

    def test_backtest_summary_exposes_derived_protocol_without_changing_result(self):
        snapshot = {
            "datasetVersion": {"protocolId": PROTOCOL_ID},
            "samplerDefinition": {"protocolId": PROTOCOL_ID},
            "pipeline": {"definition": {"protocolId": PROTOCOL_ID}},
            "environmentDefinition": {"protocolId": PROTOCOL_ID},
            "analysisDefinition": {"protocolId": PROTOCOL_ID},
        }
        row = {
            "backtest_id": "bt_01M0SCSPWCMW192DPJ4B0PX99R",
            "pipeline_id": "pipeline",
            "dataset_id": "dataset",
            "name": "Protocol Backtest",
            "status": "completed",
            "runner": "engine.backtest",
            "created_at": "2026-08-26T00:00:00Z",
            "completed_at": "2026-08-26T00:01:00Z",
            "archived_at": "",
            "archive_reason": "",
            "metrics_json": "{}",
            "request_json": json.dumps({"executionSnapshot": snapshot}),
            "indexed_result_schema_version": 8,
            "indexed_result_has_cycles": 1,
            "indexed_result_content_digest": "sha256:" + "1" * 64,
            "indexed_result_size": 1,
        }
        self.assertEqual(backtest_summary(row)["protocolId"], PROTOCOL_ID)
        snapshot["samplerDefinition"].pop("protocolId")
        row["request_json"] = json.dumps({"executionSnapshot": snapshot})
        self.assertNotIn("protocolId", backtest_summary(row))


if __name__ == "__main__":
    unittest.main()
