"""Engine resource identity tests."""

import re
import unittest

from engine.core import resource_ids


class ResourceIdTests(unittest.TestCase):
    def test_ids_are_prefixed_opaque_and_unique(self):
        dataset_ids = {resource_ids.new_resource_id("dataset") for _ in range(100)}
        self.assertEqual(len(dataset_ids), 100)
        self.assertTrue(all(re.fullmatch(r"ds_[0-9A-HJKMNP-TV-Z]{26}", value) for value in dataset_ids))

    def test_resource_kinds_use_distinct_prefixes(self):
        self.assertTrue(resource_ids.new_resource_id("module").startswith("mod_"))
        self.assertTrue(resource_ids.new_resource_id("pipeline").startswith("pipe_"))
        self.assertTrue(resource_ids.new_resource_id("pipeline").startswith("pipe_"))

    def test_generated_identity_detection_rejects_semantic_ids(self):
        value = resource_ids.new_resource_id("workspace")
        self.assertTrue(resource_ids.is_resource_id(value))
        self.assertFalse(resource_ids.is_resource_id("my-workspace"))
        self.assertFalse(resource_ids.is_resource_id("ws_not-a-ulid"))


if __name__ == "__main__":
    unittest.main()
