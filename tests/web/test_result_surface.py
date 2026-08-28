#!/usr/bin/env python3
"""Static contracts for the generic Result visualization surface."""

import html
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
APP_SOURCE = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
CHART_SOURCE = (ROOT / "web" / "chart.js").read_text(encoding="utf-8")
HTML_SOURCE = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
CHART_HTML_SOURCE = (ROOT / "web" / "chart.html").read_text(encoding="utf-8")
CSS_SOURCE = (ROOT / "web" / "styles.css").read_text(encoding="utf-8")
RESULT_VIEW_SOURCE = (
    ROOT / "engine" / "repository" / "backtest_result_views.py"
).read_text(encoding="utf-8")


class GenericResultSurfaceTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "Node.js is required for chart smoke tests")
    def test_chart_core_renders_projected_result_cycles(self):
        completed = subprocess.run(
            [shutil.which("node"), "scripts/chart_sparse_points_smoke.js"],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr or completed.stdout)
        self.assertIn("chart sparse-point smoke passed", completed.stdout)

    def test_both_result_surfaces_keep_explicit_defaults_without_peer_style_allocation(self):
        main_add = APP_SOURCE.split("function addPaneVisualizer(", 1)[1].split(
            "function visualizerTagLabel(", 1
        )[0]
        standalone_add = CHART_SOURCE.split("function addPaneVisualizer(", 1)[1].split(
            "function removePaneVisualizer(", 1
        )[0]
        main_render = APP_SOURCE.split("function renderResults()", 1)[1].split(
            "function syncVisualizationSpec(", 1
        )[0]
        main_normalizer = APP_SOURCE.split("function failOpenResultVisualizationSpec(", 1)[1].split(
            "function compactResultVersion(", 1
        )[0]
        standalone_main = CHART_SOURCE.split("async function main()", 1)[1].split(
            "main().catch", 1
        )[0]
        for add_source in (main_add, standalone_add):
            self.assertNotIn("allocateDistinctVisualizerStyles", add_source)
            self.assertNotIn("normalizeVisualizerStyleCollisions", add_source)
            self.assertNotIn("explicitVisualizerParamNames", add_source)
            self.assertIn("params,", add_source)

        self.assertIn("failOpenResultVisualizationSpec(", main_render)
        self.assertNotIn("normalizeVisualizerStyleCollisions", main_normalizer)
        self.assertNotIn("scheduleVisualizationSave(spec)", main_render)
        self.assertNotIn("normalizeVisualizerStylesForLoad", standalone_main)
        self.assertNotIn("allocateDistinctVisualizerStyles", APP_SOURCE)
        self.assertNotIn("allocateDistinctVisualizerStyles", CHART_SOURCE)
        self.assertNotIn("normalizeVisualizerStyleCollisions", APP_SOURCE)
        self.assertNotIn("normalizeVisualizerStyleCollisions", CHART_SOURCE)
        self.assertLess(
            main_render.index("failOpenResultVisualizationSpec("),
            main_render.index('$("visualizationSpec").value = JSON.stringify(spec'),
        )
        self.assertLess(
            standalone_main.index("normalizeVisualizationSpec("),
            standalone_main.index("await loadPaneResult()"),
        )

    def test_drawing_toolbar_is_capability_driven_accessible_and_present_on_both_surfaces(self):
        main_toolbar = APP_SOURCE.split("function drawingControllerCapabilities(", 1)[1].split(
            "function chartEntryForPane(", 1
        )[0]
        standalone_toolbar = CHART_SOURCE.split("function drawingControllerCapabilities(", 1)[1].split(
            "function drawPane(", 1
        )[0]

        for toolbar in (main_toolbar, standalone_toolbar):
            self.assertIn("controller.listTools()", toolbar)
            self.assertIn("data-drawing-bindings", toolbar)
            self.assertIn("data-drawing-binding", toolbar)
            self.assertIn("tool.bindings", toolbar)
            self.assertIn("candidate.visualizerId", toolbar)
            self.assertNotIn("candidate.value", toolbar)
            self.assertNotIn("candidate.id", toolbar)
            self.assertIn("controller?.activate?.(toolId, { bindings })", toolbar)
            self.assertNotIn("orderedDrawingTools", toolbar)
            self.assertNotIn("requiresTarget", toolbar)
            self.assertNotIn("targetVisualizerId", toolbar)
            self.assertIn("controller?.activate?.", toolbar)
            self.assertIn("controller?.deleteSelected?.", toolbar)
            self.assertIn("controller?.subscribe?.", toolbar)
            self.assertIn("controller?.dispose?.", toolbar)
            self.assertIn('role="toolbar"', toolbar)
            self.assertIn('role="status" aria-live="polite"', toolbar)
            self.assertIn('aria-keyshortcuts="Delete Backspace"', toolbar)
            self.assertIn('event.key === "Escape"', toolbar)
            self.assertNotIn("result.cycles", toolbar)
            self.assertNotIn("addSeries", toolbar)

        self.assertIn("chartContext?.interactionController", APP_SOURCE)
        self.assertIn("chartContext?.interactionController", CHART_SOURCE)
        main_draw = APP_SOURCE.split("function drawVisualization(", 1)[1].split(
            "function renderOverview(", 1
        )[0]
        standalone_draw = CHART_SOURCE.split("function drawPane(", 1)[1].split(
            "function renderStartupError(", 1
        )[0]
        for draw in (main_draw, standalone_draw):
            self.assertIn("createDrawingToolbar(", draw)
            self.assertIn("interactionController", draw)
            self.assertIn("container.tabIndex = 0", draw)
            self.assertIn('drawing surface`', draw)
        for source in (APP_SOURCE, CHART_SOURCE):
            controls = source.split("function renderChartControls(", 1)[1].split(
                "function ", 1
            )[0]
            self.assertIn("capabilities?.interactions", controls)
            self.assertIn("catalog.filter", controls)
        self.assertIn(".chart-drawing-toolbar {", CSS_SOURCE)
        self.assertIn("touch-action: manipulation", CSS_SOURCE)
        self.assertIn(".tv-chart:focus-visible", CSS_SOURCE)
        self.assertIn(".chart-interaction-text-input {", CSS_SOURCE)
        self.assertIn(".chart-interaction-active {", CSS_SOURCE)

    @unittest.skipUnless(shutil.which("node"), "Node.js is required")
    def test_dynamic_drawing_tool_preserves_order_and_activates_with_multiple_bindings(self):
        script = r"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

class ClassList {
  constructor() { this.values = new Set(); }
  toggle(name, active) { active ? this.values.add(name) : this.values.delete(name); }
}
class Node {
  constructor() {
    this.dataset = {};
    this.listeners = {};
    this.attributes = {};
    this.classList = new ClassList();
    this.disabled = false;
    this.textContent = '';
    this.value = '';
  }
  addEventListener(name, callback) { this.listeners[name] = callback; }
  removeEventListener() {}
  setAttribute(name, value) { this.attributes[name] = String(value); }
  emit(name, event = {}) { this.listeners[name]?.({ target: this, ...event }); }
}
class BindingHost extends Node {
  set innerHTML(markup) {
    this.markup = markup;
    this.selects = [];
    const selectPattern = /<select data-drawing-binding="([^"]+)"[^>]*>([\s\S]*?)<\/select>/g;
    for (const match of markup.matchAll(selectPattern)) {
      const select = new Node();
      select.dataset.drawingBinding = match[1];
      const selected = [...match[2].matchAll(/<option value="([^"]*)"([^>]*)>/g)]
        .find((option) => /\sselected(?:\s|>)/.test(`${option[2]}>`));
      select.value = selected?.[1] || '';
      this.selects.push(select);
    }
  }
  get innerHTML() { return this.markup || ''; }
  querySelectorAll(selector) {
    return selector === '[data-drawing-binding]' ? this.selects : [];
  }
}
class ToolbarElement extends Node {
  set innerHTML(markup) {
    this.markup = markup;
    this.bindingHost = new BindingHost();
    this.status = new Node();
    this.deleteButton = new Node();
    this.toolButtons = [];
    const pattern = /<button type="button" data-drawing-tool="([^"]+)"([^>]*)>([^<]*)<\/button>/g;
    for (const match of markup.matchAll(pattern)) {
      const button = new Node();
      button.dataset.drawingTool = match[1];
      button.textContent = match[3];
      button.disabled = /\sdisabled(?:\s|>)/.test(`${match[2]}>`);
      this.toolButtons.push(button);
    }
  }
  get innerHTML() { return this.markup; }
  querySelector(selector) {
    if (selector === '[data-drawing-bindings]') return this.bindingHost;
    if (selector === '[data-drawing-status]') return this.status;
    if (selector === '[data-drawing-delete]') return this.deleteButton;
    return null;
  }
  querySelectorAll(selector) {
    return selector === '[data-drawing-tool]' ? this.toolButtons : [];
  }
}

function extracted(source, standalone) {
  const capabilities = 'function drawingControllerCapabilities(' + source
    .split('function drawingControllerCapabilities(', 2)[1]
    .split('function persistDrawingControllerEvent(', 1)[0];
  const toolbar = 'function createDrawingToolbar(' + source
    .split('function createDrawingToolbar(', 2)[1]
    .split(standalone ? 'function drawPane(' : 'function chartEntryForPane(', 1)[0];
  return `${capabilities}\n${toolbar}`;
}

