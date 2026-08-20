#!/usr/bin/env python3

import copy
import unittest
from unittest import mock

from engine.authority import sampler as sampler_authority
from engine.contracts import strict_json
from engine.contracts import sampler as sampler_contracts
from engine.runtime import sampler as sampler_runtime
from engine.runtime.sampler_assets.row_map_sampler_runtime import (
    map_record as row_map_record,
)

class RowMapCanonicalOrderTests(unittest.TestCase):
    def test_parent_child_mapping_order_is_stable_across_canonical_json(self):
        object_schema = {
            "type": "object",
            "properties": {
                "base": {"type": "number"},
                "child": {"type": "number"},
            },
            "required": ["base"],
            "additionalProperties": False,
        }
        source_schema = {
            "parent": object_schema,
            "child": {"type": "number"},
        }
        reverse_order_definition = {
            "config": {
                "mapping": {"x.child": "child", "x": "parent"},
                "includeUnmappedFields": False,
                "unmappedPrefix": "dataset.",
            },
            "outputSchema": {
                "x.child": {"type": "number"},
                "x": object_schema,
            },
        }
        transported_definition = strict_json.loads(
            strict_json.dumps(reverse_order_definition, sort_keys=True)
        )

        original = sampler_contracts.compile_row_map_contract(
            reverse_order_definition, source_schema
        )
        transported = sampler_contracts.compile_row_map_contract(
            transported_definition, source_schema
        )

        self.assertEqual(list(original["mapping"]), ["x", "x.child"])
        self.assertEqual(original, transported)
        values = {"parent": {"base": 1}, "child": 2}
        mapped = []
        for mapping in (
            reverse_order_definition["config"]["mapping"],
            transported_definition["config"]["mapping"],
        ):
            data, provenance = row_map_record(
                values,
                sequence=7,
                event_time="2026-01-01T00:00:00Z",
                available_at="2026-01-02T00:00:00Z",
                mapping=mapping,
                include_unmapped_fields=False,
                unmapped_prefix="dataset.",
                source_fields=tuple(source_schema),
            )
            mapped.append((data, provenance))
        self.assertEqual(mapped[0], mapped[1])
        self.assertEqual(mapped[0][0], {"x": {"base": 1, "child": 2}})


class SamplerParameterAuthorityTests(unittest.TestCase):
    def test_contract_and_runtime_receive_the_same_canonical_parameters(self):
        observed = []

        def contracts(_definition, parameters, _source_schema, _protocol):
            observed.append(("contracts", copy.deepcopy(parameters)))
            return {}

        def factory(
            _authority,
            _dataset,
            parameters,
            _source_schema,
            _execution_root,
        ):
            observed.append(("runtime", copy.deepcopy(parameters)))
            return object()

        definition = {
            "config": {"middle": {"z": 3, "a": 4}},
            "parameterSchema": {
                "type": "object",
                "additionalProperties": True,
            },
        }
        parameters = {"z": 1, "a": {"z": 2, "a": 1}}
        runtime = {"protocol": "row-map-in-process-v1"}
        spec = {"requiredCapabilities": ()}
        dataset = mock.Mock(capabilities={})
        with (
            mock.patch.object(
                sampler_authority,
                "verify_sampler_runtime_bundle",
                return_value=(runtime, {}, spec),
            ),
            mock.patch.object(
                sampler_authority,
                "resolve_sampler_output_contracts",
                side_effect=contracts,
            ),
            mock.patch.object(
                sampler_runtime,
                "_create_row_map_runtime",
                side_effect=factory,
            ),
        ):
            authority = sampler_authority.verify_sampler_runtime_bundle_authority(
                definition
            )
            with self.assertRaisesRegex(AttributeError, "immutable"):
                authority._definition_json = "{}"
            with self.assertRaisesRegex(AttributeError, "immutable"):
                authority._assets = ()
            sampler_authority.resolve_verified_sampler_output_contracts(
                authority, parameters
            )
            sampler_runtime.create_verified_sampler_runtime(
                authority, dataset, parameters
            )

        dataset.require_semantically_validated_capabilities.assert_called_once_with(())
        expected = {
            "a": {"a": 1, "z": 2},
            "middle": {"a": 4, "z": 3},
            "z": 1,
        }
        self.assertEqual(observed, [("contracts", expected), ("runtime", expected)])
        self.assertEqual(list(observed[0][1]), ["a", "middle", "z"])
        self.assertEqual(list(observed[0][1]["a"]), ["a", "z"])
