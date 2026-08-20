import unittest

from engine.contracts.data_path import (
    delete_data_segments_copy_on_write,
    project_compiled_data_paths,
)
from engine.contracts.contract_expansion import expanded_contract_path_required
from engine.contracts.data_compatibility import normalized_schemas_disjoint
from engine.contracts.data import compile_data_json_validator
from engine.contracts.data_model import normalize_data_key_schema
from engine.contracts.observation_input import (
    compile_observation_projection_plan,
    normalize_pipeline_config,
)
from engine.contracts.observation_projection import (
    observation_contract_digest,
    project_observation_contract_state,
)


class ObservationProjectionTests(unittest.TestCase):
    def setUp(self):
        self.contracts = {
            "market": {
                "type": "object",
                "properties": {
                    "price": {
                        "type": "object",
                        "properties": {
                            "SPX": {"type": "number"},
                            "NDX": {"type": "number"},
                        },
                        "required": ["SPX", "NDX"],
                        "additionalProperties": False,
                    },
                    "volume": {"type": "number"},
                },
                "required": ["price", "volume"],
                "additionalProperties": False,
            },
            "ignored": {"type": "string"},
        }

    @staticmethod
    def tagged_union_contract():
        return {
            "oneOf": [
                {
                    "type": "object",
                    "properties": {
                        "kind": {"const": "A"},
                        "value": {"type": "number"},
                    },
                    "required": ["kind", "value"],
                    "additionalProperties": False,
                },
                {
                    "type": "object",
                    "properties": {
                        "kind": {"const": "B"},
                        "value": {"type": "string"},
                    },
                    "required": ["kind", "value"],
                    "additionalProperties": False,
                },
            ]
        }

    def test_contract_projection_applies_whitelist_then_nested_blacklist(self):
        projected, required_roots = project_observation_contract_state(
            self.contracts,
            ["ignored", "market"],
            {
                "observationInput": {
                    "whitelist": ["market"],
                    "blacklist": ["market.price.NDX"],
                }
            },
        )
        self.assertEqual(required_roots, {"market"})
        self.assertNotIn("ignored", projected)
        self.assertNotIn("market.price.NDX", projected)
        self.assertEqual(projected["market"]["required"], ["price", "volume"])
        self.assertEqual(projected["market.price"]["required"], ["SPX"])
        self.assertFalse(projected["market.price"]["properties"]["NDX"])

    def test_runtime_projection_does_not_mutate_observation(self):
        observation = {
            "market": {
                "price": {"SPX": 10, "NDX": 20},
                "volume": 30,
            },
            "ignored": "not selected",
        }
        data = project_compiled_data_paths(
            observation,
            (("market", ("market",)),),
            isolate_values=False,
        )
        delete_data_segments_copy_on_write(
            data,
            ("market", "price", "NDX"),
        )
        self.assertEqual(
            data,
            {"market": {"price": {"SPX": 10}, "volume": 30}},
        )
        self.assertEqual(observation["market"]["price"]["NDX"], 20)
        self.assertIsNot(data["market"], observation["market"])
        self.assertIsNot(data["market"]["price"], observation["market"]["price"])

    def test_config_preserves_unique_parent_child_paths_and_plan_collapses_them(self):
        config = {
            "observationInput": {
                "whitelist": ["market.price", "market"],
                "blacklist": ["market.price.NDX"],
            }
        }
        self.assertEqual(
            normalize_pipeline_config(config),
            {
                "observationInput": {
                    "whitelist": ["market", "market.price"],
                    "blacklist": ["market.price.NDX"],
                }
            },
        )
        self.assertEqual(
            compile_observation_projection_plan(config),
            {
                "whitelist": [
                    {"dataKey": "market", "segments": ["market"]},
                ],
                "blacklist": [
                    {
                        "dataKey": "market.price.NDX",
                        "segments": ["market", "price", "NDX"],
                    },
                ],
            },
        )

    def test_projection_preserves_optional_leaf_presence(self):
        contracts = {
            "market": {
                "type": "object",
                "properties": {
                    "required": {"type": "number"},
                    "optional": {"type": "number"},
                },
                "required": ["required"],
                "additionalProperties": False,
            }
        }
        config = {
            "observationInput": {
                "whitelist": ["market.required", "market.optional"],
                "blacklist": [],
            }
        }
        projected, required_roots = project_observation_contract_state(
            contracts,
            {"market"},
            config,
        )
        self.assertEqual(projected["market"]["required"], ["required"])
        self.assertTrue(expanded_contract_path_required(
            projected,
            "market.required",
            required_roots=required_roots,
        ))
        self.assertFalse(expanded_contract_path_required(
            projected,
            "market.optional",
            required_roots=required_roots,
        ))
        validate = compile_data_json_validator(
            projected,
            required_paths=required_roots,
            contracts_expanded=True,
        )
        observation = {"market": {"required": 1.0}}
        plan = compile_observation_projection_plan(config)
        projected_data = project_compiled_data_paths(
            observation,
            tuple(
                (entry["dataKey"], tuple(entry["segments"]))
                for entry in plan["whitelist"]
            ),
            isolate_values=False,
        )
        self.assertEqual(projected_data, observation)
        validate(projected_data)

    def test_projection_preserves_optional_root_and_conditional_required_child(self):
        projected, required_roots = project_observation_contract_state(
            {
                "state": {
                    "type": "object",
                    "properties": {"value": {"type": "number"}},
                    "required": ["value"],
                    "additionalProperties": False,
                }
            },
            set(),
            {
                "observationInput": {
                    "whitelist": ["state.value"],
                    "blacklist": [],
                }
            },
        )
        self.assertEqual(required_roots, set())
        self.assertEqual(projected["state"]["required"], ["value"])
        self.assertFalse(expanded_contract_path_required(
            projected,
            "state.value",
            required_roots=required_roots,
        ))

    def test_sibling_selection_preserves_union_correlation(self):
        projected, required_roots = project_observation_contract_state(
            {"root": self.tagged_union_contract()},
            {"root"},
            {
                "observationInput": {
                    "whitelist": ["root.kind", "root.value"],
                    "blacklist": [],
                }
            },
        )
        validate = compile_data_json_validator(
            projected,
            required_paths=required_roots,
            contracts_expanded=True,
        )
        self.assertEqual(required_roots, {"root"})
        validate({"root": {"kind": "A", "value": 1.0}})
        validate({"root": {"kind": "B", "value": "ok"}})
        with self.assertRaises(ValueError):
            validate({"root": {"kind": "A", "value": "wrong"}})

    def test_nested_blacklist_projects_union_and_literal_branches(self):
        union, union_roots = project_observation_contract_state(
            {"root": self.tagged_union_contract()},
            {"root"},
            {
                "observationInput": {
                    "whitelist": ["root"],
                    "blacklist": ["root.kind"],
                }
            },
        )
        validate_union = compile_data_json_validator(
            union,
            required_paths=union_roots,
            contracts_expanded=True,
        )
        validate_union({"root": {"value": 1.0}})
        validate_union({"root": {"value": "ok"}})
        with self.assertRaises(ValueError):
            validate_union({"root": {"kind": "A", "value": 1.0}})

        literal, literal_roots = project_observation_contract_state(
            {"root": {"const": {"a": 1, "b": 2}}},
            {"root"},
            {
                "observationInput": {
                    "whitelist": ["root"],
                    "blacklist": ["root.a"],
                }
            },
        )
        self.assertEqual(literal_roots, {"root"})
        self.assertEqual(literal["root"], {"const": {"b": 2}})

    def test_multiple_union_blacklists_are_applied_before_schema_selection(self):
        projected, required_roots = project_observation_contract_state(
            {"root": self.tagged_union_contract()},
            {"root"},
            {
                "observationInput": {
                    "whitelist": ["root"],
                    "blacklist": ["root.kind", "root.value"],
                }
            },
        )
        validate = compile_data_json_validator(
            projected,
            required_paths=required_roots,
            contracts_expanded=True,
        )

        self.assertEqual(required_roots, {"root"})
        validate({"root": {}})
        with self.assertRaises(ValueError):
            validate({})

    def test_nested_blacklist_specializes_one_typed_map_key(self):
        projected, required_roots = project_observation_contract_state(
            {
                "root": {
                    "type": "object",
                    "properties": {},
                    "required": [],
                    "additionalProperties": self.tagged_union_contract(),
                }
            },
            {"root"},
            {
                "observationInput": {
                    "whitelist": ["root"],
                    "blacklist": ["root.selected.kind"],
                }
            },
        )
        validate = compile_data_json_validator(
            projected,
            required_paths=required_roots,
            contracts_expanded=True,
        )

        validate({
            "root": {
                "selected": {"value": 1.0},
                "untouched": {"kind": "B", "value": "ok"},
            }
        })
        with self.assertRaises(ValueError):
            validate({
                "root": {
                    "selected": {"kind": "A", "value": 1.0},
                }
            })

    def test_disjunctive_literal_selection_proves_root_presence(self):
        projected, required_roots = project_observation_contract_state(
            {"root": {"enum": [{"a": 1}, {"b": 2}]}},
            {"root"},
            {
                "observationInput": {
                    "whitelist": ["root.a", "root.b"],
                    "blacklist": [],
                }
            },
        )
        validate = compile_data_json_validator(
            projected,
            required_paths=required_roots,
            contracts_expanded=True,
        )

        self.assertEqual(required_roots, {"root"})
        validate({"root": {"a": 1}})
        validate({"root": {"b": 2}})
        with self.assertRaises(ValueError):
            validate({"root": {}})

    def test_partial_all_of_image_is_rejected_instead_of_widened(self):
        number = {"type": "number"}
        branch = {
            "type": "object",
            "properties": {"a": number, "hidden": {"type": "string"}},
            "required": ["a", "hidden"],
            "additionalProperties": False,
        }
        contracts = {"root": {"allOf": [branch, branch]}}
        for config in (
            {
                "observationInput": {
                    "whitelist": ["root.a"],
                    "blacklist": [],
                }
            },
            {
                "observationInput": {
                    "whitelist": ["root"],
                    "blacklist": ["root.hidden"],
                }
            },
        ):
            with self.subTest(config=config), self.assertRaisesRegex(
                ValueError,
                "partial allOf image",
            ):
                project_observation_contract_state(
                    contracts,
                    {"root"},
                    config,
                )

    def test_partial_overlapping_one_of_image_is_rejected(self):
        contracts = {
            "root": {
                "oneOf": [
                    {
                        "type": "object",
                        "properties": {"a": {"type": "number"}},
                        "required": ["a"],
                        "additionalProperties": False,
                    },
                    {
                        "type": "object",
                        "properties": {"a": {"const": 1}},
                        "required": ["a"],
                        "additionalProperties": False,
                    },
                ]
            }
        }

        with self.assertRaisesRegex(ValueError, "overlapping oneOf image"):
            project_observation_contract_state(
                contracts,
                {"root"},
                {
                    "observationInput": {
                        "whitelist": ["root.a"],
                        "blacklist": [],
                    }
                },
            )

    def test_impossible_union_branch_has_empty_image_and_vacuous_presence(self):
        object_branch = {
            "type": "object",
            "properties": {"a": {"type": "number"}},
            "required": ["a"],
            "additionalProperties": False,
        }
        for keyword in ("anyOf", "oneOf"):
            projected, required_roots = project_observation_contract_state(
                {"root": {keyword: [False, object_branch]}},
                {"root"},
                {
                    "observationInput": {
                        "whitelist": ["root.a"],
                        "blacklist": [],
                    }
                },
            )
            validate = compile_data_json_validator(
                projected,
                required_paths=required_roots,
                contracts_expanded=True,
            )

            with self.subTest(keyword=keyword):
                self.assertEqual(required_roots, {"root"})
                validate({"root": {"a": 1.0}})
                with self.assertRaises(ValueError):
                    validate({})

    def test_provably_empty_union_branch_is_ignored_for_image_and_presence(self):
        impossible = {
            "type": "object",
            "properties": {"x": False},
            "required": ["x"],
            "additionalProperties": False,
        }
        possible = {
            "type": "object",
            "properties": {"a": {"type": "number"}},
            "required": ["a"],
            "additionalProperties": False,
        }
        for keyword in ("anyOf", "oneOf"):
            projected, required_roots = project_observation_contract_state(
                {"root": {keyword: [impossible, possible]}},
                {"root"},
                {
                    "observationInput": {
                        "whitelist": ["root.a"],
                        "blacklist": [],
                    }
                },
            )
            validate = compile_data_json_validator(
                projected,
                required_paths=required_roots,
                contracts_expanded=True,
            )

            with self.subTest(keyword=keyword):
                self.assertEqual(required_roots, {"root"})
                validate({"root": {"a": 1.0}})
                with self.assertRaises(ValueError):
                    validate({})

    def test_unknown_satisfiability_is_rejected_instead_of_widened(self):
        empty_child = {
            "oneOf": [{"type": "number"}, {"type": "number"}]
        }
        impossible = {
            "type": "object",
            "properties": {
                "a": {"const": 1},
                "x": empty_child,
            },
            "required": ["a", "x"],
            "additionalProperties": False,
        }
        possible = {
            "type": "object",
            "properties": {"a": {"const": 2}},
            "required": ["a"],
            "additionalProperties": False,
        }
        contracts = [
            {"root": impossible},
            {"root": {"anyOf": [impossible, possible]}},
            {"root": {"oneOf": [impossible, possible]}},
        ]
        for contract in contracts:
            with (
                self.subTest(contract=contract),
                self.assertRaisesRegex(ValueError, "non-empty source schema"),
            ):
                project_observation_contract_state(
                    contract,
                    {"root"},
                    {
                        "observationInput": {
                            "whitelist": ["root.a"],
                            "blacklist": [],
                        }
                    },
                )

    def test_nullable_ghost_object_variant_cannot_emit_or_be_deleted(self):
        empty_child = {
            "oneOf": [{"type": "number"}, {"type": "number"}]
        }
        schema = {
            "type": ["object", "null"],
            "properties": {
                "a": {"const": 1},
                "x": empty_child,
            },
            "required": ["a", "x"],
            "additionalProperties": False,
        }
        for config, message in (
            (
                {
                    "observationInput": {
                        "whitelist": ["root.a"],
                        "blacklist": [],
                    }
                },
                "emits any selected path",
            ),
            (
                {
                    "observationInput": {
                        "whitelist": ["root"],
                        "blacklist": ["root.x"],
                    }
                },
                "contains the deleted path",
            ),
        ):
            with (
                self.subTest(config=config),
                self.assertRaisesRegex(ValueError, message),
            ):
                project_observation_contract_state(
                    {"root": schema},
                    {"root"},
                    config,
                )

    def test_required_nullable_object_root_becomes_optional_after_leaf_selection(self):
        projected, required_roots = project_observation_contract_state(
            {
                "root": {
                    "type": ["object", "null"],
                    "properties": {"a": {"type": "number"}},
                    "required": ["a"],
                    "additionalProperties": False,
                }
            },
            {"root"},
            {
                "observationInput": {
                    "whitelist": ["root.a"],
                    "blacklist": [],
                }
            },
        )
        validate = compile_data_json_validator(
            projected,
            required_paths=required_roots,
            contracts_expanded=True,
        )

        self.assertEqual(required_roots, set())
        validate({})
        validate({"root": {"a": 1.0}})

    def test_nullable_object_image_preserves_joint_required_children(self):
        projected, required_roots = project_observation_contract_state(
            {
                "root": {
                    "type": ["object", "null"],
                    "properties": {
                        "a": {"type": "number"},
                        "b": {"type": "number"},
                    },
                    "required": ["a", "b"],
                    "additionalProperties": False,
                }
            },
            {"root"},
            {
                "observationInput": {
                    "whitelist": ["root.a", "root.b"],
                    "blacklist": [],
                }
            },
        )
        validate = compile_data_json_validator(
            projected,
            required_paths=required_roots,
            contracts_expanded=True,
        )

        self.assertEqual(required_roots, set())
        self.assertEqual(projected["root"]["required"], ["a", "b"])
        validate({})
        validate({"root": {"a": 1.0, "b": 2.0}})
        for partial in ({"a": 1.0}, {"b": 2.0}):
            with self.subTest(partial=partial), self.assertRaises(ValueError):
                validate({"root": partial})

    def test_full_nullable_parent_does_not_require_its_object_only_child(self):
        schema = {
            "type": ["object", "null"],
            "properties": {"a": {"type": "number"}},
            "required": ["a"],
            "additionalProperties": False,
        }
        projected, required_roots = project_observation_contract_state(
            {"root": schema},
            {"root"},
            {
                "observationInput": {
                    "whitelist": ["root"],
                    "blacklist": [],
                }
            },
        )

        self.assertEqual(required_roots, {"root"})
        self.assertFalse(expanded_contract_path_required(
            projected,
            "root.a",
            required_roots=required_roots,
        ))

    def test_nullable_impossible_object_variant_is_not_globally_empty(self):
        schema = normalize_data_key_schema({
            "type": ["object", "null"],
            "properties": {"x": False},
            "required": ["x"],
            "additionalProperties": False,
        })

        self.assertFalse(normalized_schemas_disjoint(schema, schema))

    def test_nullable_one_of_branches_are_not_treated_as_disjoint(self):
        branches = [
            {
                "type": ["object", "null"],
                "properties": {
                    "kind": {"const": kind},
                    "value": {"type": "number"},
                },
                "required": ["kind", "value"],
                "additionalProperties": False,
            }
            for kind in ("A", "B")
        ]

        with self.assertRaisesRegex(ValueError, "overlapping oneOf image"):
            project_observation_contract_state(
                {"root": {"oneOf": branches}},
                {"root"},
                {
                    "observationInput": {
                        "whitelist": ["root"],
                        "blacklist": ["root.kind"],
                    }
                },
            )

    def test_partial_union_with_sibling_assertions_is_rejected(self):
        for keyword in ("anyOf", "oneOf"):
            contracts = {
                "root": {
                    "type": "object",
                    "properties": {
                        "a": {"enum": [1, 2]},
                        "h": {"const": "X"},
                    },
                    "required": ["a", "h"],
                    "additionalProperties": False,
                    keyword: [
                        {"const": {"a": 1, "h": "Y"}},
                        {"const": {"a": 2, "h": "X"}},
                    ],
                }
            }
            for config in (
                {
                    "observationInput": {
                        "whitelist": ["root.a"],
                        "blacklist": [],
                    }
                },
                {
                    "observationInput": {
                        "whitelist": ["root"],
                        "blacklist": ["root.h"],
                    }
                },
            ):
                with (
                    self.subTest(keyword=keyword, config=config),
                    self.assertRaisesRegex(ValueError, "composed image"),
                ):
                    project_observation_contract_state(
                        contracts,
                        {"root"},
                        config,
                    )

    def test_observation_digest_includes_presence_and_is_canonical(self):
        first = observation_contract_digest(self.contracts, {"market", "ignored"})
        reordered = observation_contract_digest(
            {"ignored": self.contracts["ignored"], "market": self.contracts["market"]},
            {"ignored", "market"},
        )
        optional_market = observation_contract_digest(self.contracts, {"ignored"})

        self.assertEqual(first, reordered)
        self.assertRegex(first, r"^sha256:[0-9a-f]{64}$")
        self.assertNotEqual(first, optional_market)

    def test_config_rejects_duplicate_or_unavailable_selection(self):
        for config, message in (
            (
                {
                    "observationInput": {
                        "whitelist": ["market", "market"],
                        "blacklist": [],
                    }
                },
                "duplicate paths",
            ),
            (
                {
                    "observationInput": {
                        "whitelist": ["market.price"],
                        "blacklist": ["ignored"],
                    }
                },
                "outside the whitelist",
            ),
            (
                {
                    "observationInput": {
                        "whitelist": ["last.portfolio"],
                        "blacklist": [],
                    }
                },
                "reserved Engine path",
            ),
        ):
            with self.subTest(message=message), self.assertRaisesRegex(
                ValueError, message
            ):
                normalize_pipeline_config(config)

        with self.assertRaisesRegex(ValueError, "unavailable Observation"):
            project_observation_contract_state(
                self.contracts,
                ["ignored", "market"],
                {
                    "observationInput": {
                        "whitelist": ["not.available"],
                        "blacklist": [],
                    }
                },
            )


if __name__ == "__main__":
    unittest.main()