for (const [filename, isStandalone] of [
  ['web/app.js', false],
  ['web/chart.js', true],
]) {
  const source = fs.readFileSync(filename, 'utf8');
  const activations = [];
  const ui = { toolId: '', bindings: {}, selectedVisualizerId: '' };
  const controller = {
    listTools() {
      return {
        tools: [
          {
            id: 'vendor-ray', label: 'Vendor Ray', bindings: [
              {
                name: 'anchorSource', label: 'Anchor source', kind: 'series.price-coordinate',
                candidates: [{ visualizerId: 'primary-1', label: 'Primary One' }],
              },
              {
                name: 'clockSource', label: 'Clock source', kind: 'series.time-coordinate',
                candidates: [{ visualizerId: 'clock-1', label: 'Clock One' }],
              },
            ],
          },
          { id: 'inspect', label: 'Inspect', bindings: [] },
          { id: 'broken', label: 'Broken' },
          {
            id: 'legacy', label: 'Legacy aliases', bindings: [{
              name: 'anchorSource', label: 'Anchor source', kind: 'series.price-coordinate',
              candidates: [{ id: 'primary-1', value: 'primary-1', label: 'Primary One' }],
            }],
          },
        ],
      };
    },
    activate(toolId, options) { activations.push({ toolId, ...options }); return true; },
    cancel() {},
    deleteSelected() { return false; },
    subscribe() { return () => {}; },
    dispose() {},
  };
  const context = {
    document: { createElement() { return new ToolbarElement(); } },
    console,
    escapeHtml(value) { return String(value); },
    visibleText(value, fallback = '') {
      return String(value ?? '').trim() || fallback;
    },
    resultDrawingUiState() { return ui; },
    pageState: { drawingUi: ui },
    persistDrawingControllerEvent() { return false; },
  };
  vm.createContext(context);
  vm.runInContext(extracted(source, isStandalone), context);
  const surface = new Node();
  const toolbar = isStandalone
    ? context.createDrawingToolbar(controller, surface)
    : context.createDrawingToolbar(0, controller, surface);
  assert.deepEqual(
    toolbar.element.toolButtons.map((button) => button.dataset.drawingTool),
    ['vendor-ray', 'inspect', 'broken', 'legacy'],
    `${filename}: core tool order must remain authoritative`,
  );
  assert.equal(toolbar.element.toolButtons[2].disabled, true);
  assert.equal(toolbar.element.toolButtons[3].disabled, true, `${filename}: aliases are not accepted`);
  assert.match(toolbar.element.status.textContent, /bindings must be an array/);
  const custom = toolbar.element.toolButtons[0];
  assert.equal(custom.disabled, false);
  custom.emit('click');
  assert.equal(toolbar.element.bindingHost.selects.length, 2);
  assert.equal(activations.length, 0, `${filename}: incomplete bindings must not activate`);
  toolbar.element.bindingHost.selects[0].value = 'primary-1';
  toolbar.element.bindingHost.selects[0].emit('change');
  assert.equal(activations.length, 0, `${filename}: every declared binding is required`);
  toolbar.element.bindingHost.selects[1].value = 'clock-1';
  toolbar.element.bindingHost.selects[1].emit('change');
  assert.deepEqual(JSON.parse(JSON.stringify(activations.at(-1))), {
    toolId: 'vendor-ray',
    bindings: { anchorSource: 'primary-1', clockSource: 'clock-1' },
  }, filename);
}
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
    def test_drawing_events_are_metadata_gated_persisted_and_redrawn_on_both_surfaces(self):
        script = r"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const app = fs.readFileSync('web/app.js', 'utf8');
const standalone = fs.readFileSync('web/chart.js', 'utf8');
const appFunctions = [
  'function isDrawingVisualizerInstance(' + app.split('function isDrawingVisualizerInstance(', 2)[1]
    .split('function visualizerSummary(', 1)[0],
  'function persistDrawingControllerEvent(' + app.split('function persistDrawingControllerEvent(', 2)[1]
    .split('function createDrawingToolbar(', 1)[0],
].join('\n');
const standaloneFunctions = [
  'function isDrawingVisualizerInstance(' + standalone.split('function isDrawingVisualizerInstance(', 2)[1]
    .split('function visualizerDependents(', 1)[0],
  'function persistDrawingControllerEvent(' + standalone.split('function persistDrawingControllerEvent(', 2)[1]
    .split('function createDrawingToolbar(', 1)[0],
].join('\n');

const definitions = [
  {
    id: 'vendor.annotation.horizontal',
    renderer: { id: 'vendor.annotation.horizontal', apiVersion: 1 },
    capabilities: {
      interactions: ['chart.pointer'],
      provides: [],
      requires: [{
        name: 'anchor', kind: 'series.price-coordinate',
        bindingParam: 'anchorSource', matches: {},
      }],
    },
  },
  {
    id: 'drawing.name-is-not-authority',
    renderer: { id: 'drawing.name-is-not-authority', apiVersion: 1 },
    capabilities: { interactions: [] },
  },
];
const core = {
  visualizerCatalog() { return definitions; },
  upsertIdentity(items, currentId, nextItem) {
    return currentId
      ? items.map((item) => item.id === currentId ? nextItem : item)
      : [...items, nextItem];
  },
};
const committed = {
  id: 'annotation-1',
  callback: 'vendor.annotation.horizontal',
  params: { anchorSource: 'candles-1', price: 101.25, color: '#2563eb', lineWidth: 2 },
};

const mainPane = { id: 'pane', visualizers: [] };
const mainSpec = { panes: [mainPane] };
const mainRedraws = [];
const mainContext = {
  window: { TradeChartCore: core },
  state: { selectedBacktest: { dataKeys: {}, visualization: mainSpec } },
  structuredClone,
  paneScopedSpec(spec) { return spec; },
  paneResult() { return { dataKeys: {} }; },
  resultDrawingUiState() { return { selectedVisualizerId: 'annotation-1' }; },
  visualizerDefinitionMapForPane() { return new Map(definitions.map((item) => [item.id, item])); },
  visualizerDependents() { return []; },
  selectedVisualizerId() { return ''; },
  setSelectedVisualizerId() {},
  setPaneControlError() {},
  syncVisualizationSpec(spec) { mainRedraws.push(JSON.parse(JSON.stringify(spec))); },
};
vm.createContext(mainContext);
vm.runInContext(appFunctions, mainContext);
assert.equal(mainContext.persistDrawingControllerEvent(0, { type: 'commit', instance: committed }), true);
assert.deepEqual(JSON.parse(JSON.stringify(mainPane.visualizers)), [committed]);
assert.equal(mainRedraws.length, 1);
assert.equal(mainContext.persistDrawingControllerEvent(0, {
  type: 'commit',
  instance: { id: 'forged', callback: 'drawing.name-is-not-authority', params: {} },
}), false);
assert.equal(mainContext.persistDrawingControllerEvent(0, {
  type: 'delete', visualizerId: 'annotation-1',
}), true);
assert.equal(mainPane.visualizers.length, 0);
assert.equal(mainRedraws.length, 2);

