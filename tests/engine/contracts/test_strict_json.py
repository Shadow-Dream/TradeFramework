"""Strict JSON transport tests."""

import io
import json
import math
import sys
import unittest
from unittest import mock

import engine_service
from engine.contracts import strict_json
from strategy_devkit import module_sdk


def _reference_validate(value, *, path="value"):
    """The strict encoder validation used before the exact-JSON fast path."""
    if value is None or isinstance(value, (str, bool)):
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{path} must contain only finite JSON numbers.")
        return value
    if type(value) is list:
        for index, item in enumerate(value):
            _reference_validate(item, path=f"{path}[{index}]")
        return value
    if type(value) is dict:
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError(f"{path} object keys must be strings.")
            _reference_validate(
                item,
                path=f"{path}.{key}" if key else f"{path}.<empty>",
            )
        return value
    raise ValueError(
        f"{path} contains a non-JSON value of type {type(value).__name__}."
    )


def _reference_dumps(value, **kwargs):
    _reference_validate(value)
    return json.dumps(value, allow_nan=False, **kwargs)


def _call_outcome(function, value, kwargs):
    try:
        return "value", function(value, **kwargs)
    except BaseException as exc:
        return "error", type(exc), str(exc)


def _exact_equal_outcome(function, left, right):
    try:
        return "value", function(left, right)
    except BaseException as exc:
        return "error", type(exc), str(exc)


def _reference_exact_equal(left, right):
    return _reference_dumps(
        left, sort_keys=True, separators=(",", ":")
    ) == _reference_dumps(
        right, sort_keys=True, separators=(",", ":")
    )


