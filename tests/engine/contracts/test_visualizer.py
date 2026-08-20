"""Result visualizer contract tests."""

import copy
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from builtin_implementations.visualizer_contracts import visualizer_definition_map
from engine.compiler import visualization as visualization_compiler
from builtin_implementations import resources as builtin_resources
from engine.control import database as engine_database
from engine.compiler import result_projection as result_projection_compiler
from engine.contracts import contract_expansion
from engine.contracts import result as result_contracts
from engine.contracts.data_model import normalize_data_key_schema
from engine.repository import module_definitions as module_repository
from tests.support.pipeline_contract import definition as module_definition


class VisualizerContractTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.config = {
            "controlRoot": str(root / "control"),
            "releaseRoot": str(root / "releases"),
            "liveRoot": str(root / "live"),
        }
        engine_database.prepare_database(self.config)
        builtin_resources.install(self.config)
        self.result = {
            "schemaVersion": 7,
            "cycles": [],
            "dataKeys": {
                "price.close": {"schema": {"type": "number"}},
                "market.bar": {"schema": {
                    "type": "object",
                    "properties": {"close": {"type": "number"}},
                    "required": ["close"],
                    "additionalProperties": False,
                }},
                "market.candle": {"schema": {
                    "type": "object",
                    "properties": {
                        "time": {"type": "string"},
                        "open": {"type": "number"},
                        "high": {"type": "number"},
                        "low": {"type": "number"},
                        "close": {"type": "number"},
                        "complete": {"type": "boolean"},
                        "sourceDate": {"type": "string"},
                    },
                    "required": [
                        "time", "open", "high", "low", "close", "complete", "sourceDate",
                    ],
                    "additionalProperties": False,
                }},
            },
        }
        self.spec = {
            "schemaVersion": 3,
            "datasetId": "prices",
            "timeZone": "UTC",
            "panes": [{
                "id": "price",
                "title": "Price",
                "role": "financial",
                "view": {
                    "start": None,
                    "end": None,
                    "logScale": False,
                    "controlsCollapsed": False,
                },
                "visualizers": [{
                    "id": "close-line",
                    "callback": "series.line",
                    "params": {
                        "dataKey": "price.close",
                        "color": "#2563eb",
                        "lineWidth": 2,
                    },
                }],
                "temporaryModules": [],
            }],
        }

    def tearDown(self):
        self.temp.cleanup()

    def compile(self, result, spec):
        return visualization_compiler.compile_visualization_contracts(
            result["dataKeys"],
            spec,
            module_repository.load_pipeline_definitions(self.config),
            visualizer_definition_map(),
        )

    def test_backend_enforces_the_complete_visualizer_parameter_schema(self):
        contracts = self.compile(self.result, self.spec)
        self.assertIn("price.close", contracts)

        unknown = copy.deepcopy(self.spec)
        unknown["panes"][0]["visualizers"][0]["params"]["frontendOnly"] = True
        with self.assertRaisesRegex(ValueError, "frontendOnly"):
            self.compile(self.result, unknown)

        out_of_range = copy.deepcopy(self.spec)
        out_of_range["panes"][0]["visualizers"][0]["params"]["lineWidth"] = 0
        with self.assertRaisesRegex(ValueError, "minimum"):
            self.compile(self.result, out_of_range)

    def test_visualization_shape_and_data_contracts_are_exact(self):
        extra = copy.deepcopy(self.spec)
        extra["panes"][0]["renderer"] = "legacy"
        with self.assertRaisesRegex(ValueError, "renderer"):
            self.compile(self.result, extra)

        incompatible = copy.deepcopy(self.spec)
        incompatible["panes"][0]["visualizers"][0]["params"]["dataKey"] = "market.bar"
        with self.assertRaisesRegex(ValueError, "schema mismatch"):
            self.compile(self.result, incompatible)

        richer_candle = copy.deepcopy(self.spec)
        richer_candle["panes"][0]["visualizers"][0] = {
            "id": "candles",
            "callback": "ohlc.candles",
            "params": {"dataKey": "market.candle"},
        }
        self.compile(self.result, richer_candle)

    def test_compiler_accepts_an_injected_visualizer_contract(self):
        spec = copy.deepcopy(self.spec)
        spec["panes"][0]["visualizers"][0] = {
            "id": "custom",
            "callback": "custom.scalar",
            "params": {"dataKey": "price.close"},
        }
        definitions = visualizer_definition_map()
        definitions["custom.scalar"] = {
            "id": "custom.scalar",
            "label": "Custom scalar",
            "inputPorts": {
                "dataKey": {"schema": {"type": "number"}},
            },
            "paramsSchema": {
                "type": "object",
                "properties": {
                    "dataKey": {"type": "string", "minLength": 1},
                },
                "required": ["dataKey"],
                "additionalProperties": False,
            },
        }
        visualization_compiler.compile_visualization_contracts(
            self.result["dataKeys"],
            spec,
            module_repository.load_pipeline_definitions(self.config),
            definitions,
        )

    def test_visualizer_resolves_a_typed_map_child_without_flattened_declaration(self):
        result = copy.deepcopy(self.result)
        result["dataKeys"]["dynamic"] = {
            "schema": {
                "type": "object",
                "properties": {},
                "required": [],
                "additionalProperties": {"type": "number"},
            },
            "required": True,
        }
        spec = copy.deepcopy(self.spec)
        spec["panes"][0]["visualizers"][0]["params"]["dataKey"] = "dynamic.a"
        self.compile(result, spec)

    def test_bulk_requiredness_reuses_the_expanded_contract_snapshot(self):
        contracts = {
            "root": {
                "type": "object",
                "properties": {
                    "requiredValue": {"type": "number"},
                    "optionalValue": {"type": "string"},
                },
                "required": ["requiredValue"],
                "additionalProperties": False,
            },
            "optional": {"type": "boolean"},
        }
        required_roots = frozenset({"root"})
        expanded = contract_expansion.expand_contracts(contracts)
        slow_required = contract_expansion.contract_path_required
        expected = {
            data_key: {
                "label": data_key,
                "schema": normalize_data_key_schema(schema, path=data_key),
                "required": slow_required(
                    expanded, data_key, required_roots=required_roots
                ),
                "source": {"path": f"cycles.data.{data_key}"},
                "encoding": {
                    "time": "decisionTime",
                    "value": f"data.{data_key}",
                },
            }
            for data_key, schema in expanded.items()
        }

        result_fast_required = result_contracts.expanded_contract_path_required
        with mock.patch.object(
            result_contracts,
            "expanded_contract_path_required",
            wraps=result_fast_required,
        ) as result_expanded_required:
            declarations = result_contracts.result_data_key_declarations(
                contracts, required_roots
            )
            self.assertEqual(declarations, expected)
            self.assertEqual(result_expanded_required.call_count, len(expanded))

        fast_required = visualization_compiler.expanded_contract_path_required
        self.assertFalse(hasattr(visualization_compiler, "contract_path_required"))
        with mock.patch.object(
            visualization_compiler,
            "expanded_contract_path_required",
            wraps=fast_required,
        ) as expanded_required:
            spec = copy.deepcopy(self.spec)
            spec["panes"][0]["visualizers"] = []
            final_contracts = self.compile({"dataKeys": declarations}, spec)
            self.assertEqual(expanded_required.call_count, 2 * len(final_contracts))

    def test_root_temporary_modules_are_validated_even_without_panes(self):
        spec = {
            "schemaVersion": 3,
            "datasetId": "prices",
            "timeZone": "UTC",
            "panes": [],
            "temporaryModules": [{
                "instanceId": "missing",
                "kind": "Signal",
                "moduleId": "does-not-exist",
                "version": "1",
                "config": {},
                "inputs": {},
                "outputs": {},
            }],
        }
        with self.assertRaisesRegex(ValueError, "Module definition does not exist"):
            self.compile(self.result, spec)

    def test_required_temporary_input_rejects_an_optional_result_root(self):
        definition = module_definition(
            "required-input",
            inputs={
                "value": {"schema": {"type": "number"}, "required": True},
            },
            outputs={
                "result": {"schema": {"type": "number"}, "required": True},
            },
        )
        data_keys = {
            "optional": {"schema": {"type": "number"}, "required": False},
        }
        spec = copy.deepcopy(self.spec)
        spec["panes"][0]["visualizers"] = []
        spec["panes"][0]["temporaryModules"] = [{
            "instanceId": "required",
            "kind": "Signal",
            "moduleId": "required-input",
            "version": "1",
            "config": {},
            "inputs": {"value": "optional"},
            "outputs": {"result": "computed"},
        }]
        with mock.patch("engine.archive.version.verify_record"):
            with self.assertRaisesRegex(ValueError, "relies on optional DataKey"):
                visualization_compiler.compile_visualization_contracts(
                    data_keys,
                    spec,
                    {"Signal/required-input/1": definition},
                    visualizer_definition_map(),
                )

    def test_bound_optional_temporary_input_must_reference_existing_data(self):
        definition = module_definition(
            "optional-input",
            inputs={
                "value": {"schema": {"type": "number"}, "required": False},
            },
            outputs={
                "result": {"schema": {"type": "number"}, "required": True},
            },
        )
        modules = [{
            "instanceId": "optional",
            "kind": "Signal",
            "moduleId": "optional-input",
            "version": "1",
            "config": {},
            "inputs": {"value": "missing.value"},
            "outputs": {"result": "computed.value"},
        }]
        with mock.patch("engine.archive.version.verify_record"):
            with self.assertRaisesRegex(ValueError, "references unknown DataKey"):
                result_projection_compiler.compile_temporary_module_plan(
                    self.result,
                    modules,
                    {"Signal/optional-input/1": definition},
                )


if __name__ == "__main__":
    unittest.main()
