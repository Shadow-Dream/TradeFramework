"""Strict saved Visualization aggregate contracts."""

import copy
import unittest

from engine.contracts import visualization as visualization_contracts


class VisualizationAggregateContractTests(unittest.TestCase):
    def setUp(self):
        self.spec = visualization_contracts.default_spec("prices", "UTC")
        self.request = {
            "backtestId": "bt_01K00000000000000000000000",
            "visualizationId": "current chart",
            "name": "Current",
            "spec": self.spec,
        }
        self.record = {
            "visualizationId": "current-chart",
            "backtestId": self.request["backtestId"],
            "name": "Current",
            "createdAt": "2026-08-11T12:00:00Z",
            "spec": self.spec,
        }

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
        ):
            with self.subTest(field=field):
                invalid = {**self.request, field: value}
                with self.assertRaises(ValueError):
                    visualization_contracts.require_save_request(invalid)

    def test_empty_caller_id_is_rejected(self):
        request = {**self.request, "visualizationId": ""}
        with self.assertRaisesRegex(ValueError, "visualizationId is required"):
            visualization_contracts.require_save_request(request)

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
