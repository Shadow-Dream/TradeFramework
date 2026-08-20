import unittest
from collections import UserDict
from unittest import mock

from engine.contracts import data_model as data_model_contracts
from engine.contracts.data import (
    compile_declared_data_json_proof,
    compile_declared_data_json_validator,
    compile_data_json_validator,
    compile_normalized_json_isolator,
    compile_normalized_json_validator,
    validate_data_json,
)
from engine.contracts.contract_expansion import (
    contract_path_required,
    merge_object_schemas,
    resolve_contract_path,
)
from engine.contracts.data_compatibility import schemas_compatible
from engine.contracts.data_model import (
    normalize_data_key_schema,
    normalize_schema,
    validate_json_value,
    validate_normalized_json_value,
)


class PipelineSchemaContractTests(unittest.TestCase):
    def test_declared_entry_validator_checks_only_its_projected_roots(self):
        validator = compile_declared_data_json_validator(
            {
                "required": {"type": "number"},
                "optional": {"type": "string"},
            },
            required_roots={"required"},
            contracts_expanded=True,
            path="Entry",
        )
        validator({
            "required": 1,
            "unrelated": {"nonJsonValue": object()},
        })
        with self.assertRaisesRegex(ValueError, "missing required root.*required"):
            validator({"unrelated": object()})
        with self.assertRaisesRegex(ValueError, "Entry.required.*expected number"):
            validator({"required": "wrong"})
        with self.assertRaisesRegex(ValueError, "Entry.optional.*expected string"):
            validator({"required": 1, "optional": 2})

    def test_declared_entry_presence_errors_precede_all_value_errors(self):
        presence, values = compile_declared_data_json_proof(
            {
                "a": {"type": "number"},
                "z": {"type": "string"},
            },
            required_roots={"a", "z"},
            contracts_expanded=True,
            path="Entry",
        )
        with self.assertRaisesRegex(ValueError, "z"):
            presence({"a": "wrong"})
        # The schema phase runs only after the caller has resolved presence for
        # every source participating in the entry boundary.
        with self.assertRaisesRegex(ValueError, "Entry.a.*expected number"):
            values({"a": "wrong", "z": "ok"})

    def test_optional_causal_root_is_strict_when_present(self):
        _presence, values = compile_declared_data_json_proof(
            {
                "last": {
                    "type": "object",
                    "properties": {"market": {"type": "number"}},
                    "required": ["market"],
                    "additionalProperties": False,
                },
            },
            required_roots=(),
            contracts_expanded=True,
            path="Cycle Graph input",
            boundary_paths={"last.market"},
            conditional_required_paths={"last.market"},
        )
        values({})
        with self.assertRaisesRegex(ValueError, "last.market"):
            _presence({"last": {}})
        with self.assertRaisesRegex(ValueError, "last.market.*expected number"):
            values({"last": {"market": "wrong"}})
        _presence({"last": {"market": 1}})
        values({"last": {"market": 1}})

    def test_typed_object_map_compatibility_checks_additional_values_and_named_collisions(self):
        numeric_map = {
            "type": "object",
            "properties": {},
            "additionalProperties": {"type": "number"},
        }
        string_map = {
            "type": "object",
            "properties": {},
            "additionalProperties": {"type": "string"},
        }
        named_string = {
            "type": "object",
            "properties": {"x": {"type": "string"}},
            "additionalProperties": {"type": "number"},
        }
        self.assertFalse(schemas_compatible(numeric_map, string_map))
        self.assertFalse(schemas_compatible(numeric_map, named_string))
        for left, right in ((numeric_map, string_map), (string_map, numeric_map)):
            with self.assertRaisesRegex(ValueError, "conflicting schemas"):
                merge_object_schemas(left, right, "values")

    def test_typed_map_child_resolution_and_required_presence_are_structural(self):
        number = {"type": "number"}
        optional_map = {
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": number,
        }
        required_map = {
            **optional_map,
            "required": ["a"],
        }
        missing = object()
        self.assertEqual(
            resolve_contract_path({"x": optional_map}, "x.a", missing),
            number,
        )
        self.assertFalse(
            contract_path_required(
                {"x": optional_map}, "x.a", required_roots={"x"}
            )
        )
        self.assertTrue(
            contract_path_required(
                {"x": required_map}, "x.a", required_roots={"x"}
            )
        )
        required_child = {
            "type": "object",
            "properties": {"a": number},
            "required": ["a"],
            "additionalProperties": True,
        }
        self.assertTrue(schemas_compatible(required_map, required_child))

    def test_object_schema_merge_is_commutative_or_fails_explicitly(self):
        closed = {
            "type": "object",
            "properties": {"x": {"type": "number"}},
            "required": ["x"],
            "additionalProperties": False,
        }
        numeric_map = {
            "type": "object",
            "properties": {},
            "additionalProperties": {"type": "number"},
        }
        expected = merge_object_schemas(closed, numeric_map, "values")
        self.assertEqual(
            expected,
            merge_object_schemas(numeric_map, closed, "values"),
        )
        self.assertEqual(expected["additionalProperties"], {"type": "number"})
        nullable = {**closed, "type": ["object", "null"]}
        self.assertEqual(
            merge_object_schemas(closed, nullable, "values"),
            merge_object_schemas(nullable, closed, "values"),
        )

    def test_one_of_compatibility_is_reflexive_and_rejects_overlapping_consumers(self):
        overlapping = {"oneOf": [{"type": "number"}, {"type": "integer"}]}
        self.assertTrue(schemas_compatible(overlapping, overlapping))
        self.assertFalse(schemas_compatible({"type": "integer"}, overlapping))
        partially_overlapping = {
            "oneOf": [
                {"type": "number"},
                {"type": "integer", "enum": [1]},
            ]
        }
        self.assertFalse(
            schemas_compatible({"type": "integer"}, partially_overlapping)
        )
        for schema in (
            {"type": "number"},
            {"type": "object", "properties": {}, "additionalProperties": False},
            {"anyOf": [{"type": "string"}, {"type": "null"}]},
            {"allOf": [{"type": "number"}, {"enum": [1]}]},
            {"type": "string", "const": "x"},
        ):
            with self.subTest(schema=schema):
                self.assertTrue(schemas_compatible(schema, schema))

    def test_composition_keywords_intersect_with_sibling_constraints(self):
        source = {"type": "integer"}
        impossible_integer_branch = {
            "type": "string",
            "anyOf": [
                {"type": "integer"},
                {"const": "x"},
            ],
        }
        self.assertFalse(schemas_compatible(source, impossible_integer_branch))
        self.assertTrue(
            schemas_compatible(
                {
                    "type": "string",
                    "anyOf": [{"type": "integer"}, {"const": "x"}],
                },
                {"const": "x"},
            )
        )
        self.assertTrue(
            schemas_compatible(
                {"type": "string", "const": "x"},
                impossible_integer_branch,
            )
        )
        self.assertFalse(schemas_compatible({}, {"type": "integer"}))
        self.assertTrue(schemas_compatible({"type": "integer"}, {}))

    def test_json_literals_do_not_apply_python_boolean_numeric_equality(self):
        self.assertFalse(schemas_compatible({"const": False}, {"const": 0}))
        self.assertFalse(schemas_compatible({"const": 0}, {"const": False}))
        with self.assertRaisesRegex(ValueError, "outside its declared schema"):
            normalize_schema({"type": "integer", "const": False})
        self.assertEqual(
            normalize_schema({"enum": [False, 0]}),
            {"enum": [False, 0]},
        )
        for schema, accepted, rejected in (
            ({"const": 0}, 0, False),
            ({"enum": [0]}, 0, False),
            ({"const": 0}, 0, 0.0),
            ({"enum": [0]}, 0, 0.0),
            ({"const": False}, False, 0),
            ({"enum": [False]}, False, 0),
        ):
            normalized = normalize_schema(schema)
            validate_normalized_json_value(accepted, normalized)
            compile_normalized_json_validator(normalized)(accepted)
            with self.assertRaises(ValueError):
                validate_normalized_json_value(rejected, normalized)
            with self.assertRaises(ValueError):
                compile_normalized_json_validator(normalized)(rejected)

    def test_schema_literals_must_match_their_declared_type(self):
        for schema in (
            {"type": "string", "enum": [1]},
            {"type": "string", "const": 1},
            {"type": "number", "enum": [float("nan")]},
        ):
            with self.subTest(schema=schema):
                with self.assertRaisesRegex(ValueError, "outside its declared schema"):
                    normalize_schema(schema)

    def test_runtime_json_contract_rejects_nonstandard_mapping_containers(self):
        schema = {
            "type": "object",
            "properties": {"value": {"type": "number"}},
            "required": ["value"],
            "additionalProperties": False,
        }
        with self.assertRaisesRegex(ValueError, "non-JSON"):
            validate_normalized_json_value(UserDict({"value": 1}), schema)

    def test_complete_data_dict_contract_rejects_undeclared_root_keys(self):
        contracts = {"price.value": {"type": "number"}}
        for validator in (
            lambda value: validate_data_json(value, contracts),
            compile_data_json_validator(contracts),
        ):
            with self.assertRaisesRegex(ValueError, "unexpected.*not declared"):
                validator({"price": {"value": 1}, "unexpected": {"value": 2}})

    def test_compiled_data_dict_validator_is_exactly_equivalent(self):
        contracts = {
            "market.value": {"type": "number"},
            "market.label": {"type": "string"},
        }
        cases = (
            {"market": {"value": 1.0, "label": "ok"}},
            {"market": {"value": "wrong", "label": "ok"}},
            {"market": {"value": 1.0}},
            {"unexpected": {"value": float("nan")}},
        )
        compiled = compile_data_json_validator(
            contracts,
            required_paths=("market.value", "market.label"),
        )
        for value in cases:
            with self.subTest(value=value):
                expected = None
                actual = None
                try:
                    validate_data_json(
                        value,
                        contracts,
                        required_paths=("market.value", "market.label"),
                    )
                except ValueError as exc:
                    expected = str(exc)
                try:
                    compiled(value)
                except ValueError as exc:
                    actual = str(exc)
                self.assertEqual(actual, expected)

    def test_compiled_validator_is_exactly_equivalent(self):
        cases = [
            ({}, {"nested": {"value": float("nan")}}),
            ({"type": "number"}, 3.5),
            ({"type": "number"}, "wrong"),
            ({"anyOf": [{"type": "number"}, {"type": "string"}]}, None),
            ({"oneOf": [{"type": "number"}, {"type": "integer"}]}, 1),
            ({
                "type": "object",
                "properties": {"value": {"type": "number"}},
                "required": ["value"],
                "additionalProperties": False,
            }, {}),
            ({
                "type": "object",
                "properties": {},
                "additionalProperties": {"type": "number"},
            }, {"value": "wrong"}),
            ({
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {"value": {"type": "number"}},
                    "required": ["value"],
                    "additionalProperties": False,
                },
            }, [{"value": 1}, {"value": "wrong"}]),
        ]
        for schema, value in cases:
            normalized = normalize_schema(schema)
            for trusted_json in (False, True):
                with self.subTest(schema=schema, value=value, trusted=trusted_json):
                    expected = None
                    actual = None
                    try:
                        validate_normalized_json_value(
                            value,
                            normalized,
                            path="root",
                            trusted_json=trusted_json,
                        )
                    except ValueError as exc:
                        expected = str(exc)
                    try:
                        compile_normalized_json_validator(
                            normalized,
                            path="root",
                            trusted_json=trusted_json,
                        )(value)
                    except ValueError as exc:
                        actual = str(exc)
                    self.assertEqual(actual, expected)

                    isolated_error = None
                    isolated = None
                    try:
                        isolated = compile_normalized_json_isolator(
                            normalized,
                            path="root",
                            trusted_json=trusted_json,
                        )(value)
                    except ValueError as exc:
                        isolated_error = str(exc)
                    self.assertEqual(isolated_error, expected)
                    if expected is None:
                        self.assertEqual(isolated, value)

    def test_specialized_validator_preserves_contracts_without_isolating_values(self):
        leaf = {
            "type": "object",
            "properties": {
                "count": {"type": "number"},
                "kind": {"type": "string", "const": "leaf.v1"},
            },
            "required": ["count", "kind"],
            "additionalProperties": False,
        }
        schema = normalize_schema({
            "type": "object",
            "properties": {
                "left": leaf,
                "right": leaf,
                "rows": {"type": "array", "items": leaf},
                "values": {
                    "type": "object",
                    "properties": {},
                    "additionalProperties": {"type": "number"},
                },
                "metadata": {},
                "nullable": {"type": ["number", "null"]},
            },
            "required": [
                "left", "right", "rows", "values", "metadata", "nullable",
            ],
            "additionalProperties": False,
        })
        shared = {"kind": "leaf.v1", "count": 1}
        metadata = {"nested": [1, {"ok": True}]}
        source = {
            "values": {"second": 2.5, "first": 1},
            "right": shared,
            "rows": [shared],
            "metadata": metadata,
            "nullable": None,
            "left": shared,
        }
        with mock.patch.object(
            data_model_contracts,
            "compiled_validation_failure",
            wraps=data_model_contracts.compiled_validation_failure,
        ) as complete_validation:
            validator = compile_normalized_json_validator(schema, path="root")
            self.assertIsNone(validator(source))
            self.assertEqual(complete_validation.call_count, 0)

        self.assertIs(source["left"], shared)
        self.assertIs(source["right"], shared)
        self.assertIs(source["rows"][0], shared)
        self.assertIs(source["metadata"], metadata)

        literal = {"kind": "fixed", "values": [1, 2]}
        literal_schema = normalize_schema({
            "type": "object",
            "properties": {
                "kind": {"type": "string"},
                "values": {
                    "type": "array",
                    "items": {"type": "integer"},
                },
            },
            "required": ["kind", "values"],
            "additionalProperties": False,
            "const": literal,
        })
        with mock.patch.object(
            data_model_contracts,
            "compiled_validation_failure",
            wraps=data_model_contracts.compiled_validation_failure,
        ) as complete_validation:
            literal_validator = compile_normalized_json_validator(
                literal_schema,
                path="literal",
            )
            self.assertIsNone(literal_validator(literal))
            self.assertEqual(complete_validation.call_count, 0)
            with self.assertRaisesRegex(ValueError, "schema const"):
                literal_validator({"kind": "fixed", "values": [1, 3]})
            self.assertGreater(complete_validation.call_count, 0)

        invalid_values = (
            {**source, "left": {"kind": "leaf.v1"}},
            {**source, "extra": 1},
            {**source, "right": {"count": 1, "kind": "wrong"}},
            {**source, "rows": [{"count": float("inf"), "kind": "leaf.v1"}]},
            {**source, "values": {"bad": "wrong"}},
            {**source, "values": {1: 2}},
            {**source, "metadata": {"nested": [float("nan")]}},
            {**source, "nullable": "wrong"},
        )
        for value in invalid_values:
            with self.subTest(value=value):
                expected = None
                actual = None
                try:
                    validate_normalized_json_value(value, schema, path="root")
                except ValueError as exc:
                    expected = str(exc)
                with mock.patch.object(
                    data_model_contracts,
                    "compiled_validation_failure",
                    wraps=data_model_contracts.compiled_validation_failure,
                ) as complete_validation:
                    validator = compile_normalized_json_validator(
                        schema,
                        path="root",
                    )
                    try:
                        validator(value)
                    except ValueError as exc:
                        actual = str(exc)
                    self.assertGreater(complete_validation.call_count, 0)
                self.assertIsNotNone(expected)
                self.assertEqual(actual, expected)

    def test_specialized_validator_keeps_complex_and_trusted_plans_on_complete_path(self):
        cases = (
            (
                normalize_schema({
                    "anyOf": [{"type": "number"}, {"type": "string"}],
                }),
                1.5,
                False,
            ),
            (normalize_schema({"type": "string", "enum": ["ok"]}), "ok", False),
            (normalize_schema({}), {"unchecked": float("nan")}, True),
            (normalize_schema({"type": "number"}), 1.5, True),
        )
        for schema, value, trusted_json in cases:
            with self.subTest(schema=schema, trusted_json=trusted_json):
                with mock.patch.object(
                    data_model_contracts,
                    "compiled_validation_failure",
                    wraps=data_model_contracts.compiled_validation_failure,
                ) as complete_validation:
                    validator = compile_normalized_json_validator(
                        schema,
                        path="root",
                        trusted_json=trusted_json,
                    )
                    self.assertIsNone(validator(value))
                    self.assertGreater(complete_validation.call_count, 0)

    def test_specialized_isolator_preserves_contract_errors_order_and_aliases(self):
        leaf = {
            "type": "object",
            "properties": {
                "count": {"type": "number"},
                "kind": {"type": "string", "const": "leaf.v1"},
            },
            "required": ["count", "kind"],
            "additionalProperties": False,
        }
        schema = normalize_schema({
            "type": "object",
            "properties": {
                "left": leaf,
                "right": leaf,
                "rows": {"type": "array", "items": leaf},
                "values": {
                    "type": "object",
                    "properties": {},
                    "additionalProperties": {"type": "number"},
                },
                "nullable": {"type": ["number", "null"]},
                "nullableObject": {
                    **leaf,
                    "type": ["object", "null"],
                },
                "nullableRows": {
                    "type": ["array", "null"],
                    "items": leaf,
                },
            },
            "required": [
                "left", "right", "rows", "values", "nullable",
                "nullableObject", "nullableRows",
            ],
            "additionalProperties": False,
        })
        shared = {"kind": "leaf.v1", "count": 1}
        source = {
            "values": {"second": 2.5, "first": 1},
            "right": shared,
            "rows": [shared],
            "nullable": None,
            "nullableObject": shared,
            "nullableRows": None,
            "left": shared,
        }
        isolated = compile_normalized_json_isolator(
            schema,
            path="root",
            trusted_json=False,
        )(source)

        self.assertEqual(isolated, source)
        self.assertEqual(list(isolated), list(source))
        self.assertEqual(list(isolated["values"]), ["second", "first"])
        self.assertIsNot(isolated, source)
        self.assertIsNot(isolated["left"], shared)
        self.assertIsNot(isolated["left"], isolated["right"])
        self.assertIsNot(isolated["left"], isolated["rows"][0])
        self.assertIsNot(isolated["left"], isolated["nullableObject"])
        isolated["left"]["count"] = 99
        self.assertEqual(source["left"]["count"], 1)
        self.assertEqual(isolated["right"]["count"], 1)
        nullable_containers = compile_normalized_json_isolator(
            schema,
            path="root",
            trusted_json=False,
        )({
            **source,
            "nullableObject": None,
            "nullableRows": [shared],
        })
        self.assertIsNone(nullable_containers["nullableObject"])
        self.assertEqual(nullable_containers["nullableRows"], [shared])
        self.assertIsNot(nullable_containers["nullableRows"][0], shared)

        invalid_values = [
            {**source, "left": {"kind": "leaf.v1"}},
            {**source, "extra": 1},
            {**source, "rows": [{"count": "wrong", "kind": "leaf.v1"}]},
            {**source, "right": {"count": 1, "kind": "wrong"}},
            {**source, "values": {"bad": "wrong"}},
            {**source, "nullable": float("nan")},
        ]
        isolator = compile_normalized_json_isolator(
            schema,
            path="root",
            trusted_json=False,
        )
        for value in invalid_values:
            with self.subTest(value=value):
                expected = None
                actual = None
                try:
                    validate_normalized_json_value(value, schema, path="root")
                except ValueError as exc:
                    expected = str(exc)
                try:
                    isolator(value)
                except ValueError as exc:
                    actual = str(exc)
                self.assertIsNotNone(expected)
                self.assertEqual(actual, expected)

        trusted_shared = {"value": [1, 2]}
        trusted_source = {"left": trusted_shared, "right": trusted_shared}
        trusted_isolated = compile_normalized_json_isolator(
            normalize_schema({}),
            path="trusted",
            trusted_json=True,
        )(trusted_source)
        self.assertEqual(trusted_isolated, trusted_source)
        self.assertIsNot(trusted_isolated, trusted_source)
        self.assertIs(trusted_isolated["left"], trusted_isolated["right"])
        self.assertIsNot(trusted_isolated["left"], trusted_shared)

        for fallback_schema, accepted in (
            ({"type": "string", "enum": ["accepted"]}, "accepted"),
            ({"anyOf": [{"type": "number"}, {"type": "string"}]}, 1.5),
            ({"allOf": [{"type": "number"}, {"type": "integer"}]}, 2),
        ):
            normalized = normalize_schema(fallback_schema)
            with self.subTest(fallback_schema=fallback_schema):
                self.assertEqual(
                    compile_normalized_json_isolator(
                        normalized,
                        path="fallback",
                        trusted_json=False,
                    )(accepted),
                    accepted,
                )

    def test_data_key_contracts_reject_arrays_and_opaque_objects(self):
        with self.assertRaisesRegex(ValueError, "array runtime type"):
            normalize_data_key_schema({"type": "array", "items": {"type": "number"}})
        with self.assertRaisesRegex(ValueError, "close additionalProperties"):
            normalize_data_key_schema({"type": "object"})
        with self.assertRaisesRegex(ValueError, "array runtime type"):
            normalize_data_key_schema({
                "type": "object",
                "properties": {
                    "hidden": {"type": "array", "items": {"type": "number"}},
                },
                "additionalProperties": False,
            })
        with self.assertRaisesRegex(ValueError, "array values"):
            normalize_data_key_schema({
                "const": {"hidden": [1, 2, 3]},
            })

    def test_data_key_contracts_accept_closed_objects_and_typed_maps(self):
        closed = normalize_data_key_schema({
            "type": "object",
            "properties": {"value": {"type": "number"}},
            "required": ["value"],
            "additionalProperties": False,
        })
        typed_map = normalize_data_key_schema({
            "type": "object",
            "properties": {},
            "additionalProperties": closed,
        })
        self.assertFalse(closed["additionalProperties"])
        self.assertEqual(typed_map["additionalProperties"], closed)

    def test_structural_schemas_are_checked_recursively(self):
        provided = {
            "type": "object",
            "properties": {
                "a": {"type": "number"},
                "b": {
                    "type": "object",
                    "properties": {"c": {"type": "number"}},
                    "required": ["c"],
                },
            },
            "required": ["a", "b"],
        }
        required = {
            "type": "object",
            "properties": {"b": provided["properties"]["b"]},
            "required": ["b"],
        }
        self.assertTrue(schemas_compatible(provided, required))
        self.assertFalse(schemas_compatible({"type": "object"}, required))
        validate_json_value({"a": 1, "b": {"c": 2}}, provided)
        with self.assertRaisesRegex(ValueError, "b.c"):
            validate_json_value({"a": 1, "b": {"c": "wrong"}}, provided)
