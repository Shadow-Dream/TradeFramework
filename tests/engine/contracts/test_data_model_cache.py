#!/usr/bin/env python3

import copy
from collections import UserDict
import unittest
from unittest import mock

from engine.contracts import contract_expansion
from engine.contracts import data_model


class NormalizedDataKeySchemaCacheTests(unittest.TestCase):
    @staticmethod
    def schema():
        return {
            "type": "object",
            "properties": {"value": {"type": "number"}},
            "required": ["value"],
            "additionalProperties": False,
        }

    def test_scope_reuses_success_and_returns_detached_trees(self):
        schema = self.schema()
        real_normalize = data_model._normalize_data_key_schema_uncached
        with (
            contract_expansion.contract_expansion_cache_scope(),
            mock.patch.object(
                data_model,
                "_normalize_data_key_schema_uncached",
                wraps=real_normalize,
            ) as normalize_uncached,
        ):
            first = data_model.normalize_data_key_schema(schema, path="first")
            first["properties"]["value"]["type"] = "string"
            second = data_model.normalize_data_key_schema(
                copy.deepcopy(schema),
                path="second",
            )
            self.assertEqual(
                len(data_model._NORMALIZED_DATA_KEY_SCHEMA_CACHE.get()),
                1,
            )

        self.assertEqual(normalize_uncached.call_count, 1)
        self.assertIsNot(first, second)
        self.assertEqual(second, schema)
        self.assertIsNone(data_model._NORMALIZED_DATA_KEY_SCHEMA_CACHE.get())

    def test_scope_is_nested_and_cache_is_operation_local(self):
        with contract_expansion.contract_expansion_cache_scope():
            outer = data_model._NORMALIZED_DATA_KEY_SCHEMA_CACHE.get()
            with contract_expansion.contract_expansion_cache_scope():
                self.assertIs(
                    data_model._NORMALIZED_DATA_KEY_SCHEMA_CACHE.get(),
                    outer,
                )
        self.assertIsNone(data_model._NORMALIZED_DATA_KEY_SCHEMA_CACHE.get())

    def test_outside_scope_always_uses_authoritative_path(self):
        real_normalize = data_model._normalize_data_key_schema_uncached
        with mock.patch.object(
            data_model,
            "_normalize_data_key_schema_uncached",
            wraps=real_normalize,
        ) as normalize_uncached:
            data_model.normalize_data_key_schema(self.schema())
            data_model.normalize_data_key_schema(self.schema())
        self.assertEqual(normalize_uncached.call_count, 2)

    def test_invalid_schema_is_not_cached_and_keeps_each_path(self):
        invalid = {
            "type": "object",
            "properties": {"items": {"type": "array"}},
            "additionalProperties": False,
        }
        with contract_expansion.contract_expansion_cache_scope():
            for path in ("first", "second"):
                with self.assertRaisesRegex(
                    ValueError,
                    rf"DataKey contract '{path}\.items'",
                ):
                    data_model.normalize_data_key_schema(invalid, path=path)
            self.assertEqual(
                len(data_model._NORMALIZED_DATA_KEY_SCHEMA_CACHE.get()),
                0,
            )

    def test_non_exact_and_unencodable_values_use_uncached_path(self):
        class IntegerSubclass(int):
            pass

        values = (
            UserDict(self.schema()),
            {"type": "integer", "const": IntegerSubclass(1)},
            {"type": "integer", "const": 10**100},
            {"type": "string", "const": "\ud800"},
            {"const": {"\ud800": 1}},
        )
        real_normalize = data_model._normalize_data_key_schema_uncached
        with (
            contract_expansion.contract_expansion_cache_scope(),
            mock.patch.object(
                data_model,
                "_normalize_data_key_schema_uncached",
                wraps=real_normalize,
            ) as normalize_uncached,
        ):
            for schema in values:
                for _attempt in range(2):
                    data_model.normalize_data_key_schema(schema)
            self.assertEqual(
                len(data_model._NORMALIZED_DATA_KEY_SCHEMA_CACHE.get()),
                0,
            )
        self.assertEqual(normalize_uncached.call_count, len(values) * 2)

    def test_shared_cycle_and_deep_topologies_use_uncached_path(self):
        shared = {"type": "number"}
        aliased = {
            "anyOf": [shared, shared],
        }
        cyclic = {"type": "object", "additionalProperties": False}
        cyclic["properties"] = {"self": cyclic}
        deep = {"type": "object", "additionalProperties": False}
        current = deep
        for _index in range(70):
            child = {"type": "object", "additionalProperties": False}
            current["properties"] = {"child": child}
            current = child

        real_normalize = data_model._normalize_data_key_schema_uncached
        with (
            contract_expansion.contract_expansion_cache_scope(),
            mock.patch.object(
                data_model,
                "_normalize_data_key_schema_uncached",
                wraps=real_normalize,
            ) as normalize_uncached,
        ):
            data_model.normalize_data_key_schema(aliased)
            with self.assertRaises(RecursionError):
                data_model.normalize_data_key_schema(cyclic)
            data_model.normalize_data_key_schema(deep)
            self.assertEqual(
                len(data_model._NORMALIZED_DATA_KEY_SCHEMA_CACHE.get()),
                0,
            )
        self.assertEqual(normalize_uncached.call_count, 3)

    def test_cache_is_bounded(self):
        with (
            contract_expansion.contract_expansion_cache_scope(),
            mock.patch.object(
                data_model,
                "_NORMALIZED_DATA_KEY_SCHEMA_CACHE_MAX_ENTRIES",
                2,
            ),
        ):
            for index in range(3):
                data_model.normalize_data_key_schema({
                    "type": "object",
                    "properties": {f"value{index}": {"type": "number"}},
                    "additionalProperties": False,
                })
            self.assertEqual(
                len(data_model._NORMALIZED_DATA_KEY_SCHEMA_CACHE.get()),
                2,
            )

    def test_missing_optional_codec_uses_uncached_path(self):
        real_normalize = data_model._normalize_data_key_schema_uncached
        with (
            contract_expansion.contract_expansion_cache_scope(),
            mock.patch.object(data_model, "_orjson", None),
            mock.patch.object(
                data_model,
                "_normalize_data_key_schema_uncached",
                wraps=real_normalize,
            ) as normalize_uncached,
        ):
            data_model.normalize_data_key_schema(self.schema())
            data_model.normalize_data_key_schema(self.schema())
            self.assertEqual(
                len(data_model._NORMALIZED_DATA_KEY_SCHEMA_CACHE.get()),
                0,
            )
        self.assertEqual(normalize_uncached.call_count, 2)


if __name__ == "__main__":
    unittest.main()