const standalonePane = { id: 'pane', visualizers: [] };
const standaloneSpec = { panes: [standalonePane] };
let standaloneRedraws = 0;
const standaloneContext = {
  window: { TradeChartCore: core },
  pageState: {
    pane: standalonePane,
    spec: standaloneSpec,
    backtest: { dataKeys: {} },
    drawingUi: { selectedVisualizerId: 'annotation-1' },
  },
  paneIndex: 0,
  structuredClone,
  paneScopedSpec() { return standaloneSpec; },
  visualizerById(id) { return standalonePane.visualizers.find((item) => item.id === id); },
  visualizerDefinitionMap() { return new Map(definitions.map((item) => [item.id, item])); },
  visualizerDependents() { return []; },
  uiState() { return { selectedVisualizerId: '' }; },
  syncSpec() { standaloneRedraws += 1; },
  document: { getElementById() { return { textContent: '' }; } },
};
vm.createContext(standaloneContext);
vm.runInContext(standaloneFunctions, standaloneContext);
assert.equal(standaloneContext.persistDrawingControllerEvent({ type: 'commit', instance: committed }), true);
assert.deepEqual(JSON.parse(JSON.stringify(standalonePane.visualizers)), [committed]);
assert.equal(standaloneRedraws, 1);
assert.equal(standaloneContext.persistDrawingControllerEvent({
  type: 'delete', visualizerId: 'annotation-1',
}), true);
assert.equal(standalonePane.visualizers.length, 0);
assert.equal(standaloneRedraws, 2);
"""
        completed = subprocess.run(
            [shutil.which("node"), "-e", script],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr or completed.stdout)

    def test_visualizer_dependencies_block_provider_removal_and_incompatible_edits(self):
        main_remove = APP_SOURCE.split("function removePaneLayer(", 1)[1].split(
            "function removePaneTemporaryModule(", 1
        )[0]
        standalone_remove = CHART_SOURCE.split("function removePaneVisualizer(", 1)[1].split(
            "function isDrawingVisualizerInstance(", 1
        )[0]
        main_add = APP_SOURCE.split("function addPaneVisualizer(", 1)[1].split(
            "function visualizerTagLabel(", 1
        )[0]
        standalone_add = CHART_SOURCE.split("function addPaneVisualizer(", 1)[1].split(
            "function removePaneVisualizer(", 1
        )[0]

        for remove_source in (main_remove, standalone_remove):
            self.assertLess(
                remove_source.index("visualizerDependents("),
                remove_source.index(".filter((item) => item.id !=="),
            )
            self.assertIn("return false", remove_source)
        self.assertIn("dependent data display", main_remove)
        self.assertNotIn("dependents.map", main_remove)
        self.assertIn("Remove dependent display(s) first", standalone_remove)
        self.assertIn('labels.get(item.id) || "Data Display"', standalone_remove)
        for add_source in (main_add, standalone_add):
            self.assertIn("incompatibleVisualizerDependents", add_source)
        self.assertIn("dependent data display", main_add)
        self.assertIn("dependent display(s)", standalone_add)

    @unittest.skipUnless(shutil.which("node"), "Node.js is required")
    def test_provider_remove_is_blocked_until_explicit_dependents_are_removed(self):
        script = r"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const definitions = new Map([
  ['series.price', {
    id: 'series.price',
    capabilities: {
      provides: [{
        name: 'series', kind: 'series.price-coordinate',
        attributes: { scale: 'scaleId' },
      }],
      requires: [],
    },
  }],
  ['vendor.annotation', {
    id: 'vendor.annotation',
    capabilities: {
      provides: [],
      requires: [{
        name: 'anchor', kind: 'series.price-coordinate',
        bindingParam: 'anchorSource', matches: { scale: 'scaleId' },
      }],
    },
  }],
]);
const provider = { id: 'primary-1', callback: 'series.price', params: { scaleId: 'right' } };
const dependent = {
  id: 'drawing-1', callback: 'vendor.annotation',
  params: { anchorSource: 'primary-1', scaleId: 'right', price: 10 },
};

const app = fs.readFileSync('web/app.js', 'utf8');
const appFunctions = 'function visualizerCapabilityDescriptors(' + app
  .split('function visualizerCapabilityDescriptors(', 2)[1]
  .split('function removePaneTemporaryModule(', 1)[0];
const appPane = { id: 'pane', visualizers: [provider, dependent] };
let appError = '';
let appSyncs = 0;
const appContext = {
  state: { selectedBacktest: { visualization: { panes: [appPane] } } },
  visualizerDefinitionMapForPane() { return definitions; },
  setPaneControlError(_index, message) { appError = message; },
  selectedVisualizerId() { return ''; },
  setSelectedVisualizerId() {},
  syncVisualizationSpec() { appSyncs += 1; },
};
vm.createContext(appContext);
vm.runInContext(appFunctions, appContext);
assert.equal(appContext.removePaneLayer(0, 'primary-1'), false);
assert.deepEqual(appPane.visualizers.map((item) => item.id), ['primary-1', 'drawing-1']);
assert.match(appError, /Remove 1 dependent data display first/);
assert.doesNotMatch(appError, /primary-1|drawing-1/);
assert.equal(appSyncs, 0);
assert.equal(appContext.removePaneLayer(0, 'drawing-1'), true);
assert.deepEqual(appPane.visualizers.map((item) => item.id), ['primary-1']);
assert.equal(appSyncs, 1);

const standalone = fs.readFileSync('web/chart.js', 'utf8');
const standaloneFunctions = [
  'function visualizerCapabilityDescriptors(' + standalone.split('function visualizerCapabilityDescriptors(', 2)[1]
    .split('function validateVisualizerOverlayDependencies(', 1)[0],
  'function removePaneVisualizer(' + standalone.split('function removePaneVisualizer(', 2)[1]
    .split('function isDrawingVisualizerInstance(', 1)[0],
  'function visualizerDependents(' + standalone.split('function visualizerDependents(', 2)[1]
    .split('function visualizerTagLabel(', 1)[0],
].join('\n');
const standalonePane = { id: 'pane', visualizers: [provider, dependent] };
const status = { textContent: '' };
let standaloneSyncs = 0;
const standaloneContext = {
  pageState: { pane: standalonePane },
  visualizerDefinitionMap() { return definitions; },
  document: { getElementById() { return status; } },
  uiState() { return { selectedVisualizerId: '' }; },
  syncSpec() { standaloneSyncs += 1; },
};
vm.createContext(standaloneContext);
vm.runInContext(standaloneFunctions, standaloneContext);
assert.equal(standaloneContext.removePaneVisualizer('primary-1'), false);
assert.deepEqual(standalonePane.visualizers.map((item) => item.id), ['primary-1', 'drawing-1']);
assert.match(status.textContent, /Remove dependent display\(s\) first: Data Display/);
assert.doesNotMatch(status.textContent, /primary-1|drawing-1/);
assert.equal(standaloneSyncs, 0);
assert.equal(standaloneContext.removePaneVisualizer('drawing-1'), true);
assert.deepEqual(standalonePane.visualizers.map((item) => item.id), ['primary-1']);
assert.equal(standaloneSyncs, 1);
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
    def test_temporary_module_child_consumers_block_removal_on_both_surfaces(self):
        script = r"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const definition = { id: 'series.line', inputPorts: { dataKey: {} } };
const makePane = () => ({
  id: 'pane',
  temporaryModules: [
    { instanceId: 'producer', outputs: { value: 'derived' }, inputs: {} },
    { instanceId: 'downstream', outputs: { value: 'next' }, inputs: { source: 'derived.child' } },
  ],
  visualizers: [{
    id: 'line-child', callback: 'series.line',
    params: { dataKey: 'derived.value', unrelated: 'derived-but-not-a-child' },
  }],
});

const app = fs.readFileSync('web/app.js', 'utf8');
const appFunctions = 'function dataKeyConsumesOutput(' + app
  .split('function dataKeyConsumesOutput(', 2)[1]
  .split('function allResultModuleDefinitions(', 1)[0];
const appPane = makePane();
const appSpec = { panes: [appPane], temporaryModules: [] };
let appError = '';
let appSyncs = 0;
const appContext = {
  window: { TradeChartCore: { visualizerCatalog() { return [definition]; } } },
  state: { selectedBacktest: { dataKeys: {}, visualization: appSpec } },
  paneScopedSpec() { return appSpec; },
  setPaneControlError(_index, message) { appError = message; },
  selectedTempModuleId() { return ''; },
  setSelectedTempModuleId() {},
  invalidatePaneResult() {},
  syncVisualizationSpec() { appSyncs += 1; },
};
vm.createContext(appContext);
vm.runInContext(appFunctions, appContext);
assert.equal(appContext.removePaneTemporaryModule(0, 'producer'), false);
assert.match(appError, /Remove 2 dependent display instances first/);
assert.doesNotMatch(appError, /producer|line-child|downstream/);
assert.deepEqual(appPane.temporaryModules.map((item) => item.instanceId), ['producer', 'downstream']);
assert.equal(appSyncs, 0);
appPane.visualizers = [];
appPane.temporaryModules = appPane.temporaryModules.filter((item) => item.instanceId !== 'downstream');
assert.equal(appContext.removePaneTemporaryModule(0, 'producer'), true);
assert.equal(appPane.temporaryModules.length, 0);
assert.equal(appSyncs, 1);

const standalone = fs.readFileSync('web/chart.js', 'utf8');
const standaloneFunctions = 'function dataKeyConsumesOutput(' + standalone
  .split('function dataKeyConsumesOutput(', 2)[1]
  .split('function selectedVisualizerDefinition(', 1)[0];
const standalonePane = makePane();
const standaloneSpec = { panes: [standalonePane], temporaryModules: [] };
const status = { textContent: '' };
let standaloneSyncs = 0;
const standaloneContext = {
  window: { TradeChartCore: { visualizerCatalog() { return [definition]; } } },
  pageState: { pane: standalonePane, spec: standaloneSpec, backtest: { dataKeys: {} } },
  paneScopedSpec() { return standaloneSpec; },
  document: { getElementById() { return status; } },
  uiState() { return { selectedTempModuleId: '' }; },
  syncSpec() { standaloneSyncs += 1; },
};
vm.createContext(standaloneContext);
vm.runInContext(standaloneFunctions, standaloneContext);
assert.equal(standaloneContext.removePaneTemporaryModule('producer'), false);
assert.match(status.textContent, /Remove dependent instance\(s\) first: Data Display, Temporary Module/);
assert.doesNotMatch(status.textContent, /producer|line-child|downstream/);
assert.deepEqual(
  standalonePane.temporaryModules.map((item) => item.instanceId),
  ['producer', 'downstream'],
);
assert.equal(standaloneSyncs, 0);
standalonePane.visualizers = [];
standalonePane.temporaryModules = standalonePane.temporaryModules
  .filter((item) => item.instanceId !== 'downstream');
assert.equal(standaloneContext.removePaneTemporaryModule('producer'), true);
assert.equal(standalonePane.temporaryModules.length, 0);
assert.equal(standaloneSyncs, 1);
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
    def test_temporary_output_edit_prefix_remaps_consumers_and_blocks_orphans(self):
        script = r"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const definition = { id: 'series.line', inputPorts: { dataKey: {} } };
const makePane = () => {
  const producer = { instanceId: 'producer', outputs: { value: 'derived' }, inputs: {} };
  return {
    producer,
    pane: {
      id: 'pane',
      temporaryModules: [
        producer,
        { instanceId: 'downstream', outputs: { value: 'next' }, inputs: { source: 'derived.child' } },
      ],
      visualizers: [{
        id: 'line-child', callback: 'series.line',
        params: { dataKey: 'derived.value', styleToken: 'derived.value' },
      }],
    },
  };
};

const app = fs.readFileSync('web/app.js', 'utf8');
const appFunctions = [
  'function remapDataKeyBinding(' + app.split('function remapDataKeyBinding(', 2)[1]
    .split('function addPaneTemporaryModule(', 1)[0],
  'function dataKeyConsumesOutput(' + app.split('function dataKeyConsumesOutput(', 2)[1]
    .split('function removePaneTemporaryModule(', 1)[0],
].join('\n');
const appData = makePane();
const appSpec = { panes: [appData.pane], temporaryModules: [] };
const appContext = {
  window: { TradeChartCore: { visualizerCatalog() { return [definition]; } } },
  state: { selectedBacktest: { dataKeys: {}, visualization: appSpec } },
  paneScopedSpec() { return appSpec; },
};
vm.createContext(appContext);
vm.runInContext(appFunctions, appContext);
const appRemapped = appContext.remapTemporaryModuleConsumers(
  appData.pane, appSpec, appData.producer, { value: 'renamed' },
);
assert.equal(appRemapped.error, undefined);
assert.equal(appRemapped.visualizers[0].params.dataKey, 'renamed.value');
assert.equal(appRemapped.visualizers[0].params.styleToken, 'derived.value');
assert.equal(appRemapped.temporaryModules[1].inputs.source, 'renamed.child');
const appBlocked = appContext.remapTemporaryModuleConsumers(
  appData.pane, appSpec, appData.producer, {},
);
assert.match(appBlocked.error, /remove the dependent 1 data display and 1 temporary module first/);
assert.doesNotMatch(appBlocked.error, /producer|line-child|downstream/);

const standalone = fs.readFileSync('web/chart.js', 'utf8');
const standaloneFunctions = [
  'function remapDataKeyBinding(' + standalone.split('function remapDataKeyBinding(', 2)[1]
    .split('function addPaneTemporaryModule(', 1)[0],
  'function dataKeyConsumesOutput(' + standalone.split('function dataKeyConsumesOutput(', 2)[1]
    .split('function removePaneTemporaryModule(', 1)[0],
].join('\n');
const standaloneData = makePane();
const standaloneSpec = { panes: [standaloneData.pane], temporaryModules: [] };
const standaloneContext = {
  window: { TradeChartCore: { visualizerCatalog() { return [definition]; } } },
  pageState: {
    pane: standaloneData.pane, spec: standaloneSpec, backtest: { dataKeys: {} },
  },
  paneScopedSpec() { return standaloneSpec; },
};
vm.createContext(standaloneContext);
vm.runInContext(standaloneFunctions, standaloneContext);
const standaloneRemapped = standaloneContext.remapTemporaryModuleConsumers(
  standaloneData.producer, { value: 'renamed' },
);
assert.equal(standaloneRemapped.visualizers[0].params.dataKey, 'renamed.value');
assert.equal(standaloneRemapped.visualizers[0].params.styleToken, 'derived.value');
assert.equal(standaloneRemapped.temporaryModules[1].inputs.source, 'renamed.child');
assert.throws(
  () => standaloneContext.remapTemporaryModuleConsumers(standaloneData.producer, {}),
  /dependent instance\(s\): Data Display, Temporary Module/,
);
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
    def test_result_panel_survives_missing_or_failed_style_migration_and_save(self):
        script = r"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('web/app.js', 'utf8');
const failOpenSource = 'function failOpenResultVisualizationSpec(' + source
  .split('function failOpenResultVisualizationSpec(', 2)[1]
  .split('function compactResultVersion(', 1)[0];
const renderSource = 'function renderResults()' + source
  .split('function renderResults()', 2)[1]
  .split('function syncVisualizationSpec(', 1)[0];

function runScenario({ core, normalizeSpec, scheduleSave }) {
  const nodes = new Map();
  const errors = [];
  let draws = 0;
  const context = {
    window: { TradeChartCore: core },
    state: {
      selectedBacktest: {
        backtestId: 'bt_1',
        name: 'Result',
        dataKeys: {},
        visualization: { schemaVersion: 3, timeZone: 'UTC', panes: [{ id: 'prices', visualizers: [] }] },
      },
    },
    currentResultBacktestId() { return 'bt_1'; },
    currentResultViewError() { return null; },
    $(id) {
      if (!nodes.has(id)) nodes.set(id, { innerHTML: '', value: '', textContent: '' });
      return nodes.get(id);
    },
    clearResultCharts() {},
    renderResultContext() {},
    setResultsActionError() {},
    setVisualizationSpecError(message) { if (message) errors.push(message); },
    syncResultsActionState() {},
    normalizeVisualizationSpec: normalizeSpec,
    scheduleVisualizationSave: scheduleSave,
    visibleResourceName(record, fallback = 'Resource') { return record?.name || fallback; },
    visibleText(value, fallback = '') { return String(value ?? '').trim() || fallback; },
    renderResultViewLoadError() {},
    drawVisualization() {
      draws += 1;
      context.$('chartArea').innerHTML = '<section class="chart-panel">rendered</section>';
    },
    console,
    JSON,
    structuredClone,
  };
  vm.createContext(context);
  vm.runInContext(`${failOpenSource}\n${renderSource}`, context);
  assert.doesNotThrow(() => context.renderResults());
  assert.equal(draws, 1);
  assert.match(context.$('chartArea').innerHTML, /chart-panel/);
  return errors;
}

runScenario({
  core: {},
  normalizeSpec(_result, spec) { return spec; },
  scheduleSave() {},
});
assert.deepEqual(runScenario({
  core: { normalizeVisualizerStyleCollisions() { throw new Error('must not be called'); } },
  normalizeSpec(_result, spec) { return spec; },
  scheduleSave() { throw new Error('must not save a peer-derived migration'); },
}), []);
assert.match(runScenario({
  core: {},
  normalizeSpec() { throw new Error('spec normalization exploded'); },
  scheduleSave() {},
})[0], /spec normalization exploded/);
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
    def test_result_view_failure_replaces_loading_with_retry_and_restores_cached_surface(self):
        script = r"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('web/app.js', 'utf8');
const currentErrorSource = 'function currentResultBacktestId()' + source
  .split('function currentResultBacktestId()', 2)[1]
  .split('function syncResultsActionState()', 1)[0];
const failureSource = 'function renderResultViewLoadError(' + source
  .split('function renderResultViewLoadError(', 2)[1]
  .split('function renderResults()', 1)[0];
const renderSource = 'function renderResults()' + source
  .split('function renderResults()', 2)[1]
  .split('function syncVisualizationSpec(', 1)[0];
const refreshSource = 'async function refreshSelectedBacktest()' + source
  .split('async function refreshSelectedBacktest()', 2)[1]
  .split('document.querySelectorAll(".nav-btn")', 1)[0];

const retryButton = {
  disabled: false,
  addEventListener(type, callback) {
    assert.equal(type, 'click');
    this.click = callback;
  },
};
const nodes = new Map();
const chartArea = {
  dataset: {},
  innerHTML: '',
  insertAdjacentHTML(position, markup) {
    assert.equal(position, 'afterbegin');
    this.innerHTML = markup + this.innerHTML;
  },
  querySelector(selector) {
    return selector === '[data-retry-result-view]' && this.innerHTML.includes('data-retry-result-view')
      ? retryButton
      : null;
  },
};
nodes.set('chartArea', chartArea);
const uiContext = {
  state: {
    resultBacktestId: 'bt_500',
    selectedBacktest: null,
    resultViewError: { backtestId: 'bt_500', message: 'view returned 500' },
  },
  $(id) {
    if (!nodes.has(id)) nodes.set(id, { innerHTML: '', value: '', textContent: '' });
    return nodes.get(id);
  },
  clearResultCharts() {},
  renderResultContext() {},
  setVisualizationSpecError() {},
  setResultsActionError() {},
  syncResultsActionState() {},
  escapeHtml(value) { return String(value); },
  visibleResourceName(record, fallback = 'Resource') { return record?.name || fallback; },
  visibleText(value, fallback = '') { return String(value ?? '').trim() || fallback; },
  runUiAction(_label, action) { return action(); },
  refreshSelectedBacktest() { uiContext.retryCount += 1; return Promise.resolve(); },
  retryCount: 0,
  console,
};
vm.createContext(uiContext);
vm.runInContext(`${currentErrorSource}\n${failureSource}\n${renderSource}`, uiContext);
uiContext.renderResults();
assert.match(chartArea.innerHTML, /Result unavailable/);
assert.match(chartArea.innerHTML, /view returned 500/);
assert.match(chartArea.innerHTML, /data-retry-result-view/);
assert.equal(typeof retryButton.click, 'function');
retryButton.click({ currentTarget: retryButton });
assert.equal(retryButton.disabled, true);
assert.equal(uiContext.retryCount, 1);

const readyEntry = { status: 'ready', result: { token: 'cached' } };
const loadingEntry = { status: 'loading' };
const cached = {
  backtestId: 'bt_500',
  visualization: { panes: [] },
  paneProjectionCaches: new Map([['pane', {
    entries: new Map([['ready', readyEntry], ['loading', loadingEntry]]),
  }]]),
};
let failureRenders = 0;
let abortedEntries = 0;
const refreshContext = {
  state: {
    resultBacktestId: 'bt_500',
    selectedBacktest: cached,
    resultViewError: null,
  },
  resultSelectionSeq: 0,
  resultDiscoveryRequest: null,
  visualizationSaveSeq: 0,
  visualizationSaveEpoch: 0,
  visualizationSaveTimer: null,
  clearTimeout() {},
  clearResultCharts() {},
  renderResultContext() {},
  syncResultsActionState() {},
  abortVisualizerProjection(entry) {
    assert.equal(entry, loadingEntry);
    abortedEntries += 1;
  },
  renderResults() { failureRenders += 1; },
  async getJson() { throw new Error('view returned 500'); },
  async discoverResultDataKeys() { throw new Error('discovery must not run'); },
  $(id) {
    assert.equal(id, 'chartArea');
    return chartArea;
  },
  encodeURIComponent,
  Object,
};
vm.createContext(refreshContext);
vm.runInContext(refreshSource, refreshContext);
(async () => {
  await assert.rejects(refreshContext.refreshSelectedBacktest(), /view returned 500/);
  assert.equal(refreshContext.state.selectedBacktest, cached);
  assert.equal(refreshContext.state.resultViewError.backtestId, 'bt_500');
  assert.equal(refreshContext.state.resultViewError.message, 'view returned 500');
  assert.equal(failureRenders, 1);
  assert.equal(abortedEntries, 1);
  const entries = cached.paneProjectionCaches.get('pane').entries;
  assert.equal(entries.get('ready'), readyEntry);
  assert.equal(entries.has('loading'), false);
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
    def test_result_uses_server_current_visualization_id_for_uppercase_backtest(self):
        source = "function currentVisualizationRecord(" + APP_SOURCE.split(
            "function currentVisualizationRecord(", 1
        )[1].split("function visualizationRevisionConflict(", 1)[0]
        script = f"""
