#!/usr/bin/env python3

import hashlib

from builtin_implementations.visualizer_contracts import visualizer_definition_map
from engine.archive import backtest_result as backtest_result_archive
from engine.archive import dataset as dataset_archive
from engine.contracts import dataset as dataset_contracts
from engine.contracts import visualization as visualization_contracts
from engine.repository import backtest_results as result_repository
from engine.repository import datasets
from engine.service import visualizations as visualization_service
from tests.support.backtest_runtime import BacktestIntegrationTestCase

class ResultVisualizationIntegrationTests(BacktestIntegrationTestCase):
    def test_default_visualization_uses_the_pinned_dataset_capability(self):
        version = datasets.ensure_dataset_version(self.config, "prices")
        self.assertEqual(
            visualization_contracts.default_spec(
                version["datasetId"],
                dataset_archive.visualization_time_zone(version["capabilities"]),
            ),
            {
                "schemaVersion": 3,
                "datasetId": "prices",
                "timeZone": "UTC",
                "panes": [],
            },
        )
        self.assertEqual(
            dataset_archive.visualization_time_zone(version["capabilities"]),
            "UTC",
        )

        with self.assertRaisesRegex(ValueError, "must declare the visualization capability"):
            dataset_archive.visualization_time_zone({})
        for capability, error in (
            (
                {
                    "protocol": "trade.dataset.visualization/unknown",
                    "descriptor": {"timeZone": "UTC"},
                },
                "contract is invalid",
            ),
            (
                {
                    "protocol": dataset_contracts.VISUALIZATION_PROTOCOL,
                    "descriptor": {"timeZone": "Not/A_Real_Zone"},
                },
                "valid IANA time zone",
            ),
            (
                {
                    "protocol": dataset_contracts.VISUALIZATION_PROTOCOL,
                    "descriptor": {"timeZone": "UTC", "fallback": "America/New_York"},
                },
                "contract is invalid",
            ),
        ):
            with self.subTest(capability=capability), self.assertRaisesRegex(ValueError, error):
                dataset_archive.normalize_capabilities({
                    dataset_contracts.VISUALIZATION_CAPABILITY: capability,
                })

    def test_saved_visualization_does_not_mutate_sealed_result_evidence(self):
        completed = self.fixture.run_minimal_backtest(
            "visualization-evidence-pipeline"
        )
        backtest_id = completed["backtestId"]
        result_directory = (
            backtest_result_archive.archive_root(self.config["releaseRoot"])
            / backtest_id
        )
        manifest_path = result_directory / "result-manifest.json"
        manifest_before = manifest_path.read_bytes()
        manifest_digest_before = hashlib.sha256(manifest_before).hexdigest()
        evidence_before = result_repository.load_result_archive_evidence(
            self.config,
            backtest_id,
            verify_digest=True,
        )
        sealed_default = evidence_before["manifest"]["catalog"]["visualization"]

        saved_spec = {
            **sealed_default,
            "panes": [{
                "id": "empty",
                "title": "Saved empty pane",
                "role": "financial",
                "view": {
                    "start": None,
                    "end": None,
                    "logScale": False,
                    "controlsCollapsed": False,
                },
                "visualizers": [],
                "temporaryModules": [],
            }],
        }
        response = visualization_service.save_visualization(
            self.config,
            {
                "backtestId": backtest_id,
                "visualizationId": f"{backtest_id}-current",
                "name": "Current",
                "spec": saved_spec,
            },
            visualizer_definition_map(),
        )

        evidence_after = result_repository.load_result_archive_evidence(
            self.config,
            backtest_id,
            verify_digest=True,
        )
        manifest_after = manifest_path.read_bytes()
        current = result_repository.get_backtest_meta(self.config, backtest_id)
        self.assertEqual(response["visualization"]["spec"], saved_spec)
        self.assertEqual(current["visualization"], saved_spec)
        self.assertEqual(manifest_after, manifest_before)
        self.assertEqual(
            hashlib.sha256(manifest_after).hexdigest(),
            manifest_digest_before,
        )
        self.assertEqual(
            evidence_after["contentDigest"],
            evidence_before["contentDigest"],
        )
        self.assertEqual(
            evidence_after["manifest"]["catalog"]["visualization"],
            sealed_default,
        )
