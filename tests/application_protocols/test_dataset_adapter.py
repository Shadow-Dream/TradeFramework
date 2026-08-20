"""Dataset and Sampler integration tests for Basic Workflow v2."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from application_protocols.basic_workflow.conformance import (
    require_basic_workflow_capability,
    require_basic_workflow_descriptor,
    validate_dataset_directory,
)
from application_protocols.basic_workflow.manifest import (
    PROFILE_ID,
    PROTOCOL_ID,
    PROTOCOL_VERSION,
)
from builtin_implementations import resources as builtin_resources
from builtin_implementations.basic_workflow_contracts import SAMPLER_OUTPUT_SCHEMA
from dataset_adapters import basic_workflow
from engine.authority.dataset import verify_dataset_version_storage_authority
from engine.authority.sampler import verify_sampler_runtime_bundle_authority
from engine.control import database as engine_database
from engine.repository import datasets, samplers
from engine.runtime.dataset import create_dataset_handle
from engine.runtime.sampler import create_verified_sampler_runtime


def _descriptor(**changes):
    value = {
        "protocolId": PROTOCOL_ID,
        "protocolVersion": PROTOCOL_VERSION,
        "profile": PROFILE_ID,
        "cashUnit": "USD",
        "quantityUnit": "share",
        "executionConvention": "prior-approved-intent-next-bar-open",
        "valuationConvention": "current-bar-close",
    }
    value.update(changes)
    return value


class BasicWorkflowDatasetTests(unittest.TestCase):
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

    def _source(self, name="source"):
        root = self.root / name
        rows = {
            "day/SPY.csv": (
                "time,open,close,high,low\n"
                "2026-01-02T21:00:00Z,100,101,102,99\n"
                "2026-01-05T21:00:00Z,200,201,202,199\n"
            ),
            "day/QQQ.csv": (
                "time,open,close,high,low\n"
                "2026-01-02T21:00:00Z,50,51,52,49\n"
                "2026-01-06T21:00:00Z,60,61,62,59\n"
            ),
            "week/SPY.csv": (
                "time,open,close,high,low\n"
                "2026-01-02T21:00:00Z,90,101,103,89\n"
            ),
        }
        for relative, content in rows.items():
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        return root

    def _publish(self, source_root=None, dataset_id="basic-prices"):
        return basic_workflow.register_dataset(
            self.config,
            dataset_id=dataset_id,
            name="Basic Prices",
            source_root=source_root or self._source(dataset_id + "-source"),
            descriptor=_descriptor(),
            source={"type": "test-fixture", "details": {"provider": "unit"}},
            display_time_zone="America/New_York",
        )

    def test_descriptor_capability_and_directory_report_are_exact(self):
        descriptor = _descriptor()
        self.assertEqual(require_basic_workflow_descriptor(descriptor), descriptor)
        capability = {
            "basicWorkflow": {
                "protocol": basic_workflow.CAPABILITY_PROTOCOL,
                "descriptor": descriptor,
            }
        }
        self.assertEqual(require_basic_workflow_capability(capability), descriptor)
        report = validate_dataset_directory(self._source(), descriptor)
        self.assertEqual(report["fileCount"], 3)
        self.assertEqual(report["rowCount"], 5)
        self.assertEqual(
            report["periods"],
            {
                "day": {"instruments": ["QQQ", "SPY"], "rowCount": 4},
                "week": {"instruments": ["SPY"], "rowCount": 1},
            },
        )
        for invalid in (
            _descriptor(protocolVersion="1.0.0"),
            _descriptor(profile="single-instrument-bar-position"),
            {**descriptor, "future": True},
        ):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                require_basic_workflow_descriptor(invalid)

    def test_publisher_seals_csv_tree_and_application_capability(self):
        published = self._publish()
        version = datasets.ensure_dataset_version(
            self.config,
            published["datasetId"],
            published["latestVersionId"],
        )
        self.assertEqual(
            version["capabilities"]["basicWorkflow"],
            {
                "protocol": "trade.app.basic-workflow-dataset/v2",
                "descriptor": _descriptor(),
            },
        )
        self.assertNotIn("records", version["capabilities"])
        report = version["manifest"]["dataset"]["metadata"]["conformance"]
        self.assertEqual(report["protocolVersion"], "2.0.0")
        self.assertEqual(report["periods"]["day"]["instruments"], ["QQQ", "SPY"])
        self.assertEqual(
            version["capabilities"]["visualization"]["descriptor"]["timeZone"],
            "America/New_York",
        )

    def test_installed_sampler_aligns_multi_period_multi_instrument_prices(self):
        published = self._publish()
        installed = builtin_resources.install(self.config)
        sampler_record = next(item for item in installed if item.get("samplerId"))
        version = datasets.verify_dataset_version_id(
            self.config,
            published["latestVersionId"],
        )
        _version, dataset_authority = verify_dataset_version_storage_authority(
            self.config["releaseRoot"],
            version,
        )
        dataset = create_dataset_handle(dataset_authority)
        definition = samplers.get_sampler(
            self.config,
            sampler_record["samplerId"],
            sampler_record["version"],
        )
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
        self.assertEqual(definition["outputSchema"], SAMPLER_OUTPUT_SCHEMA)
        self.assertEqual(
            [sample.decision_time for sample in samples],
            [
                "2026-01-02T21:00:00Z",
                "2026-01-05T21:00:00Z",
                "2026-01-06T21:00:00Z",
            ],
        )
        self.assertEqual(samples[0].data["time"], samples[0].decision_time)
        self.assertEqual(samples[0].data["price"]["day"]["SPY"]["open"], 100.0)
        self.assertEqual(samples[0].data["price"]["week"]["SPY"]["close"], 101.0)
        self.assertEqual(samples[1].data["price"]["day"]["QQQ"]["open"], 50.0)
        self.assertEqual(samples[2].data["price"]["day"]["SPY"]["open"], 200.0)
        self.assertEqual(
            samples[1].provenance["price.day.SPY"]["sourcePath"],
            "day/SPY.csv",
        )

    def test_invalid_csv_fails_before_dataset_publication(self):
        source = self._source("invalid-source")
        (source / "day" / "SPY.csv").write_text(
            "time,open,close,high,low\n"
            "2026-01-02T21:00:00Z,100,101,100,99\n",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(ValueError, "OHLC upper bound"):
            self._publish(source, dataset_id="invalid-prices")
        with self.assertRaisesRegex(ValueError, "Unknown dataset"):
            datasets.get_dataset(self.config, "invalid-prices")


if __name__ == "__main__":
    unittest.main()
