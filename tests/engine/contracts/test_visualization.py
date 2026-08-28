"""Strict saved Visualization aggregate contracts."""

import copy
import unittest

from engine.contracts import visualization as visualization_contracts


class VisualizationAggregateContractTests(unittest.TestCase):
    def setUp(self):
        self.spec = visualization_contracts.default_spec("prices", "UTC")
        self.request = {
            "backtestId": "bt_01K00000000000000000000000",
            "expectedRevision": 0,
            "visualizationId": "current chart",
            "name": "Current",
            "spec": self.spec,
        }
        self.record = {
            "visualizationId": "current-chart",
            "backtestId": self.request["backtestId"],
            "name": "Current",
            "createdAt": "2026-08-11T12:00:00Z",
            "revision": 1,
            "spec": self.spec,
        }

    def spec_with_visualizer_id(self, visualizer_id):
        spec = copy.deepcopy(self.spec)
        spec["panes"].append({
            "id": "price",
            "title": "Price",
            "role": "financial",
            "view": {
                "start": None,
                "end": None,
                "logScale": False,
                "controlsCollapsed": False,
            },
            "visualizers": [{
                "id": visualizer_id,
                "callback": "series.line",
                "params": {},
            }],
            "temporaryModules": [],
        })
        return spec

    def test_save_request_has_exact_fields_and_wire_types(self):
        self.assertIs(
            visualization_contracts.require_save_request(self.request),
            self.request,
        )

        extra = {**self.request, "frontendOnly": True}
        with self.assertRaisesRegex(ValueError, "frontendOnly"):
            visualization_contracts.require_save_request(extra)

        for field, value in (
            ("backtestId", 1),
            ("visualizationId", 1),
            ("name", 1),
            ("expectedRevision", -1),
            ("expectedRevision", True),
        ):
            with self.subTest(field=field):
                invalid = {**self.request, field: value}
                with self.assertRaises(ValueError):
                    visualization_contracts.require_save_request(invalid)

    def test_empty_caller_id_is_rejected(self):
        request = {**self.request, "visualizationId": ""}
        with self.assertRaisesRegex(ValueError, "visualizationId is required"):
            visualization_contracts.require_save_request(request)

    def test_unsafe_runtime_visualizer_ids_are_rejected_before_save(self):
        for visualizer_id in ("__proto__", "constructor", "prototype"):
            with self.subTest(visualizer_id=visualizer_id):
                request = {
                    **self.request,
                    "spec": self.spec_with_visualizer_id(visualizer_id),
                }
                with self.assertRaisesRegex(
                    ValueError,
                    rf"Visualizer identity '{visualizer_id}' is unsafe",
                ):
                    visualization_contracts.require_save_request(request)

    def test_non_reserved_visualizer_ids_remain_valid(self):
        for visualizer_id in (
            "price-line",
            "__proto__.line",
            "Constructor",
            "prototype-1",
        ):
            with self.subTest(visualizer_id=visualizer_id):
                request = {
                    **self.request,
                    "spec": self.spec_with_visualizer_id(visualizer_id),
                }
                self.assertIs(
                    visualization_contracts.require_save_request(request),
                    request,
                )

    def test_record_has_exact_fields_types_and_strict_spec(self):
        self.assertIs(
            visualization_contracts.require_record(self.record),
            self.record,
        )
        for field, value in (
            ("visualizationId", ""),
            ("backtestId", 1),
            ("name", " "),
            ("createdAt", None),
            ("revision", 0),
            ("revision", True),
        ):
            with self.subTest(field=field):
                invalid = {**self.record, field: value}
                with self.assertRaises(ValueError):
                    visualization_contracts.require_record(invalid)

        invalid_spec = copy.deepcopy(self.record)
        invalid_spec["spec"]["legacyRenderer"] = "canvas"
        with self.assertRaisesRegex(ValueError, "legacyRenderer"):
            visualization_contracts.require_record(invalid_spec)


if __name__ == "__main__":
    unittest.main()
