#!/usr/bin/env python3
"""Mutable admission checks for already-frozen Backtest submissions."""

import copy
import unittest
from unittest import mock

from engine.contracts import backtest as backtest_contracts
from engine.service import backtests as backtest_service


def frozen_request():
    raw = {
        "pipeline": {"pipelineId": "pipeline", "version": "1"},
        "datasetId": "dataset",
        "datasetVersionId": "dataset@version",
        "sampler": {"samplerId": "sampler", "version": "1", "parameters": {}},
        "environment": {"environmentId": "environment", "version": "1"},
        "analysis": {"analysisId": "analysis", "version": "1"},
    }
    return {
        **raw,
        "executionSnapshot": {
            "executionInputs": backtest_contracts.backtest_execution_inputs(raw),
        },
    }


class PreparedBacktestAdmissionTests(unittest.TestCase):
    def test_active_exact_resources_pass_without_full_composition_resolution(self):
        frozen = frozen_request()
        with (
            mock.patch.object(
                backtest_service.pipeline_repository,
                "load_pipeline_execution_version",
                return_value=({}, {}),
            ) as pipeline,
            mock.patch.object(
                backtest_service.dataset_repository,
                "get_dataset",
                return_value={"status": "active"},
            ) as dataset,
            mock.patch.object(
                backtest_service,
                "resolve_backtest_composition",
                side_effect=AssertionError("admission recompiled composition"),
            ),
        ):
            result = backtest_service.require_frozen_backtest_admission({}, frozen)
        self.assertIs(result, frozen)
        pipeline.assert_called_once_with({}, "pipeline", "1")
        dataset.assert_called_once_with({}, "dataset")

    def test_inactive_pipeline_and_archived_dataset_fail_closed(self):
        frozen = frozen_request()
        with (
            mock.patch.object(
                backtest_service.pipeline_repository,
                "load_pipeline_execution_version",
                side_effect=ValueError("Pipeline is inactive"),
            ),
            mock.patch.object(
                backtest_service.dataset_repository,
                "get_dataset",
            ) as dataset,
            self.assertRaisesRegex(ValueError, "inactive"),
        ):
            backtest_service.require_frozen_backtest_admission({}, frozen)
        dataset.assert_not_called()

        with (
            mock.patch.object(
                backtest_service.pipeline_repository,
                "load_pipeline_execution_version",
                return_value=({}, {}),
            ),
            mock.patch.object(
                backtest_service.dataset_repository,
                "get_dataset",
                return_value={"status": "archived"},
            ),
            self.assertRaisesRegex(ValueError, "archived and cannot be used"),
        ):
            backtest_service.require_frozen_backtest_admission({}, frozen)

    def test_execution_input_tampering_has_priority_over_repository_reads(self):
        frozen = frozen_request()
        tampered = copy.deepcopy(frozen)
        tampered["sampler"]["parameters"]["extra"] = True
        with (
            mock.patch.object(
                backtest_service.pipeline_repository,
                "load_pipeline_execution_version",
            ) as pipeline,
            self.assertRaisesRegex(ValueError, "execution inputs"),
        ):
            backtest_service.require_frozen_backtest_admission({}, tampered)
        pipeline.assert_not_called()


if __name__ == "__main__":
    unittest.main()
