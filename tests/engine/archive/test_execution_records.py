#!/usr/bin/env python3

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from engine.authority import execution_records


class FrozenExecutionRecordLocationTests(unittest.TestCase):
    def test_outside_records_are_rejected_before_archive_content_is_read(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            release_root = root / "release"
            release_root.mkdir()
            outside = root / "outside"
            outside.mkdir()

            cases = (
                (
                    "pipeline",
                    {
                        "pipelineId": "pipeline-a",
                        "version": "1",
                        "archive": {
                            "resourceType": "pipeline",
                            "resourceId": "pipeline-a",
                            "root": str(outside),
                            "manifestDigest": "digest",
                        },
                    },
                    lambda record: execution_records.verify_pipeline_record(
                        release_root,
                        record,
                        pipeline_id="pipeline-a",
                        version="1",
                    ),
                ),
                (
                    "environment",
                    {
                        "environmentId": "environment-a",
                        "version": "1",
                        "archive": {
                            "resourceType": "environment",
                            "resourceId": "environment-a",
                            "root": str(outside),
                            "manifestDigest": "digest",
                        },
                    },
                    lambda record: execution_records.verify_cycle_graph_record(
                        release_root,
                        record,
                        resource_type="environment",
                    ),
                ),
                (
                    "analysis",
                    {
                        "analysisId": "analysis-a",
                        "version": "1",
                        "archive": {
                            "resourceType": "analysis",
                            "resourceId": "analysis-a",
                            "root": str(outside),
                            "manifestDigest": "digest",
                        },
                    },
                    lambda record: execution_records.verify_cycle_graph_record(
                        release_root,
                        record,
                        resource_type="analysis",
                    ),
                ),
                (
                    "sampler",
                    {
                        "samplerId": "sampler-a",
                        "version": "1",
                        "archive": {
                            "resourceType": "sampler",
                            "resourceId": "sampler-a",
                            "root": str(outside),
                            "manifestDigest": "digest",
                        },
                    },
                    lambda record: execution_records.verify_sampler_record(
                        release_root,
                        record,
                    ),
                ),
                (
                    "module",
                    {
                        "kind": "Target",
                        "moduleId": "module-a",
                        "version": "1",
                        "archive": {
                            "resourceType": "module",
                            "resourceId": "Target/module-a",
                            "root": str(outside),
                            "manifestDigest": "digest",
                        },
                    },
                    lambda record: execution_records.verify_module_definition_record(
                        release_root,
                        record,
                    ),
                ),
            )
            for label, record, verify in cases:
                with self.subTest(label=label), mock.patch(
                    "engine.archive.version.verify_record"
                ) as verify_record:
                    with self.assertRaisesRegex(
                        ValueError,
                        "outside its managed root",
                    ):
                        verify(record)
                    verify_record.assert_not_called()

    def test_descriptor_identity_is_rejected_before_location_or_content(self):
        record = {
            "analysisId": "analysis-a",
            "version": "1",
            "archive": {
                "resourceType": "analysis",
                "resourceId": "analysis-b",
                "root": "/outside",
                "manifestDigest": "digest",
            },
        }
        with mock.patch(
            "engine.archive.version.verify_record_location"
        ) as verify_location:
            with self.assertRaisesRegex(ValueError, "identity does not match"):
                execution_records.verify_cycle_graph_record(
                    "/release",
                    record,
                    resource_type="analysis",
                )
            verify_location.assert_not_called()

    def test_missing_cycle_identity_is_a_value_error_before_location(self):
        record = {
            "version": "1",
            "archive": {
                "resourceType": "analysis",
                "resourceId": "analysis-a",
                "root": "/outside",
                "manifestDigest": "digest",
            },
        }
        with mock.patch(
            "engine.archive.version.verify_record_location"
        ) as verify_location:
            with self.assertRaisesRegex(ValueError, "analysisId"):
                execution_records.verify_cycle_graph_record(
                    "/release",
                    record,
                    resource_type="analysis",
                    expected_identity="analysis-a",
                    expected_version="1",
                )
            verify_location.assert_not_called()


if __name__ == "__main__":
    unittest.main()
