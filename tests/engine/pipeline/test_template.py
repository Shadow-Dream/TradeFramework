import copy
import pickle
import unittest
from unittest import mock

from engine.service import control_api as control
from engine.authority import pipeline as pipeline_authority
from engine.compiler import pipeline as pipeline_compiler
from engine.contracts import strict_json
from engine.runtime import graph as graph_runtime
from engine.runtime import module_invoker
from engine.runtime.pipeline import BacktestPipelineRuntime
from engine.contracts.contract_reducer import (
    apply_expanded_contract_writes,
    write_contract_state,
)
from engine.contracts.contract_expansion import (
    contract_path_required,
    expand_contracts,
    expanded_contract_path_required,
    expanded_contract_root_paths,
    resolve_expanded_contract_path,
)
from tests.support.pipeline_contract import compiled_graph


class PipelineTemplateTests(unittest.TestCase):
    def test_validated_pipeline_plan_proof_is_nominal_immutable_and_strict(self):
        plan = pipeline_compiler.compile_pipeline_contract_plan(
            {
                "name": "Validated proof",
                "config": {
                    "observationInput": {"whitelist": [], "blacklist": []}
                },
                "modules": [],
                "topology": [],
                "universe": [],
                "signalGraph": {"nodes": [], "inputs": {}, "outputs": {}},
                "target": [],
                "constraint": [],
            },
            {},
            {},
        )
        proof = pipeline_authority._validate_and_seal_pipeline_plan(plan)
        self.assertEqual(
            pipeline_authority.validated_pipeline_plan_material(proof),
            plan,
        )
        plan["topology"].append("forged")
        self.assertEqual(
            pipeline_authority.validated_pipeline_plan_material(proof)["topology"],
            [],
        )
        for operation in (
            lambda: copy.copy(proof),
            lambda: copy.deepcopy(proof),
            lambda: pickle.dumps(proof),
        ):
            with self.assertRaisesRegex(TypeError, "cannot be"):
                operation()
        with self.assertRaisesRegex(ValueError, "Pipeline plan"):
            pipeline_authority._validate_and_seal_pipeline_plan({})

        object.__setattr__(proof, "_plan_json", "{}")
        with self.assertRaisesRegex(ValueError, "canonical authority"):
            pipeline_authority.validated_pipeline_plan_material(proof)

    def test_pipeline_contract_template_validates_once_and_isolates_state_binding(self):
        initial_contracts = {"source.value": {"type": "number"}}
        manifest = {
            "name": "Contract template",
            "config": {
                "observationInput": {
                    "whitelist": ["source.value"],
                    "blacklist": [],
                }
            },
            "modules": [],
            "topology": [],
            "universe": [],
            "signalGraph": {"nodes": [], "inputs": {}, "outputs": {}},
            "target": [],
            "constraint": [],
        }
        expected = pipeline_compiler.compile_pipeline_contract_plan(
            manifest, {}, initial_contracts
        )

        strict_validator = (
            pipeline_authority.pipeline_contract_template_from_verified_authorities
        )
        with mock.patch.object(
            pipeline_authority,
            "pipeline_contract_template_from_verified_authorities",
            wraps=strict_validator,
        ) as validate:
            template = pipeline_compiler.compile_pipeline_contract_template(
                manifest, {}
            )
            first = pipeline_authority.bound_pipeline_contract_plan(
                pipeline_compiler.bind_pipeline_contract_plan(
                    template, initial_contracts
                )
            )
            second = pipeline_authority.bound_pipeline_contract_plan(
                pipeline_compiler.bind_pipeline_contract_plan(
                    template, initial_contracts
                )
            )
        self.assertEqual(validate.call_count, 1)
        self.assertEqual(first, expected)
        self.assertEqual(second, expected)

        manifest["config"]["observationInput"]["whitelist"] = ["source.other"]
        self.assertEqual(
            pipeline_authority.bound_pipeline_contract_plan(
                pipeline_compiler.bind_pipeline_contract_plan(
                    template, initial_contracts
                )
            ),
            expected,
        )
        with self.assertRaisesRegex(ValueError, "unavailable Observation DataKey"):
            pipeline_compiler.compile_pipeline_contract_plan(
                manifest, {}, initial_contracts
            )
        with self.assertRaisesRegex(TypeError, "verified Pipeline template"):
            pipeline_compiler.bind_pipeline_contract_plan({}, initial_contracts)

        self.assertFalse(
            hasattr(BacktestPipelineRuntime, "compile_contracts")
        )
        frozen_manifest = pipeline_authority.pipeline_contract_template_material(
            template
        )["manifest"]
        other_template = pipeline_compiler.compile_pipeline_contract_template(
            frozen_manifest,
            {},
        )
        foreign_plan = pipeline_compiler.bind_pipeline_contract_plan(
            other_template,
            initial_contracts,
        )
        with mock.patch("engine.archive.version.verify_record"):
            identity = pipeline_authority.verify_pipeline_definition_authority({
                "pipelineId": "contract-template",
                "version": "1",
                "manifestHash": control.json_digest(frozen_manifest),
            })
        with self.assertRaisesRegex(AttributeError, "immutable"):
            identity._version = "forged"
        with self.assertRaisesRegex(ValueError, "does not belong"):
            pipeline_authority.bind_compiled_pipeline_authority(
                identity,
                template,
                foreign_plan,
            )

    def test_forged_complete_contract_plan_cannot_be_resealed_or_materialized(self):
        manifest = {
            "name": "Canonical authority",
            "config": {
                "observationInput": {"whitelist": [], "blacklist": []}
            },
            "modules": [],
            "topology": [],
            "universe": [],
            "signalGraph": {"nodes": [], "inputs": {}, "outputs": {}},
            "target": [],
            "constraint": [],
        }
        template = pipeline_compiler.compile_pipeline_contract_template(
            manifest,
            {},
        )
        bound = pipeline_compiler.bind_pipeline_contract_plan(template, {})
        plan, _signal_authority, _direct_authorities = (
            pipeline_authority.bound_pipeline_contract_plan_material(bound)
        )
        plan["outputContracts"] = {
            "forged.value": {"type": "string"},
        }
        plan["outputRequiredRoots"] = ["forged"]

        self.assertFalse(
            hasattr(
                pipeline_authority,
                "bind_pipeline_contract_plan_authority",
            )
        )
        self.assertNotIn(
            "seal_pipeline_contract_plan_authority",
            pipeline_authority.__all__,
        )
        with mock.patch("engine.archive.version.verify_record"):
            identity = pipeline_authority.verify_pipeline_definition_authority({
                "pipelineId": "canonical-authority",
                "version": "1",
                "manifestHash": control.json_digest(manifest),
            })
        with self.assertRaisesRegex(TypeError, "bound plan"):
            pipeline_authority.bind_compiled_pipeline_authority(
                identity,
                template,
                plan,
            )

        # Even reflective mutation of the sealed proof cannot substitute a
        # different full canonical contract plan before Runtime resources exist.
        object.__setattr__(
            bound,
            "_plan_json",
            strict_json.dumps(plan, sort_keys=True, separators=(",", ":")),
        )
        with (
            mock.patch.object(
                module_invoker.ModuleInvoker,
                "from_authority",
            ) as module_resource,
            mock.patch.object(
                graph_runtime.ModuleGraphRuntime,
                "from_compiled_authority",
            ) as graph_resource,
        ):
            with self.assertRaisesRegex(ValueError, "complete canonical authority"):
                pipeline_authority.bind_compiled_pipeline_authority(
                    identity,
                    template,
                    bound,
                )
        module_resource.assert_not_called()
        graph_resource.assert_not_called()

    def test_ordered_write_batch_matches_individual_state_transitions(self):
        typed_number_map = {
            "type": "object",
            "properties": {},
            "additionalProperties": {"type": "number"},
        }
        object_value = {
            "type": "object",
            "properties": {"base": {"type": "number"}},
            "required": ["base"],
            "additionalProperties": False,
        }
        cases = (
            (
                {},
                frozenset(),
                (
                    ("x.child", {"type": "number"}, True),
                    ("x", object_value, True),
                    ("x.tail", {"type": "string"}, False),
                ),
            ),
            (
                {"x": object_value, "y": {"type": "boolean"}},
                frozenset({"x"}),
                (
                    ("y", {"type": "boolean"}, False),
                    ("x.child", {"type": "integer"}, True),
                    ("y", {"type": "boolean"}, True),
                    ("x.child", {"type": "number"}, False),
                ),
            ),
            (
                {"dynamic": typed_number_map},
                frozenset({"dynamic"}),
                (
                    ("dynamic.named", {"type": "number"}, True),
                    ("dynamic.optional", {"type": "integer"}, False),
                ),
            ),
            (
                {
                    "literal": {"const": {"fixed": "yes", "value": 1}},
                    "composed": {
                        "oneOf": [object_value, object_value],
                    },
                },
                frozenset({"literal", "composed"}),
                (
                    ("literal.value", {"type": "number"}, True),
                    ("composed.base", {"type": "number"}, False),
                ),
            ),
        )
        for contracts, required_roots, writes in cases:
            with self.subTest(writes=writes):
                expanded = expand_contracts(contracts)
                original = expand_contracts(contracts)
                individual_contracts = expanded
                individual_roots = required_roots
                for path, schema, required in writes:
                    individual_contracts, individual_roots = write_contract_state(
                        individual_contracts,
                        individual_roots,
                        path,
                        schema,
                        required=required,
                    )
                batched_contracts, batched_roots = apply_expanded_contract_writes(
                    expanded,
                    required_roots,
                    writes,
                )
                self.assertEqual(batched_contracts, individual_contracts)
                self.assertEqual(list(batched_contracts), list(individual_contracts))
                self.assertEqual(batched_roots, individual_roots)
                self.assertEqual(expanded, original)

    def test_ordered_write_batch_materializes_once(self):
        expanded = expand_contracts({
            "x": {
                "type": "object",
                "properties": {},
                "required": [],
                "additionalProperties": False,
            }
        })
        real_expand = expand_contracts
        with mock.patch(
            "engine.contracts.contract_expansion.expand_contracts",
            wraps=real_expand,
        ) as expand:
            apply_expanded_contract_writes(
                expanded,
                {"x"},
                (
                    ("x.a", {"type": "number"}, True),
                    ("x.b", {"type": "string"}, False),
                    ("y", {"type": "boolean"}, True),
                ),
            )
        self.assertEqual(expand.call_count, 1)

    def test_expanded_contract_queries_do_not_reexpand_the_snapshot(self):
        contracts = {
            "root": {
                "type": "object",
                "properties": {"value": {"type": "number"}},
                "required": ["value"],
                "additionalProperties": False,
            }
        }
        expanded = expand_contracts(contracts)
        with mock.patch(
            "engine.contracts.contract_expansion.expand_contracts",
            side_effect=AssertionError("expanded query re-expanded its input"),
        ):
            self.assertEqual(
                resolve_expanded_contract_path(expanded, "root.value"),
                {"type": "number"},
            )
            self.assertEqual(expanded_contract_root_paths(expanded), {"root"})
            self.assertTrue(
                expanded_contract_path_required(
                    expanded, "root.value", required_roots={"root"}
                )
            )

    def test_expanded_presence_check_matches_the_public_contract_semantics(self):
        contracts = {
            "root": {
                "type": "object",
                "properties": {
                    "requiredValue": {"type": "number"},
                    "optionalValue": {"type": "string"},
                },
                "required": ["requiredValue"],
                "additionalProperties": False,
            }
        }
        expanded = expand_contracts(contracts)
        for required_roots in ({"root"}, frozenset()):
            for path in ("root", "root.requiredValue", "root.optionalValue"):
                with self.subTest(required_roots=required_roots, path=path):
                    self.assertEqual(
                        expanded_contract_path_required(
                            expanded, path, required_roots=required_roots
                        ),
                        contract_path_required(
                            contracts, path, required_roots=required_roots
                        ),
                    )
