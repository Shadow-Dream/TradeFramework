import unittest

from engine.contracts.protocol import normalize_protocol_id


class PassiveProtocolIdContractTests(unittest.TestCase):
    def test_protocol_id_is_an_opaque_canonical_string(self):
        for value in ("trade.basic-workflow", "vendor.uninstalled-protocol"):
            with self.subTest(value=value):
                self.assertEqual(normalize_protocol_id(value), value)

    def test_protocol_id_rejects_noncanonical_values_without_resolving_ids(self):
        for value in (None, "", " trade.basic-workflow", "trade.basic-workflow ", 1, []):
            with self.subTest(value=value), self.assertRaisesRegex(
                ValueError,
                "canonical non-empty string",
            ):
                normalize_protocol_id(value)


if __name__ == "__main__":
    unittest.main()
