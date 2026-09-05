#!/usr/bin/env python3
"""Focused contracts for the standalone Result chart lifecycle."""

import json
from pathlib import Path
import shutil
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[2]
CHART_SOURCE = (ROOT / "web" / "chart.js").read_text(encoding="utf-8")


class StandaloneChartTests(unittest.TestCase):
    def function_source(self, name, next_name):
        return CHART_SOURCE.split(f"function {name}(", 1)[1].split(
            f"function {next_name}(", 1
        )[0]

    def test_saved_styles_are_not_rewritten_from_peer_visualizers(self):
        main_source = CHART_SOURCE.split("async function main()", 1)[1]

        self.assertNotIn("normalizeVisualizerStylesForLoad", CHART_SOURCE)
        self.assertNotIn("normalizeVisualizerStyleCollisions", CHART_SOURCE)
        self.assertNotIn("allocateDistinctVisualizerStyles", CHART_SOURCE)
        self.assertIn("pageState.backtest.visualization || {}", main_source)

    @unittest.skipUnless(shutil.which("node"), "Node.js is required")
    def test_pane_id_and_scoped_temporary_modules_drive_per_visualizer_requests(self):
        script = r"""
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('web/chart.js', 'utf8').replace(/\nmain\(\)\.catch\([\s\S]*$/, '');
const context = {
  URLSearchParams,
  location: { search: '?backtestId=bt_1&paneId=target', pathname: '/chart.html', hash: '' },
  window: {
    TradeModuleForms: {},
    TradeChartCore: {
      visualizerDependencyPlan(_result, _pane, scoped) {
        if (scoped.temporaryModules.map((item) => item.instanceId).join(',') !== 'root,pane') {
          throw new Error('temporary module scope is incomplete');
        }
        return [
          {
            visualizerId: 'z-line',
            paths: ['cycles.data.z'],
            temporaryModules: scoped.temporaryModules.filter((item) => item.instanceId === 'pane'),
          },
          { visualizerId: 'drawing', paths: [], temporaryModules: [] },
          { visualizerId: 'a-line', paths: ['cycles.data.a'], temporaryModules: [] },
        ];
      },
    },
  },
  console,
  setTimeout,
  clearTimeout,
  AbortController,
};
vm.createContext(context);
vm.runInContext(source, context);
const output = vm.runInContext(`
  pageState.backtest = { dataKeys: { a: { schema: { type: 'number' } } } };
  pageState.spec = {
    temporaryModules: [{ instanceId: 'root' }],
    panes: [
      { id: 'first', temporaryModules: [], visualizers: [] },
      { id: 'target', temporaryModules: [{ instanceId: 'pane' }], visualizers: [] },
    ],
  };
  paneIndex = resolveRequestedPaneIndex(pageState.spec);
  pageState.pane = pageState.spec.panes[paneIndex];
  pageState.paneId = pageState.pane.id;
  JSON.stringify({ paneIndex, paneId: pageState.paneId, request: paneResultRequest() });
`, context);
process.stdout.write(output);
"""
        completed = subprocess.run(
            [shutil.which("node"), "-e", script],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        result = json.loads(completed.stdout)
        self.assertEqual(result["paneIndex"], 1)
        self.assertEqual(result["paneId"], "target")
        self.assertEqual(
            [item["visualizerId"] for item in result["request"]["plans"]],
            ["a-line", "z-line"],
        )
        self.assertEqual(result["request"]["plans"][0]["paths"], ["cycles.data.a"])
        self.assertEqual(result["request"]["plans"][1]["paths"], ["cycles.data.z"])
        self.assertEqual(
            [
                item["instanceId"]
                for item in result["request"]["plans"][1]["temporaryModules"]
            ],
            ["pane"],
        )
        self.assertNotIn("key", result["request"])

    @unittest.skipUnless(shutil.which("node"), "Node.js is required")
    def test_typed_map_metadata_is_discovered_before_the_first_pane_load(self):
        script = r"""
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('web/chart.js', 'utf8').replace(/\nmain\(\)\.catch\([\s\S]*$/, '');
const context = {
  URLSearchParams,
  location: { search: '?backtestId=bt_1&pane=0', pathname: '/chart.html', hash: '' },
  window: {
    TradeModuleForms: {},
    TradeChartCore: {
      discoverySourcePaths() { return ['cycles.data.price']; },
      dataKeyDeclarations(result) {
        if (!result.cycles?.length || !result.dataKeys?.price) throw new Error('discovery merge was incomplete');
        return { ...result.dataKeys, 'price.hour.NASDAQ_COMPOSITE': { schema: { type: 'object' } } };
      },
    },
  },
  console,
  setTimeout,
  clearTimeout,
  AbortController,
};
vm.createContext(context);
vm.runInContext(source, context);
(async () => {
  const output = await vm.runInContext(`(async () => {
    pageState.backtest = { dataKeys: { price: { schema: { type: 'object' } } } };
    const calls = [];
    postJson = async (path, payload) => {
      calls.push({ path, payload: JSON.parse(JSON.stringify(payload)) });
      return { result: { cycles: [{ data: { price: { hour: { NASDAQ_COMPOSITE: {} } } } }] } };
    };
    await discoverBacktestDataKeys();
    window.TradeChartCore.discoverySourcePaths = () => [];
    await discoverBacktestDataKeys();
    return JSON.stringify({ calls, dataKeyNames: Object.keys(pageState.backtest.dataKeys).sort() });
  })()`, context);
  process.stdout.write(output);
})().catch((error) => { console.error(error); process.exitCode = 1; });
"""
        completed = subprocess.run(
            [shutil.which("node"), "-e", script],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        result = json.loads(completed.stdout)
        self.assertEqual(len(result["calls"]), 1)
        self.assertEqual(result["calls"][0]["payload"]["paths"], ["cycles.data.price"])
        self.assertEqual(result["calls"][0]["payload"]["temporaryModules"], [])
        self.assertEqual(
            result["dataKeyNames"],
            ["price", "price.hour.NASDAQ_COMPOSITE"],
        )
        main_source = CHART_SOURCE.split("async function main()", 1)[1]
        self.assertLess(
            main_source.index("await discoverBacktestDataKeys()"),
            main_source.index("await loadPaneResult()"),
        )

    def test_data_dependency_changes_reload_and_failures_require_explicit_retry(self):
        sync_source = self.function_source("syncSpec", "scheduleSpecSave")
        load_source = self.function_source("loadPaneResult", "uiState")
        draw_source = self.function_source("drawPane", "main")

        self.assertIn("loadPaneResult()", sync_source)
        self.assertIn("cache.entries.get(plan.visualizerId)", load_source)
        self.assertIn("existing?.dependencyKey === plan.dependencyKey", load_source)
        self.assertIn("temporaryModules: plan.temporaryModules", load_source)
        self.assertIn("entry.result = response.result || {}", load_source)
        self.assertIn('entry.status = "error"', load_source)
        self.assertIn("current.generation !== generation", load_source)
        self.assertIn("if (plan.planningError)", load_source)
        self.assertIn("dataset.retryChartData", draw_source)
        self.assertIn("loadPaneResult({ force: true, visualizerId:", CHART_SOURCE)
        self.assertNotIn("loadPaneResult()", draw_source)

    @unittest.skipUnless(shutil.which("node"), "Node.js is required")
    def test_result_request_planning_failures_enter_recoverable_error_state(self):
        script = r"""
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('web/chart.js', 'utf8').replace(/\nmain\(\)\.catch\([\s\S]*$/, '');
const status = { textContent: '' };
const context = {
  URLSearchParams,
  location: { search: '?backtestId=bt_1&pane=0', pathname: '/chart.html', hash: '' },
  window: {
    TradeModuleForms: {},
    TradeChartCore: {
      visualizerDependencyPlan() { throw new Error('invalid dependency plan'); },
    },
  },
  document: { getElementById() { return status; } },
  status,
  console,
};
vm.createContext(context);
vm.runInContext(source, context);
(async () => {
  const output = await vm.runInContext(`(async () => {
    pageState.backtest = { dataKeys: {} };
    pageState.spec = { temporaryModules: [], panes: [] };
    pageState.pane = { id: 'pane', temporaryModules: [], visualizers: [] };
    let draws = 0;
    drawPane = () => { draws += 1; };
    const resolved = await loadPaneResult();
    return JSON.stringify({
      resolved,
      draws,
      status: pageState.resultStatus,
      error: pageState.resultError,
      cacheGeneration: pageState.projectionCache.generation,
      statusText: status.textContent,
    });
  })()`, context);
  process.stdout.write(output);
})().catch((error) => { console.error(error); process.exitCode = 1; });
"""
        completed = subprocess.run(
            [shutil.which("node"), "-e", script],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        result = json.loads(completed.stdout)
        self.assertFalse(result["resolved"])
        self.assertEqual(result["draws"], 1)
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["error"], "Chart data dependencies could not be planned.")
        self.assertEqual(result["statusText"], "Chart data dependencies could not be planned.")
        self.assertEqual(result["cacheGeneration"], 0)

    @unittest.skipUnless(shutil.which("node"), "Node.js is required")
    def test_result_loader_reuses_dependencies_and_does_not_retry_a_failure(self):
        script = r"""
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('web/chart.js', 'utf8').replace(/\nmain\(\)\.catch\([\s\S]*$/, '');
const status = { textContent: '' };
const context = {
  URLSearchParams,
  location: { search: '?backtestId=bt_1&pane=0', pathname: '/chart.html', hash: '' },
  window: {
    TradeModuleForms: {},
    TradeChartCore: {
      visualizerDependencyPlan(_result, pane, scoped) {
        return [{
          visualizerId: pane.visualizers[0].id,
          paths: [`cycles.data.${pane.visualizers[0].params.dataKey}`],
          temporaryModules: scoped.temporaryModules,
        }];
      },
    },
  },
  document: { getElementById() { return status; } },
  console,
  setTimeout,
  clearTimeout,
  AbortController,
};
vm.createContext(context);
vm.runInContext(source, context);
(async () => {
  const output = await vm.runInContext(`(async () => {
    pageState.backtest = { dataKeys: {} };
    pageState.spec = {
      temporaryModules: [{ instanceId: 'root' }],
      panes: [{
        id: 'pane',
        temporaryModules: [{ instanceId: 'local' }],
        visualizers: [{ id: 'line', callback: 'series.line', params: { dataKey: 'a' } }],
      }],
    };
    paneIndex = 0;
    pageState.pane = pageState.spec.panes[0];
    pageState.paneId = 'pane';
    drawPane = () => {};
    const calls = [];
    postJson = async (_path, payload) => {
      calls.push(JSON.parse(JSON.stringify(payload)));
      if (payload.paths[0].endsWith('.broken')) throw new Error('projection failed');
      return { result: { dataKeys: {}, cycles: [] } };
    };
    await loadPaneResult();
    await loadPaneResult();
    pageState.pane.visualizers[0].params.dataKey = 'b';
    await loadPaneResult();
    pageState.pane.visualizers[0].params.dataKey = 'broken';
    await loadPaneResult();
    const callsAfterFailure = calls.length;
    await new Promise((resolve) => setTimeout(resolve, 10));
    const callsWithoutRetry = calls.length;
    await loadPaneResult({ force: true, visualizerId: 'line' });
    return JSON.stringify({
      calls,
      callsAfterFailure,
      callsWithoutRetry,
      status: pageState.resultStatus,
      errors: pageState.result.errors,
    });
  })()`, context);
  process.stdout.write(output);
})().catch((error) => { console.error(error); process.exitCode = 1; });
"""
        completed = subprocess.run(
            [shutil.which("node"), "-e", script],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        result = json.loads(completed.stdout)
        self.assertEqual(result["callsAfterFailure"], 3)
        self.assertEqual(result["callsWithoutRetry"], 3)
        self.assertEqual(len(result["calls"]), 4)
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["errors"]["line"]["message"], "projection failed")
        self.assertEqual(
            [item["instanceId"] for item in result["calls"][0]["temporaryModules"]],
            ["root", "local"],
        )

    @unittest.skipUnless(shutil.which("node"), "Node.js is required")
    def test_instances_load_independently_and_retry_preserves_successful_slices(self):
        script = r"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('web/chart.js', 'utf8').replace(/\nmain\(\)\.catch\([\s\S]*$/, '');
const plans = [
  { visualizerId: 'a', paths: ['cycles.data.a'], temporaryModules: [] },
  {
    visualizerId: 'b',
    paths: ['cycles.data.raw'],
    temporaryModules: [{ instanceId: 'thrower' }],
  },
  { visualizerId: 'drawing', paths: [], temporaryModules: [] },
];
const context = {
  URLSearchParams,
  location: { search: '?backtestId=bt_1&pane=0', pathname: '/chart.html', hash: '' },
  window: {
    TradeModuleForms: {},
    TradeChartCore: { visualizerDependencyPlan() { return plans; } },
  },
  document: { getElementById() { return { textContent: '' }; } },
  console,
  setTimeout,
  clearTimeout,
  AbortController,
};
vm.createContext(context);
vm.runInContext(source, context);
(async () => {
  const output = await vm.runInContext(`(async () => {
    pageState.backtest = { dataKeys: {} };
    pageState.spec = { temporaryModules: [], panes: [{
      id: 'pane', temporaryModules: [], visualizers: [
        { id: 'a', params: { color: '#111111' } },
        { id: 'b', params: {} },
        { id: 'drawing', params: {} },
      ],
    }] };
    pageState.pane = pageState.spec.panes[0];
    pageState.paneId = 'pane';
    let failB = true;
    let draws = 0;
    drawPane = () => { draws += 1; };
    const calls = [];
    postResultJson = async (_path, payload) => {
      calls.push(JSON.parse(JSON.stringify(payload)));
      if (payload.temporaryModules[0]?.instanceId === 'thrower' && failB) {
        throw new Error('temporary module failed');
      }
      return { result: { cycles: [{ data: { token: payload.paths[0] } }] } };
    };
    const keyBefore = JSON.stringify(paneResultRequest().plans.map(
      ({ visualizerId, dependencyKey }) => ({ visualizerId, dependencyKey }),
    ));
    await loadPaneResult();
    const afterFailure = JSON.parse(JSON.stringify(pageState.result));
    await loadPaneResult();
    pageState.pane.visualizers[0].params.color = '#222222';
    const keyAfterStyle = JSON.stringify(paneResultRequest().plans.map(
      ({ visualizerId, dependencyKey }) => ({ visualizerId, dependencyKey }),
    ));
    await loadPaneResult();
    const callsBeforeRetry = calls.length;
    failB = false;
    await loadPaneResult({ force: true, visualizerId: 'b' });
    return JSON.stringify({
      calls,
      callsBeforeRetry,
      draws,
      keyBefore,
      keyAfterStyle,
      afterFailure,
      finalResult: pageState.result,
      status: pageState.resultStatus,
    });
  })()`, context);
  const result = JSON.parse(output);
  assert.equal(result.callsBeforeRetry, 2);
  assert.equal(result.calls.length, 3);
  assert.deepEqual(result.calls[0], {
    paths: ['cycles.data.a'], temporaryModules: [],
    projectionFormat: 'columns-v2', window: null,
  });
  assert.deepEqual(result.calls[1], {
    paths: ['cycles.data.raw'], temporaryModules: [{ instanceId: 'thrower' }],
    projectionFormat: 'columns-v2', window: null,
  });
  assert.deepEqual(result.calls[2], result.calls[1]);
  assert.equal(result.keyBefore, result.keyAfterStyle);
  assert.ok(result.afterFailure.instanceResults.a);
  assert.equal(result.afterFailure.errors.b.message, 'temporary module failed');
  assert.equal(result.afterFailure.instanceResults.drawing, undefined);
  assert.ok(result.finalResult.instanceResults.a);
  assert.ok(result.finalResult.instanceResults.b);
  assert.deepEqual(result.finalResult.errors, {});
  assert.equal(result.status, 'ready');
  assert.ok(result.draws >= 4);
  process.stdout.write('ok');
})().catch((error) => { console.error(error); process.exitCode = 1; });
"""
        completed = subprocess.run(
            [shutil.which("node"), "-e", script],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr or completed.stdout)

    @unittest.skipUnless(shutil.which("node"), "Node.js is required")
    def test_stale_instance_response_cannot_publish_and_reserved_ids_are_safe(self):
        script = r"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('web/chart.js', 'utf8').replace(/\nmain\(\)\.catch\([\s\S]*$/, '');
const context = {
  URLSearchParams,
  location: { search: '?backtestId=bt_1&pane=0', pathname: '/chart.html', hash: '' },
  window: {
    TradeModuleForms: {},
    TradeChartCore: {
      visualizerDependencyPlan(_result, pane) {
        return [
          {
            visualizerId: '__proto__',
            paths: [`cycles.data.${pane.visualizers[0].params.dataKey}`],
            temporaryModules: [],
          },
          {
            visualizerId: 'constructor',
            paths: ['cycles.data.failure'],
            temporaryModules: [],
          },
        ];
      },
    },
  },
  document: { getElementById() { return { textContent: '' }; } },
  console,
  setTimeout,
  clearTimeout,
  AbortController,
};
vm.createContext(context);
vm.runInContext(source, context);
(async () => {
  const output = await vm.runInContext(`(async () => {
    pageState.backtest = { dataKeys: {} };
    pageState.spec = { temporaryModules: [], panes: [{
      id: 'pane', temporaryModules: [],
      visualizers: [{ id: '__proto__', params: { dataKey: 'old' } }],
    }] };
    pageState.pane = pageState.spec.panes[0];
    pageState.paneId = 'pane';
    drawPane = () => {};
    let resolveOld;
    postResultJson = async (_path, payload) => {
      if (payload.paths[0] === 'cycles.data.failure') throw new Error('reserved failure');
      if (payload.paths[0] === 'cycles.data.old') {
        return new Promise((resolve) => { resolveOld = resolve; });
      }
      return { result: { token: 'new' } };
    };
    const oldLoad = loadPaneResult();
    await Promise.resolve();
    pageState.pane.visualizers[0].params.dataKey = 'new';
    const newLoad = loadPaneResult();
    await newLoad;
    const beforeOldSettles = pageState.result.instanceResults.__proto__.token;
    resolveOld({ result: { token: 'old' } });
    await oldLoad;
    return JSON.stringify({
      beforeOldSettles,
      finalToken: pageState.result.instanceResults.__proto__.token,
      resultPrototypeIsNull: Object.getPrototypeOf(pageState.result.instanceResults) === null,
      errorPrototypeIsNull: Object.getPrototypeOf(pageState.result.errors) === null,
      ownsProto: Object.prototype.hasOwnProperty.call(pageState.result.instanceResults, '__proto__'),
      ownsConstructor: Object.prototype.hasOwnProperty.call(pageState.result.errors, 'constructor'),
      constructorMessage: pageState.result.errors.constructor.message,
      objectPrototypePolluted: Object.prototype.token !== undefined,
    });
  })()`, context);
  const result = JSON.parse(output);
  assert.equal(result.beforeOldSettles, 'new');
  assert.equal(result.finalToken, 'new');
  assert.equal(result.resultPrototypeIsNull, true);
  assert.equal(result.errorPrototypeIsNull, true);
  assert.equal(result.ownsProto, true);
  assert.equal(result.ownsConstructor, true);
  assert.equal(result.constructorMessage, 'reserved failure');
  assert.equal(result.objectPrototypePolluted, false);
  process.stdout.write('ok');
})().catch((error) => { console.error(error); process.exitCode = 1; });
"""
        completed = subprocess.run(
            [shutil.which("node"), "-e", script],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr or completed.stdout)

    @unittest.skipUnless(shutil.which("node"), "Node.js is required")
    def test_instance_cache_reloads_only_changed_visualizer_and_localizes_planning_failures(self):
        script = r"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('web/chart.js', 'utf8').replace(/\nmain\(\)\.catch\([\s\S]*$/, '');
let resolveSlowB;
let slowController;
let forbidA = false;
const calls = [];
const context = {
  assert,
  calls,
  forbidA,
  resolveSlowB,
  slowController,
  URLSearchParams,
  location: { search: '?backtestId=bt_1&pane=0', pathname: '/chart.html', hash: '' },
  window: {
    TradeModuleForms: {},
    TradeChartCore: {
      visualizerDependencyPlan(_result, pane) {
        return pane.visualizers.flatMap((item) => {
          if (item.visible === false) return [];
          if (item.id === 'drawing') {
            return [{ visualizerId: item.id, paths: [], temporaryModules: [] }];
          }
          if (item.params.planningFailure) {
            return [{
              visualizerId: item.id, paths: [], temporaryModules: [],
              planningError: { code: 'dependency-cycle', message: 'B cannot be planned' },
            }];
          }
          return [{
            visualizerId: item.id,
            paths: [`cycles.data.${item.params.dataKey}`],
            temporaryModules: [],
          }];
        });
      },
    },
  },
  document: { getElementById() { return { textContent: '' }; } },
  console,
  setTimeout,
  clearTimeout,
  AbortController,
};
vm.createContext(context);
vm.runInContext(source, context);
(async () => {
  const output = await vm.runInContext(`(async () => {
    pageState.backtest = { dataKeys: {} };
    pageState.spec = { temporaryModules: [], panes: [{
      id: 'pane', temporaryModules: [], visualizers: [
        { id: 'a', visible: true, params: { dataKey: 'a', color: '#111' } },
        { id: 'b', visible: true, params: { dataKey: 'b1' } },
        { id: 'drawing', visible: true, params: {} },
      ],
    }] };
    pageState.pane = pageState.spec.panes[0];
    pageState.paneId = 'pane';
    drawPane = () => {};
    postResultJson = async (_path, payload, controller) => {
      const body = JSON.parse(JSON.stringify(payload));
      calls.push(body);
      const dataKey = body.paths[0];
      if (dataKey === 'cycles.data.a' && forbidA) throw new Error('A must not reload');
      if (dataKey === 'cycles.data.bSlow') {
        slowController = controller;
        return new Promise((resolve) => { resolveSlowB = resolve; });
      }
      return { result: { token: dataKey } };
    };

    await loadPaneResult();
    const cache = projectionCache();
    const aEntry = cache.entries.get('a');
    forbidA = true;
    pageState.pane.visualizers[1].params.dataKey = 'b2';
    await loadPaneResult();
    assert.equal(calls.length, 3);
    assert.deepEqual(calls[2], {
      paths: ['cycles.data.b2'], temporaryModules: [],
      projectionFormat: 'columns-v2', window: null,
    });
    assert.equal(cache.entries.get('a'), aEntry);
    assert.equal(pageState.result.instanceResults.a.token, 'cycles.data.a');

    pageState.pane.visualizers[1].params.dataKey = 'bSlow';
    const staleLoad = loadPaneResult();
    await Promise.resolve();
    assert.equal(pageState.resultStatus, 'ready');
    assert.equal(pageState.result.instanceResults.a.token, 'cycles.data.a');
    assert.deepEqual([...pendingVisualizerIds(cache, paneResultRequest())], ['b']);
    pageState.pane.visualizers[1].params.dataKey = 'bNew';
    await loadPaneResult();
    assert.equal(slowController.signal.aborted, true);
    resolveSlowB({ result: { token: 'cycles.data.bSlow' } });
    await staleLoad;
    assert.equal(pageState.result.instanceResults.b.token, 'cycles.data.bNew');
    assert.equal(cache.entries.get('a'), aEntry);

    const callsBeforeHide = calls.length;
    pageState.pane.visualizers[1].visible = false;
    await loadPaneResult();
    assert.equal(calls.length, callsBeforeHide);
    assert.equal(cache.entries.get('a'), aEntry);
    assert.equal(cache.entries.has('b'), false);
    assert.equal(pageState.result.instanceResults.a.token, 'cycles.data.a');

    pageState.pane.visualizers[1].visible = true;
    pageState.pane.visualizers[1].params.planningFailure = true;
    await loadPaneResult();
    assert.equal(calls.length, callsBeforeHide);
    assert.equal(pageState.result.instanceResults.a.token, 'cycles.data.a');
    assert.equal(pageState.result.errors.b.code, 'instance-planning-error');
    assert.equal(pageState.result.errors.b.planningCode, 'dependency-cycle');
    assert.equal(cache.entries.get('a'), aEntry);
    return JSON.stringify({ calls: calls.length, status: pageState.resultStatus });
  })()`, context);
  const result = JSON.parse(output);
  assert.equal(result.calls, 5);
  assert.equal(result.status, 'ready');
  process.stdout.write('ok');
})().catch((error) => { console.error(error); process.exitCode = 1; });
"""
        completed = subprocess.run(
            [shutil.which("node"), "-e", script],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr or completed.stdout)

    def test_edit_drafts_preserve_temporary_and_visualizer_values(self):
        temp_source = self.function_source(
            "fillTemporaryModuleDraft", "addPaneTemporaryModule"
        )
        add_temp_source = self.function_source(
            "addPaneTemporaryModule", "removePaneTemporaryModule"
        )
        visualizer_source = self.function_source(
            "fillVisualizerDraft", "addPaneVisualizer"
        )

        self.assertIn("selectedItem?.instanceId", temp_source)
        self.assertIn("selectedItem?.config", temp_source)
        self.assertIn("selectedItem?.inputs", temp_source)
        self.assertIn("selectedItem?.outputs", temp_source)
        self.assertIn("const instanceId = selectedId ||", add_temp_source)
        self.assertIn("remapTemporaryModuleConsumers(previousItem, outputs)", add_temp_source)
        self.assertIn("previousItem?.outputs", temp_source)
        self.assertIn("selectedItem?.params", visualizer_source)

    @unittest.skipUnless(shutil.which("node"), "Node.js is required")
    def test_visualizer_edit_preserves_visibility(self):
        script = r"""
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('web/chart.js', 'utf8').replace(/\nmain\(\)\.catch\([\s\S]*$/, '');
const definition = { id: 'series.line', params: [], inputPorts: {}, optionMap: {} };
const context = {
  URLSearchParams,
  location: { search: '?backtestId=bt_1&pane=0', pathname: '/chart.html', hash: '' },
  window: {
    TradeModuleForms: { readParamFields() { return { dataKey: 'price' }; } },
    TradeChartCore: {
      visualizerCatalog() { return [definition]; },
      createVisualizerInstance(item, values) {
        return { id: values.id, callback: item.id, params: values.params };
      },
      upsertIdentity(items, currentId, nextItem) {
        return items.map((item) => item.id === currentId ? nextItem : item);
      },
    },
  },
  document: {
    querySelector(selector) {
      if (selector === '[data-visualizer-select]') return { value: 'series.line' };
      if (selector === '[data-visualizer-fields]') return {};
      return null;
    },
  },
  console,
};
vm.createContext(context);
vm.runInContext(source, context);
const output = vm.runInContext(`
  pageState.backtest = { dataKeys: {} };
  pageState.spec = { temporaryModules: [], panes: [] };
  pageState.pane = {
    temporaryModules: [],
    visualizers: [{
      id: 'line-1', callback: 'series.line',
      params: { dataKey: 'old-price' }, visible: false,
    }],
  };
  uiState().selectedVisualizerId = 'line-1';
  syncSpec = () => {};
  addPaneVisualizer();
  JSON.stringify(pageState.pane.visualizers[0]);
`, context);
process.stdout.write(output);
"""
        completed = subprocess.run(
            [shutil.which("node"), "-e", script],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        result = json.loads(completed.stdout)
        self.assertEqual(result["id"], "line-1")
        self.assertEqual(result["params"], {"dataKey": "price"})
        self.assertIs(result["visible"], False)

    @unittest.skipUnless(shutil.which("node"), "Node.js is required")
    def test_same_millisecond_visualizer_adds_receive_unique_ids(self):
        script = r"""
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('web/chart.js', 'utf8').replace(/\nmain\(\)\.catch\([\s\S]*$/, '');
const definition = { id: 'ohlc.candles', params: [], inputPorts: {}, optionMap: {} };
const context = {
  URLSearchParams,
  Date: class FixedDate extends Date { static now() { return 1234; } },
  location: { search: '?backtestId=bt_1&pane=0', pathname: '/chart.html', hash: '' },
  window: {
    TradeModuleForms: { readParamFields() { return {}; } },
    TradeChartCore: {
      visualizerCatalog() { return [definition]; },
      createVisualizerInstance(item, values) {
        return { id: values.id, callback: item.id, params: values.params };
      },
      upsertIdentity(items, currentId, nextItem) {
        if (!currentId) return [...items, nextItem];
        return items.map((item) => item.id === currentId ? nextItem : item);
      },
    },
  },
  document: {
    querySelector(selector) {
      if (selector === '[data-visualizer-select]') return { value: 'ohlc.candles' };
      if (selector === '[data-visualizer-fields]') return {};
      return null;
    },
  },
  console,
};
vm.createContext(context);
vm.runInContext(source, context);
const output = vm.runInContext(`
  pageState.backtest = { dataKeys: {} };
  pageState.spec = { temporaryModules: [], panes: [] };
  pageState.pane = { temporaryModules: [], visualizers: [] };
  syncSpec = () => {};
  addPaneVisualizer();
  addPaneVisualizer();
  JSON.stringify(pageState.pane.visualizers.map((item) => item.id));
`, context);
process.stdout.write(output);
"""
        completed = subprocess.run(
            [shutil.which("node"), "-e", script],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        ids = json.loads(completed.stdout)
        self.assertEqual(len(ids), 2)
        self.assertEqual(len(set(ids)), 2)
        self.assertEqual(ids[1], f"{ids[0]}.2")

    @unittest.skipUnless(shutil.which("node"), "Node.js is required")
    def test_temporary_module_required_ports_are_rejected_before_creation(self):
        script = r"""
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('web/chart.js', 'utf8').replace(/\nmain\(\)\.catch\([\s\S]*$/, '');
const context = {
  URLSearchParams,
  location: { search: '?backtestId=bt_1&pane=0', pathname: '/chart.html', hash: '' },
  window: {
    TradeModuleForms: { humanizeName(value) { return value; } },
    TradeChartCore: {},
  },
  console,
};
vm.createContext(context);
vm.runInContext(source, context);
const output = vm.runInContext(`
  const messages = [];
  const capture = (callback) => {
    try { callback(); messages.push('accepted'); }
    catch (error) { messages.push(error.message); }
  };
  capture(() => validateTemporaryModuleBindings({}, { result: 'out' }, {
    ports: { inputs: { source: { required: true } }, outputs: { result: { required: true } } },
  }));
  capture(() => validateTemporaryModuleBindings({}, {}, {
    ports: { inputs: {}, outputs: { result: { required: true } } },
  }));
  capture(() => validateTemporaryModuleBindings({}, {}, {
    ports: { inputs: {}, outputs: { result: { required: false } } },
  }));
  JSON.stringify(messages);
`, context);
process.stdout.write(output);
"""
        completed = subprocess.run(
            [shutil.which("node"), "-e", script],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertEqual(
            json.loads(completed.stdout),
            [
                "source input is required",
                "result output is required",
                "Temporary Module must bind at least one output",
            ],
        )

    @unittest.skipUnless(shutil.which("node"), "Node.js is required")
    def test_overlay_displays_require_a_visible_capability_provider_instance(self):
        script = r"""
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('web/chart.js', 'utf8').replace(/\nmain\(\)\.catch\([\s\S]*$/, '');
const context = {
  URLSearchParams,
  location: { search: '?backtestId=bt_1&pane=0', pathname: '/chart.html', hash: '' },
  window: {
    TradeModuleForms: { humanizeName(value) { return value; } },
    TradeChartCore: {
      visualizerCatalog() {
        return [
          {
            id: 'series.line',
            capabilities: {
              provides: [{
                name: 'series', kind: 'series.price-coordinate',
                attributes: { scale: 'scaleId' },
              }],
              requires: [],
            },
          },
          {
            id: 'series.clock',
            capabilities: {
              provides: [{
                name: 'clock', kind: 'series.time-coordinate',
                attributes: { domain: 'domainId' },
              }],
              requires: [],
            },
          },
          {
            id: 'overlay.markers',
            params: [
              { name: 'anchorSource', label: 'Anchor', type: 'string' },
              { name: 'clockSource', label: 'Clock', type: 'string' },
              { name: 'scaleId', label: 'Scale', type: 'string' },
              { name: 'domainId', label: 'Domain', type: 'string' },
            ],
            capabilities: {
              provides: [],
              requires: [
                {
                  name: 'anchor', kind: 'series.price-coordinate',
                  bindingParam: 'anchorSource', matches: { scale: 'scaleId' },
                },
                {
                  name: 'clock', kind: 'series.time-coordinate',
                  bindingParam: 'clockSource', matches: { domain: 'domainId' },
                },
              ],
            },
          },
        ];
      },
    },
  },
  console,
};
vm.createContext(context);
vm.runInContext(source, context);
const output = vm.runInContext(`
  pageState.backtest = { dataKeys: {} };
  pageState.spec = { temporaryModules: [], panes: [] };
  pageState.pane = {
    temporaryModules: [],
    visualizers: [
      { id: 'line', callback: 'series.line', params: {
        dataKey: 'price', scaleId: { mode: 'linear', axis: 'right' },
      } },
      { id: 'clock', callback: 'series.clock', params: { domainId: 'market' } },
      { id: 'markers', callback: 'overlay.markers', params: {
        anchorSource: 'line', clockSource: 'clock',
        scaleId: { axis: 'right', mode: 'linear' }, domainId: 'market',
      } },
    ],
  };
  const definition = window.TradeChartCore.visualizerCatalog()[2];
  const messages = [];
  const capture = (callback) => {
    try { callback(); messages.push('accepted'); }
    catch (error) { messages.push(error.message); }
  };
  capture(() => validateVisualizerOverlayDependencies(
    definition, {
      anchorSource: 'missing', clockSource: 'clock',
      scaleId: { axis: 'right', mode: 'linear' }, domainId: 'market',
    },
  ));
  capture(() => validateVisualizerOverlayDependencies(
    definition, {
      anchorSource: 'line', clockSource: 'clock',
      scaleId: { axis: 'right', mode: 'linear' }, domainId: 'market',
    },
  ));
  const editor = visualizerDefinitionForEditor(definition, {
    scaleId: { axis: 'right', mode: 'linear' }, domainId: 'market',
  });
  messages.push(editor.params.filter((field) => field.name.endsWith('Source')).map((field) => ({
    name: field.name, type: field.type, options: field.options,
  })));
  uiState().selectedVisualizerId = 'line';
  capture(() => validateVisualizerOverlayDependencies(
    definition, {
      anchorSource: 'line', clockSource: 'clock',
      scaleId: { axis: 'right', mode: 'linear' }, domainId: 'market',
    },
  ));
  JSON.stringify(messages);
`, context);
process.stdout.write(output);
"""
        completed = subprocess.run(
            [shutil.which("node"), "-e", script],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        result = json.loads(completed.stdout)
        self.assertIn("Anchor must reference", result[0])
        self.assertEqual(result[1], "accepted")
        self.assertEqual(
            result[2],
            [
                {
                    "name": "anchorSource",
                    "type": "visualizerRef",
                    "options": [
                        {"value": "", "label": "Select…"},
                        {"value": "line", "label": "Data Display 1"},
                    ],
                },
                {
                    "name": "clockSource",
                    "type": "visualizerRef",
                    "options": [
                        {"value": "", "label": "Select…"},
                        {"value": "clock", "label": "Data Display 2"},
                    ],
                },
            ],
        )
        self.assertIn("Anchor must reference", result[3])

    def test_template_and_data_display_share_the_searchable_hierarchy(self):
        renderer = self.function_source("renderChartControls", "bindControls")

        self.assertIn("data-combobox-path", renderer)
        self.assertIn("data-combobox-meta", renderer)
        self.assertIn("Search or browse Templates", renderer)
        self.assertIn("Search or browse Data Displays", renderer)
        self.assertEqual(renderer.count("forms.enhanceSearchableSelect("), 2)

    def test_standalone_tags_match_the_compact_accessible_result_contract(self):
        renderer = self.function_source("renderChartControls", "bindControls")
        labeler = self.function_source("visualizerTagLabel", "visualizerSummary")
        summary = self.function_source("visualizerSummary", "applyButtonLabel")

        self.assertEqual(renderer.count('class="chart-layer-tag-group"'), 2)
        self.assertEqual(renderer.count('</button><button class="tag-remove"'), 2)
        self.assertNotIn(">Remove</button>", renderer)
        self.assertEqual(renderer.count('<span aria-hidden="true">×</span>'), 2)
        self.assertEqual(renderer.count('aria-label="Remove '), 2)
        self.assertIn('title="${escapeHtml(tagTitle)}"', renderer)
        self.assertIn('title="${escapeHtml(summary)}"', renderer)
        self.assertIn('<span class="layer-key">${escapeHtml(tagLabel)}</span>', renderer)
        self.assertIn('data-remove-visualizer="${escapeHtml(visualizer.id)}"', renderer)
        self.assertIn('data-remove-temp-module="${escapeHtml(module.instanceId)}"', renderer)

        self.assertIn("definition?.inputPorts", labeler)
        self.assertIn("visualizer.params?.[name]", labeler)
        self.assertIn("new Set", labeler)
        self.assertIn('join(" · ")', labeler)
        self.assertIn("visualizerTagLabel", summary)

    @unittest.skipUnless(shutil.which("node"), "Node.js is required")
    def test_semantic_labels_keep_opaque_values_internal_only(self):
        script = r"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const context = {
  URLSearchParams,
  location: { search: '?backtestId=bt_01M0Z2D7FZACAARVSZPZH34P63&pane=0', pathname: '/chart.html', hash: '' },
  window: {},
  console,
};
context.window.window = context.window;
vm.createContext(context);
vm.runInContext(fs.readFileSync('web/module_forms.js', 'utf8'), context);
context.window.TradeChartCore = {
  visualizerCatalog() {
    return [
      { id: 'ohlc.candles', label: 'Candles', inputPorts: { dataKey: {} } },
      { id: 'drawing.horizontalLine', label: 'Horizontal Line', inputPorts: {} },
    ];
  },
};
const source = fs.readFileSync('web/chart.js', 'utf8').replace(/\nmain\(\)\.catch\([\s\S]*$/, '');
vm.runInContext(source, context);
const output = vm.runInContext(`(() => {
  pageState.backtest = { dataKeys: {} };
  pageState.resultModules = {
    module: {
      key: 'module', kind: 'analysis', moduleId: 'mod_01M0Z2D7FZACAARVSZPZH34P63',
      version: 1, name: 'Moving Average', ports: { outputs: { result: {} } },
    },
  };
  pageState.spec = { temporaryModules: [], panes: [] };
  pageState.pane = {
    id: 'pane_01M0Z2D7FZACAARVSZPZH34P63',
    title: 'pane_01M0Z2D7FZACAARVSZPZH34P63',
    temporaryModules: [{
      instanceId: 'inst_0123456789abcdef', kind: 'analysis',
      moduleId: 'mod_01M0Z2D7FZACAARVSZPZH34P63', version: 1,
      outputs: { result: 'price.average' },
    }],
    visualizers: [
      { id: 'ohlc.candles.m9v0z7q2', callback: 'ohlc.candles', params: { dataKey: 'price.day', targetVisualizerId: 'ohlc.candles.x8y7z6w5' } },
      { id: 'ohlc.candles.x8y7z6w5', callback: 'ohlc.candles', params: { dataKey: 'price.week' } },
    ],
  };
  const catalog = window.TradeChartCore.visualizerCatalog();
  const visualizerLabels = [...visualizerDisplayLabelMap(pageState.pane, catalog).values()];
  const temporaryLabels = [...temporaryModuleDisplayLabelMap().values()];
  const tag = visualizerTagLabel(pageState.pane.visualizers[0], catalog[0], visualizerLabels[0]);
  return JSON.stringify({ visualizerLabels, temporaryLabels, tag, title: paneDisplayTitle() });
})()`, context);
const result = JSON.parse(output);
assert.deepEqual(result.visualizerLabels, ['Candles 1', 'Candles 2']);
assert.deepEqual(result.temporaryLabels, ['Moving Average']);
assert.equal(result.tag, 'Candles 1 · price.day');
assert.equal(result.title, 'Chart');
for (const opaque of [
  'ohlc.candles.m9v0z7q2', 'ohlc.candles.x8y7z6w5',
  'inst_0123456789abcdef', 'pane_01M0Z2D7FZACAARVSZPZH34P63',
]) assert.equal(output.includes(opaque), false);
process.stdout.write('ok');
"""
        completed = subprocess.run(
            [shutil.which("node"), "-e", script],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr or completed.stdout)

    @unittest.skipUnless(shutil.which("node"), "Node.js is required")
    def test_active_tag_clicks_clear_edit_selection_and_redraw_drafts(self):
        script = r"""
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('web/chart.js', 'utf8').replace(/\nmain\(\)\.catch\([\s\S]*$/, '');
const context = {
  URLSearchParams,
  location: { search: '?backtestId=bt_1&pane=0', pathname: '/chart.html', hash: '' },
  window: { TradeModuleForms: {}, TradeChartCore: {} },
  document: { querySelector() { return null; } },
  console,
};
vm.createContext(context);
vm.runInContext(source, context);
const output = vm.runInContext(`
  const tempTag = {
    dataset: { selectTempModule: 'tmp-1' },
    addEventListener(_name, callback) { this.click = callback; },
  };
  const visualizerTag = {
    dataset: { selectVisualizer: 'candles-1' },
    addEventListener(_name, callback) { this.click = callback; },
  };
  const area = {
    querySelectorAll(selector) {
      if (selector === '[data-select-temp-module]') return [tempTag];
      if (selector === '[data-select-visualizer]') return [visualizerTag];
      return [];
    },
    querySelector() { return null; },
  };
  const draws = [];
  drawPane = () => draws.push({ ...uiState() });
  bindControls(area);
  tempTag.click();
  tempTag.click();
  visualizerTag.click();
  visualizerTag.click();
  JSON.stringify({ draws, final: uiState() });
`, context);
process.stdout.write(output);
"""
        completed = subprocess.run(
            [shutil.which("node"), "-e", script],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        result = json.loads(completed.stdout)
        self.assertEqual(
            result["draws"],
            [
                {"selectedTempModuleId": "tmp-1", "selectedVisualizerId": ""},
                {"selectedTempModuleId": "", "selectedVisualizerId": ""},
                {"selectedTempModuleId": "", "selectedVisualizerId": "candles-1"},
                {"selectedTempModuleId": "", "selectedVisualizerId": ""},
            ],
        )
        self.assertEqual(
            result["final"],
            {"selectedTempModuleId": "", "selectedVisualizerId": ""},
        )
        temp_draft = self.function_source(
            "fillTemporaryModuleDraft", "addPaneTemporaryModule"
        )
        visualizer_draft = self.function_source(
            "fillVisualizerDraft", "addPaneVisualizer"
        )
        self.assertIn('instanceInput.value = ""', temp_draft)
        self.assertIn('fields.innerHTML = ""', visualizer_draft)

    def test_missing_chart_library_keeps_the_configuration_editor_available(self):
        draw_source = self.function_source("drawPane", "renderStartupError")
        main_source = CHART_SOURCE.split("async function main()", 1)[1]

        self.assertLess(
            draw_source.index("controls = renderChartControls()"),
            draw_source.index("!window.LightweightCharts?.createChart"),
        )
        self.assertIn("Configuration remains available above", draw_source)
        self.assertIn("bindControls(area)", draw_source)
        self.assertIn("renderPaneDrawFailure(area, panel, controls, error)", draw_source)
        self.assertNotIn("if (!window.LightweightCharts)", main_source)

    @unittest.skipUnless(shutil.which("node"), "Node.js is required")
    def test_draw_failures_preserve_controls_rollback_resources_and_retry_in_place(self):
        script = r"""
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('web/chart.js', 'utf8').replace(/\nmain\(\)\.catch\([\s\S]*$/, '');

class FakeElement {
  constructor(tagName = 'div') {
    this.tagName = tagName;
    this.children = [];
    this.parentNode = null;
    this.className = '';
    this.dataset = {};
    this.style = {};
    this.listeners = {};
    this.textContent = '';
    this.hidden = false;
  }
  appendChild(child) {
    if (child.parentNode) child.parentNode.removeChild(child);
    this.children.push(child);
    child.parentNode = this;
    return child;
  }
  insertBefore(child, before) {
    if (child.parentNode) child.parentNode.removeChild(child);
    const index = this.children.indexOf(before);
    this.children.splice(index < 0 ? this.children.length : index, 0, child);
    child.parentNode = this;
    return child;
  }
  removeChild(child) {
    const index = this.children.indexOf(child);
    if (index >= 0) this.children.splice(index, 1);
    child.parentNode = null;
    return child;
  }
  remove() { this.parentNode?.removeChild(this); }
  addEventListener(name, callback) { this.listeners[name] = callback; }
  setAttribute(name, value) { this[name] = value; }
  querySelector(selector) {
    const match = (node) => {
      if (selector.startsWith('.')) return node.className.split(/\s+/).includes(selector.slice(1));
      const data = selector.match(/^\[data-([a-z-]+)\]$/)?.[1];
      if (data) {
        const key = data.replace(/-([a-z])/g, (_all, letter) => letter.toUpperCase());
        return Object.prototype.hasOwnProperty.call(node.dataset, key);
      }
      return false;
    };
    const queue = [...this.children];
    while (queue.length) {
      const node = queue.shift();
      if (match(node)) return node;
      queue.push(...node.children);
    }
    return null;
  }
  querySelectorAll() { return []; }
  set innerHTML(_value) {
    this.children.forEach((child) => { child.parentNode = null; });
    this.children = [];
  }
  get innerHTML() { return ''; }
}

const area = new FakeElement('main');
const status = new FakeElement('span');
const errors = [];
const context = {
  URLSearchParams,
  location: { search: '?backtestId=bt_1&pane=0', pathname: '/chart.html', hash: '' },
  window: { TradeModuleForms: {}, LightweightCharts: { createChart() {} }, TradeChartCore: {} },
  document: {
    createElement(tagName) { return new FakeElement(tagName); },
    getElementById(id) { return id === 'singleChartArea' ? area : id === 'chartStatus' ? status : null; },
    querySelector() { return null; },
  },
  ResizeObserver: class { observe() {} disconnect() {} },
  console: { error(...args) { errors.push(args.map(String).join(' ')); }, log() {} },
  area,
  status,
  errors,
  setTimeout,
  clearTimeout,
  AbortController,
};
vm.createContext(context);
vm.runInContext(source, context);
const output = vm.runInContext(`(() => {
  let phase = '';
  let boundControls = 0;
  let removedCharts = 0;
  let toolbarCleanups = 0;
  let contextCleanups = 0;
  renderChartControls = () => {
    const controls = document.createElement('section');
    controls.className = 'chart-controls';
    return controls;
  };
  bindControls = () => { boundControls += 1; };
  currentPaneProjection = () => ({
    cache: { entries: new Map() },
    request: { plans: [] },
  });
  renderPaneDiagnostics = () => {};
  subscribePaneView = () => {};
  window.TradeChartCore = {
    prepareFinancialPane() { return {}; },
    paneTimeInfo() {
      if (phase === 'time') throw new Error('paneTimeInfo exploded');
      return { start: 1, end: 2, showTime: false, diagnostics: [] };
    },
    chartTime() { return null; },
    formatRangeInput() { return ''; },
    createFinancialChart() {
      if (phase === 'create') throw new Error('createChart exploded');
      return {
        remove() {
          removedCharts += 1;
          if (phase === 'toolbar') throw new Error('chart cleanup exploded');
        },
        timeScale() { return { fitContent() {} }; },
        applyOptions() {},
      };
    },
    drawFinancialPane() {
      if (phase === 'draw') throw new Error('drawFinancialPane exploded');
      return {
        diagnostics: [],
        interactionController: {},
        cleanups: [
          () => { contextCleanups += 1; throw new Error('context cleanup exploded'); },
          () => { contextCleanups += 1; },
        ],
      };
    },
  };
  createDrawingToolbar = () => {
    const toolbar = {
      cleanup() { toolbarCleanups += 1; throw new Error('toolbar cleanup exploded'); },
    };
    if (phase === 'toolbar') {
      Object.defineProperty(toolbar, 'element', {
        get() { throw new Error('drawing toolbar exploded'); },
      });
    } else {
      toolbar.element = document.createElement('nav');
      toolbar.element.className = 'chart-drawing-toolbar';
    }
    return toolbar;
  };

  pageState.backtest = { backtestId: 'bt_1', dataKeys: {} };
  pageState.result = { dataKeys: {}, instanceResults: {}, errors: {} };
  pageState.spec = { timeZone: 'UTC', temporaryModules: [], panes: [] };
  pageState.pane = {
    id: 'pane', title: 'Pane', temporaryModules: [],
    view: { start: null, end: null, logScale: false, controlsCollapsed: false },
    visualizers: [{ id: 'line', callback: 'series.line', params: {} }],
  };
  pageState.resultStatus = 'ready';

  const results = [];
  for (const name of ['library', 'time', 'create', 'draw', 'toolbar']) {
    phase = name;
    if (name === 'library') delete window.LightweightCharts.createChart;
    else window.LightweightCharts.createChart = () => {};
    drawPane();
    const panel = area.querySelector('.chart-panel');
    const retry = panel?.querySelector('[data-retry-chart-render]');
    const failure = panel?.querySelector('.chart-load-error');
    const failureState = {
      childClasses: (panel?.children || []).map((child) => child.className),
      detail: failure?.children?.[0]?.textContent || '',
      chartIsNull: pageState.chart === null,
      observerIsNull: pageState.observer === null,
      drawingCleanupIsNull: pageState.drawingCleanup === null,
    };
    phase = 'healthy';
    window.LightweightCharts.createChart = () => {};
    retry?.listeners?.click?.();
    const redrawnPanel = area.querySelector('.chart-panel');
    results.push({
      name,
      ...failureState,
      retry: !!retry,
      redrawn: !!redrawnPanel?.querySelector('.tv-chart')
        && !redrawnPanel?.querySelector('.chart-load-error')
        && pageState.chart !== null,
    });
  }
  return JSON.stringify({
    results,
    boundControls,
    removedCharts,
    toolbarCleanups,
    contextCleanups,
    errors: errors.length,
    status: status.textContent,
  });
})()`, context);
process.stdout.write(output);
"""
        completed = subprocess.run(
            [shutil.which("node"), "-e", script],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        result = json.loads(completed.stdout)
        self.assertEqual(
            [item["name"] for item in result["results"]],
            ["library", "time", "create", "draw", "toolbar"],
        )
        for item in result["results"]:
            with self.subTest(phase=item["name"]):
                self.assertEqual(
                    item["childClasses"],
                    ["chart-controls", "chart-load-error"],
                )
                self.assertIn("Chart rendering failed:", item["detail"])
                self.assertTrue(item["retry"])
                self.assertTrue(item["chartIsNull"])
                self.assertTrue(item["observerIsNull"])
                self.assertTrue(item["drawingCleanupIsNull"])
                self.assertTrue(item["redrawn"])
        self.assertEqual(result["boundControls"], 10)
        self.assertEqual(result["removedCharts"], 5)
        self.assertEqual(result["toolbarCleanups"], 5)
        self.assertEqual(result["contextCleanups"], 8)
        self.assertGreaterEqual(result["errors"], 5)
        self.assertEqual(result["status"], "Ready")

    def test_add_actions_are_disabled_until_their_fields_are_valid(self):
        labels = self.function_source("setActionButtonLabels", "syncSpec")
        binder = self.function_source("bindControls", "setViewError")

        self.assertIn("temporaryModuleActionState()", labels)
        self.assertIn("visualizerActionState()", labels)
        self.assertEqual(labels.count(".disabled = action.disabled"), 2)
        self.assertIn('addEventListener("input", setActionButtonLabels)', binder)
        self.assertIn('addEventListener("change", setActionButtonLabels)', binder)

    @unittest.skipUnless(shutil.which("node"), "Node.js is required")
    def test_renderer_diagnostics_show_semantic_label_without_instance_id(self):
        script = r"""
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('web/chart.js', 'utf8').replace(/\nmain\(\)\.catch\([\s\S]*$/, '');
const context = {
  URLSearchParams,
  location: { search: '?backtestId=bt_1&pane=0', pathname: '/chart.html', hash: '' },
  window: { TradeModuleForms: {}, TradeChartCore: {} },
  document: { createElement() { return {
    className: '', textContent: '', children: [], dataset: {},
    appendChild(node) { this.children.push(node); },
    addEventListener() {},
  }; } },
  console,
};
vm.createContext(context);
vm.runInContext(source, context);
const output = vm.runInContext(`
  const panel = { children: [], appendChild(node) { this.children.push(node); } };
  renderPaneDiagnostics(panel, [{
    code: 'input-schema-mismatch',
    visualizerId: 'candles-legacy',
    renderer: 'ohlc.candles',
    message: 'eventTime encoding is required for Candles.',
  }]);
  JSON.stringify(panel.children[0]);
`, context);
process.stdout.write(output);
"""
        completed = subprocess.run(
            [shutil.which("node"), "-e", script],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        result = json.loads(completed.stdout)
        self.assertEqual(result["className"], "chart-load-error")
        self.assertEqual(
            result["children"][0]["children"][0]["textContent"],
            "Data Display · input-schema-mismatch: eventTime encoding is required for Candles.",
        )
        draw_source = self.function_source("drawPane", "renderStartupError")
        self.assertIn("chartContext?.diagnostics", draw_source)
        self.assertIn("pageState.timeInfo?.diagnostics", draw_source)

    @unittest.skipUnless(shutil.which("node"), "Node.js is required")
    def test_log_scale_targets_the_single_chart_pane(self):
        script = r"""
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('web/chart.js', 'utf8').replace(/\nmain\(\)\.catch\([\s\S]*$/, '');
const context = {
  URLSearchParams,
  location: { search: '?backtestId=bt_1&pane=0', pathname: '/chart.html', hash: '' },
  window: {
    TradeModuleForms: {},
    TradeChartCore: { priceScaleMode(logScale) { return logScale ? 'log' : 'linear'; } },
  },
  console,
};
vm.createContext(context);
vm.runInContext(source, context);
const output = vm.runInContext(`
  const calls = [];
  const chart = {
    priceScale(...args) {
      return { applyOptions(options) { calls.push({ args, mode: options.mode }); } };
    },
  };
  pageState.chart = chart;
  pageState.pane = { view: { logScale: false } };
  let saves = 0;
  persistPaneView = () => { saves += 1; };
  const button = {
    textContent: '',
    classList: { toggle(_name, active) { button.active = active; } },
  };
  toggleLogScale(button);
  JSON.stringify({ calls, logScale: pageState.pane.view.logScale, button, saves });
`, context);
process.stdout.write(output);
"""
        completed = subprocess.run(
            [shutil.which("node"), "-e", script],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        result = json.loads(completed.stdout)
        self.assertEqual(
            result["calls"],
            [{"args": ["right"], "mode": "log"}],
        )
        self.assertIs(result["logScale"], True)
        self.assertIs(result["button"]["active"], True)
        self.assertEqual(result["button"]["textContent"], "Log")
        self.assertEqual(result["saves"], 1)
        self.assertNotIn("chartPaneCount", CHART_SOURCE)
        self.assertNotIn("applyChartLogScale", CHART_SOURCE)

    @unittest.skipUnless(shutil.which("node"), "Node.js is required")
    def test_visible_range_is_written_immediately_and_tracking_is_cleaned_up(self):
        script = r"""
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('web/chart.js', 'utf8').replace(/\nmain\(\)\.catch\([\s\S]*$/, '');
const context = {
  URLSearchParams,
  location: { search: '?backtestId=bt_1&pane=0', pathname: '/chart.html', hash: '' },
  window: {
    TradeModuleForms: {},
    TradeChartCore: { chartTime(value) { return value; } },
  },
  console,
  setTimeout,
  clearTimeout,
};
vm.createContext(context);
vm.runInContext(source, context);
const output = vm.runInContext(`
  pageState.backtest = { backtestId: 'bt_1' };
  pageState.spec = { panes: [] };
  pageState.pane = {
    id: 'pane',
    view: { start: null, end: null, logScale: false, controlsCollapsed: false },
  };
  let listener = null;
  let unsubscribed = 0;
  let saves = 0;
  scheduleSpecSave = () => { saves += 1; };
  const scale = {
    subscribeVisibleTimeRangeChange(callback) { listener = callback; },
    unsubscribeVisibleTimeRangeChange(callback) {
      if (callback === listener) unsubscribed += 1;
    },
  };
  subscribePaneView({ timeScale() { return scale; } });
  listener({ from: 10, to: 20 });
  const immediate = { ...pageState.pane.view };
  listener({ from: 11, to: 21 });
  cleanupPaneViewTracking({ flush: true });
  JSON.stringify({ immediate, final: pageState.pane.view, unsubscribed, saves, timer: pageState.viewSaveTimer });
`, context);
process.stdout.write(output);
"""
        completed = subprocess.run(
            [shutil.which("node"), "-e", script],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        result = json.loads(completed.stdout)
        self.assertEqual(result["immediate"]["start"], 10)
        self.assertEqual(result["immediate"]["end"], 20)
        self.assertEqual(result["final"]["start"], 11)
        self.assertEqual(result["final"]["end"], 21)
        self.assertEqual(result["unsubscribed"], 1)
        self.assertEqual(result["saves"], 1)
        self.assertIsNone(result["timer"])

    @unittest.skipUnless(shutil.which("node"), "Node.js is required")
    def test_spec_saves_are_serialized_and_lifecycle_flushes_use_keepalive(self):
        script = r"""
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('web/chart.js', 'utf8').replace(/\nmain\(\)\.catch\([\s\S]*$/, '');
const status = { textContent: '' };
const windowListeners = {};
const documentListeners = {};
const context = {
  URLSearchParams,
  structuredClone,
  location: { search: '?backtestId=bt_1&pane=0', pathname: '/chart.html', hash: '' },
  window: {
    TradeModuleForms: {},
    TradeChartCore: {},
    addEventListener(name, callback) { windowListeners[name] = callback; },
  },
  document: {
    visibilityState: 'visible',
    getElementById() { return status; },
    addEventListener(name, callback) { documentListeners[name] = callback; },
  },
  windowListeners,
  documentListeners,
  status,
  console,
  setTimeout,
  clearTimeout,
};
vm.createContext(context);
vm.runInContext(source, context);
(async () => {
  const output = await vm.runInContext(`(async () => {
    pageState.backtest = { backtestId: 'bt_1' };
    pageState.visualizationId = 'bt_1-current';
    pageState.spec = { revision: 1 };
    const calls = [];
    let active = 0;
    let maxActive = 0;
    let releaseFirst;
    const firstGate = new Promise((resolve) => { releaseFirst = resolve; });
    postJson = async (_path, payload, options) => {
      calls.push({
        spec: JSON.parse(JSON.stringify(payload.spec)),
        expectedRevision: payload.expectedRevision,
        keepalive: !!options?.keepalive,
      });
      active += 1;
      maxActive = Math.max(maxActive, active);
      if (calls.length === 1) await firstGate;
      active -= 1;
      return {
        accepted: true,
        visualization: {
          backtestId: 'bt_1',
          visualizationId: 'bt_1-current',
          revision: payload.expectedRevision + 1,
        },
      };
    };

    const first = flushSpecSave();
    await Promise.resolve();
    pageState.spec = { revision: 2 };
    const second = flushSpecSave();
    await Promise.resolve();
    const beforeRelease = calls.length;
    releaseFirst();
    await Promise.all([first, second]);

    bindSpecSaveLifecycle();
    pageState.spec = { revision: 3 };
    scheduleSpecSave();
    windowListeners.pagehide();
    await pageState.saveQueue;

    pageState.spec = { revision: 4 };
    scheduleSpecSave();
    document.visibilityState = 'hidden';
    documentListeners.visibilitychange();
    await pageState.saveQueue;
    return JSON.stringify({
      beforeRelease,
      maxActive,
      revisions: calls.map((item) => item.spec.revision),
      expectedRevisions: calls.map((item) => item.expectedRevision),
      keepalive: calls.map((item) => item.keepalive),
      windowEvents: Object.keys(windowListeners),
      documentEvents: Object.keys(documentListeners),
      status: status.textContent,
    });
  })()`, context);
  process.stdout.write(output);
})().catch((error) => { console.error(error); process.exitCode = 1; });
"""
        completed = subprocess.run(
            [shutil.which("node"), "-e", script],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        result = json.loads(completed.stdout)
        self.assertEqual(result["beforeRelease"], 1)
        self.assertEqual(result["maxActive"], 1)
        self.assertEqual(result["revisions"], [1, 2, 3, 4])
        self.assertEqual(result["expectedRevisions"], [0, 1, 2, 3])
        self.assertEqual(result["keepalive"], [False, False, True, True])
        self.assertEqual(result["windowEvents"], ["pagehide"])
        self.assertEqual(result["documentEvents"], ["visibilitychange"])
        self.assertEqual(result["status"], "Saved")

    @unittest.skipUnless(shutil.which("node"), "Node.js is required")
    def test_chart_uses_server_current_visualization_id_for_uppercase_backtest(self):
        script = r"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const full = fs.readFileSync('web/chart.js', 'utf8');
const source = 'function currentVisualizationRecord(' + full
  .split('function currentVisualizationRecord(', 2)[1]
  .split('function visualizationRevisionConflict(', 1)[0];
const context = {
  backtestId: 'bt_01M0Z2D7FZACAARVSZPZH34P63',
  pageState: {
    backtest: { backtestId: 'bt_01M0Z2D7FZACAARVSZPZH34P63', visualization: { source: 'view' } },
    visualizationId: '',
    visualizationRevision: 0,
  },
  structuredClone,
};
vm.createContext(context);
vm.runInContext(source, context);
const response = {
  currentVisualizationId: 'bt_01m0z2d7fzacaarvszpzh34p63-current',
  visualizations: [{
    visualizationId: 'bt_01m0z2d7fzacaarvszpzh34p63-current',
    backtestId: context.backtestId,
    revision: 9,
    spec: { source: 'repository' },
  }],
};
context.applyCurrentVisualizationRecord(response);
assert.equal(context.pageState.visualizationId, response.currentVisualizationId);
assert.equal(context.pageState.visualizationRevision, 9);
assert.equal(context.pageState.backtest.visualization.source, 'repository');
assert.throws(() => context.applyCurrentVisualizationRecord({ visualizations: [] }), /currentVisualizationId/);
"""
        completed = subprocess.run(
            [shutil.which("node"), "-e", script],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr or completed.stdout)

    @unittest.skipUnless(shutil.which("node"), "Node.js is required")
    def test_revision_conflict_reloads_and_cancels_standalone_stale_save(self):
        script = r"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('web/chart.js', 'utf8').replace(/\nmain\(\)\.catch\([\s\S]*$/, '');
const status = { textContent: '' };
const context = {
  URLSearchParams,
  structuredClone,
  location: { search: '?backtestId=bt_cas&pane=0', pathname: '/chart.html', hash: '' },
  window: { TradeModuleForms: {}, TradeChartCore: {} },
  document: { getElementById() { return status; } },
  console,
  setTimeout,
  clearTimeout,
};
vm.createContext(context);
vm.runInContext(source, context);
(async () => {
  const output = await vm.runInContext(`(async () => {
    pageState.backtest = { backtestId: 'bt_cas' };
    pageState.visualizationId = 'bt_cas-current';
    pageState.spec = { source: 'local' };
    pageState.visualizationRevision = 1;
    let posts = 0;
    let reloads = 0;
    saveCurrentSpec = async (_spec, options) => {
      posts += 1;
      if (options.expectedRevision !== 1) throw new Error('wrong expected revision');
      const error = new Error('conflict');
      error.status = 409;
      error.payload = { code: 'visualization_revision_conflict' };
      throw error;
    };
    reloadVisualizationAfterConflict = async () => {
      reloads += 1;
      pageState.saveEpoch += 1;
      pageState.visualizationRevision = 2;
      pageState.spec = { source: 'server' };
    };
    const first = enqueueSpecSave(1, { source: 'local-1' });
    const second = enqueueSpecSave(2, { source: 'local-2' });
    const settled = await Promise.allSettled([first, second]);
    return JSON.stringify({
      statuses: settled.map((item) => item.status),
      posts,
      reloads,
      source: pageState.spec.source,
    });
  })()`, context);
  const result = JSON.parse(output);
  assert.deepEqual(result.statuses, ['rejected', 'rejected']);
  assert.equal(result.posts, 1);
  assert.equal(result.reloads, 1);
  assert.equal(result.source, 'server');
})().catch((error) => { console.error(error); process.exitCode = 1; });
"""
        completed = subprocess.run(
            [shutil.which("node"), "-e", script],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr or completed.stdout)

    @unittest.skipUnless(shutil.which("node"), "Node.js is required")
    def test_successful_post_json_does_not_swallow_body_parse_failures(self):
        script = r"""
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('web/chart.js', 'utf8').replace(/\nmain\(\)\.catch\([\s\S]*$/, '');
const context = {
  URLSearchParams,
  location: { search: '?backtestId=bt_1&pane=0', pathname: '/chart.html', hash: '' },
  window: { TradeModuleForms: {}, TradeChartCore: {} },
  console,
};
vm.createContext(context);
vm.runInContext(source, context);
(async () => {
  const output = await vm.runInContext(`(async () => {
    const messages = [];
    authenticatedFetch = async () => ({
      ok: true,
      status: 200,
      async json() {
        const error = new Error('body aborted');
        error.name = 'AbortError';
        throw error;
      },
    });
    try { await postJson('/ok', {}); }
    catch (error) { messages.push([error.name, error.message]); }

    authenticatedFetch = async () => ({
      ok: false,
      status: 503,
      async json() { throw new Error('not json'); },
    });
    try { await postJson('/failed', {}); }
    catch (error) { messages.push([error.name, error.message]); }
    return JSON.stringify(messages);
  })()`, context);
  process.stdout.write(output);
})().catch((error) => { console.error(error); process.exitCode = 1; });
"""
        completed = subprocess.run(
            [shutil.which("node"), "-e", script],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertEqual(
            json.loads(completed.stdout),
            [
                ["AbortError", "body aborted"],
                ["Error", "/failed returned 503"],
            ],
        )

    def test_legacy_numeric_pane_urls_remain_supported(self):
        resolver = self.function_source("resolveRequestedPaneIndex", "syncPaneReference")
        startup_error = self.function_source("renderStartupError", "main")

        self.assertIn("requestedPaneId", resolver)
        self.assertIn("legacyPaneValue", resolver)
        self.assertIn("Number(legacyPaneValue)", resolver)
        self.assertIn('retry.textContent = "Retry"', startup_error)
        self.assertIn("location.reload()", startup_error)


if __name__ == "__main__":
    unittest.main()