const assert = require('node:assert/strict');
const vm = require('node:vm');
const context = {{ structuredClone }};
vm.createContext(context);
vm.runInContext({json.dumps(source)}, context);
const backtest = {{ backtestId: 'bt_01M0Z2D7FZACAARVSZPZH34P63', visualization: {{ source: 'view' }} }};
const response = {{
  currentVisualizationId: 'bt_01m0z2d7fzacaarvszpzh34p63-current',
  visualizations: [{{
    visualizationId: 'bt_01m0z2d7fzacaarvszpzh34p63-current',
    backtestId: backtest.backtestId,
    revision: 7,
    spec: {{ source: 'repository' }},
  }}],
}};
context.applyCurrentVisualizationRecord(backtest, response);
assert.equal(backtest.visualizationId, response.currentVisualizationId);
assert.equal(backtest.visualizationRevision, 7);
assert.equal(backtest.visualization.source, 'repository');
assert.throws(
  () => context.applyCurrentVisualizationRecord({{ backtestId: backtest.backtestId }}, {{ visualizations: [] }}),
  /currentVisualizationId/,
);
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
    def test_result_revision_conflict_reloads_and_cancels_queued_stale_save(self):
        conflict_source = "function visualizationRevisionConflict(" + APP_SOURCE.split(
            "function visualizationRevisionConflict(", 1
        )[1].split("function enqueueVisualizationSave(", 1)[0]
        enqueue_source = "function enqueueVisualizationSave(" + APP_SOURCE.split(
            "function enqueueVisualizationSave(", 1
        )[1].split("function createLayerInstanceId(", 1)[0]
        script = f"""
const assert = require('node:assert/strict');
const vm = require('node:vm');
const context = {{
  state: {{ selectedBacktest: {{
    backtestId: 'bt_cas',
    visualizationId: 'bt_cas-current',
    visualizationRevision: 1,
  }} }},
  visualizationSaveEpoch: 0,
  visualizationSaveQueue: Promise.resolve(),
  structuredClone,
  postCalls: [],
  reloads: 0,
}};
context.postJson = async (path, payload) => {{
  context.postCalls.push({{ path, payload }});
  const error = new Error('conflict');
  error.status = 409;
  error.payload = {{ code: 'visualization_revision_conflict' }};
  throw error;
}};
context.refreshSelectedBacktest = async () => {{
  context.reloads += 1;
  context.visualizationSaveEpoch += 1;
  context.state.selectedBacktest = {{
    backtestId: 'bt_cas', visualizationId: 'bt_cas-current', visualizationRevision: 2,
    visualization: {{ source: 'server' }},
  }};
}};
vm.createContext(context);
vm.runInContext({json.dumps(conflict_source + enqueue_source)}, context);
(async () => {{
  const first = context.enqueueVisualizationSave('bt_cas', {{ source: 'local-1' }});
  const second = context.enqueueVisualizationSave('bt_cas', {{ source: 'local-2' }});
  const settled = await Promise.allSettled([first, second]);
  assert.deepEqual(settled.map((item) => item.status), ['rejected', 'rejected']);
  assert.equal(context.postCalls.length, 1);
  assert.equal(context.postCalls[0].payload.expectedRevision, 1);
  assert.equal(context.reloads, 1);
  assert.equal(context.state.selectedBacktest.visualization.source, 'server');
}})().catch((error) => {{ console.error(error); process.exitCode = 1; }});
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

    def test_result_header_contains_only_human_composition_identity(self):
        renderer = APP_SOURCE.split("function renderResultContext(", 1)[1].split(
            "function renderResults()", 1
        )[0]
        version_formatter = APP_SOURCE.split("function compactResultVersion(", 1)[1].split(
            "function resultContextResource(", 1
        )[0]

        for label in (
            "Backtest",
            "Dataset",
            "Sampler",
            "Pipeline",
            "Environment",
            "Analyzer",
        ):
            self.assertIn(f'"{label}"', renderer)
        self.assertNotIn('"Backtest ID"', renderer)
        self.assertIn("visibleResourceName", renderer)
        self.assertNotIn("<code", renderer)
        self.assertNotIn("${escapeHtml(identity", renderer)
        self.assertIn('"Sealed version"', version_formatter)
        self.assertNotIn(".slice(", version_formatter)
        for specialized_label in (
            "Cycles",
            "Annualized",
            "Sharpe",
            "Max Drawdown",
        ):
            self.assertNotIn(specialized_label, renderer)
        self.assertNotIn("metrics", renderer)

    def test_result_view_projects_exact_frozen_resource_versions(self):
        for resource in ("dataset", "sampler", "pipeline", "environment", "analysis"):
            self.assertIn(f'"{resource}": {{', RESULT_VIEW_SOURCE)
        self.assertIn('execution_chain["pipeline"]["version"]', RESULT_VIEW_SOURCE)
        self.assertIn('execution_chain["environment"]["version"]', RESULT_VIEW_SOURCE)
        self.assertIn('execution_chain["analysis"]["version"]', RESULT_VIEW_SOURCE)

    def test_visible_timezone_controls_are_removed(self):
        combined = "\n".join((APP_SOURCE, CHART_SOURCE, HTML_SOURCE, CHART_HTML_SOURCE))

        self.assertNotIn("resultTimezoneBtn", combined)
        self.assertNotIn("chartTimezoneBtn", combined)
        self.assertNotIn("TZ:", combined)
        self.assertNotIn("IANA time zone", combined)

    def test_result_layout_is_one_surface_with_a_flat_chart_hierarchy(self):
        result_html = HTML_SOURCE.split('<div id="results"', 1)[1].split(
            "</section>\n          </div>", 1
        )[0]

        self.assertIn('class="view result-workspace"', result_html)
        self.assertIn('class="result-surface"', result_html)
        self.assertIn('id="resultContextBar"', result_html)
        self.assertNotIn('class="panel"', result_html)
        self.assertNotIn("metricStrip", result_html)
        self.assertNotIn("Advanced Spec", result_html)
        self.assertIn(".chart-panel {", CSS_SOURCE)
        chart_panel = CSS_SOURCE.split(".chart-panel {", 1)[1].split("}", 1)[0]
        self.assertIn("border-top", chart_panel)
        self.assertNotIn("border-radius", chart_panel)
        self.assertNotIn("box-shadow", chart_panel)

        result_surface = CSS_SOURCE.split(".result-surface {", 1)[1].split("}", 1)[0]
        self.assertIn("overflow: visible", result_surface)
        result_route = CSS_SOURCE.split("html:has(body.route-result) {", 1)[1].split("}", 1)[0]
        self.assertIn("overflow-y: auto", result_route)
        search_menu = CSS_SOURCE.split(".search-tree-menu {", 1)[1].split("}", 1)[0]
        self.assertIn("grid-template-rows: auto minmax(0, 1fr)", search_menu)
        self.assertIn("var(--search-tree-space", search_menu)

    def test_data_display_reports_compatibility_and_uses_searchable_hierarchy(self):
        renderer = APP_SOURCE.split("function renderChartControls(", 1)[1].split(
            "function clearResultCharts()", 1
        )[0]

        self.assertIn("visualizerCompatibleDataState", APP_SOURCE)
        self.assertIn("compatible DataKeys", APP_SOURCE)
        self.assertIn("data-combobox-path", renderer)
        self.assertIn("data-combobox-meta", renderer)
        self.assertIn("enhanceSearchableSelect", renderer)
        self.assertIn("Search or browse Data Displays", renderer)
        self.assertIn("No Data Display is compatible with this Result.", renderer)

    def test_result_tags_are_compact_semantic_and_accessible(self):
        renderer = APP_SOURCE.split("function renderChartControls(", 1)[1].split(
            "function clearResultCharts()", 1
        )[0]
        labeler = APP_SOURCE.split("function visualizerTagLabel(", 1)[1].split(
            "function visualizerSummary(", 1
        )[0]
        summary = APP_SOURCE.split("function visualizerSummary(", 1)[1].split(
            "function applyButtonLabel(", 1
        )[0]

        self.assertEqual(renderer.count('class="chart-layer-tag-group"'), 2)
        self.assertEqual(renderer.count('</button><button class="tag-remove"'), 2)
        self.assertNotIn(">Remove</button>", renderer)
        self.assertEqual(renderer.count('<span aria-hidden="true">×</span>'), 2)
        self.assertEqual(renderer.count('aria-label="Remove '), 2)
        self.assertIn('title="${escapeHtml(tagTitle)}"', renderer)
        self.assertIn('title="${escapeHtml(summary)}"', renderer)
        self.assertIn('<span class="layer-key">${escapeHtml(tagLabel)}</span>', renderer)
        self.assertIn('data-remove-layer="${escapeHtml(visualizer.id)}"', renderer)
        self.assertIn('data-remove-temp-module="${escapeHtml(module.instanceId)}"', renderer)

        self.assertIn("definition?.inputPorts", labeler)
        self.assertIn("visualizer.params?.[name]", labeler)
        self.assertIn("new Set", labeler)
        self.assertIn('join(" · ")', labeler)
        self.assertIn("return visualizerTagLabel", summary)
        self.assertNotIn("Object.entries(params)", summary)

        group_style = CSS_SOURCE.split(".chart-layer-tag-group {", 1)[1].split("}", 1)[0]
        tag_style = CSS_SOURCE.split(".chart-layer-tag {", 1)[1].split("}", 1)[0]
        key_style = CSS_SOURCE.split(".layer-key {", 1)[1].split("}", 1)[0]
        remove_style = CSS_SOURCE.split(".tag-remove {", 1)[1].split("}", 1)[0]
        self.assertIn("max-width: min(100%, 420px)", group_style)
        self.assertIn("overflow: hidden", tag_style)
        self.assertIn("text-overflow: ellipsis", key_style)
        self.assertIn("white-space: nowrap", key_style)
        self.assertIn("opacity: 0", remove_style)
        self.assertIn("pointer-events: none", remove_style)
        self.assertIn(".chart-layer-tag-group:hover .tag-remove", CSS_SOURCE)
        self.assertIn(".chart-layer-tag-group:focus-within .tag-remove", CSS_SOURCE)
        self.assertIn("@media (hover: none)", CSS_SOURCE)

    def test_result_tag_real_browser_ellipsis_and_remove_focus_lifecycle(self):
        chrome = next((
            path for name in (
                "google-chrome-stable", "google-chrome", "chromium", "chromium-browser",
            )
            if (path := shutil.which(name))
        ), None)
        if not chrome:
            self.skipTest("Chrome or Chromium is required for the tag browser regression test")

        document = f"""<!doctype html>
