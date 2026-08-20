#!/usr/bin/env python3

import tempfile

from engine.authority.dataset import verify_dataset_storage_authority
from engine.authority.sampler import verify_sampler_runtime_bundle_authority
from engine.composition import backtest as backtest_composition
from engine.repository import datasets
from engine.runtime.dataset import create_dataset_handle
from engine.runtime.sampler import (
    RowMappingSampler,
    SamplerRuntime,
    create_verified_sampler_runtime,
)
from tests.support.backtest_runtime import BacktestIntegrationTestCase


class SamplerBacktestIntegrationTests(BacktestIntegrationTestCase):
    def test_sampler_controls_causal_visibility(self):
        version = datasets.verify_dataset_version(
            self.config,
            datasets.ensure_dataset_version(self.config, "prices"),
        )
        dataset_authority = verify_dataset_storage_authority(
            dataset_id=version["datasetId"],
            dataset_version_id=version["datasetVersionId"],
            storage=version["storage"],
            content_hash=version["contentHash"],
            capabilities=version["capabilities"],
            manifest=version["manifest"],
        )
        dataset = create_dataset_handle(dataset_authority)
        with tempfile.TemporaryDirectory() as execution_root:
            sampler_authority = (
                verify_sampler_runtime_bundle_authority(
                    self.row_sampler
                )
            )
            sampler = create_verified_sampler_runtime(
                sampler_authority,
                dataset,
                source_schema=backtest_composition.dataset_field_schema(version),
                execution_root=execution_root,
            )
            self.assertIsInstance(sampler, SamplerRuntime)
            self.assertEqual(sampler._state, "ready")
            self.assertEqual(len(sampler), 3)
            self.assertEqual(sampler._state, "ready")
            samples = list(sampler)
            sampler.close()
        self.assertEqual(
            [sample.data for sample in samples],
            [
                {"price": {"close": 10}},
                {"price": {"close": 12}},
                {"price": {"close": 13}},
            ],
        )
        self.assertEqual(
            [sample.decision_time for sample in samples],
            [record.available_at for record in dataset.records()],
        )
        self.assertFalse(hasattr(RowMappingSampler, "bind"))
        self.assertFalse(hasattr(RowMappingSampler, "sample"))
        with self.assertRaisesRegex(TypeError, "verified Sampler runtime bundle"):
            RowMappingSampler()