class StrictJsonBoundaryTests(unittest.TestCase):
    def test_exact_json_alias_proof_rejects_shared_and_cyclic_containers(self):
        shared = []
        self.assertTrue(strict_json.is_exact_json({"left": shared, "right": shared}))
        self.assertFalse(
            strict_json.is_exact_json(
                {"left": shared, "right": shared},
                reject_aliases=True,
            )
        )
        cycle = []
        cycle.append(cycle)
        self.assertFalse(
            strict_json.is_exact_json(cycle, reject_aliases=True)
        )
        self.assertFalse(
            strict_json.is_exact_json({1: "invalid"}, reject_aliases=True)
        )

    def test_exact_equal_preserves_canonical_value_and_error_semantics(self):
        class DictSubclass(dict):
            pass

        shared = {"value": [1, 2.5, None]}
        circular = []
        circular.append(circular)
        pairs = (
            ({"a": 1, "b": [2]}, {"b": [2], "a": 1}),
            ([1, 2], [2, 1]),
            (True, 1),
            (1, 1.0),
            (-0.0, 0.0),
            ({"text": "data\n\u6570\u636e"}, {"text": "data\n\u6570\u636e"}),
            (2 ** 100, 2 ** 100),
            ({"left": shared, "right": shared}, {
                "left": {"value": [1, 2.5, None]},
                "right": {"value": [1, 2.5, None]},
            }),
            ({1: "invalid"}, {}),
            ({}, {1: "invalid"}),
            (DictSubclass({"value": 1}), {"value": 1}),
            ({"value": float("nan")}, {"value": float("nan")}),
            ({"value": (1, 2)}, {"value": [1, 2]}),
            (circular, circular),
        )
        for index, (left, right) in enumerate(pairs):
            with self.subTest(index=index):
                self.assertEqual(
                    _exact_equal_outcome(_reference_exact_equal, left, right),
                    _exact_equal_outcome(strict_json.exact_equal, left, right),
                )

    def test_exact_equal_uses_optional_fast_path_and_strict_fallback(self):
        if strict_json._orjson is None:
            self.skipTest("orjson is not installed")
        with mock.patch.object(
            strict_json,
            "dumps",
            wraps=strict_json.dumps,
        ) as strict_encoder:
            self.assertTrue(strict_json.exact_equal(
                {"unicode": "\u6570\u636e", "number": -0.0},
                {"number": -0.0, "unicode": "\u6570\u636e"},
            ))
        strict_encoder.assert_not_called()

        for value in (2 ** 100, "\ud800"):
            with self.subTest(value=repr(value)), mock.patch.object(
                strict_json,
                "dumps",
                wraps=strict_json.dumps,
            ) as strict_encoder:
                self.assertTrue(strict_json.exact_equal(value, value))
            self.assertEqual(strict_encoder.call_count, 2)

        with mock.patch.object(strict_json, "_orjson", None), mock.patch.object(
            strict_json,
            "dumps",
            wraps=strict_json.dumps,
        ) as strict_encoder:
            self.assertTrue(strict_json.exact_equal({"value": 1}, {"value": 1}))
        self.assertEqual(strict_encoder.call_count, 2)

    def test_decoder_rejects_duplicate_keys_constants_and_numeric_overflow(self):
        for payload in (
            '{"x":1,"x":2}',
            '{"x":NaN}',
            '{"x":Infinity}',
            '{"x":1e400}',
            '{"x":-1e400}',
        ):
            with self.subTest(payload=payload):
                with self.assertRaises(ValueError):
                    strict_json.loads(payload)
                with self.assertRaises(ValueError):
                    module_sdk._PROTOCOL_DECODER(payload)

    def test_encoder_rejects_non_string_keys_and_container_subclasses(self):
        class HiddenDict(dict):
            def items(self):
                return {}.items()

        class HiddenList(list):
            def __iter__(self):
                return iter(())

        for value in (
            {1: "value"},
            {"nested": {1: "value"}},
            HiddenDict({1: "hidden"}),
            HiddenList([float("nan")]),
        ):
            with self.subTest(value_type=type(value).__name__):
                with self.assertRaises(ValueError):
                    strict_json.dumps(value)
                with self.assertRaises(ValueError):
                    module_sdk._PROTOCOL_ENCODER(value)

    def test_encoder_fast_path_is_byte_and_error_equivalent_to_reference(self):
        class DictSubclass(dict):
            pass

        class ListSubclass(list):
            pass

        class StringSubclass(str):
            pass

        class IntegerSubclass(int):
            pass

        class FloatSubclass(float):
            pass

        shared = {"value": [1, 2.5, None]}
        circular_list = []
        circular_list.append(circular_list)
        circular_dict = {}
        circular_dict["self"] = circular_dict
        near_deep = 0
        for _index in range(sys.getrecursionlimit() - 20):
            near_deep = [near_deep]
        deep = 0
        for _index in range(sys.getrecursionlimit() + 50):
            deep = [deep]

        values = (
            None,
            True,
            False,
            0,
            -17,
            2 ** 100,
            -0.0,
            1e-7,
            1e20,
            "data\n\u6570\u636e",
            StringSubclass("subclass"),
            IntegerSubclass(4),
            FloatSubclass(1.5),
            FloatSubclass(float("inf")),
            [],
            {},
            {"unicode": "\u6570\u636e", "nested": [1, {"": False}]},
            {StringSubclass("key"): "string-subclass key"},
            {"left": shared, "right": shared},
            float("nan"),
            float("inf"),
            float("-inf"),
            {"nested": [{"number": float("nan")}]},
            {1: "non-string key"},
            {"tuple": (1, 2)},
            {"set": {1, 2}},
            DictSubclass({"hidden": 1}),
            ListSubclass([1]),
            circular_list,
            circular_dict,
            near_deep,
            deep,
        )
        options = (
            {},
            {"sort_keys": True},
            {"sort_keys": True, "separators": (",", ":")},
            {"ensure_ascii": False},
            {"indent": 2},
            {"check_circular": True},
            {"check_circular": False},
            {"check_circular": None},
            {"skipkeys": True},
            {"allow_nan": True},
        )
        for value_index, value in enumerate(values):
            for option_index, option in enumerate(options):
                with self.subTest(
                    value_index=value_index,
                    option_index=option_index,
                ):
                    self.assertEqual(
                        _call_outcome(_reference_dumps, value, option),
                        _call_outcome(strict_json.dumps, value, option),
                    )

    def test_exact_json_encoder_uses_fast_validation_without_diagnostic(self):
        value = {
            "boolean": True,
            "integer": 3,
            "number": 2.5,
            "nothing": None,
            "nested": [{"text": "\u6570\u636e"}],
        }
        with mock.patch.object(
            strict_json,
            "validate",
            wraps=strict_json.validate,
        ) as detailed:
            strict_json.dumps(value)
        detailed.assert_not_called()

    def test_encoder_defaults_circular_check_without_overriding_caller(self):
        omitted = object()
        for explicit, expected in (
            (omitted, False),
            (None, None),
            (True, True),
            (False, False),
        ):
            kwargs = {} if explicit is omitted else {"check_circular": explicit}
            label = "omitted" if explicit is omitted else explicit
            with self.subTest(explicit=label), mock.patch.object(
                strict_json.json,
                "dumps",
                return_value="encoded",
            ) as encoder:
                self.assertEqual(strict_json.dumps({"value": 1}, **kwargs), "encoded")
            encoder.assert_called_once_with(
                {"value": 1},
                allow_nan=False,
                check_circular=expected,
            )

    def test_custom_encoder_keeps_the_standard_circular_default(self):
        observed = []

        class ObservingEncoder(json.JSONEncoder):
            def __init__(self, *args, **kwargs):
                observed.append(kwargs["check_circular"])
                super().__init__(*args, **kwargs)

        actual = strict_json.dumps({"value": 1}, cls=ObservingEncoder)
        expected = _reference_dumps({"value": 1}, cls=ObservingEncoder)
        self.assertEqual(actual, expected)
        self.assertEqual(observed, [True, True])

    def test_http_request_boundary_uses_the_strict_decoder(self):
        class Handler:
            headers = {"Content-Length": str(len(b'{"x":1e400}'))}
            rfile = io.BytesIO(b'{"x":1e400}')

        with self.assertRaises(ValueError):
            engine_service.read_request_json(Handler())


if __name__ == "__main__":
    unittest.main()