<html>
  <head>
    <meta charset="utf-8" />
    <link rel="stylesheet" href="{(ROOT / 'web' / 'styles.css').as_uri()}" />
    <style>.tag-remove {{ transition: none !important; }}</style>
  </head>
  <body>
    <div class="chart-layer-tags" style="width: 280px">
      <span class="chart-layer-tag-group">
        <button class="chart-layer-tag" type="button" title="Candles (dataKey=price.day.NASDAQ_COMPOSITE, upColor=#089981, downColor=#f23645)">
          <span class="layer-key">Candles · price.day.NASDAQ_COMPOSITE.with.an.intentionally.long.suffix</span>
        </button>
        <button class="tag-remove" type="button" aria-label="Remove data tag Candles · price.day.NASDAQ_COMPOSITE" title="Remove Candles · price.day.NASDAQ_COMPOSITE"><span aria-hidden="true">×</span></button>
      </span>
    </div>
    <pre id="result"></pre>
    <script>
      const group = document.querySelector(".chart-layer-tag-group");
      const tag = group.querySelector(".chart-layer-tag");
      const key = tag.querySelector(".layer-key");
      const remove = group.querySelector(".tag-remove");
      const hoverNone = matchMedia("(hover: none)").matches;
      const initialOpacity = getComputedStyle(remove).opacity;
      const hiddenOrTouchVisible = initialOpacity === (hoverNone ? "1" : "0");
      tag.focus();
      const focusWithinReveals = getComputedStyle(remove).opacity === "1";
      remove.focus();
      const keyboardReachable = document.activeElement === remove
        && getComputedStyle(remove).pointerEvents === "auto";
      document.getElementById("result").textContent = JSON.stringify({{
        validSiblingButtons: tag.parentElement === group
          && remove.parentElement === group
          && !tag.contains(remove),
        truncated: key.scrollWidth > key.clientWidth
          && getComputedStyle(key).textOverflow === "ellipsis"
          && getComputedStyle(key).whiteSpace === "nowrap",
        bounded: group.getBoundingClientRect().width <= 280
          && tag.getBoundingClientRect().width <= 280,
        fullTitle: tag.title.includes("upColor=#089981")
          && !key.textContent.includes("upColor"),
        hiddenOrTouchVisible,
        focusWithinReveals,
        keyboardReachable,
        accessibleName: remove.getAttribute("aria-label").startsWith("Remove data tag"),
      }});
    </script>
  </body>
