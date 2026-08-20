"""Visualization service ordering and composition tests."""

import unittest
from unittest import mock

from builtin_implementations.visualizer_contracts import visualizer_definition_map
from engine.contracts import visualization as visualization_contracts
from engine.service import visualizations as visualization_service


class VisualizationServiceTests(unittest.TestCase):
    def setUp(self):
        self.config = {
            "controlRoot": "/unused/control",
            "releaseRoot": "/unused/releases",
            "liveRoot": "/unused/live",
        }
        self.spec = visualization_contracts.default_spec("prices", "UTC")
        self.request = {
            "backtestId": "bt_01K00000000000000000000000",
            "visualizationId": " Current View ",
            "name": " Current chart ",
            "spec": self.spec,
        }
        self.backtest = {
            "backtestId": self.request["backtestId"],
            "datasetId": "prices",
            "dataKeys": {
                "price.close": {"schema": {"type": "number"}},
            },
        }
        self.visualizer_definitions = visualizer_definition_map()

    def test_dataset_mismatch_fails_before_compiler_or_repository_write(self):
        mismatch = {
            **self.request,
            "spec": visualization_contracts.default_spec("other", "UTC"),
        }
        with (
            mock.patch.object(
                visualization_service.result_repository,
                "get_backtest_meta",
                return_value=self.backtest,
            ),
            mock.patch.object(
                visualization_service.module_repository,
                "load_pipeline_definitions",
            ) as load_definitions,
            mock.patch.object(
                visualization_service.visualization_repository,
                "save_visualization",
            ) as save,
        ):
            with self.assertRaisesRegex(ValueError, "does not match"):
                visualization_service.save_visualization(
                    self.config,
                    mismatch,
                    self.visualizer_definitions,
                )
        load_definitions.assert_not_called()
        save.assert_not_called()

    def test_compiler_failure_happens_before_any_repository_write(self):
        with (
            mock.patch.object(
                visualization_service.result_repository,
                "get_backtest_meta",
                return_value=self.backtest,
            ),
            mock.patch.object(
                visualization_service.module_repository,
                "load_pipeline_definitions",
                return_value={},
            ),
            mock.patch.object(
                visualization_service.visualization_compiler,
                "compile_visualization_contracts",
                side_effect=ValueError("contract mismatch"),
            ),
            mock.patch.object(
                visualization_service.visualization_repository,
                "save_visualization",
            ) as save,
        ):
            with self.assertRaisesRegex(ValueError, "contract mismatch"):
                visualization_service.save_visualization(
                    self.config,
                    self.request,
                    self.visualizer_definitions,
                )
        save.assert_not_called()

    def test_success_preserves_caller_id_normalization_and_trimmed_name(self):
        expected_record = {
            "visualizationId": "current-view",
            "backtestId": self.request["backtestId"],
            "name": "Current chart",
            "createdAt": "2026-08-11T12:00:00Z",
            "spec": self.spec,
        }
        with (
            mock.patch.object(
                visualization_service.result_repository,
                "get_backtest_meta",
                return_value=self.backtest,
            ),
            mock.patch.object(
                visualization_service.module_repository,
                "load_pipeline_definitions",
                return_value={},
            ),
            mock.patch.object(
                visualization_service.visualization_compiler,
                "compile_visualization_contracts",
                return_value={},
            ) as compile_contracts,
            mock.patch.object(
                visualization_service.engine_clock,
                "utc_now",
                return_value=expected_record["createdAt"],
            ),
            mock.patch.object(
                visualization_service.visualization_repository,
                "save_visualization",
                return_value=expected_record,
            ) as save,
        ):
            response = visualization_service.save_visualization(
                self.config,
                self.request,
                self.visualizer_definitions,
            )
        compile_contracts.assert_called_once_with(
            self.backtest["dataKeys"],
            self.spec,
            {},
            self.visualizer_definitions,
        )
        save.assert_called_once_with(self.config, expected_record)
        self.assertEqual(
            response,
            {"accepted": True, "visualization": expected_record},
        )

    def test_empty_caller_id_is_rejected_before_lookup_or_write(self):
        request = {**self.request, "visualizationId": ""}
        with (
            mock.patch.object(
                visualization_service.result_repository,
                "get_backtest_meta",
            ) as get_backtest,
            mock.patch.object(
                visualization_service.visualization_repository,
                "save_visualization",
            ) as save,
        ):
            with self.assertRaisesRegex(ValueError, "visualizationId is required"):
                visualization_service.save_visualization(
                    self.config,
                    request,
                    self.visualizer_definitions,
                )
        get_backtest.assert_not_called()
        save.assert_not_called()


if __name__ == "__main__":
    unittest.main()
