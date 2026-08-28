#!/usr/bin/env python3

import unittest

from engine.contracts import config_override


class ConfigOverrideContractTests(unittest.TestCase):
    def test_module_overrides_deep_merge_only_inner_module_config(self):
        definition = {
            "resourceId": "resource",
            "instances": {
                "module": {
                    "instanceId": "module",
                    "config": {
                        "window": {"length": 14, "mode": "causal"},
                        "enabled": True,
                    },
                }
            },
        }
        effective = config_override.apply_module_config_overrides(
            definition,
            {"module": {"window": {"length": 21}}},
            label="Test",
        )
        self.assertEqual(
            effective["instances"]["module"]["config"],
            {
                "window": {"length": 21, "mode": "causal"},
                "enabled": True,
            },
        )
        self.assertEqual(
            definition["instances"]["module"]["config"]["window"]["length"],
            14,
        )

    def test_pipeline_config_override_is_independent_and_normalized(self):
        definition = {
            "config": {
                "observationInput": {
                    "whitelist": ["price"],
                    "blacklist": [],
                }
            },
            "instances": {"module": {"config": {"period": 2}}},
        }
        effective = config_override.apply_pipeline_config_override(
            definition,
            {
                "observationInput": {
                    "whitelist": ["price.close", "time"],
                }
            },
        )
        self.assertEqual(
            effective["config"],
            {
                "observationInput": {
                    "whitelist": ["price.close", "time"],
                    "blacklist": [],
                }
            },
        )
        self.assertEqual(effective["instances"], definition["instances"])
        self.assertEqual(
            definition["config"]["observationInput"]["whitelist"],
            ["price"],
        )

    def test_overrides_reject_unknown_fields_instances_and_non_object_values(self):
        definition = {"instances": {"known": {"config": {}}}}
        with self.assertRaisesRegex(ValueError, "object keyed by instanceId"):
            config_override.normalize_module_config_overrides(None)
        with self.assertRaisesRegex(ValueError, "unknown instance.*missing"):
            config_override.apply_module_config_overrides(
                definition,
                {"missing": {}},
                label="Test",
            )
        with self.assertRaisesRegex(ValueError, "known must be an object"):
            config_override.apply_module_config_overrides(
                definition,
                {"known": 1},
                label="Test",
            )
        with self.assertRaisesRegex(ValueError, "unsupported field.*unknown"):
            config_override.apply_pipeline_config_override(
                {
                    "config": {
                        "observationInput": {"whitelist": [], "blacklist": []}
                    }
                },
                {"unknown": {}},
            )


if __name__ == "__main__":
    unittest.main()