</html>"""
        with tempfile.TemporaryDirectory() as root:
            root_path = Path(root)
            page = root_path / "chart-tag.html"
            page.write_text(document, encoding="utf-8")
            completed = subprocess.run(
                [
                    chrome,
                    "--headless=new",
                    "--no-sandbox",
                    "--disable-gpu",
                    "--disable-dev-shm-usage",
                    "--window-size=800,420",
                    f"--user-data-dir={root_path / 'profile'}",
                    "--dump-dom",
                    page.as_uri(),
                ],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
                timeout=30,
            )
        self.assertEqual(
            completed.returncode,
            0,
            msg=f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}",
        )
        match = re.search(r'<pre id="result">(.*?)</pre>', completed.stdout, re.DOTALL)
        self.assertIsNotNone(match, msg=completed.stdout)
        result = json.loads(html.unescape(match.group(1)))
        self.assertTrue(all(result.values()), msg=result)

    @unittest.skipUnless(shutil.which("node"), "Node.js is required")
    def test_visualizer_tag_and_title_only_show_semantic_label_and_datakeys(self):
        script = r"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('web/app.js', 'utf8');
const surfaceSource = 'function semanticVisualizerLabel(' + source.split('function semanticVisualizerLabel(', 2)[1]
  .split('function applyButtonLabel(', 1)[0];
const context = {
  window: { TradeChartCore: { visualizerCatalog() { return [{
    id: 'ohlc.candles', label: 'Candles', inputPorts: { dataKey: {} },
  }]; } } },
  forms: { opaqueMachineIdentityKind() { return ''; } },
  visibleText(value, fallback = '') { return String(value ?? '').trim() || fallback; },
};
vm.createContext(context);
vm.runInContext(surfaceSource, context);
const visualizer = {
  id: 'market-candles',
  callback: 'ohlc.candles',
  params: {
    dataKey: 'price.day.NASDAQ_COMPOSITE',
    upColor: '#089981',
    downColor: '#f23645',
    timeDomainId: 'basic-market-time',
    priceScaleId: 'basic-market-price',
    targetVisualizerId: 'market-candles',
  },
};
const label = context.visualizerTagLabel({}, {}, visualizer);
const title = context.visualizerSummary({}, {}, null, visualizer);
assert.equal(label, 'Candles · price.day.NASDAQ_COMPOSITE');
assert.equal(title, label);
for (const hidden of ['market-candles', 'basic-market-time', 'basic-market-price', '#089981', '#f23645']) {
  assert.doesNotMatch(title, new RegExp(hidden.replaceAll('#', '\\#')));
}
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
    def test_log_scale_targets_the_single_chart_pane(self):
        script = r"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('web/app.js', 'utf8');
const toggleSource = 'function toggleChartLogScale(' + source.split('function toggleChartLogScale(', 2)[1]
  .split('function toggleChartControlsCollapsed(', 1)[0];
const calls = [];
const pane = { view: { logScale: false } };
const entry = {
  chart: {
    priceScale(...args) {
      return { applyOptions(options) { calls.push({ args, mode: options.mode }); } };
    },
  },
  logButton: { classList: { toggle() {} }, textContent: '' },
};
const context = {
  state: { selectedBacktest: { visualization: { panes: [pane] } } },
  window: { TradeChartCore: { priceScaleMode(logScale) { return logScale ? 'log' : 'linear'; } } },
  chartEntryForPane() { return entry; },
  persistVisualizationView() {},
};
vm.createContext(context);
vm.runInContext(toggleSource, context);
context.toggleChartLogScale(0);
assert.deepEqual(calls, [{ args: ['right'], mode: 'log' }]);
assert.doesNotMatch(toggleSource, /\.panes\?\.\(/);
"""
        completed = subprocess.run(
            [shutil.which("node"), "-e", script],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr or completed.stdout)

    def test_pane_projection_is_keyed_by_data_dependencies_and_has_bounded_retry(self):
        request_key = APP_SOURCE.split("function paneResultRequest(", 1)[1].split(
            "function paneProjectionCache(", 1
        )[0]
        loader = APP_SOURCE.split("async function ensurePaneResultLoaded(", 1)[1].split(
            "function paneLoadError(", 1
        )[0]

        self.assertIn("visualizerDependencyPlan", request_key)
        self.assertIn("baseResult", request_key)
        self.assertIn("pane,", request_key)
        self.assertIn("pane, scoped)", request_key)
        self.assertIn("paths: [...(plan.paths || [])].sort()", request_key)
        self.assertIn("visualizerId", request_key)
        self.assertIn("plan.planningError || plan.paths.length || plan.temporaryModules.length", request_key)
        self.assertIn("dependencyKey", request_key)
        self.assertIn("planningError: plan.planningError", request_key)
        self.assertIn("cache.entries.get(plan.visualizerId)", loader)
        self.assertIn("existing?.dependencyKey === plan.dependencyKey", loader)
        self.assertIn("current.generation !== generation", loader)
        self.assertIn("temporaryModules: plan.temporaryModules", loader)
        self.assertIn("entry.result = response.result || {}", loader)
        self.assertIn('entry.status = "error"', loader)
        self.assertIn("if (plan.planningError)", loader)
        self.assertIn('data-retry-chart="${paneIndex}"', APP_SOURCE)
        self.assertIn("data-retry-chart-instance", APP_SOURCE)

    @unittest.skipUnless(shutil.which("node"), "Node.js is required")
    def test_main_surface_isolates_instance_posts_failures_and_targeted_retry(self):
        script = r"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('web/app.js', 'utf8');
const loader = 'function instanceValueMap(' + source.split('function instanceValueMap(', 2)[1]
  .split('function scheduleVisualizationSave(', 1)[0];
