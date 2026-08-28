import json
from pathlib import Path
import subprocess
import tempfile
import textwrap
import unittest


ROOT = Path(__file__).resolve().parents[2]


class BasicIndicatorPaneTests(unittest.TestCase):
    def test_basic_catalog_exposes_exact_twenty_descriptors(self):
        source = (ROOT / "web" / "basic_workflow_workspace.js").read_text()
        expected = ["sma", "ema", "wma", "vwma", "bb", "rsi", "macd", "atr", "stochastic", "obv", "roc", "cci", "williams-r", "dmi", "supertrend", "mfi", "parabolic-sar", "volume", "anchored-vwap", "ichimoku"]
        for item in expected:
            self.assertIn(f'indicatorDescriptor("{item}"', source)
        self.assertEqual(source.count("indicatorDescriptor(\""), 20)
        self.assertNotIn("requireBasicLineDefinition", source)
        self.assertIn("requireBasicSeriesDefinitions(indicator);", source)

    def test_rsi_and_macd_use_parameterized_managed_oscillator_panes(self):
        source = ROOT / "web" / "basic_workflow_workspace.js"
        script = textwrap.dedent(
            f"""
            const assert = require('node:assert/strict');
            const fs = require('node:fs');
            const vm = require('node:vm');
            const filename = {json.dumps(str(source))};
            const original = fs.readFileSync(filename, 'utf8');
            const instrumented = original.replace(
              'void main();',
              'globalThis.__paneTest = {{ state, applyIndicatorMutation, addedIndicatorRecords, financialPaneLayout, synchronizePaneTimeScales }};',
            );
            const context = {{
              console, structuredClone, URLSearchParams, AbortController,
              window: {{}}, document: {{}},
              location: {{ pathname: '/', search: '', hash: '', replace() {{}} }},
              fetch() {{ throw new Error('fetch must not run'); }},
            }};
            context.globalThis = context;
            vm.runInNewContext(instrumented, context, {{ filename }});
            const api = context.__paneTest;
            const basic = 'trade.basic-workflow';
            const ports = (input, outputs) => ({{
              inputs: {{ [input]: {{ schema: {{}}, required: true }} }},
              outputs: Object.fromEntries(outputs.map((name) => [name, {{ schema: {{}}, required: true }}])),
            }});
            const schema = (properties) => ({{ type: 'object', properties, additionalProperties: false }});
            api.state.catalog = {{
              visualizers: [
                {{ id: 'ohlc.candles', protocolId: basic }},
                {{ id: 'series.line', protocolId: basic }},
                {{ id: 'series.histogram', protocolId: basic }},
              ],
              modules: [
                {{ kind: 'Signal', moduleId: 'basic-price-close-selector', version: '1', protocolId: basic, ports: ports('price', ['close']) }},
                {{ kind: 'Signal', moduleId: 'rsi-indicator', version: '2', builtin: true, ports: ports('price', ['rsi']), configSchema: schema({{ period: {{ type: 'integer', default: 14, minimum: 1 }} }}) }},
                {{ kind: 'Signal', moduleId: 'macd-indicator', version: '3', builtin: true, ports: ports('price', ['macd', 'signal', 'histogram']), configSchema: schema({{
                  fastPeriod: {{ type: 'integer', default: 12, minimum: 1 }},
                  slowPeriod: {{ type: 'integer', default: 26, minimum: 1 }},
                  signalPeriod: {{ type: 'integer', default: 9, minimum: 1 }},
                }}) }},
              ],
            }};
            api.state.selectedInstrumentId = 'US-AAPL';
            api.state.period = 'day';
            const base = {{ schemaVersion: 3, datasetId: 'dataset', timeZone: 'UTC', temporaryModules: [], panes: [{{
              id: 'market', title: 'Market', role: 'financial', temporaryModules: [],
              view: {{ start: null, end: null, logScale: false, controlsCollapsed: false }},
              visualizers: [{{ id: 'candles', callback: 'ohlc.candles', params: {{
                dataKey: 'price.day.US-AAPL', timeDomainId: 'market-time', priceScaleId: 'market-price',
              }} }}],
            }}] }};
            const withRsi = api.applyIndicatorMutation(base, {{ type: 'add', indicatorTypeId: 'rsi', config: {{ period: 7 }} }});
            const complete = api.applyIndicatorMutation(withRsi, {{
              type: 'add', indicatorTypeId: 'macd',
              config: {{ fastPeriod: 8, slowPeriod: 21, signalPeriod: 5 }},
            }});
            assert.equal(complete.panes.length, 3);
            assert.deepEqual(Array.from(complete.panes, (pane) => pane.role), ['financial', 'oscillator', 'oscillator']);
            assert.deepEqual(Array.from(complete.panes[1].visualizers, (item) => item.callback), ['series.line']);
            assert.deepEqual(Array.from(complete.panes[2].visualizers, (item) => item.callback), ['series.histogram', 'series.line', 'series.line']);
            assert.equal(JSON.stringify(Array.from(api.addedIndicatorRecords(complete), (record) => record.config)), JSON.stringify([
              {{ period: 7 }}, {{ fastPeriod: 8, slowPeriod: 21, signalPeriod: 5 }},
            ]));
            const candle = {{ pane: complete.panes[0] }};
            assert.deepEqual(Array.from(api.financialPaneLayout(complete, candle), (pane) => pane.id), [
              'market', 'basic-indicator-rsi-1-pane', 'basic-indicator-macd-1-pane',
            ]);
            const withoutRsi = api.applyIndicatorMutation(complete, {{ type: 'remove', instanceId: 'basic-indicator-rsi-1' }});
            assert.equal(withoutRsi.panes.some((pane) => pane.id === 'basic-indicator-rsi-1-pane'), false);
            assert.equal(withoutRsi.panes.some((pane) => pane.id === 'basic-indicator-macd-1-pane'), true);

            const listeners = [];
            const ranges = [[], []];
            const chart = (index) => ({{ timeScale() {{ return {{
              subscribeVisibleTimeRangeChange(listener) {{ listeners[index] = listener; }},
              unsubscribeVisibleTimeRangeChange(listener) {{ assert.equal(listener, listeners[index]); }},
              setVisibleRange(range) {{ ranges[index].push(range); }},
            }}; }} }});
            const charts = [chart(0), chart(1)];
            const unsubscribe = api.synchronizePaneTimeScales(charts);
            listeners[0]({{ from: 10, to: 30 }});
            assert.deepEqual(ranges[1], [{{ from: 10, to: 30 }}]);
            assert.deepEqual(ranges[0], []);
            unsubscribe.forEach((callback) => callback());
            """
        )
        completed = subprocess.run(
            ["node", "-e", script],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_rsi_and_macd_panes_compile_against_installed_public_contracts(self):
        from builtin_implementations import resources as builtin_resources
        from builtin_implementations.basic_workflow_contracts import PRICE_SCHEMA
        from builtin_implementations.visualizer_contracts import visualizer_definition_map
        from engine.compiler import visualization as visualization_compiler
        from engine.control import database as engine_database
        from engine.repository import module_definitions as module_repository

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = {
                "controlRoot": str(root / "control"),
                "releaseRoot": str(root / "releases"),
                "liveRoot": str(root / "live"),
            }
            engine_database.prepare_database(config)
            builtin_resources.install(config)
            definitions = module_repository.load_pipeline_definitions(config)

            def latest(module_id):
                matches = [
                    item for item in definitions.values()
                    if item["kind"] == "Signal" and item["moduleId"] == module_id
                ]
                self.assertTrue(matches)
                return max(matches, key=lambda item: int(item["version"]))["version"]

            source = {
                "instanceId": "basic-indicator-price-close",
                "kind": "Signal",
                "moduleId": "basic-price-close-selector",
                "version": latest("basic-price-close-selector"),
                "config": {"decisionPeriod": "day", "instrumentId": "US-AAPL"},
                "inputs": {"price": "price"},
                "outputs": {"close": "indicator.source.close"},
            }
            rsi = {
                "instanceId": "basic-indicator-rsi-1",
                "kind": "Signal",
                "moduleId": "rsi-indicator",
                "version": latest("rsi-indicator"),
                "config": {"period": 14},
                "inputs": {"price": "indicator.source.close"},
                "outputs": {"rsi": "indicator.basic.rsi.1.rsi"},
            }
            macd = {
                "instanceId": "basic-indicator-macd-1",
                "kind": "Signal",
                "moduleId": "macd-indicator",
                "version": latest("macd-indicator"),
                "config": {"fastPeriod": 12, "slowPeriod": 26, "signalPeriod": 9},
                "inputs": {"price": "indicator.source.close"},
                "outputs": {
                    "macd": "indicator.basic.macd.1.macd",
                    "signal": "indicator.basic.macd.1.signal",
                    "histogram": "indicator.basic.macd.1.histogram",
                },
            }
            view = {
                "start": None,
                "end": None,
                "logScale": False,
                "controlsCollapsed": False,
            }
            series = lambda identifier, callback, data_key, color: {
                "id": identifier,
                "callback": callback,
                "params": {
                    "dataKey": data_key,
                    "timeKey": "time",
                    "timeDomainId": "market-time",
                    "priceScaleId": f"{identifier}-scale",
                    "color": color,
                    **({"positiveColor": "#089981", "negativeColor": "#f23645"}
                       if callback == "series.histogram" else {"lineWidth": 2}),
                },
            }
            spec = {
                "schemaVersion": 3,
                "datasetId": "dataset",
                "timeZone": "UTC",
                "temporaryModules": [source, rsi, macd],
                "panes": [
                    {
                        "id": "market",
                        "title": "Market",
                        "role": "financial",
                        "view": dict(view),
                        "temporaryModules": [],
                        "visualizers": [{
                            "id": "candles",
                            "callback": "ohlc.candles",
                            "params": {
                                "dataKey": "price.day.US-AAPL",
                                "timeDomainId": "market-time",
                                "priceScaleId": "market-price",
                            },
                        }],
                    },
                    {
                        "id": "basic-indicator-rsi-1-pane",
                        "title": "RSI",
                        "role": "oscillator",
                        "view": dict(view),
                        "temporaryModules": [],
                        "visualizers": [series(
                            "basic-indicator-rsi-1-rsi",
                            "series.line",
                            "indicator.basic.rsi.1.rsi",
                            "#7c3aed",
                        )],
                    },
                    {
                        "id": "basic-indicator-macd-1-pane",
                        "title": "MACD",
                        "role": "oscillator",
                        "view": dict(view),
                        "temporaryModules": [],
                        "visualizers": [
                            series("basic-indicator-macd-1-histogram", "series.histogram", "indicator.basic.macd.1.histogram", "#64748b"),
                            series("basic-indicator-macd-1-macd", "series.line", "indicator.basic.macd.1.macd", "#2563eb"),
                            series("basic-indicator-macd-1-signal", "series.line", "indicator.basic.macd.1.signal", "#f59e0b"),
                        ],
                    },
                ],
            }
            contracts = visualization_compiler.compile_visualization_contracts(
                {
                    "time": {"schema": {"type": "string"}, "required": True},
                    "price": {"schema": PRICE_SCHEMA, "required": True},
                },
                spec,
                definitions,
                visualizer_definition_map(),
            )
            for data_key in (
                "indicator.basic.rsi.1.rsi",
                "indicator.basic.macd.1.macd",
                "indicator.basic.macd.1.signal",
                "indicator.basic.macd.1.histogram",
            ):
                self.assertIn(data_key, contracts)

    def test_all_twenty_catalog_items_compile_and_preserve_legacy_source(self):
        from builtin_implementations import resources as builtin_resources
        from builtin_implementations.basic_workflow_contracts import (
            OHLCV_PRICE_SCHEMA,
        )
        from builtin_implementations.visualizer_contracts import (
            visualizer_definition_map,
        )
        from engine.compiler import visualization as visualization_compiler
        from engine.control import database as engine_database
        from engine.repository import module_definitions as module_repository

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = {
                "controlRoot": str(root / "control"),
                "releaseRoot": str(root / "releases"),
                "liveRoot": str(root / "live"),
            }
            engine_database.prepare_database(config)
            builtin_resources.install(config)
            definitions = module_repository.load_pipeline_definitions(config)
            catalog_modules = []
            for definition in definitions.values():
                if definition["kind"] != "Signal":
                    continue
                catalog_modules.append({
                    field: definition[field]
                    for field in (
                        "kind",
                        "moduleId",
                        "version",
                        "ports",
                        "configSchema",
                    )
                } | {
                    **(
                        {"protocolId": definition["protocolId"]}
                        if "protocolId" in definition
                        else {"builtin": True}
                    ),
                    "status": "active",
                })
            visualizers = [
                {"id": definition["id"], "protocolId": definition["protocolId"]}
                for definition in visualizer_definition_map().values()
                if definition.get("protocolId") == "trade.basic-workflow"
            ]
            source = ROOT / "web" / "basic_workflow_workspace.js"
            script = textwrap.dedent(
                f"""
                const assert = require('node:assert/strict');
                const fs = require('node:fs');
                const vm = require('node:vm');
                const filename = {json.dumps(str(source))};
                const original = fs.readFileSync(filename, 'utf8');
                const instrumented = original.replace(
                  'void main();',
                  'globalThis.__allIndicatorTest = {{ state, BASIC_INDICATORS, applyIndicatorMutation, addedIndicatorRecords }};',
                );
                const context = {{
                  console: {{ log() {{}}, warn() {{}}, error() {{}} }},
                  structuredClone, URLSearchParams, AbortController,
                  window: {{}}, document: {{}},
                  location: {{ pathname: '/', search: '', hash: '', replace() {{}} }},
                  fetch() {{ throw new Error('fetch must not run'); }},
                }};
                context.globalThis = context;
                vm.runInNewContext(instrumented, context, {{ filename }});
                const api = context.__allIndicatorTest;
                api.state.catalog = {{
                  modules: {json.dumps(catalog_modules)},
                  visualizers: {json.dumps(visualizers)},
                }};
                api.state.resultView = {{ dataKeys: {{ price: {{
                  schema: {json.dumps(OHLCV_PRICE_SCHEMA)},
                }} }} }};
                api.state.selectedInstrumentId = 'US-AAPL';
                api.state.period = 'day';
                const base = {{ schemaVersion: 3, datasetId: 'dataset', timeZone: 'UTC', temporaryModules: [], panes: [{{
                  id: 'market', title: 'Market', role: 'financial', temporaryModules: [],
                  view: {{ start: null, end: null, logScale: false, controlsCollapsed: false }},
                  visualizers: [{{ id: 'candles', callback: 'ohlc.candles', params: {{
                    dataKey: 'price.day.US-AAPL', timeDomainId: 'market-time', priceScaleId: 'market-price',
                  }} }}],
                }}] }};

                let complete = base;
                for (const indicator of api.BASIC_INDICATORS) {{
                  complete = api.applyIndicatorMutation(complete, {{
                    type: 'add', indicatorTypeId: indicator.typeId, config: {{}},
                  }});
                }}
                const records = api.addedIndicatorRecords(complete);
                assert.equal(records.length, 20);
                assert.equal(complete.panes.length, 12);
                assert.deepEqual(
                  Array.from(records, (record) => record.indicator.typeId),
                  Array.from(api.BASIC_INDICATORS, (indicator) => indicator.typeId),
                );
                const installedModules = api.state.catalog.modules;
                api.state.catalog.modules = installedModules.filter((item) => (
                  item.moduleId !== 'basic-price-bar-selector'
                ));
                assert.throws(() => api.applyIndicatorMutation(base, {{
                  type: 'add', indicatorTypeId: 'sma', config: {{ period: 20 }},
                }}), /Basic OHLCV price selector has no installed Module definition/);
                api.state.catalog.modules = installedModules;
                const ichimoku = records.find((record) => record.indicator.typeId === 'ichimoku');
                assert.deepEqual(
                  Array.from(ichimoku.indicator.series, (series) => (
                    ichimoku.pane.visualizers.find((item) => (
                      item.params.dataKey === ichimoku.outputs[series.outputPort]
                    )).params.offsetBars || 0
                  )),
                  [0, 0, 26, 26, -26],
                );

                let anchored = base;
                for (const anchor of ['dataset', 'week', 'month', 'year']) {{
                  anchored = api.applyIndicatorMutation(anchored, {{
                    type: 'add', indicatorTypeId: 'anchored-vwap', config: {{ anchor }},
                  }});
                }}
                assert.deepEqual(
                  Array.from(api.addedIndicatorRecords(anchored), (record) => record.module.inputs.reset),
                  ['indicator.source.resetDataset', 'indicator.source.resetWeek', 'indicator.source.resetMonth', 'indicator.source.resetYear'],
                );
                assert.throws(() => api.applyIndicatorMutation(base, {{
                  type: 'add', indicatorTypeId: 'anchored-vwap', config: {{ anchor: 'session' }},
                }}), /supported values/);
                assert.throws(() => api.applyIndicatorMutation(base, {{
                  type: 'add', indicatorTypeId: 'supertrend', config: {{ multiplier: 0 }},
                }}), /greater than/);

                let legacy = api.applyIndicatorMutation(base, {{
                  type: 'add', indicatorTypeId: 'sma', config: {{ period: 20 }},
                }});
                const legacySource = legacy.temporaryModules.find((item) => item.instanceId === 'basic-indicator-price-close');
                legacySource.moduleId = 'basic-price-close-selector';
                legacySource.version = api.state.catalog.modules
                  .filter((item) => item.moduleId === 'basic-price-close-selector')
                  .sort((left, right) => Number(right.version) - Number(left.version))[0].version;
                legacySource.outputs = {{ close: 'indicator.source.close' }};
                assert.equal(api.addedIndicatorRecords(legacy).length, 1);
                const migrated = api.applyIndicatorMutation(legacy, {{
                  type: 'add', indicatorTypeId: 'ema', config: {{ period: 12 }},
                }});
                const migratedSource = migrated.temporaryModules.find((item) => item.instanceId === 'basic-indicator-price-close');
                assert.equal(migratedSource.moduleId, 'basic-price-bar-selector');
                assert.equal(Object.keys(migratedSource.outputs).length, 11);
                assert.equal(api.addedIndicatorRecords(migrated).length, 2);
                const withoutEma = api.applyIndicatorMutation(migrated, {{
                  type: 'remove', instanceId: 'basic-indicator-ema-1',
                }});
                const withoutSma = api.applyIndicatorMutation(withoutEma, {{
                  type: 'remove', instanceId: 'basic-indicator-sma-1',
                }});
                assert.equal(withoutSma.temporaryModules.some((item) => item.instanceId === 'basic-indicator-price-close'), false);

                const reserved = structuredClone(base);
                reserved.temporaryModules.push({{
                  instanceId: 'outside', kind: 'Signal', moduleId: 'outside', version: '1',
                  config: {{}}, inputs: {{}}, outputs: {{ value: 'indicator.source.volume' }},
                }});
                assert.throws(() => api.applyIndicatorMutation(reserved, {{
                  type: 'add', indicatorTypeId: 'volume', config: {{}},
                }}), /reserved output DataKey/);
                process.stdout.write(JSON.stringify(complete));
                """
            )
            completed = subprocess.run(
                ["node", "-e", script],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            spec = json.loads(completed.stdout)
            contracts = visualization_compiler.compile_visualization_contracts(
                {
                    "time": {"schema": {"type": "string"}, "required": True},
                    "price": {"schema": OHLCV_PRICE_SCHEMA, "required": True},
                },
                spec,
                definitions,
                visualizer_definition_map(),
            )
            indicator_keys = {
                value
                for module in spec["temporaryModules"]
                if module["instanceId"].startswith("basic-indicator-")
                and module["instanceId"] != "basic-indicator-price-close"
                for value in module["outputs"].values()
            }
            technical_visualizers = [
                visualizer
                for pane in spec["panes"]
                for visualizer in pane["visualizers"]
                if visualizer["callback"] != "ohlc.candles"
            ]
            self.assertTrue(indicator_keys)
            self.assertTrue(indicator_keys.issubset(contracts))
            self.assertEqual(len(technical_visualizers), 31)
            self.assertTrue(all(
                visualizer["params"]["dataKey"] in indicator_keys
                for visualizer in technical_visualizers
            ))
            self.assertFalse(any(
                visualizer["params"]["dataKey"].startswith("price")
                for visualizer in technical_visualizers
            ))
            self.assertEqual(len(contracts), 89)


if __name__ == "__main__":
    unittest.main()
