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
from tests.support.pipeline_contract import (
    definition as module_definition,
    instance as module_instance,
)


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
                "time": {"schema": {"type": "string"}, "required": True},
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
                        "eventTime": {"type": "string"},
                        "open": {"type": "number"},
                        "high": {"type": "number"},
                        "low": {"type": "number"},
                        "close": {"type": "number"},
                        "complete": {"type": "boolean"},
                        "sourceDate": {"type": "string"},
                    },
                    "required": [
                        "eventTime", "open", "high", "low", "close", "complete", "sourceDate",
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
                        "timeKey": "time",
                        "timeDomainId": "result-cycle",
                        "priceScaleId": "price",
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

    def test_numeric_series_bar_offset_is_a_strict_optional_integer(self):
        definitions = visualizer_definition_map()
        for callback in ("series.line", "series.histogram"):
            definition = definitions[callback]
            schema = definition["paramsSchema"]["properties"]["offsetBars"]
            self.assertEqual(schema["type"], "integer")
            self.assertEqual(schema["default"], 0)
            self.assertEqual(schema["minimum"], -9007199254740991)
            self.assertEqual(schema["maximum"], 9007199254740991)
            self.assertNotIn("offsetBars", definition["paramsSchema"]["required"])
            field = next(
                item for item in definition["params"]
                if item["name"] == "offsetBars"
            )
            self.assertEqual(
                field,
                {
                    "name": "offsetBars",
                    "label": "Bar Offset",
                    "type": "integer",
                    "required": False,
                    "default": 0,
                    "min": -9007199254740991,
                    "max": 9007199254740991,
                },
            )

        for offset in (-26, 0, 26):
            shifted = copy.deepcopy(self.spec)
            shifted["panes"][0]["visualizers"][0]["params"][
                "offsetBars"
            ] = offset
            self.assertIn("price.close", self.compile(self.result, shifted))

        fractional = copy.deepcopy(self.spec)
        fractional["panes"][0]["visualizers"][0]["params"][
            "offsetBars"
        ] = 1.5
        with self.assertRaisesRegex(ValueError, "integer"):
            self.compile(self.result, fractional)

        unsafe = copy.deepcopy(self.spec)
        unsafe["panes"][0]["visualizers"][0]["params"][
            "offsetBars"
        ] = 9007199254740992
        with self.assertRaisesRegex(ValueError, "maximum"):
            self.compile(self.result, unsafe)

        histogram = copy.deepcopy(self.spec)
        visualizer = histogram["panes"][0]["visualizers"][0]
        visualizer["callback"] = "series.histogram"
        visualizer["params"].pop("lineWidth")
        visualizer["params"].update({
            "color": "#64748b",
            "positiveColor": "#089981",
            "negativeColor": "#f23645",
            "offsetBars": -26,
        })
        self.assertIn("price.close", self.compile(self.result, histogram))

    def test_builtin_visualizers_explicitly_belong_to_basic_workflow(self):
        definitions = visualizer_definition_map()
        self.assertEqual(len(definitions), 11)
        self.assertEqual(
            {definition.get("protocolId") for definition in definitions.values()},
            {"trade.basic-workflow"},
        )
        for definition in definitions.values():
            self.assertNotIn("protocolId", definition["inputPorts"])
            self.assertNotIn(
                "protocolId",
                definition["paramsSchema"]["properties"],
            )

    def test_visualizer_protocol_id_is_optional_passive_metadata(self):
        unbound = visualizer_definition_map()
        unbound["series.line"].pop("protocolId")
        visualization_compiler.compile_visualization_contracts(
            self.result["dataKeys"],
            self.spec,
            module_repository.load_pipeline_definitions(self.config),
            unbound,
        )

        foreign = visualizer_definition_map()
        foreign["series.line"]["protocolId"] = "vendor.uninstalled-protocol"
        visualization_compiler.compile_visualization_contracts(
            self.result["dataKeys"],
            self.spec,
            module_repository.load_pipeline_definitions(self.config),
            foreign,
        )

    def test_visualizer_protocol_id_is_structurally_validated_when_present(self):
        for invalid in (None, "", " trade.basic-workflow", "trade.basic-workflow ", 1):
            with self.subTest(invalid=invalid):
                definitions = visualizer_definition_map()
                definitions["series.line"]["protocolId"] = invalid
                with self.assertRaisesRegex(
                    ValueError,
                    "protocolId must be a canonical non-empty string",
                ):
                    visualization_compiler.compile_visualization_contracts(
                        self.result["dataKeys"],
                        self.spec,
                        module_repository.load_pipeline_definitions(self.config),
                        definitions,
                    )

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
            "params": {
                "dataKey": "market.candle",
                "timeDomainId": "market-time",
                "priceScaleId": "market-price",
            },
        }
        self.compile(self.result, richer_candle)

        typed_map_result = copy.deepcopy(self.result)
        typed_map_result["dataKeys"]["priceMap"] = {
            "required": True,
            "schema": {
                "type": "object",
                "additionalProperties": {
                    "type": "object",
                    "additionalProperties": {
                        "type": "object",
                        "properties": {
                            "eventTime": {"type": "string"},
                            "open": {"type": "number"},
                            "high": {"type": "number"},
                            "low": {"type": "number"},
                            "close": {"type": "number"},
                        },
                        "required": ["eventTime", "open", "high", "low", "close"],
                        "additionalProperties": False,
                    },
                },
            },
        }
        typed_map_candle = copy.deepcopy(self.spec)
        typed_map_candle["panes"][0]["visualizers"][0] = {
            "id": "nasdaq-hour",
            "callback": "ohlc.candles",
            "params": {
                "dataKey": "priceMap.hour.NASDAQ_COMPOSITE",
                "timeDomainId": "market-time",
                "priceScaleId": "market-price",
            },
        }
        self.compile(typed_map_result, typed_map_candle)

        typed_map_result["dataKeys"]["events"] = {
            "required": False,
            "schema": {
                "type": ["object", "null"],
                "properties": {
                    "side": {"type": "string"},
                    "reason": {"type": ["string", "null"]},
                },
                "required": [],
                "additionalProperties": False,
            },
        }
        typed_map_candle["panes"][0]["visualizers"].append({
            "id": "nasdaq-events",
            "callback": "overlay.markers",
            "params": {
                "dataKey": "events",
                "timeKey": "time",
                "timeDomainId": "market-time",
                "targetVisualizerId": "nasdaq-hour",
            },
        })
        self.compile(typed_map_result, typed_map_candle)

    def test_candles_require_event_time_and_declare_complete_as_optional(self):
        definition = visualizer_definition_map()["ohlc.candles"]
        candle_schema = definition["inputPorts"]["dataKey"]["schema"]
        self.assertIn("eventTime", candle_schema["required"])
        self.assertIn("complete", candle_schema["properties"])
        self.assertNotIn("complete", candle_schema["required"])

        spec = copy.deepcopy(self.spec)
        spec["panes"][0]["visualizers"][0] = {
            "id": "candles",
            "callback": "ohlc.candles",
            "params": {
                "dataKey": "market.candle",
                "timeDomainId": "market-time",
                "priceScaleId": "market-price",
            },
        }
        without_complete = copy.deepcopy(self.result)
        candle = without_complete["dataKeys"]["market.candle"]["schema"]
        candle["properties"].pop("complete")
        candle["required"].remove("complete")
        self.compile(without_complete, spec)

        legacy = copy.deepcopy(without_complete)
        legacy_candle = legacy["dataKeys"]["market.candle"]["schema"]
        legacy_candle["properties"].pop("eventTime")
        legacy_candle["required"].remove("eventTime")
        with self.assertRaisesRegex(ValueError, "schema mismatch"):
            self.compile(legacy, spec)

        optional_event_time = copy.deepcopy(without_complete)
        optional_schema = optional_event_time["dataKeys"]["market.candle"]["schema"]
        optional_schema["required"].remove("eventTime")
        with self.assertRaisesRegex(ValueError, "schema mismatch"):
            self.compile(optional_event_time, spec)

    def test_time_reading_visualizers_require_an_explicit_time_key(self):
        definitions = visualizer_definition_map()
        for identifier in (
            "series.line",
            "series.scatter",
            "series.histogram",
            "overlay.markers",
            "overlay.priceLine",
        ):
            with self.subTest(identifier=identifier):
                definition = definitions[identifier]
                self.assertEqual(
                    definition["inputPorts"]["timeKey"]["schema"],
                    {"type": "string"},
                )
                self.assertIn("timeKey", definition["paramsSchema"]["required"])
        marker = definitions["overlay.markers"]
        self.assertNotIn("targetDataKey", marker["inputPorts"])
        self.assertIn("targetVisualizerId", marker["paramsSchema"]["required"])
        reducer = definitions["overlay.priceLine"]["paramsSchema"]["properties"][
            "reducer"
        ]
        self.assertEqual(reducer["enum"], ["latest"])
        self.assertNotIn("default", reducer)
        price_line_ui = {
            field["name"]: field
            for field in definitions["overlay.priceLine"]["params"]
        }
        self.assertEqual(
            price_line_ui["targetVisualizerId"]["type"],
            "visualizerRef",
        )
        self.assertEqual(price_line_ui["reducer"]["options"], ["latest"])

        missing = copy.deepcopy(self.spec)
        missing["panes"][0]["visualizers"][0]["params"].pop("timeKey")
        with self.assertRaisesRegex(ValueError, "timeKey"):
            self.compile(self.result, missing)

        wrong_type = copy.deepcopy(self.spec)
        wrong_type["panes"][0]["visualizers"][0]["params"]["timeKey"] = (
            "price.close"
        )
        with self.assertRaisesRegex(ValueError, "series.line.timeKey.*schema mismatch"):
            self.compile(self.result, wrong_type)

    def test_target_dependencies_are_instance_and_capability_checked(self):
        valid = copy.deepcopy(self.spec)
        valid["panes"][0]["visualizers"].append({
            "id": "latest-price",
            "callback": "overlay.priceLine",
            "params": {
                "dataKey": "price.close",
                "timeKey": "time",
                "timeDomainId": "result-cycle",
                "targetVisualizerId": "close-line",
                "reducer": "latest",
            },
        })
        self.compile(self.result, valid)

        missing_reducer = copy.deepcopy(valid)
        missing_reducer["panes"][0]["visualizers"][1]["params"].pop("reducer")
        with self.assertRaisesRegex(ValueError, "reducer"):
            self.compile(self.result, missing_reducer)

        invalid_reducer = copy.deepcopy(valid)
        invalid_reducer["panes"][0]["visualizers"][1]["params"]["reducer"] = (
            "last-cycle"
        )
        with self.assertRaisesRegex(ValueError, "latest"):
            self.compile(self.result, invalid_reducer)

        unknown = copy.deepcopy(valid)
        unknown["panes"][0]["visualizers"][1]["params"][
            "targetVisualizerId"
        ] = "missing"
        with self.assertRaisesRegex(ValueError, "unknown target"):
            self.compile(self.result, unknown)

        self_target = copy.deepcopy(valid)
        self_target["panes"][0]["visualizers"][1]["params"][
            "targetVisualizerId"
        ] = "latest-price"
        with self.assertRaisesRegex(ValueError, "may not reference itself"):
            self.compile(self.result, self_target)

        hidden = copy.deepcopy(valid)
        hidden["panes"][0]["visualizers"][0]["visible"] = False
        with self.assertRaisesRegex(ValueError, "must be visible"):
            self.compile(self.result, hidden)

        incompatible = copy.deepcopy(valid)
        incompatible["panes"][0]["visualizers"].insert(1, {
            "id": "horizontal",
            "callback": "drawing.horizontalLine",
            "params": {
                "targetVisualizerId": "close-line",
                "price": 1,
            },
        })
        incompatible["panes"][0]["visualizers"][2]["params"][
            "targetVisualizerId"
        ] = "horizontal"
        with self.assertRaisesRegex(ValueError, "required capability"):
            self.compile(self.result, incompatible)

        wrong_domain = copy.deepcopy(valid)
        wrong_domain["panes"][0]["visualizers"][1]["params"][
            "timeDomainId"
        ] = "other-time"
        with self.assertRaisesRegex(ValueError, "attribute 'time-domain' must match"):
            self.compile(self.result, wrong_domain)

    def test_primary_axes_and_time_domain_are_explicit_composition(self):
        definitions = visualizer_definition_map()
        for identifier in (
            "ohlc.candles",
            "series.line",
            "series.scatter",
            "series.histogram",
        ):
            with self.subTest(identifier=identifier):
                required = definitions[identifier]["paramsSchema"]["required"]
                self.assertIn("timeDomainId", required)
                self.assertIn("priceScaleId", required)
                self.assertEqual(
                    definitions[identifier]["capabilities"]["provides"],
                    [{
                        "name": "series",
                        "kind": "series.price-coordinate",
                        "attributes": {
                            "price-scale": "priceScaleId",
                            "time-domain": "timeDomainId",
                        },
                    }],
                )

        same_domain = copy.deepcopy(self.spec)
        second = copy.deepcopy(same_domain["panes"][0]["visualizers"][0])
        second["id"] = "second-line"
        second["params"]["priceScaleId"] = "separate-price-scale"
        same_domain["panes"][0]["visualizers"].append(second)
        self.compile(self.result, same_domain)

        conflict = copy.deepcopy(same_domain)
        conflict["panes"][0]["visualizers"][1]["params"]["timeDomainId"] = (
            "another-domain"
        )
        with self.assertRaisesRegex(ValueError, "share one explicit time-domain"):
            self.compile(self.result, conflict)

        conflict["panes"][0]["visualizers"][1]["visible"] = False
        self.compile(self.result, conflict)

    def test_drawing_definitions_persist_complete_pointer_state(self):
        definitions = visualizer_definition_map()
        for identifier in ("drawing.horizontalLine", "drawing.trendLine"):
            definition = definitions[identifier]
            self.assertEqual(definition["renderer"], {"id": identifier, "apiVersion": 1})
            self.assertEqual(
                definition["capabilities"]["interactions"],
                ["chart.pointer"],
            )
            self.assertEqual(
                definition["capabilities"]["requires"],
                [{
                    "name": "target",
                    "kind": "series.price-coordinate",
                    "bindingParam": "targetVisualizerId",
                    "matches": {},
                }],
            )

        spec = copy.deepcopy(self.spec)
        spec["panes"][0]["visualizers"].extend([
            {
                "id": "horizontal",
                "callback": "drawing.horizontalLine",
                "params": {"targetVisualizerId": "close-line", "price": 101.5},
            },
            {
                "id": "trend",
                "callback": "drawing.trendLine",
                "params": {
                    "targetVisualizerId": "close-line",
                    "points": [
                        {"time": "2026-01-01T00:00:00Z", "price": 100},
                        {"time": "2026-01-02T00:00:00Z", "price": 102},
                    ],
                },
            },
        ])
        self.compile(self.result, spec)

        incomplete = copy.deepcopy(spec)
        incomplete["panes"][0]["visualizers"][2]["params"]["points"].pop()
        with self.assertRaisesRegex(ValueError, "too short"):
            self.compile(self.result, incomplete)

    def test_rectangle_brush_and_text_have_exact_persisted_state_contracts(self):
        definitions = visualizer_definition_map()
        expected_requirement = [{
            "name": "target",
            "kind": "series.price-coordinate",
            "bindingParam": "targetVisualizerId",
            "matches": {},
        }]
        for identifier in (
            "drawing.rectangle",
            "drawing.brush",
            "drawing.text",
        ):
            with self.subTest(identifier=identifier):
                definition = definitions[identifier]
                self.assertEqual(definition["inputPorts"], {})
                self.assertEqual(
                    definition["capabilities"]["requires"],
                    expected_requirement,
                )
                params = {field["name"]: field for field in definition["params"]}
                self.assertEqual(
                    params["targetVisualizerId"]["type"],
                    "visualizerRef",
                )

        self.assertEqual(
            definitions["drawing.rectangle"]["capabilities"]["interactions"],
            ["chart.pointer"],
        )
        self.assertEqual(
            definitions["drawing.brush"]["paramsSchema"]["properties"]["points"][
                "maxItems"
            ],
            8192,
        )
        self.assertEqual(
            definitions["drawing.text"]["capabilities"]["interactions"],
            ["chart.pointer", "chart.text-input"],
        )

        spec = copy.deepcopy(self.spec)
        rectangle = {
            "id": "rectangle",
            "callback": "drawing.rectangle",
            "params": {
                "targetVisualizerId": "close-line",
                "corners": [
                    {"time": "2026-01-03T00:00:00Z", "price": 103},
                    {"time": "2026-01-01T00:00:00Z", "price": 99},
                ],
                "fillOpacity": 0.25,
            },
        }
        brush = {
            "id": "brush",
            "callback": "drawing.brush",
            "params": {
                "targetVisualizerId": "close-line",
                "points": [
                    {"time": "2026-01-02T00:00:00Z", "price": 100},
                    {"time": "2026-01-01T00:00:00Z", "price": 101},
                    {"time": "2026-01-02T00:00:00Z", "price": 102},
                ],
            },
        }
        text = {
            "id": "text",
            "callback": "drawing.text",
            "params": {
                "targetVisualizerId": "close-line",
                "anchor": {"time": "2026-01-02T00:00:00Z", "price": 102},
                "text": "Breakout",
            },
        }
        spec["panes"][0]["visualizers"].extend([rectangle, brush, text])
        original_points = copy.deepcopy(brush["params"]["points"])
        self.compile(self.result, spec)
        self.assertEqual(
            spec["panes"][0]["visualizers"][2]["params"]["points"],
            original_points,
        )

        one_corner = copy.deepcopy(spec)
        one_corner["panes"][0]["visualizers"][1]["params"]["corners"].pop()
        with self.assertRaisesRegex(ValueError, "too short"):
            self.compile(self.result, one_corner)

        implicit_coordinate = copy.deepcopy(spec)
        implicit_coordinate["panes"][0]["visualizers"][1]["params"]["corners"][0][
            "x"
        ] = 10
        with self.assertRaisesRegex(ValueError, "Additional properties"):
            self.compile(self.result, implicit_coordinate)

        short_brush = copy.deepcopy(spec)
        short_brush["panes"][0]["visualizers"][2]["params"]["points"] = [
            {"time": "2026-01-01T00:00:00Z", "price": 100},
        ]
        with self.assertRaisesRegex(ValueError, "too short"):
            self.compile(self.result, short_brush)

        empty_text = copy.deepcopy(spec)
        empty_text["panes"][0]["visualizers"][3]["params"]["text"] = ""
        with self.assertRaisesRegex(ValueError, "non-empty"):
            self.compile(self.result, empty_text)

        invalid_style = copy.deepcopy(spec)
        invalid_style["panes"][0]["visualizers"][1]["params"]["fillOpacity"] = 2
        with self.assertRaisesRegex(ValueError, "maximum"):
            self.compile(self.result, invalid_style)

    def test_capability_descriptors_resolve_arbitrary_required_parameter_names(self):
        def definition(identifier, properties, required, *, provides=(), requires=()):
            return {
                "id": identifier,
                "label": identifier,
                "inputPorts": {},
                "paramsSchema": {
                    "type": "object",
                    "properties": copy.deepcopy(properties),
                    "required": list(required),
                    "additionalProperties": False,
                },
                "params": [{
                    "name": name,
                    "label": name,
                    "type": "string",
                    "required": name in required,
                } for name in properties],
                "renderer": {"id": identifier, "apiVersion": 1},
                "capabilities": {
                    "provides": copy.deepcopy(list(provides)),
                    "requires": copy.deepcopy(list(requires)),
                    "interactions": [],
                },
            }

        definitions = visualizer_definition_map()
        definitions["custom.coordinates"] = definition(
            "custom.coordinates",
            {
                "domain-slot.v2": {"type": "string", "minLength": 1},
                "scale-slot.v2": {"type": "string", "minLength": 1},
            },
            ["domain-slot.v2", "scale-slot.v2"],
            provides=[{
                "name": "coordinates",
                "kind": "custom.coordinate-kind",
                "attributes": {
                    "domain": "domain-slot.v2",
                    "time-domain": "domain-slot.v2",
                    "scale": "scale-slot.v2",
                },
            }],
        )
        definitions["custom.annotation"] = definition(
            "custom.annotation",
            {
                "source-ref.v2": {"type": "string", "minLength": 1},
                "own-domain.v2": {"type": "string", "minLength": 1},
            },
            ["source-ref.v2", "own-domain.v2"],
            requires=[{
                "name": "canvas",
                "kind": "custom.coordinate-kind",
                "bindingParam": "source-ref.v2",
                "matches": {"domain": "own-domain.v2"},
            }],
        )
        spec = copy.deepcopy(self.spec)
        spec["panes"][0]["visualizers"] = [
            {
                "id": "coordinates",
                "callback": "custom.coordinates",
                "params": {
                    "domain-slot.v2": "shared",
                    "scale-slot.v2": "left",
                },
            },
            {
                "id": "annotation",
                "callback": "custom.annotation",
                "params": {
                    "source-ref.v2": "coordinates",
                    "own-domain.v2": "shared",
                },
            },
        ]
        visualization_compiler.compile_visualization_contracts(
            self.result["dataKeys"],
            spec,
            module_repository.load_pipeline_definitions(self.config),
            definitions,
        )

        mismatch = copy.deepcopy(spec)
        mismatch["panes"][0]["visualizers"][1]["params"][
            "own-domain.v2"
        ] = "other"
        with self.assertRaisesRegex(ValueError, "attribute 'domain' must match"):
            visualization_compiler.compile_visualization_contracts(
                self.result["dataKeys"],
                mismatch,
                module_repository.load_pipeline_definitions(self.config),
                definitions,
            )

    def test_capability_descriptor_shape_is_strict_and_never_inferred(self):
        for field, identifier in (
            ("provides", "series.line"),
            ("requires", "drawing.horizontalLine"),
        ):
            with self.subTest(field=field):
                definitions = visualizer_definition_map()
                definitions[identifier]["capabilities"][field] = [
                    "series.price-coordinate"
                ]
                spec = copy.deepcopy(self.spec)
                if field == "requires":
                    spec["panes"][0]["visualizers"].append({
                        "id": "horizontal",
                        "callback": "drawing.horizontalLine",
                        "params": {
                            "targetVisualizerId": "close-line",
                            "price": 100,
                        },
                    })
                with self.assertRaisesRegex(ValueError, rf"{field}\[0\] must be an object"):
                    visualization_compiler.compile_visualization_contracts(
                        self.result["dataKeys"],
                        spec,
                        module_repository.load_pipeline_definitions(self.config),
                        definitions,
                    )

        unsorted = visualizer_definition_map()
        descriptor = unsorted["series.line"]["capabilities"]["provides"][0]
        unsorted["series.line"]["capabilities"]["provides"] = [
            {**copy.deepcopy(descriptor), "name": "z"},
            {**copy.deepcopy(descriptor), "name": "a"},
        ]
        with self.assertRaisesRegex(ValueError, "sorted by unique"):
            visualization_compiler.compile_visualization_contracts(
                self.result["dataKeys"],
                self.spec,
                module_repository.load_pipeline_definitions(self.config),
                unsorted,
            )

        duplicate_name = visualizer_definition_map()
        duplicate_name["series.line"]["capabilities"]["requires"] = [{
            "name": "series",
            "kind": "custom.kind",
            "bindingParam": "dataKey",
            "matches": {},
        }]
        with self.assertRaisesRegex(ValueError, "capability names must be unique"):
            visualization_compiler.compile_visualization_contracts(
                self.result["dataKeys"],
                self.spec,
                module_repository.load_pipeline_definitions(self.config),
                duplicate_name,
            )

        optional_mapping = visualizer_definition_map()
        optional_mapping["series.line"]["capabilities"]["provides"][0][
            "attributes"
        ]["time-domain"] = "color"
        with self.assertRaisesRegex(ValueError, "map to a required paramsSchema property"):
            visualization_compiler.compile_visualization_contracts(
                self.result["dataKeys"],
                self.spec,
                module_repository.load_pipeline_definitions(self.config),
                optional_mapping,
            )

    def test_capability_identifiers_reject_unicode_in_every_descriptor_position(self):
        cases = (
            ("name", "series.line"),
            ("kind", "series.line"),
            ("interactions", "series.line"),
            ("attributes", "series.line"),
            ("attributes-value", "series.line"),
            ("bindingParam", "drawing.horizontalLine"),
            ("matches", "drawing.horizontalLine"),
            ("matches-value", "drawing.horizontalLine"),
        )
        for location, identifier in cases:
            with self.subTest(location=location):
                definitions = visualizer_definition_map()
                if location in {"name", "kind"}:
                    definitions[identifier]["capabilities"]["provides"][0][
                        location
                    ] = "séries"
                elif location == "interactions":
                    definitions[identifier]["capabilities"][location] = [
                        "chart.pöinter"
                    ]
                elif location == "attributes":
                    definitions[identifier]["capabilities"]["provides"][0][
                        location
                    ]["price-échelle"] = "priceScaleId"
                elif location == "attributes-value":
                    definitions[identifier]["capabilities"]["provides"][0][
                        "attributes"
                    ]["price-scale"] = "price-échelle"
                elif location == "bindingParam":
                    definitions[identifier]["capabilities"]["requires"][0][
                        location
                    ] = "target-réference"
                elif location == "matches-value":
                    definitions[identifier]["capabilities"]["requires"][0][
                        "matches"
                    ]["price-domain"] = "prïce"
                else:
                    definitions[identifier]["capabilities"]["requires"][0][
                        location
                    ]["time-dömain"] = "price"

                spec = copy.deepcopy(self.spec)
                if identifier == "drawing.horizontalLine":
                    spec["panes"][0]["visualizers"].append({
                        "id": "horizontal",
                        "callback": identifier,
                        "params": {
                            "targetVisualizerId": "close-line",
                            "price": 100,
                        },
                    })
                with self.assertRaisesRegex(
                    ValueError,
                    "ASCII capability identifier grammar",
                ):
                    visualization_compiler.compile_visualization_contracts(
                        self.result["dataKeys"],
                        spec,
                        module_repository.load_pipeline_definitions(self.config),
                        definitions,
                    )

    def test_capability_identifiers_reject_exact_unsafe_names(self):
        cases = (
            ("name", "constructor"),
            ("kind", "prototype"),
            ("interactions", "__proto__"),
            ("attributes", "constructor"),
            ("attributes-value", "__proto__"),
            ("bindingParam", "constructor"),
            ("matches", "prototype"),
            ("matches-value", "prototype"),
        )
        for location, unsafe_name in cases:
            with self.subTest(location=location, unsafe_name=unsafe_name):
                definitions = visualizer_definition_map()
                identifier = (
                    "drawing.horizontalLine"
                    if location in {"bindingParam", "matches", "matches-value"}
                    else "series.line"
                )
                if location in {"name", "kind"}:
                    definitions[identifier]["capabilities"]["provides"][0][
                        location
                    ] = unsafe_name
                elif location == "interactions":
                    definitions[identifier]["capabilities"][location] = [unsafe_name]
                elif location == "attributes":
                    definitions[identifier]["capabilities"]["provides"][0][
                        location
                    ][unsafe_name] = "priceScaleId"
                elif location == "attributes-value":
                    definitions[identifier]["capabilities"]["provides"][0][
                        "attributes"
                    ]["price-scale"] = unsafe_name
                elif location == "bindingParam":
                    definitions[identifier]["capabilities"]["requires"][0][
                        location
                    ] = unsafe_name
                elif location == "matches-value":
                    definitions[identifier]["capabilities"]["requires"][0][
                        "matches"
                    ]["price-domain"] = unsafe_name
                else:
                    definitions[identifier]["capabilities"]["requires"][0][
                        location
                    ][unsafe_name] = "price"

                spec = copy.deepcopy(self.spec)
                if identifier == "drawing.horizontalLine":
                    spec["panes"][0]["visualizers"].append({
                        "id": "horizontal",
                        "callback": identifier,
                        "params": {
                            "targetVisualizerId": "close-line",
                            "price": 100,
                        },
                    })
                with self.assertRaisesRegex(
                    ValueError,
                    rf"unsafe capability identifier '{unsafe_name}'",
                ):
                    visualization_compiler.compile_visualization_contracts(
                        self.result["dataKeys"],
                        spec,
                        module_repository.load_pipeline_definitions(self.config),
                        definitions,
                    )

    def test_capability_identifiers_reject_invalid_ascii_segments(self):
        for invalid_name in (
            "1series",
            "series.",
            "series..price",
            "series.-price",
            "series__price",
        ):
            with self.subTest(invalid_name=invalid_name):
                definitions = visualizer_definition_map()
                definitions["series.line"]["capabilities"]["provides"][0][
                    "kind"
                ] = invalid_name
                with self.assertRaisesRegex(
                    ValueError,
                    "ASCII capability identifier grammar",
                ):
                    visualization_compiler.compile_visualization_contracts(
                        self.result["dataKeys"],
                        self.spec,
                        module_repository.load_pipeline_definitions(self.config),
                        definitions,
                    )

    def test_capability_identifiers_accept_legal_dot_and_hyphen_segments(self):
        definitions = visualizer_definition_map()
        provider = definitions["series.line"]["capabilities"]["provides"][0]
        provider["name"] = "constructor.v2"
        provider["kind"] = "vendor.series-price.v2"
        provider["attributes"]["coordinate-domain.v2"] = "timeDomainId"
        definitions["series.line"]["capabilities"]["interactions"] = [
            "chart.pointer-v2",
            "pointer_drag.v2",
        ]

        requirement = definitions["overlay.priceLine"]["capabilities"][
            "requires"
        ][0]
        requirement["name"] = "drawing.target-slot"
        requirement["kind"] = provider["kind"]
        requirement["matches"] = {
            "coordinate-domain.v2": "timeDomainId",
        }
        spec = copy.deepcopy(self.spec)
        spec["panes"][0]["visualizers"].append({
            "id": "latest-price",
            "callback": "overlay.priceLine",
            "params": {
                "dataKey": "price.close",
                "timeKey": "time",
                "timeDomainId": "result-cycle",
                "targetVisualizerId": "close-line",
                "reducer": "latest",
            },
        })
        visualization_compiler.compile_visualization_contracts(
            self.result["dataKeys"],
            spec,
            module_repository.load_pipeline_definitions(self.config),
            definitions,
        )

    def test_compiler_has_no_builtin_parameter_or_visualizer_capability_literals(self):
        source = Path(visualization_compiler.__file__).read_text(encoding="utf-8")
        for literal in (
            "targetVisualizerId",
            "timeDomainId",
            "priceScaleId",
            "series.price-coordinate",
            "drawing.rectangle",
            "drawing.brush",
            "drawing.text",
        ):
            with self.subTest(literal=literal):
                self.assertNotIn(literal, source)

    def test_definition_metadata_is_strict_but_unused_plugins_are_isolated(self):
        definitions = visualizer_definition_map()
        definitions["malformed.unused"] = {"id": "malformed.unused"}
        visualization_compiler.compile_visualization_contracts(
            self.result["dataKeys"],
            self.spec,
            module_repository.load_pipeline_definitions(self.config),
            definitions,
        )

        referenced = copy.deepcopy(self.spec)
        referenced["panes"][0]["visualizers"][0] = {
            "id": "bad",
            "callback": "malformed.unused",
            "params": {},
        }
        with self.assertRaisesRegex(ValueError, "Visualizer definition"):
            visualization_compiler.compile_visualization_contracts(
                self.result["dataKeys"],
                referenced,
                module_repository.load_pipeline_definitions(self.config),
                definitions,
            )

        invalid_renderer = visualizer_definition_map()
        invalid_renderer["series.line"]["renderer"]["apiVersion"] = 2
        with self.assertRaisesRegex(ValueError, "apiVersion 1"):
            visualization_compiler.compile_visualization_contracts(
                self.result["dataKeys"],
                self.spec,
                module_repository.load_pipeline_definitions(self.config),
                invalid_renderer,
            )

    def test_builtin_scalar_visualizers_follow_backend_literal_subset_semantics(self):
        for schema in ({"const": 1}, {"enum": [1, 2]}, False):
            with self.subTest(schema=schema):
                result = copy.deepcopy(self.result)
                result["dataKeys"]["literal"] = {
                    "schema": schema,
                    "required": False,
                }
                spec = copy.deepcopy(self.spec)
                spec["panes"][0]["visualizers"][0]["params"]["dataKey"] = (
                    "literal"
                )
                self.compile(result, spec)

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
            "params": [{
                "name": "dataKey",
                "label": "Data",
                "type": "dataKey",
                "required": True,
            }],
            "renderer": {"id": "custom.scalar", "apiVersion": 1},
            "capabilities": {
                "provides": [],
                "requires": [],
                "interactions": [],
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

    def test_root_and_pane_temporary_module_instance_ids_may_not_collide(self):
        definition = module_definition(
            "producer",
            outputs={"value": {"schema": {"type": "number"}}},
        )
        spec = copy.deepcopy(self.spec)
        spec["panes"][0]["visualizers"] = []
        spec["temporaryModules"] = [module_instance(
            "shared", "producer", outputs={"value": "root.value"},
        )]
        spec["panes"][0]["temporaryModules"] = [module_instance(
            "shared", "producer", outputs={"value": "pane.value"},
        )]
        with mock.patch(
            "engine.archive.version.verify_record",
            side_effect=lambda record: record,
        ):
            with self.assertRaisesRegex(
                ValueError,
                "instanceId 'shared' conflicts with root temporaryModules",
            ):
                visualization_compiler.compile_visualization_contracts(
                    self.result["dataKeys"],
                    spec,
                    {"Signal/producer/1": definition},
                    visualizer_definition_map(),
                )

    def test_root_and_pane_temporary_modules_may_not_produce_the_same_data_key(self):
        definition = module_definition(
            "producer",
            outputs={"value": {"schema": {"type": "number"}}},
        )
        spec = copy.deepcopy(self.spec)
        spec["panes"][0]["visualizers"] = []
        spec["temporaryModules"] = [module_instance(
            "root-producer", "producer", outputs={"value": "shared.value"},
        )]
        spec["panes"][0]["temporaryModules"] = [module_instance(
            "pane-producer", "producer", outputs={"value": "shared.value"},
        )]
        with mock.patch(
            "engine.archive.version.verify_record",
            side_effect=lambda record: record,
        ):
            with self.assertRaisesRegex(
                ValueError,
                "multiple temporary module producers for DataKey 'shared.value'",
            ):
                visualization_compiler.compile_visualization_contracts(
                    self.result["dataKeys"],
                    spec,
                    {"Signal/producer/1": definition},
                    visualizer_definition_map(),
                )

    def test_separate_panes_may_reuse_temporary_module_ids_and_output_keys(self):
        definition = module_definition(
            "producer",
            outputs={"value": {"schema": {"type": "number"}}},
        )
        spec = copy.deepcopy(self.spec)
        first = spec["panes"][0]
        first["visualizers"] = []
        first["temporaryModules"] = [module_instance(
            "local", "producer", outputs={"value": "derived.value"},
        )]
        second = copy.deepcopy(first)
        second["id"] = "second"
        second["title"] = "Second"
        spec["panes"].append(second)
        with mock.patch(
            "engine.archive.version.verify_record",
            side_effect=lambda record: record,
        ):
            contracts = visualization_compiler.compile_visualization_contracts(
                self.result["dataKeys"],
                spec,
                {"Signal/producer/1": definition},
                visualizer_definition_map(),
            )
        self.assertIn("time", contracts)

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