const plans = [
  { visualizerId: 'a', paths: ['cycles.data.a'], temporaryModules: [] },
  {
    visualizerId: 'b',
    paths: ['cycles.data.raw'],
    temporaryModules: [{ instanceId: 'thrower' }],
  },
  { visualizerId: 'drawing', paths: [], temporaryModules: [] },
];
const pane = {
  id: 'pane',
  visualizers: [
    { id: 'a', params: { color: '#111111' } },
    { id: 'b', params: {} },
    { id: 'drawing', params: {} },
  ],
};
const spec = { temporaryModules: [], panes: [pane] };
const owner = {
  backtestId: 'bt_1', dataKeys: {}, visualization: spec,
  paneProjectionCaches: new Map(),
};
const snapshots = [];
const context = {
  window: { TradeChartCore: { visualizerDependencyPlan() { return plans; } } },
  state: { selectedBacktest: owner },
  paneScopedSpec(value) { return value; },
  renderResults() {
    const wrapper = context.paneResult?.(pane, spec);
    if (wrapper) snapshots.push(JSON.parse(JSON.stringify(wrapper)));
  },
  postResultJson: null,
  encodeURIComponent,
  AbortController,
  Map,
  Promise,
  JSON,
  String,
  console,
};
vm.createContext(context);
vm.runInContext(loader, context);
(async () => {
  let failB = true;
  const calls = [];
  context.postResultJson = async (_path, payload) => {
    calls.push(JSON.parse(JSON.stringify(payload)));
    if (payload.temporaryModules[0]?.instanceId === 'thrower' && failB) {
      throw new Error('temporary module failed');
    }
    return { result: { cycles: [{ data: { token: payload.paths[0] } }] } };
  };
  await context.ensurePaneResultLoaded(pane, spec);
  const afterFailure = JSON.parse(JSON.stringify(context.paneResult(pane, spec)));
  const keyBefore = JSON.stringify(context.paneResultRequest(pane, spec).plans.map(
    ({ visualizerId, dependencyKey }) => ({ visualizerId, dependencyKey }),
  ));
  pane.visualizers[0].params.color = '#222222';
  const keyAfterStyle = JSON.stringify(context.paneResultRequest(pane, spec).plans.map(
    ({ visualizerId, dependencyKey }) => ({ visualizerId, dependencyKey }),
  ));
  await context.ensurePaneResultLoaded(pane, spec);
  const callsBeforeRetry = calls.length;
  failB = false;
  await context.ensurePaneResultLoaded(
    pane, spec, { force: true, visualizerId: 'b' },
  );
  assert.equal(callsBeforeRetry, 2);
  assert.equal(calls.length, 3);
  assert.deepEqual(calls[0], { paths: ['cycles.data.a'], temporaryModules: [] });
  assert.deepEqual(calls[1], {
    paths: ['cycles.data.raw'], temporaryModules: [{ instanceId: 'thrower' }],
  });
  assert.deepEqual(calls[2], calls[1]);
  assert.equal(keyBefore, keyAfterStyle);
  assert.ok(afterFailure.instanceResults.a);
  assert.equal(afterFailure.errors.b.message, 'temporary module failed');
  assert.equal(afterFailure.instanceResults.drawing, undefined);
  const finalResult = context.paneResult(pane, spec);
  assert.ok(finalResult.instanceResults.a);
  assert.ok(finalResult.instanceResults.b);
  assert.deepEqual(Object.keys(finalResult.errors), []);
  assert.ok(snapshots.some((wrapper) => (
    wrapper.instanceResults.a && wrapper.errors.b?.message === 'temporary module failed'
  )));
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
    def test_main_surface_ignores_stale_posts_and_safely_keys_reserved_ids(self):
        script = r"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('web/app.js', 'utf8');
const loader = 'function instanceValueMap(' + source.split('function instanceValueMap(', 2)[1]
  .split('function scheduleVisualizationSave(', 1)[0];
const pane = {
  id: 'pane',
  visualizers: [{ id: '__proto__', params: { dataKey: 'old' } }],
};
const spec = { temporaryModules: [], panes: [pane] };
const owner = {
  backtestId: 'bt_1', dataKeys: {}, visualization: spec,
  paneProjectionCaches: new Map(),
};
const context = {
  window: {
    TradeChartCore: {
      visualizerDependencyPlan(_result, currentPane) {
        return [{
          visualizerId: '__proto__',
          paths: [`cycles.data.${currentPane.visualizers[0].params.dataKey}`],
          temporaryModules: [],
        }];
      },
    },
  },
  state: { selectedBacktest: owner },
  paneScopedSpec(value) { return value; },
  renderResults() {},
  encodeURIComponent,
  AbortController,
  Map,
  Promise,
  JSON,
  String,
  console,
};
vm.createContext(context);
vm.runInContext(loader, context);
(async () => {
  let resolveOld;
  context.postResultJson = async (_path, payload) => {
    if (payload.paths[0] === 'cycles.data.old') {
      return new Promise((resolve) => { resolveOld = resolve; });
    }
    return { result: { token: 'new' } };
  };
  const oldLoad = context.ensurePaneResultLoaded(pane, spec);
  await Promise.resolve();
  pane.visualizers[0].params.dataKey = 'new';
  const newLoad = context.ensurePaneResultLoaded(pane, spec);
  await newLoad;
  resolveOld({ result: { token: 'old' } });
  await oldLoad;
  const values = context.paneResult(pane, spec).instanceResults;
  assert.equal(values.__proto__.token, 'new');
  assert.equal(Object.getPrototypeOf(values), null);
  assert.equal(Object.prototype.hasOwnProperty.call(values, '__proto__'), true);
  assert.equal(Object.prototype.token, undefined);
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
    def test_main_surface_reloads_only_changed_instance_and_isolates_planning_and_stale_work(self):
        script = r"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('web/app.js', 'utf8');
const loader = 'function instanceValueMap(' + source.split('function instanceValueMap(', 2)[1]
  .split('function scheduleVisualizationSave(', 1)[0];
const pane = {
  id: 'pane',
  visualizers: [
    { id: 'a', visible: true, params: { dataKey: 'a', color: '#111' } },
    { id: 'b', visible: true, params: { dataKey: 'b1' } },
    { id: 'drawing', visible: true, params: {} },
  ],
};
const spec = { temporaryModules: [], panes: [pane] };
const owner = {
  backtestId: 'bt_1', dataKeys: {}, visualization: spec,
  paneProjectionCaches: new Map(),
};
let resolveSlowB;
let forbidA = false;
const calls = [];
const context = {
  window: {
    TradeChartCore: {
      visualizerDependencyPlan(_result, currentPane) {
        return currentPane.visualizers.flatMap((item) => {
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
  state: { selectedBacktest: owner },
  paneScopedSpec(value) { return value; },
  renderResults() {},
  queueMicrotask,
  encodeURIComponent,
  AbortController,
  Map,
  Promise,
  JSON,
  String,
  console,
  async postResultJson(_path, payload) {
    const body = JSON.parse(JSON.stringify(payload));
    calls.push(body);
    const dataKey = body.paths[0];
    if (dataKey === 'cycles.data.a' && forbidA) throw new Error('A must not reload');
    if (dataKey === 'cycles.data.bSlow') {
      return new Promise((resolve) => { resolveSlowB = resolve; });
    }
    return { result: { token: dataKey } };
  },
};
vm.createContext(context);
vm.runInContext(loader, context);
(async () => {
  await context.ensurePaneResultLoaded(pane, spec);
  assert.equal(calls.length, 2);
  const cache = owner.paneProjectionCaches.get('pane');
  const aEntry = cache.entries.get('a');
  forbidA = true;

  pane.visualizers[1].params.dataKey = 'b2';
  await context.ensurePaneResultLoaded(pane, spec);
  assert.equal(calls.length, 3);
  assert.deepEqual(calls[2], { paths: ['cycles.data.b2'], temporaryModules: [] });
  assert.equal(cache.entries.get('a'), aEntry);
  assert.equal(context.paneResult(pane, spec).instanceResults.a.token, 'cycles.data.a');

  pane.visualizers[1].params.dataKey = 'bSlow';
  const staleLoad = context.ensurePaneResultLoaded(pane, spec);
  await Promise.resolve();
  pane.visualizers[1].params.dataKey = 'bNew';
  await context.ensurePaneResultLoaded(pane, spec);
  resolveSlowB({ result: { token: 'cycles.data.bSlow' } });
  await staleLoad;
  assert.equal(context.paneResult(pane, spec).instanceResults.b.token, 'cycles.data.bNew');
  assert.equal(cache.entries.get('a'), aEntry);

  const callsBeforeHide = calls.length;
  pane.visualizers[1].visible = false;
  await context.ensurePaneResultLoaded(pane, spec);
  assert.equal(calls.length, callsBeforeHide);
  assert.equal(cache.entries.get('a'), aEntry);
  assert.equal(cache.entries.has('b'), false);
  assert.equal(context.paneResult(pane, spec).instanceResults.a.token, 'cycles.data.a');

  pane.visualizers[1].visible = true;
  pane.visualizers[1].params.planningFailure = true;
  await context.ensurePaneResultLoaded(pane, spec);
  const planned = context.paneResult(pane, spec);
  assert.equal(calls.length, callsBeforeHide);
  assert.equal(planned.instanceResults.a.token, 'cycles.data.a');
  assert.equal(planned.errors.b.code, 'instance-planning-error');
  assert.equal(planned.errors.b.planningCode, 'dependency-cycle');
  assert.equal(planned.errors.b.message, 'B cannot be planned');
  assert.equal(cache.entries.get('a'), aEntry);
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

    def test_result_projection_requests_timeout_abort_and_ignore_stale_work(self):
        post_json = APP_SOURCE.split("async function postJson(", 1)[1].split(
            "async function postResultJson(", 1
        )[0]
        timed_post = APP_SOURCE.split("async function postResultJson(", 1)[1].split(
            "const uiResourceMutationFingerprints", 1
        )[0]
        loader = APP_SOURCE.split("async function ensurePaneResultLoaded(", 1)[1].split(
            "function paneLoadError(", 1
        )[0]
        retry = APP_SOURCE.split("function retryPaneResult(", 1)[1].split(
            "function scheduleVisualizationSave(", 1
        )[0]
        discovery = APP_SOURCE.split("async function discoverResultDataKeys(", 1)[1].split(
            "async function retryResultDataKeyDiscovery(", 1
        )[0]
        refresh = APP_SOURCE.split("async function refreshSelectedBacktest(", 1)[1].split(
            'document.querySelectorAll(".nav-btn")', 1
        )[0]

        self.assertIn("signal: options.signal", post_json)
        # A successful HTTP response whose body fails to parse (including an
        # AbortError raised while streaming JSON) must not become a false `{}`
        # success and an incorrect "No chart data" state.
        self.assertNotIn("response.json().catch(() => ({}))", post_json)
        self.assertIn("RESULT_REQUEST_TIMEOUT_MS", timed_post)
        self.assertIn("controller.abort()", timed_post)
        self.assertIn("clearTimeout(timeout)", timed_post)
        self.assertIn("if (timedOut)", timed_post)

        self.assertIn("if (existing) abortVisualizerProjection(existing)", loader)
        self.assertIn("cache.entries.set(plan.visualizerId, entry)", loader)
        self.assertIn("const controller = new AbortController()", loader)
        self.assertIn("current !== entry", loader)
        self.assertIn("current.dependencyKey !== plan.dependencyKey", loader)
        self.assertIn("Chart data request timed out", loader)
        self.assertIn("force: true", retry)
        self.assertIn("resultDiscoveryRequest?.controller?.abort()", discovery)
        self.assertIn("resultDiscoveryRequest !== request", discovery)
        self.assertIn("DataKey discovery timed out", discovery)
        self.assertIn("resultDiscoveryRequest?.controller?.abort()", refresh)
        self.assertIn("state.selectedBacktest?.paneProjectionCaches", refresh)
        self.assertIn("abortVisualizerProjection(entry)", refresh)
        self.assertIn("cache.entries.delete(visualizerId)", refresh)

    @unittest.skipUnless(shutil.which("node"), "Node.js is required")
    def test_visualization_sync_events_use_revision_as_version_not_digest(self):
        source = "const uiResourceMutationFingerprints" + APP_SOURCE.split(
            "const uiResourceMutationFingerprints", 1
        )[1].split("const uiOperationFingerprints", 1)[0]
        script = f"""
const assert = require('node:assert/strict');
const vm = require('node:vm');
const events = [];
const context = {{
  Date,
  Math,
  String,
  Number,
  window: {{
    TradeUiSync: {{
      publishResourceChange(event) {{ events.push(event); return Promise.resolve(); }},
    }},
  }},
}};
vm.createContext(context);
vm.runInContext({json.dumps(source)}, context);
const response = (revision) => ({{
  visualization: {{
    visualizationId: 'bt_cas-current',
    revision,
    contentDigest: 'must-not-be-used',
  }},
}});
context.publishUiResourceMutation('/api/visualizations', response(1));
context.publishUiResourceMutation('/api/visualizations', response(2));
context.publishUiResourceMutation('/api/visualizations', response(2));
assert.equal(events.length, 2);
assert.deepEqual(events.map((event) => event.version), ['1', '2']);
assert(events.every((event) => !Object.hasOwn(event, 'digest')));
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
    def test_result_timeout_maps_abort_to_retryable_error_and_preserves_body_failure(self):
        script = r"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('web/app.js', 'utf8');
const postJsonSource = 'async function postJson(' + source.split('async function postJson(', 2)[1]
  .split('async function postResultJson(', 1)[0];
const postResultSource = 'async function postResultJson(' + source.split('async function postResultJson(', 2)[1]
  .split('const uiResourceMutationFingerprints', 1)[0];
const context = {
  AbortController,
  Error,
  JSON,
  Promise,
  RESULT_REQUEST_TIMEOUT_MS: 5,
  authState: { csrfToken: 'csrf' },
  clearTimeout,
  console,
  publishUiResourceMutation() {},
  setTimeout,
};
vm.createContext(context);
vm.runInContext(`${postJsonSource}\n${postResultSource}`, context);

(async () => {
  context.authenticatedFetch = async () => ({
    ok: true,
    status: 200,
    async json() { throw new Error('body stream aborted'); },
  });
  const bodyFailure = await vm.runInContext(
    `postJson('/api/backtests/bt/result', {}).then(() => '', (error) => error.message)`,
    context,
  );
  assert.equal(bodyFailure, 'body stream aborted');

  vm.runInContext(`postJson = async (_path, _payload, options) => new Promise((_resolve, reject) => {
    options.signal.addEventListener('abort', () => reject(new Error('aborted')), { once: true });
  })`, context);
  const timeoutFailure = await vm.runInContext(
    `postResultJson('/api/backtests/bt/result', {}, new AbortController(), 'projection timed out')
      .then(() => '', (error) => error.message)`,
    context,
  );
  assert.equal(timeoutFailure, 'projection timed out');
  process.stdout.write(JSON.stringify({ bodyFailure, timeoutFailure }));
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

    def test_chart_configuration_survives_renderer_and_single_pane_failures(self):
        draw = APP_SOURCE.split("function drawVisualization(", 1)[1].split(
            "function renderOverview(", 1
        )[0]

        panes = draw.index("const panes =")
        pane_try = draw.index("try {", panes)
        pane_result = draw.index("const result = paneResult", panes)
        controls = draw.index("const controls = renderChartControls", panes)
        library_check = draw.index("if (!library?.createChart)", panes)
        pane_catch = draw.index("} catch (error) {", pane_try)
        global_listeners = draw.index('area.querySelectorAll("[data-open-chart]")')

        self.assertNotIn("if (!library?.createChart)", draw[:panes])
        self.assertLess(pane_try, pane_result)
        self.assertLess(pane_try, controls)
        self.assertLess(controls, library_check)
        self.assertLess(pane_catch, global_listeners)
        self.assertIn("Configuration remains available above", draw)

    def test_required_ports_use_presence_and_visualizer_edits_preserve_visibility(self):
        action = APP_SOURCE.split("function visualizerActionState(", 1)[1].split(
            "function paneValidationMessage(", 1
        )[0]
        input_validation = APP_SOURCE.split(
            "function validateTemporaryModuleInputs(", 1
        )[1].split("function validateTemporaryModuleOutputs(", 1)[0]
        output_validation = APP_SOURCE.split(
            "function validateTemporaryModuleOutputs(", 1
        )[1].split("function validateVisualizerInputs(", 1)[0]
        visualizer_validation = APP_SOURCE.split(
            "function validateVisualizerInputs(", 1
        )[1].split("function temporaryModuleOutputConflict(", 1)[0]
        add_visualizer = APP_SOURCE.split("function addPaneVisualizer(", 1)[1].split(
            "function visualizerSummary(", 1
        )[0]

        self.assertIn("field.required", action)
        self.assertIn("params[field.name] === undefined", action)
        self.assertIn('params[field.name] === ""', action)
        self.assertNotIn("!params[field.name]", action)
        self.assertIn("port?.required", input_validation)
        self.assertIn("is required", input_validation)
        self.assertIn("port?.required", output_validation)
        self.assertIn("Bind at least one temporary module output", output_validation)
        self.assertIn("instance.visible === false", visualizer_validation)
        self.assertIn('Object.prototype.hasOwnProperty.call(previousItem, "visible")', add_visualizer)
        self.assertIn("nextItem.visible = previousItem.visible", add_visualizer)

    def test_pane_ui_identity_and_open_chart_use_stable_pane_ids(self):
        self.assertIn("function paneUiStateKey(paneIndex)", APP_SOURCE)
        self.assertIn("selectedTempByPane[paneUiStateKey(paneIndex)]", APP_SOURCE)
        self.assertIn("selectedVisualizerByPane[paneUiStateKey(paneIndex)]", APP_SOURCE)
        self.assertIn("while (usedIds.has(`chart-${index}`))", APP_SOURCE)
        self.assertIn("&paneId=${encodeURIComponent(pane.id)}", APP_SOURCE)

    def test_empty_optional_editors_do_not_report_validation_errors(self):
        validation = APP_SOURCE.split("function paneValidationMessage(", 1)[1].split(
            "function emptyPaneSelectionMessage(", 1
        )[0]
        self.assertNotIn("idleMessages", validation)
        self.assertIn("return paneSelectionHint(paneIndex)", validation)

    def test_overlay_displays_reference_visible_capability_providers_by_instance_id(self):
        validation = APP_SOURCE.split("function validateVisualizerInputs(", 1)[1].split(
            "function temporaryModuleOutputConflict(", 1
        )[0]
        editor = APP_SOURCE.split("function visualizerDefinitionForEditor(", 1)[1].split(
            "function incompatibleVisualizerDependents(", 1
        )[0]
        renderer = APP_SOURCE.split("function drawVisualization(", 1)[1].split(
            "function renderOverview(", 1
        )[0]
        self.assertNotIn("targetDataKey", validation)
        self.assertNotIn('definition.id === "overlay.', validation)
        self.assertIn("visualizerReferenceRequirements(definition)", validation)
        self.assertIn("requirement.bindingParam", validation)
        self.assertIn("visualizerProviderMatchesRequirement(", validation)
        self.assertIn('visualizerCapabilityDescriptors(providerDefinition, "provides")', APP_SOURCE)
        self.assertIn("instance.visible === false", validation)
        self.assertIn('type: "visualizerRef"', editor)
        self.assertIn('{ value: "", label: "Select…" }', editor)
        self.assertIn("value: item.id", editor)
        self.assertIn("label: visualizerTagLabel(", editor)
        self.assertIn("const diagnostics = mergeChartDiagnostics(", renderer)
        self.assertIn("timeInfo?.diagnostics", renderer)
        self.assertIn("chartContext?.diagnostics", renderer)
        self.assertIn("renderChartDiagnostics", renderer)
        diagnostic_message = APP_SOURCE.split("function chartDiagnosticMessage(", 1)[1].split(
            "function mergeChartDiagnostics(", 1
        )[0]
        diagnostic_renderer = APP_SOURCE.split("function renderChartDiagnostics(", 1)[1].split(
            "function runChartCleanups(", 1
        )[0]
        self.assertIn("diagnostic?.message", diagnostic_message)
        self.assertIn("diagnostic?.code", diagnostic_message)
        self.assertNotIn("diagnostic?.visualizerId", diagnostic_message)
        self.assertNotIn("diagnostic?.instanceId", diagnostic_message)
        self.assertIn("message.textContent = chartDiagnosticMessage(item)", diagnostic_renderer)

    def test_diagnostics_are_merged_deduplicated_and_cleanup_is_failure_isolated(self):
        main_draw = APP_SOURCE.split("function drawVisualization(", 1)[1].split(
            "function renderOverview(", 1
        )[0]
        standalone_draw = CHART_SOURCE.split("function drawPane(", 1)[1].split(
            "function renderStartupError(", 1
        )[0]
        main_merge = APP_SOURCE.split("function mergeChartDiagnostics(", 1)[1].split(
            "function renderChartDiagnostics(", 1
        )[0]
        standalone_merge = CHART_SOURCE.split("function mergePaneDiagnostics(", 1)[1].split(
            "function runChartCleanups(", 1
        )[0]
        main_cleanup = APP_SOURCE.split("function runChartCleanups(", 1)[1].split(
            "function resultDrawingUiState(", 1
        )[0]
        standalone_cleanup = CHART_SOURCE.split("function runChartCleanups(", 1)[1].split(
            "function renderPaneDiagnostics(", 1
        )[0]

        self.assertIn(
            "const diagnostics = mergeChartDiagnostics(",
            main_draw,
        )
        self.assertIn(
            "const diagnostics = mergePaneDiagnostics(",
            standalone_draw,
        )
        self.assertIn('item?.code === "missing-instance-result"', main_draw)
        self.assertIn('item?.code === "missing-instance-result"', standalone_draw)
        for merge in (main_merge, standalone_merge):
            self.assertIn("const seen = new Set()", merge)
            self.assertIn("seen.has(key)", merge)
        for cleanup in (main_cleanup, standalone_cleanup):
            self.assertIn("for (const cleanup of cleanups)", cleanup)
            self.assertIn("try { cleanup?.(); } catch", cleanup)

    def test_dynamic_datakeys_are_discovered_before_the_result_editor_settles(self):
        discovery = APP_SOURCE.split("async function discoverResultDataKeys(", 1)[1].split(
            "async function retryResultDataKeyDiscovery(", 1
        )[0]
        refresh = APP_SOURCE.split("async function refreshSelectedBacktest(", 1)[1].split(
            'document.querySelectorAll(".nav-btn")', 1
        )[0]

        self.assertIn("TradeChartCore.discoverySourcePaths", discovery)
        self.assertIn("temporaryModules: []", discovery)
        self.assertIn("TradeChartCore.dataKeyDeclarations", discovery)
        self.assertIn("await discoverResultDataKeys(selected, selectionSeq)", refresh)
        self.assertIn("Retry Data discovery", APP_SOURCE)

    def test_visualization_saves_are_serialized_and_manual_save_cancels_debounce(self):
        scheduler = APP_SOURCE.split("function scheduleVisualizationSave(", 1)[1].split(
            "function createLayerInstanceId(", 1
        )[0]
        manual = APP_SOURCE.split('$("saveVisualizationBtn").addEventListener', 1)[1].split(
            '$("cancelUnloadBtn")', 1
        )[0]

        self.assertIn("visualizationSaveQueue.then", scheduler)
        self.assertIn("structuredClone(spec)", scheduler)
        self.assertIn("visualizationSaveQueue = operation.catch", scheduler)
        self.assertIn('setHealth(true, "Saving visualization")', scheduler)
        self.assertNotIn('setHealth(false, "Saving visualization")', scheduler)
        self.assertIn("clearTimeout(visualizationSaveTimer)", manual)
        self.assertIn("enqueueVisualizationSave(backtestId, spec)", manual)


if __name__ == "__main__":
    unittest.main()
