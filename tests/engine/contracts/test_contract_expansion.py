#!/usr/bin/env python3

import copy
import unittest
from unittest import mock

from engine.contracts import contract_expansion


class ContractExpansionMemoizationTests(unittest.TestCase):
    @staticmethod
    def contracts():
        return {
            "root.value": {"type": "number"},
            "root.label": {"type": "string"},
        }

    def test_equal_content_reuses_compilation_but_returns_detached_trees(self):
        contracts = self.contracts()
        equivalent = copy.deepcopy(contracts)
        real_expand = contract_expansion._expand_contracts_uncached
        with (
            contract_expansion.contract_expansion_cache_scope(),
            mock.patch.object(
                contract_expansion,
                "_expand_contracts_uncached",
                wraps=real_expand,
            ) as compile_uncached,
        ):
            first = contract_expansion.expand_contracts(contracts)
            first["root"]["properties"]["value"]["type"] = "string"
            second = contract_expansion.expand_contracts(equivalent)

        self.assertEqual(compile_uncached.call_count, 1)
        self.assertEqual(
            second["root"]["properties"]["value"]["type"],
            "number",
        )
        self.assertIsNot(first, second)

    def test_input_mutation_compiles_a_new_exact_material(self):
        contracts = self.contracts()
        real_expand = contract_expansion._expand_contracts_uncached
        with (
            contract_expansion.contract_expansion_cache_scope(),
            mock.patch.object(
                contract_expansion,
                "_expand_contracts_uncached",
                wraps=real_expand,
            ) as compile_uncached,
        ):
            first = contract_expansion.expand_contracts(contracts)
            contracts["root.value"] = {"type": "integer"}
            second = contract_expansion.expand_contracts(contracts)

        self.assertEqual(compile_uncached.call_count, 2)
        self.assertEqual(first["root.value"]["type"], "number")
        self.assertEqual(second["root.value"]["type"], "integer")

    def test_scalar_subclass_uses_uncached_path_and_preserves_type(self):
        class IntegerSubclass(int):
            pass

        contracts = {"root": {"const": IntegerSubclass(7)}}
        real_expand = contract_expansion._expand_contracts_uncached
        with (
            contract_expansion.contract_expansion_cache_scope(),
            mock.patch.object(
                contract_expansion,
                "_expand_contracts_uncached",
                wraps=real_expand,
            ) as compile_uncached,
        ):
            first = contract_expansion.expand_contracts(contracts)
            second = contract_expansion.expand_contracts(contracts)

            self.assertEqual(len(contract_expansion._EXPAND_CONTRACT_CACHE.get()), 0)
        self.assertEqual(compile_uncached.call_count, 2)
        self.assertIs(type(first["root"]["const"]), IntegerSubclass)
        self.assertIs(type(second["root"]["const"]), IntegerSubclass)

    def test_shared_container_uses_uncached_path_and_preserves_alias_topology(self):
        shared = {"value": 1}
        contracts = {
            "root": {
                "const": {
                    "left": shared,
                    "right": shared,
                },
            },
        }
        real_expand = contract_expansion._expand_contracts_uncached
        with (
            contract_expansion.contract_expansion_cache_scope(),
            mock.patch.object(
                contract_expansion,
                "_expand_contracts_uncached",
                wraps=real_expand,
            ) as compile_uncached,
        ):
            first = contract_expansion.expand_contracts(contracts)
            second = contract_expansion.expand_contracts(contracts)

            self.assertEqual(len(contract_expansion._EXPAND_CONTRACT_CACHE.get()), 0)
        self.assertEqual(compile_uncached.call_count, 2)
        for expanded in (first, second):
            literal = expanded["root"]["const"]
            self.assertIs(literal["left"], literal["right"])
            self.assertIsNot(literal["left"], shared)

    def test_surrogate_pair_uses_uncached_path_and_preserves_python_string(self):
        pair = "\ud83d\ude00"
        contracts = {"root": {"const": pair}}
        real_expand = contract_expansion._expand_contracts_uncached
        with (
            contract_expansion.contract_expansion_cache_scope(),
            mock.patch.object(
                contract_expansion,
                "_expand_contracts_uncached",
                wraps=real_expand,
            ) as compile_uncached,
        ):
            first = contract_expansion.expand_contracts(contracts)
            second = contract_expansion.expand_contracts(contracts)

            self.assertEqual(len(contract_expansion._EXPAND_CONTRACT_CACHE.get()), 0)
        self.assertEqual(compile_uncached.call_count, 2)
        for expanded in (first, second):
            self.assertEqual(expanded["root"]["const"], pair)
            self.assertEqual(len(expanded["root"]["const"]), 2)

    def test_surrogate_pair_does_not_collide_with_scalar_unicode_cache_entry(self):
        pair = "\ud83d\ude00"
        scalar = "\U0001f600"
        real_expand = contract_expansion._expand_contracts_uncached
        with (
            contract_expansion.contract_expansion_cache_scope(),
            mock.patch.object(
                contract_expansion,
                "_expand_contracts_uncached",
                wraps=real_expand,
            ) as compile_uncached,
        ):
            scalar_first = contract_expansion.expand_contracts({
                "root": {"const": scalar},
            })
            pair_second = contract_expansion.expand_contracts({
                "root": {"const": pair},
            })
            scalar_third = contract_expansion.expand_contracts({
                "root": {"const": scalar},
            })

        self.assertEqual(compile_uncached.call_count, 2)
        self.assertEqual(scalar_first["root"]["const"], scalar)
        self.assertEqual(pair_second["root"]["const"], pair)
        self.assertEqual(scalar_third["root"]["const"], scalar)
        self.assertNotEqual(pair_second["root"]["const"], scalar_third["root"]["const"])

    def test_surrogate_pair_in_object_key_uses_uncached_path(self):
        pair = "\ud83d\ude00"
        contracts = {"root": {"const": {pair: 1}}}
        real_expand = contract_expansion._expand_contracts_uncached
        with (
            contract_expansion.contract_expansion_cache_scope(),
            mock.patch.object(
                contract_expansion,
                "_expand_contracts_uncached",
                wraps=real_expand,
            ) as compile_uncached,
        ):
            first = contract_expansion.expand_contracts(contracts)
            second = contract_expansion.expand_contracts(contracts)

            self.assertEqual(len(contract_expansion._EXPAND_CONTRACT_CACHE.get()), 0)
        self.assertEqual(compile_uncached.call_count, 2)
        self.assertEqual(tuple(first["root"]["const"]), (pair,))
        self.assertEqual(tuple(second["root"]["const"]), (pair,))
        self.assertEqual(len(next(iter(first["root"]["const"]))), 2)
        self.assertEqual(len(next(iter(second["root"]["const"]))), 2)

    def test_cache_value_encoding_failure_returns_original_and_does_not_cache(self):
        contracts = self.contracts()
        expected = contract_expansion._expand_contracts_uncached(contracts)
        original_error = ValueError("original expansion failure")
        real_dumps = contract_expansion.strict_json.dumps

        def fail_cache_value_encoding(value, **kwargs):
            if kwargs.get("sort_keys") is True:
                raise ValueError("cache value encoding failure")
            return real_dumps(value, **kwargs)

        with (
            contract_expansion.contract_expansion_cache_scope(),
            mock.patch.object(
                contract_expansion,
                "_expand_contracts_uncached",
                side_effect=(expected, original_error),
            ) as compile_uncached,
            mock.patch.object(
                contract_expansion.strict_json,
                "dumps",
                side_effect=fail_cache_value_encoding,
            ),
        ):
            first = contract_expansion.expand_contracts(contracts)
            self.assertIs(first, expected)
            self.assertEqual(len(contract_expansion._EXPAND_CONTRACT_CACHE.get()), 0)
            with self.assertRaisesRegex(ValueError, "original expansion failure"):
                contract_expansion.expand_contracts(contracts)
            self.assertEqual(len(contract_expansion._EXPAND_CONTRACT_CACHE.get()), 0)

        self.assertEqual(compile_uncached.call_count, 2)

    def test_invalid_schema_is_never_cached(self):
        invalid = {"root": {"type": "not-a-json-schema-type"}}
        real_expand = contract_expansion._expand_contracts_uncached
        with (
            contract_expansion.contract_expansion_cache_scope(),
            mock.patch.object(
                contract_expansion,
                "_expand_contracts_uncached",
                wraps=real_expand,
            ) as compile_uncached,
        ):
            for _attempt in range(2):
                with self.assertRaises(ValueError):
                    contract_expansion.expand_contracts(copy.deepcopy(invalid))
            self.assertEqual(len(contract_expansion._EXPAND_CONTRACT_CACHE.get()), 0)
        self.assertEqual(compile_uncached.call_count, 2)
        self.assertIsNone(contract_expansion._EXPAND_CONTRACT_CACHE.get())

    def test_cache_is_bounded(self):
        with (
            contract_expansion.contract_expansion_cache_scope(),
            mock.patch.object(
                contract_expansion,
                "_EXPAND_CONTRACT_CACHE_MAX_ENTRIES",
                2,
            ),
        ):
            for index in range(3):
                contract_expansion.expand_contracts({
                    f"root.value{index}": {"type": "number"},
                })
            self.assertEqual(
                len(contract_expansion._EXPAND_CONTRACT_CACHE.get()),
                2,
            )
        self.assertIsNone(contract_expansion._EXPAND_CONTRACT_CACHE.get())


if __name__ == "__main__":
    unittest.main()
