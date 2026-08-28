"""Causal Dataset and Sampler integration tests for Basic Workflow v3."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from application_protocols.basic_workflow.manifest import (
    PROTOCOL_ID,
    V3_PROFILE_ID,
    V3_PROTOCOL_VERSION,
)
from builtin_implementations import resources as builtin_resources
from builtin_implementations.basic_workflow_contracts import (
    OHLCV_BAR_SCHEMA,
    OHLCV_SAMPLER_OUTPUT_SCHEMA,
)
from dataset_adapters import basic_workflow_v3
from dataset_adapters.basic_workflow_v3_conformance import (
    require_basic_workflow_v3_capability,
    require_basic_workflow_v3_descriptor,
    validate_dataset_directory,
)
from engine.authority.dataset import verify_dataset_version_storage_authority
from engine.authority.sampler import verify_sampler_runtime_bundle_authority
from engine.control import database as engine_database
from engine.repository import datasets, samplers
from engine.runtime.dataset import create_dataset_handle
from engine.runtime.sampler import create_verified_sampler_runtime


def _descriptor(**changes):
    value = {
        "protocolId": PROTOCOL_ID,
        "protocolVersion": V3_PROTOCOL_VERSION,
        "profile": V3_PROFILE_ID,
        "cashUnit": "USD",
        "quantityUnit": "share",
        "executionConvention": "prior-approved-intent-next-bar-open",
        "valuationConvention": "current-bar-close",
    }
    value.update(changes)
    return value


class BasicWorkflowV3DatasetTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.config = {
            "controlRoot": str(self.root / "control"),
            "releaseRoot": str(self.root / "release"),
            "liveRoot": str(self.root / "live"),
        }
        engine_database.prepare_database(self.config)

    def tearDown(self):
        self.temporary.cleanup()

    def _source(self, content=None, *, name="source"):
        root = self.root / name
        path = root / "day" / "US-TSLA.csv"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            content
            or (
                "time,eventTime,open,close,high,low,volume\n"
                "2026-08-03T20:15:00Z,2026-08-03T20:00:00Z,100,101,102,99,1000\n"
                "2026-08-04T20:15:00Z,2026-08-04T20:00:00Z,101,103,104,100,0\n"
            ),
            encoding="utf-8",
        )
        return root

    def test_descriptor_capability_publish_and_arbitrary_instrument_sampler_are_exact(self):
        descriptor = _descriptor()
        self.assertEqual(require_basic_workflow_v3_descriptor(descriptor), descriptor)
        self.assertEqual(
            require_basic_workflow_v3_capability({
                "basicWorkflow": {
                    "protocol": basic_workflow_v3.CAPABILITY_PROTOCOL,
                    "descriptor": descriptor,
                }
            }),
            descriptor,
        )
        report = validate_dataset_directory(self._source(), descriptor)
        self.assertEqual(report["protocolVersion"], "3.0.0")
        self.assertEqual(report["rowCount"], 2)
        self.assertEqual(report["firstTime"], "2026-08-03T20:15:00Z")

        published = basic_workflow_v3.register_dataset(
            self.config,
            dataset_id="basic-ohlcv-tsla",
            name="TSLA OHLCV",
            source_root=self._source(name="publish-source"),
            descriptor=descriptor,
            source={
                "type": "test-fixture",
                "details": {"providerId": "fixture", "rawSha256": "sha256:test"},
            },
            display_time_zone="America/New_York",
        )
        version = datasets.ensure_dataset_version(
            self.config,
            published["datasetId"],
            published["latestVersionId"],
        )
        self.assertEqual(version["protocolId"], PROTOCOL_ID)
        self.assertEqual(
            version["capabilities"]["basicWorkflow"],
            {
                "protocol": "trade.app.basic-workflow-dataset/v3",
                "descriptor": descriptor,
            },
        )

        installed = builtin_resources.install(self.config)
        sampler_record = next(
            item
            for item in installed
            if item.get("samplerId") == "basic-ohlcv-map-sampler"
        )
        stored = datasets.verify_dataset_version_id(
            self.config,
            published["latestVersionId"],
        )
        _version, dataset_authority = verify_dataset_version_storage_authority(
            self.config["releaseRoot"],
            stored,
        )
        dataset = create_dataset_handle(dataset_authority)
        definition = samplers.get_sampler(
            self.config,
            sampler_record["samplerId"],
            sampler_record["version"],
        )
        self.assertEqual(definition["protocolId"], PROTOCOL_ID)
        self.assertEqual(definition["outputSchema"], OHLCV_SAMPLER_OUTPUT_SCHEMA)
        sampler_authority = verify_sampler_runtime_bundle_authority(definition)
        with tempfile.TemporaryDirectory(dir=self.root) as execution_root:
            runtime = create_verified_sampler_runtime(
                sampler_authority,
                dataset,
                {"decisionPeriod": "day"},
                execution_root=execution_root,
            )
            try:
                samples = list(runtime)
            finally:
                runtime.close()
        self.assertEqual(
            [sample.decision_time for sample in samples],
            ["2026-08-03T20:15:00Z", "2026-08-04T20:15:00Z"],
        )
        first = samples[0].data["price"]["day"]["US-TSLA"]
        self.assertEqual(set(first), set(OHLCV_BAR_SCHEMA["required"]))
        self.assertEqual(first["eventTime"], "2026-08-03T20:00:00Z")
        self.assertEqual(first["volume"], 1000.0)
        self.assertEqual(
            samples[0].provenance["price.day.US-TSLA"]["availableAt"],
            samples[0].decision_time,
        )

    def test_conformance_rejects_missing_or_negative_volume_and_causality_drift(self):
        invalid = {
            "missing-volume": (
                "time,eventTime,open,close,high,low\n"
                "2026-08-03T20:15:00Z,2026-08-03T20:00:00Z,100,101,102,99\n",
                "header must be exactly",
            ),
            "negative-volume": (
                "time,eventTime,open,close,high,low,volume\n"
                "2026-08-03T20:15:00Z,2026-08-03T20:00:00Z,100,101,102,99,-1\n",
                "non-negative",
            ),
            "availability-before-event": (
                "time,eventTime,open,close,high,low,volume\n"
                "2026-08-03T19:59:00Z,2026-08-03T20:00:00Z,100,101,102,99,1\n",
                "must not precede",
            ),
            "duplicate-time": (
                "time,eventTime,open,close,high,low,volume\n"
                "2026-08-03T20:15:00Z,2026-08-03T20:00:00Z,100,101,102,99,1\n"
                "2026-08-03T20:15:00Z,2026-08-03T20:00:00Z,100,101,102,99,1\n",
                "strictly increasing",
            ),
        }
        for index, (name, (content, message)) in enumerate(invalid.items()):
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, message):
                validate_dataset_directory(
                    self._source(content, name=f"invalid-{index}"),
                    _descriptor(),
                )

    def test_v2_identity_cannot_be_misrepresented_as_v3(self):
        for invalid in (
            _descriptor(protocolId="trade.app.basic-workflow-dataset/v3"),
            _descriptor(protocolVersion="2.0.0"),
            _descriptor(profile="multi-instrument-bar-position"),
        ):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                require_basic_workflow_v3_descriptor(invalid)


if __name__ == "__main__":
    unittest.main()
