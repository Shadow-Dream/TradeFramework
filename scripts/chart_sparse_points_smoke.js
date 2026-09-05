const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const sandbox = { console, structuredClone, Intl, Date, setTimeout, clearTimeout };
sandbox.window = sandbox;
sandbox.LightweightCharts = {
  PriceScaleMode: { Normal: 0, Logarithmic: 1 },
  LineStyle: { Solid: 0, Dotted: 1, Dashed: 2 },
};
const coreSource = fs.readFileSync('web/chart_core.js', 'utf8');
vm.runInNewContext(coreSource, sandbox);
const core = sandbox.TradeChartCore;
const plain = (value) => JSON.parse(JSON.stringify(value));
const hostObjectPrototypeSnapshot = Object.getOwnPropertyDescriptors(Object.prototype);
const sandboxObjectPrototype = vm.runInNewContext('Object.prototype', sandbox);
const sandboxObjectPrototypeSnapshot = Object.getOwnPropertyDescriptors(sandboxObjectPrototype);

const timestamp = { type: 'string' };
const scalar = { type: ['number', 'null'] };
const timedOhlc = {
  type: 'object',
  properties: {
    eventTime: timestamp,
    open: { type: 'number' }, high: { type: 'number' },
    low: { type: 'number' }, close: { type: 'number' }, complete: { type: 'boolean' },
  },
  required: ['eventTime', 'open', 'high', 'low', 'close'],
  additionalProperties: false,
};
const legacyOhlc = {
  type: 'object',
  properties: {
    open: { type: 'number' }, high: { type: 'number' },
    low: { type: 'number' }, close: { type: 'number' },
  },
  required: ['open', 'high', 'low', 'close'], additionalProperties: false,
};
const eventSchema = {
  type: ['object', 'null'],
  properties: { side: { type: 'string' }, reason: { type: ['string', 'null'] } },
  additionalProperties: false,
};

function def(id, {
  label = id, inputPorts = {}, fields = [], provides = [], requires = [],
  interactions = [], rendererId = id, apiVersion = 1,
} = {}) {
  const bindingParams = new Set(requires.map((descriptor) => descriptor.bindingParam));
  return {
    id, label, inputPorts, params: fields,
    paramsSchema: {
      type: 'object',
      properties: Object.fromEntries(fields.map((field) => [
        field.name, bindingParams.has(field.name) ? { type: 'string' } : {},
      ])),
      required: fields.filter((field) => field.required).map((field) => field.name),
      additionalProperties: false,
    },
    renderer: { id: rendererId, apiVersion },
    capabilities: { provides, requires, interactions },
  };
}
const priceCoordinateProvider = () => ({
  name: 'series', kind: 'series.price-coordinate',
  attributes: { 'price-scale': 'priceScaleId', 'time-domain': 'timeDomainId' },
});
const priceCoordinateTarget = (matchTimeDomain = false) => ({
  name: 'target', kind: 'series.price-coordinate', bindingParam: 'targetVisualizerId',
  matches: matchTimeDomain ? { 'time-domain': 'timeDomainId' } : {},
});
const primaryFields = [
  { name: 'dataKey', required: true }, { name: 'timeKey', required: true },
  { name: 'timeDomainId', required: true }, { name: 'priceScaleId', required: true },
];
const definitions = [
  def('ohlc.candles', {
    label: 'Candles', inputPorts: { dataKey: { schema: timedOhlc } },
    fields: [
      { name: 'dataKey', required: true }, { name: 'timeDomainId', required: true },
      { name: 'priceScaleId', required: true },
      { name: 'upColor', default: '#089981' }, { name: 'downColor', default: '#f23645' },
    ], provides: [priceCoordinateProvider()],
  }),
  def('series.line', {
    label: 'Line', inputPorts: { dataKey: { schema: scalar }, timeKey: { schema: timestamp } },
    fields: [
      ...primaryFields, { name: 'color', default: '#2563eb' }, { name: 'lineWidth', default: 2 },
      { name: 'offsetBars', default: 0 },
    ],
    provides: [priceCoordinateProvider()],
  }),
  def('series.scatter', {
    label: 'Scatter', inputPorts: { dataKey: { schema: scalar }, timeKey: { schema: timestamp } },
    fields: [...primaryFields, { name: 'color', default: '#2563eb' }, { name: 'pointRadius', default: 3 }],
    provides: [priceCoordinateProvider()],
  }),
  def('series.histogram', {
    label: 'Histogram', inputPorts: { dataKey: { schema: scalar }, timeKey: { schema: timestamp } },
    fields: [
      ...primaryFields, { name: 'color', default: '#64748b' },
      { name: 'positiveColor', default: '#089981' }, { name: 'negativeColor', default: '#f23645' },
      { name: 'offsetBars', default: 0 },
    ], provides: [priceCoordinateProvider()],
  }),
  def('overlay.markers', {
    label: 'Markers', inputPorts: { dataKey: { schema: eventSchema }, timeKey: { schema: timestamp } },
    fields: [
      { name: 'dataKey', required: true }, { name: 'timeKey', required: true },
      { name: 'timeDomainId', required: true }, { name: 'targetVisualizerId', required: true },
    ], requires: [priceCoordinateTarget(true)],
  }),
  def('overlay.priceLine', {
    label: 'Price Line', inputPorts: { dataKey: { schema: { type: 'number' } }, timeKey: { schema: timestamp } },
    fields: [
      { name: 'dataKey', required: true }, { name: 'timeKey', required: true },
      { name: 'timeDomainId', required: true }, { name: 'targetVisualizerId', required: true },
      { name: 'reducer', required: true }, { name: 'color', default: '#475569' },
      { name: 'lineWidth', default: 1 },
    ], requires: [priceCoordinateTarget(true)],
  }),
  def('drawing.horizontalLine', {
    label: 'Horizontal Line', fields: [
      { name: 'targetVisualizerId', required: true }, { name: 'price', required: true },
      { name: 'color', default: '#475569' }, { name: 'lineWidth', default: 1 },
    ], requires: [priceCoordinateTarget()], interactions: ['chart.pointer'],
  }),
  def('drawing.trendLine', {
    label: 'Trend Line', fields: [
      { name: 'targetVisualizerId', required: true }, { name: 'points', required: true },
      { name: 'color', default: '#475569' }, { name: 'lineWidth', default: 1 },
    ], requires: [priceCoordinateTarget()], interactions: ['chart.pointer'],
  }),
  def('drawing.rectangle', {
    label: 'Rectangle', fields: [
      { name: 'targetVisualizerId', required: true }, { name: 'corners', required: true },
      { name: 'color', default: '#475569' }, { name: 'lineWidth', default: 1 },
      { name: 'fillColor', default: '#2563eb' }, { name: 'fillOpacity', default: 0.12 },
    ], requires: [priceCoordinateTarget()], interactions: ['chart.pointer'],
  }),
  def('drawing.brush', {
    label: 'Brush', fields: [
      { name: 'targetVisualizerId', required: true }, { name: 'points', required: true },
      { name: 'color', default: '#475569' }, { name: 'lineWidth', default: 2 },
    ], requires: [priceCoordinateTarget()], interactions: ['chart.pointer'],
  }),
  def('drawing.text', {
    label: 'Text', fields: [
      { name: 'targetVisualizerId', required: true }, { name: 'anchor', required: true },
      { name: 'text', required: true }, { name: 'color', default: '#172026' },
      { name: 'fontSize', default: 14 },
    ], requires: [priceCoordinateTarget()], interactions: ['chart.pointer', 'chart.text-input'],
  }),
];
core.setVisualizerDefinitions(definitions);

function declaration(schema, path, value) {
  return { label: path, schema, source: { path }, encoding: { value } };
}
const result = {
  dataKeys: {
    candleA: declaration(timedOhlc, 'cycles.data.candleA', 'data.candleA'),
    candleB: declaration(timedOhlc, 'cycles.data.candleB', 'data.candleB'),
    legacy: declaration(legacyOhlc, 'cycles.data.legacy', 'data.legacy'),
    a: declaration(scalar, 'cycles.data.a', 'data.a'),
    b: declaration(scalar, 'cycles.data.b', 'data.b'),
    threshold: declaration({ type: 'number' }, 'cycles.data.threshold', 'data.threshold'),
    ta: declaration(timestamp, 'cycles.data.ta', 'data.ta'),
    tb: declaration(timestamp, 'cycles.data.tb', 'data.tb'),
    events: declaration(eventSchema, 'cycles.data.events', 'data.events'),
    hidden: declaration(scalar, 'cycles.data.hidden', 'data.hidden'),
  },
  cycles: [
    { decisionTime: '1999-01-01T00:00:00Z', data: {
      candleA: { eventTime: '2026-01-01T14:30:00Z', open: 10, high: 12, low: 9, close: 11 },
      candleB: { eventTime: '2026-01-01T14:30:00Z', open: 100, high: 120, low: 90, close: 110 },
      legacy: { open: 1, high: 2, low: 0, close: 1 }, a: 10, b: 200, threshold: 11,
      ta: '2026-01-01T14:30:00Z', tb: '2026-01-02T14:30:00Z',
      events: { side: 'buy', reason: 'entry' }, hidden: 999,
    } },
    { decisionTime: '1999-01-02T00:00:00Z', data: {
      candleA: { eventTime: '2026-01-02T14:30:00Z', open: 10, high: 12, low: 9, close: 11 },
      candleB: { eventTime: '2026-01-02T14:30:00Z', open: 101, high: 121, low: 91, close: 111 },
      legacy: { open: 2, high: 3, low: 1, close: 2 }, a: 12, b: 100, threshold: 13,
      ta: '2026-01-02T14:30:00Z', tb: '2026-01-01T14:30:00Z', events: null, hidden: 1000,
    } },
  ],
};

// Strict time and schema semantics.
const optionalTime = structuredClone(timedOhlc);
optionalTime.required = optionalTime.required.filter((name) => name !== 'eventTime');
assert.equal(core.visualizerSchemasCompatible(optionalTime, timedOhlc), false);
assert.deepEqual(plain(core.completeCandlePoints([{ open: 1, high: 2, low: 0, close: 1 }])), []);
assert.equal(core.completeCandlePoints([
  { eventTime: '2026-01-01T00:00:00Z', open: 1, high: 2, low: 0, close: 1 },
  { eventTime: '2026-01-02T00:00:00Z', open: 1, high: 2, low: 0, close: 1 },
]).length, 2);
assert.deepEqual(plain(core.sparseLinePoints([1, null, 3], [
  '2026-01-01T00:00:00Z', '2026-01-02T00:00:00Z', '2026-01-03T00:00:00Z',
])), [{ time: 1767225600, value: 1 }, { time: 1767398400, value: 3 }]);
const irregularTimes = [
  '2026-01-02T00:00:00Z', '2026-01-05T00:00:00Z',
  '2026-01-08T00:00:00Z', '2026-01-09T00:00:00Z',
];
assert.deepEqual(plain(core.sparseLinePoints([10, 20, 30, 40], irregularTimes, 1)), [
  { time: core.chartTime(irregularTimes[1]), value: 10 },
  { time: core.chartTime(irregularTimes[2]), value: 20 },
  { time: core.chartTime(irregularTimes[3]), value: 30 },
]);
assert.deepEqual(plain(core.sparseLinePoints([10, 20, 30, 40], irregularTimes, -1)), [
  { time: core.chartTime(irregularTimes[0]), value: 20 },
  { time: core.chartTime(irregularTimes[1]), value: 30 },
  { time: core.chartTime(irregularTimes[2]), value: 40 },
]);
assert.deepEqual(
  plain(core.sparseLinePoints([10, 20], irregularTimes)),
  plain(core.sparseLinePoints([10, 20], irregularTimes, 0)),
);
assert.throws(() => core.sparseLinePoints([1], irregularTimes, 0.5), /safe integer/);

// No peer style coupling.
const colors = { dataKey: 'candleB', upColor: '#089981', downColor: '#f23645' };
assert.deepEqual(plain(core.allocateDistinctVisualizerStyles(
  definitions[0], colors, [{ callback: 'ohlc.candles', params: colors }],
)), { params: colors, changed: false });
const styleSpec = { panes: [{ visualizers: [{ id: 'x', callback: 'ohlc.candles', params: colors }] }] };
assert.deepEqual(plain(core.normalizeVisualizerStyleCollisions(styleSpec)), { spec: styleSpec, changed: false });

// Discovery is definition-independent and bad unused definitions are isolated.
const dynamic = { dataKeys: { map: declaration({
  type: 'object', additionalProperties: { type: 'object', additionalProperties: { type: 'number' } },
}, 'cycles.data.map', 'data.map') } };
const discovered = core.discoverySourcePaths(dynamic);
const malformed = def('custom.malformed', { inputPorts: { dataKey: { schema: { badKeyword: true } } } });
core.setVisualizerDefinitions([...definitions, malformed]);
assert.deepEqual(plain(core.discoverySourcePaths(dynamic)), plain(discovered));
const catalog = core.visualizerCatalog({ dataKeys: result.dataKeys }, {});
assert(catalog.find((item) => item.id === 'series.line').optionMap.dataKey.length > 0);
assert.equal(catalog.find((item) => item.id === 'custom.malformed').optionMap.dataKey.length, 0);
core.setVisualizerDefinitions(definitions);

// Hidden sources are absent; temporary producer closure follows child bindings.
const hiddenPane = { visualizers: [
  { id: 'shown', callback: 'series.line', params: {
    dataKey: 'a', timeKey: 'ta', timeDomainId: 'market', priceScaleId: 'right',
  } },
  { id: 'hidden', callback: 'series.line', visible: false, params: {
    dataKey: 'hidden', timeKey: 'tb', timeDomainId: 'market', priceScaleId: 'hidden',
  } },
] };
assert.deepEqual(plain(core.visualizerDependencyPlan(result, hiddenPane)), [{
  visualizerId: 'shown',
  paths: ['cycles.data.a', 'cycles.data.ta'],
  temporaryModules: [],
}]);
const objectValue = { type: 'object', properties: { value: { type: 'number' } }, required: ['value'], additionalProperties: false };
core.setTemporaryModuleDefinitions([
  { kind: 'analysis', moduleId: 'up', version: 1, ports: { outputs: { out: { schema: objectValue } } } },
  { kind: 'analysis', moduleId: 'down', version: 1, ports: { outputs: { out: { schema: objectValue } } } },
  { kind: 'analysis', moduleId: 'unused', version: 1, ports: { outputs: { out: { schema: scalar } } } },
]);
const modules = [
  { instanceId: 'up', kind: 'analysis', moduleId: 'up', version: 1, config: {}, inputs: { x: 'b' }, outputs: { out: 'derived' } },
  { instanceId: 'down', kind: 'analysis', moduleId: 'down', version: 1, config: {}, inputs: { x: 'derived.value' }, outputs: { out: 'final' } },
  { instanceId: 'unused', kind: 'analysis', moduleId: 'unused', version: 1, config: {}, inputs: { x: 'hidden' }, outputs: { out: 'unused' } },
];
const modulePane = { visualizers: [{ id: 'temp', callback: 'series.line', params: {
  dataKey: 'final.value', timeKey: 'ta', timeDomainId: 'market', priceScaleId: 'right',
} }] };
assert.deepEqual(plain(core.visualizerDependencyPlan(result, modulePane, { temporaryModules: modules })), [{
  visualizerId: 'temp',
  paths: ['cycles.data.final.value', 'cycles.data.ta'],
  temporaryModules: modules.slice(0, 2),
}]);
const splitPlans = core.visualizerDependencyPlan(result, { visualizers: [
  ...modulePane.visualizers,
  { id: 'unrelated', callback: 'series.line', params: {
    dataKey: 'b', timeKey: 'tb', timeDomainId: 'market', priceScaleId: 'left',
  } },
] }, { temporaryModules: modules });
assert.deepEqual(plain(splitPlans.map((plan) => ({
  visualizerId: plan.visualizerId,
  paths: plan.paths,
  modules: plan.temporaryModules.map((module) => module.instanceId),
}))), [
  { visualizerId: 'temp', paths: ['cycles.data.final.value', 'cycles.data.ta'], modules: ['up', 'down'] },
  { visualizerId: 'unrelated', paths: ['cycles.data.b', 'cycles.data.tb'], modules: [] },
]);

// Dependency planning is instance-local. An unreferenced malformed Temporary
// Module is not inspected for a direct Result binding, while instances that
// bind malformed/unknown producers receive zero-request error plans.
core.setTemporaryModuleDefinitions([
  { kind: 'analysis', moduleId: 'up', version: 1, ports: { outputs: { out: { schema: objectValue } } } },
  { kind: 'analysis', moduleId: 'down', version: 1, ports: { outputs: { out: { schema: objectValue } } } },
  { kind: 'analysis', moduleId: 'unused', version: 1, ports: { outputs: { out: { schema: scalar } } } },
  { kind: 'analysis', moduleId: 'bad-schema', version: 1, ports: { outputs: { out: { schema: { badKeyword: true } } } } },
]);
let badModuleConfigReads = 0;
let badModuleSecretReads = 0;
const badSchemaModule = {
  instanceId: 'bad-schema', kind: 'analysis', moduleId: 'bad-schema', version: 1,
  inputs: {}, outputs: { out: 'broken' },
};
Object.defineProperty(badSchemaModule, 'config', {
  enumerable: true,
  get() { badModuleConfigReads += 1; throw new Error('unreferenced config was read'); },
});
Object.defineProperty(badSchemaModule, 'secret', {
  enumerable: true,
  get() { badModuleSecretReads += 1; throw new Error('unreferenced secret was read'); },
});
const unknownModule = {
  instanceId: 'unknown-temp', kind: 'analysis', moduleId: 'not-installed', version: 1,
  config: {}, inputs: {}, outputs: { out: 'unknownBroken' },
};
const planningSpec = { temporaryModules: [...modules, badSchemaModule, unknownModule] };
const directPane = { visualizers: [{ id: 'direct-a', callback: 'series.line', params: {
  dataKey: 'a', timeKey: 'ta', timeDomainId: 'market', priceScaleId: 'right',
} }] };
assert.deepEqual(plain(core.visualizerDependencyPlan(result, directPane, planningSpec)), [{
  visualizerId: 'direct-a', paths: ['cycles.data.a', 'cycles.data.ta'], temporaryModules: [],
}]);
assert.equal(badModuleConfigReads, 0);
const isolatedPlanning = core.visualizerDependencyPlan(result, { visualizers: [
  ...directPane.visualizers,
  { id: 'bad-b', callback: 'series.line', params: {
    dataKey: 'broken', timeKey: 'tb', timeDomainId: 'market', priceScaleId: 'left',
  } },
  { id: 'unknown-b', callback: 'series.line', params: {
    dataKey: 'unknownBroken', timeKey: 'tb', timeDomainId: 'market', priceScaleId: 'left',
  } },
] }, planningSpec);
assert.deepEqual(plain(isolatedPlanning[0]), {
  visualizerId: 'direct-a', paths: ['cycles.data.a', 'cycles.data.ta'], temporaryModules: [],
});
assert.deepEqual(plain(isolatedPlanning[1]), {
  visualizerId: 'bad-b', paths: [], temporaryModules: [],
  planningError: {
    code: 'temporary-schema-invalid',
    message: 'The data display could not be rendered.',
  },
});
assert.equal(isolatedPlanning[2].planningError.code, 'unknown-temporary-module');
assert.deepEqual(plain(isolatedPlanning[2].paths), []);
assert.deepEqual(plain(isolatedPlanning[2].temporaryModules), []);
assert.equal(badModuleConfigReads, 0);
assert.equal(badModuleSecretReads, 0);

// Catalog construction isolates each Temporary Module output. Base Result A
// and valid Temporary C remain selectable while bad/unknown outputs surface
// local unavailable diagnostics and never expose unrelated config/secret data.
const isolatedCatalog = core.visualizerCatalog({ dataKeys: result.dataKeys }, planningSpec);
const lineCatalog = isolatedCatalog.find((item) => item.id === 'series.line');
const lineDataKeys = new Set(lineCatalog.optionMap.dataKey.map((item) => item.value));
assert(lineDataKeys.has('a'));
assert(lineDataKeys.has('derived.value'));
assert(lineDataKeys.has('final.value'));
assert.equal(lineDataKeys.has('broken'), false);
assert.equal(lineDataKeys.has('unknownBroken'), false);
assert.equal(lineCatalog.unavailableReason, undefined);
const isolatedDeclarations = core.dataKeyDeclarations({ dataKeys: result.dataKeys }, planningSpec);
assert.equal(Object.prototype.hasOwnProperty.call(isolatedDeclarations, 'broken'), false);
assert.equal(Object.prototype.hasOwnProperty.call(isolatedDeclarations, 'unknownBroken'), false);
const catalogDiagnostics = core.dataKeyCatalogDiagnostics({ dataKeys: result.dataKeys }, planningSpec);
assert(catalogDiagnostics.some((item) => (
  item.code === 'temporary-schema-invalid'
  && item.dataKey === 'broken'
  && item.unavailable === true
  && item.module.instanceId === 'bad-schema'
)));
assert(catalogDiagnostics.some((item) => (
  item.code === 'unknown-temporary-module'
  && item.dataKey === 'unknownBroken'
  && item.unavailable === true
  && item.module.instanceId === 'unknown-temp'
)));
assert.equal(badModuleConfigReads, 0);
assert.equal(badModuleSecretReads, 0);

// A configured Temporary Module producer takes precedence over an identically
// named Result declaration, including recursively through a producer chain.
// Descendant producers are selected only when the bound port schema authorizes
// the descendant field.
const parentClose = {
  type: 'object', properties: { close: { type: 'number' } },
  required: ['close'], additionalProperties: false,
};
core.setTemporaryModuleDefinitions([
  { kind: 'analysis', moduleId: 'scalar-override', version: 1, ports: {
    inputs: { source: { schema: scalar } }, outputs: { out: { schema: scalar } },
  } },
  { kind: 'analysis', moduleId: 'parent-override', version: 1, ports: {
    inputs: { source: { schema: scalar } }, outputs: { out: { schema: parentClose } },
  } },
]);
let unboundSecretInputReads = 0;
const unboundSecretProducer = {
  instanceId: 'secret-child', kind: 'analysis', moduleId: 'scalar-override', version: 1,
  config: {}, outputs: { out: 'c.secret' },
};
Object.defineProperty(unboundSecretProducer, 'inputs', {
  enumerable: true,
  get() { unboundSecretInputReads += 1; throw new Error('unauthorized child producer selected'); },
});
const overrideModules = [
  { instanceId: 'override-b', kind: 'analysis', moduleId: 'scalar-override', version: 1,
    config: {}, inputs: { source: 'threshold' }, outputs: { out: 'b' } },
  { instanceId: 'override-a', kind: 'analysis', moduleId: 'scalar-override', version: 1,
    config: {}, inputs: { source: 'b' }, outputs: { out: 'a' } },
  { instanceId: 'close-child', kind: 'analysis', moduleId: 'scalar-override', version: 1,
    config: {}, inputs: { source: 'threshold' }, outputs: { out: 'c.close' } },
  unboundSecretProducer,
  { instanceId: 'parent-root', kind: 'analysis', moduleId: 'parent-override', version: 1,
    config: {}, inputs: { source: 'threshold' }, outputs: { out: 'parent' } },
  { instanceId: 'open-child', kind: 'analysis', moduleId: 'scalar-override', version: 1,
    config: {}, inputs: { source: 'threshold' }, outputs: { out: 'open.extra' } },
];
const overrideResult = {
  dataKeys: {
    ...result.dataKeys,
    c: declaration(timedOhlc, 'cycles.data.c', 'data.c'),
    parent: declaration(parentClose, 'cycles.data.parent', 'data.parent'),
    open: declaration({ type: 'object' }, 'cycles.data.open', 'data.open'),
  },
  cycles: result.cycles,
};
const exactOverride = core.visualizerDependencyPlan(overrideResult, { visualizers: [{
  id: 'exact-override', callback: 'series.line', params: {
    dataKey: 'a', timeKey: 'ta', timeDomainId: 'market', priceScaleId: 'right',
  },
}] }, { temporaryModules: overrideModules });
assert.deepEqual(plain(exactOverride.map((plan) => ({
  paths: plan.paths, modules: plan.temporaryModules.map((module) => module.instanceId),
}))), [{
  paths: ['cycles.data.a', 'cycles.data.ta'], modules: ['override-b', 'override-a'],
}]);
const childOverride = core.visualizerDependencyPlan(overrideResult, { visualizers: [{
  id: 'child-override', callback: 'ohlc.candles', params: {
    dataKey: 'c', timeDomainId: 'market', priceScaleId: 'right',
  },
}] }, { temporaryModules: overrideModules });
assert.deepEqual(plain(childOverride.map((plan) => ({
  paths: plan.paths, modules: plan.temporaryModules.map((module) => module.instanceId),
}))), [{ paths: ['cycles.data.c'], modules: ['close-child'] }]);
assert.equal(unboundSecretInputReads, 0);
const openObjectDefinition = def('custom.open-object', {
  inputPorts: { dataKey: { schema: { type: 'object' } } },
  fields: [{ name: 'dataKey', required: true }],
});
core.setVisualizerDefinitions([...definitions, openObjectDefinition]);
const openChildOverride = core.visualizerDependencyPlan(overrideResult, { visualizers: [{
  id: 'open-child-override', callback: 'custom.open-object', params: { dataKey: 'open' },
}] }, { temporaryModules: overrideModules });
assert.deepEqual(plain(openChildOverride.map((plan) => ({
  paths: plan.paths, modules: plan.temporaryModules.map((module) => module.instanceId),
}))), [{ paths: ['cycles.data.open'], modules: ['open-child'] }]);
core.setVisualizerDefinitions(definitions);
const ancestorOverride = core.visualizerDependencyPlan(overrideResult, { visualizers: [{
  id: 'ancestor-override', callback: 'series.line', params: {
    dataKey: 'parent.close', timeKey: 'ta', timeDomainId: 'market', priceScaleId: 'right',
  },
}] }, { temporaryModules: overrideModules });
assert.deepEqual(plain(ancestorOverride.map((plan) => ({
  paths: plan.paths, modules: plan.temporaryModules.map((module) => module.instanceId),
}))), [{
  paths: ['cycles.data.parent.close', 'cycles.data.ta'], modules: ['parent-root'],
}]);
const duplicateProducer = {
  instanceId: 'duplicate-a', kind: 'analysis', moduleId: 'scalar-override', version: 1,
  config: {}, inputs: { source: 'threshold' }, outputs: { out: 'a' },
};
const ambiguousOverride = core.visualizerDependencyPlan(overrideResult, { visualizers: [
  { id: 'duplicate-sibling', callback: 'series.line', params: {
    dataKey: 'threshold', timeKey: 'ta', timeDomainId: 'market', priceScaleId: 'left',
  } },
  { id: 'ambiguous-override', callback: 'series.line', params: {
    dataKey: 'a', timeKey: 'ta', timeDomainId: 'market', priceScaleId: 'right',
  } },
] }, { temporaryModules: [...overrideModules, duplicateProducer] });
assert.deepEqual(plain(ambiguousOverride[0]), {
  visualizerId: 'duplicate-sibling',
  paths: ['cycles.data.threshold', 'cycles.data.ta'],
  temporaryModules: [],
});
assert.equal(ambiguousOverride[1].planningError.code, 'ambiguous-temporary-producer');
assert.deepEqual(plain(ambiguousOverride[1].paths), []);
assert.deepEqual(plain(ambiguousOverride[1].temporaryModules), []);
assert.equal(unboundSecretInputReads, 0);

function makeChart() {
  const records = [];
  const chart = {
    records, removed: [], clickSubscriptions: 0,
    addCandlestickSeries: (options) => series('candlestick', options),
    addLineSeries: (options) => series('line', options),
    addHistogramSeries: (options) => series('histogram', options),
    addSeries: (_type, options) => series('generic', options),
    removeSeries(item) { chart.removed.push(item); },
    subscribeClick() { chart.clickSubscriptions += 1; },
    timeScale: () => ({
      timeToCoordinate: (time) => typeof time === 'number' ? time : core.chartTime(time),
      coordinateToTime: (x) => x,
    }),
  };
  function series(family, options) {
    const item = {
      family, options, data: [], priceLines: [], primitives: [], primitiveUpdates: 0,
      setData(rows) { item.data = plain(rows); },
      applyOptions(next) { Object.assign(item.options, plain(next)); },
      coordinateToPrice: (y) => y, priceToCoordinate: (price) => price,
      createPriceLine(config) {
        const line = {
          ...plain(config),
          applyOptions(next) { Object.assign(line, plain(next)); },
        };
        item.priceLines.push(line);
        return line;
      },
      removePriceLine(line) { item.priceLines = item.priceLines.filter((value) => value !== line); },
      setMarkers(markers) { item.markers = plain(markers); },
      attachPrimitive(primitive) {
        item.primitives.push(primitive);
        primitive.attached?.({ chart, series: item, requestUpdate() { item.primitiveUpdates += 1; } });
      },
      detachPrimitive(primitive) {
        item.primitives = item.primitives.filter((value) => value !== primitive);
        primitive.detached?.();
      },
    };
    records.push(item);
    return item;
  }
  return chart;
}
sandbox.LightweightCharts.createSeriesMarkers = (target, markers) => {
  target.markers = plain(markers);
  return { detach() { target.markers = []; } };
};

// Duplicate pane identities are rejected at every public planning/rendering
// entrance. Neither duplicate provider is selected, and its consumer cannot
// draw or become an interaction binding candidate.
const duplicateProviderPane = { visualizers: [
  { id: 'duplicate-provider', callback: 'series.line', params: {
    dataKey: 'a', timeKey: 'ta', timeDomainId: 'market', priceScaleId: 'right',
  } },
  { id: 'duplicate-provider', callback: 'series.line', params: {
    dataKey: 'b', timeKey: 'tb', timeDomainId: 'market', priceScaleId: 'left',
  } },
  { id: 'duplicate-consumer', callback: 'drawing.horizontalLine', params: {
    targetVisualizerId: 'duplicate-provider', price: 10,
  } },
] };
const duplicateDependencyPlans = core.visualizerDependencyPlan(result, duplicateProviderPane);
assert.equal(duplicateDependencyPlans.length, 3);
assert(duplicateDependencyPlans.slice(0, 2).every((plan) => (
  plan.planningError?.code === 'duplicate-visualizer-id'
  && plan.paths.length === 0
  && plan.temporaryModules.length === 0
)));
assert(duplicateDependencyPlans.every((plan) => plan.paths.length === 0));
assert.equal(duplicateDependencyPlans[2].planningError.code, 'duplicate-visualizer-id');
const duplicateTimeInfo = core.paneTimeInfo(result, duplicateProviderPane);
assert.equal(duplicateTimeInfo.start, null);
assert.equal(duplicateTimeInfo.end, null);
assert.equal(duplicateTimeInfo.timeDomainId, null);
assert.equal(duplicateTimeInfo.diagnostics.filter((item) => (
  item.code === 'duplicate-visualizer-id' && item.visualizerId === 'duplicate-provider'
)).length, 2);
assert(duplicateTimeInfo.diagnostics.some((item) => (
  item.code === 'duplicate-visualizer-id' && item.visualizerId === 'duplicate-consumer'
)));
const duplicateProviderChart = makeChart();
const duplicateDraw = core.drawFinancialPane(
  sandbox.LightweightCharts, duplicateProviderChart, result, duplicateProviderPane,
);
assert.equal(duplicateProviderChart.records.length, 0);
assert.equal(duplicateDraw.diagnostics.filter((item) => (
  item.code === 'duplicate-visualizer-id' && item.visualizerId === 'duplicate-provider'
)).length, 2);
assert(duplicateDraw.diagnostics.some((item) => (
  item.visualizerId === 'duplicate-consumer' && item.code === 'duplicate-visualizer-id'
)));
duplicateDraw.interactionController.dispose();

// Capability contracts are canonical, not normalized by the browser. Names
// are global across provide/require, descriptor arrays and interactions must
// already be sorted, and each bad Definition fails without affecting a valid
// sibling or authorizing a data load.
const definitionClone = (id) => plain(definitions.find((item) => item.id === id));
const sameCapabilityName = definitionClone('drawing.horizontalLine');
sameCapabilityName.capabilities.provides = [{
  name: 'target', kind: 'series.price-coordinate', attributes: {},
}];
const unsortedProvides = definitionClone('series.histogram');
unsortedProvides.capabilities.provides = [
  { ...priceCoordinateProvider(), name: 'z-series' },
  { ...priceCoordinateProvider(), name: 'a-series' },
];
const unsortedRequires = definitionClone('drawing.trendLine');
unsortedRequires.capabilities.requires = [
  { ...priceCoordinateTarget(), name: 'z-target' },
  priceCoordinateTarget(),
];
const unsortedInteractions = definitionClone('drawing.text');
unsortedInteractions.capabilities.interactions = ['chart.text-input', 'chart.pointer'];
core.setVisualizerDefinitions([
  definitionClone('series.line'), sameCapabilityName, unsortedProvides,
  unsortedRequires, unsortedInteractions,
]);
const capabilityContractPane = { visualizers: [
  { id: 'capability-good', callback: 'series.line', params: {
    dataKey: 'a', timeKey: 'ta', timeDomainId: 'market', priceScaleId: 'right',
  } },
  { id: 'capability-same-name', callback: 'drawing.horizontalLine', params: {
    targetVisualizerId: 'capability-good', price: 10,
  } },
  { id: 'capability-unsorted-provides', callback: 'series.histogram', params: {
    dataKey: 'b', timeKey: 'tb', timeDomainId: 'market', priceScaleId: 'left',
  } },
  { id: 'capability-unsorted-requires', callback: 'drawing.trendLine', params: {
    targetVisualizerId: 'capability-good', points: [
      { time: '2026-01-01T14:30:00Z', price: 10 },
      { time: '2026-01-02T14:30:00Z', price: 11 },
    ],
  } },
  { id: 'capability-unsorted-interactions', callback: 'drawing.text', params: {
    targetVisualizerId: 'capability-good',
    anchor: { time: '2026-01-01T14:30:00Z', price: 10 }, text: 'bad order',
  } },
] };
const capabilityContractPlans = core.visualizerDependencyPlan(result, capabilityContractPane);
assert.deepEqual(plain(capabilityContractPlans[0].paths), ['cycles.data.a', 'cycles.data.ta']);
for (const plan of capabilityContractPlans.slice(1)) {
  assert.equal(plan.planningError.code, 'invalid-capability-contract');
  assert.deepEqual(plain(plan.paths), []);
  assert.deepEqual(plain(plan.temporaryModules), []);
}
const capabilityTimeInfo = core.paneTimeInfo(result, capabilityContractPane);
assert.equal(capabilityTimeInfo.start, core.chartTime('2026-01-01T14:30:00Z'));
assert.equal(capabilityTimeInfo.end, core.chartTime('2026-01-02T14:30:00Z'));
for (const instance of capabilityContractPane.visualizers.slice(1)) {
  assert(capabilityTimeInfo.diagnostics.some((item) => (
    item.visualizerId === instance.id && item.code === 'invalid-capability-contract'
  )));
}
const capabilityContractChart = makeChart();
const capabilityContractDraw = core.drawFinancialPane(
  sandbox.LightweightCharts, capabilityContractChart, result, capabilityContractPane,
);
assert.equal(capabilityContractChart.records.length, 1);
assert.equal(capabilityContractChart.records[0].options.priceScaleId, 'right');
for (const instance of capabilityContractPane.visualizers.slice(1)) {
  assert(capabilityContractDraw.diagnostics.some((item) => (
    item.visualizerId === instance.id && item.code === 'invalid-capability-contract'
  )));
}
capabilityContractDraw.interactionController.dispose();

const asciiCapabilityCases = [
  {
    label: 'unicode descriptor name', callback: 'drawing.horizontalLine',
    mutate: (definition) => { definition.capabilities.requires[0].name = '目标'; },
  },
  {
    label: 'unicode descriptor kind', callback: 'drawing.horizontalLine',
    mutate: (definition) => { definition.capabilities.requires[0].kind = 'series.价格'; },
  },
  {
    label: 'unicode interaction', callback: 'drawing.horizontalLine',
    mutate: (definition) => { definition.capabilities.interactions = ['chart.鼠标']; },
  },
  {
    label: 'unicode attribute key', callback: 'series.histogram',
    mutate: (definition) => {
      definition.capabilities.provides[0].attributes = { 'price-比例': 'priceScaleId' };
    },
  },
  {
    label: 'unicode match key', callback: 'drawing.horizontalLine',
    mutate: (definition) => {
      definition.capabilities.requires[0].matches = { 'time-域': 'price' };
    },
  },
  {
    label: 'unicode attribute parameter', callback: 'series.histogram',
    mutate: (definition) => {
      definition.capabilities.provides[0].attributes['price-scale'] = '比例参数';
    },
  },
  {
    label: 'unicode match parameter', callback: 'drawing.horizontalLine',
    mutate: (definition) => {
      definition.capabilities.requires[0].matches = { 'time-domain': '价格参数' };
    },
  },
  {
    label: 'unicode binding parameter', callback: 'drawing.horizontalLine',
    mutate: (definition) => { definition.capabilities.requires[0].bindingParam = '目标参数'; },
  },
  {
    label: 'unsafe proto name', callback: 'drawing.horizontalLine',
    mutate: (definition) => { definition.capabilities.requires[0].name = '__proto__'; },
  },
  {
    label: 'unsafe constructor kind', callback: 'drawing.horizontalLine',
    mutate: (definition) => { definition.capabilities.requires[0].kind = 'constructor'; },
  },
  {
    label: 'unsafe prototype interaction', callback: 'drawing.horizontalLine',
    mutate: (definition) => { definition.capabilities.interactions = ['prototype']; },
  },
  {
    label: 'unsafe constructor attribute parameter', callback: 'series.histogram',
    mutate: (definition) => {
      definition.capabilities.provides[0].attributes['price-scale'] = 'constructor';
    },
  },
  {
    label: 'unsafe prototype match parameter', callback: 'drawing.horizontalLine',
    mutate: (definition) => {
      definition.capabilities.requires[0].matches = { 'time-domain': 'prototype' };
    },
  },
  {
    label: 'unsafe proto binding parameter', callback: 'drawing.horizontalLine',
    mutate: (definition) => { definition.capabilities.requires[0].bindingParam = '__proto__'; },
  },
  {
    label: 'empty identifier segment', callback: 'drawing.horizontalLine',
    mutate: (definition) => { definition.capabilities.requires[0].kind = 'series..price'; },
  },
  {
    label: 'attribute parameter is optional', callback: 'series.histogram',
    mutate: (definition) => {
      definition.capabilities.provides[0].attributes['price-scale'] = 'color';
    },
  },
  {
    label: 'match parameter is optional', callback: 'drawing.horizontalLine',
    mutate: (definition) => {
      definition.capabilities.requires[0].matches = { 'time-domain': 'color' };
    },
  },
  {
    label: 'binding parameter schema is not string', callback: 'drawing.horizontalLine',
    mutate: (definition) => {
      definition.paramsSchema.properties.targetVisualizerId = { type: 'number' };
    },
  },
];
for (const testCase of asciiCapabilityCases) {
  const badDefinition = definitionClone(testCase.callback);
  testCase.mutate(badDefinition);
  core.setVisualizerDefinitions([definitionClone('series.line'), badDefinition]);
  const badParams = testCase.callback === 'series.histogram'
    ? { dataKey: 'b', timeKey: 'tb', timeDomainId: 'market', priceScaleId: 'left' }
    : { targetVisualizerId: 'ascii-good', price: 10 };
  const pane = { visualizers: [
    { id: 'ascii-good', callback: 'series.line', params: {
      dataKey: 'a', timeKey: 'ta', timeDomainId: 'market', priceScaleId: 'right',
    } },
    { id: `ascii-bad-${asciiCapabilityCases.indexOf(testCase)}`, callback: testCase.callback, params: badParams },
  ] };
  const plans = core.visualizerDependencyPlan(result, pane);
  assert.deepEqual(plain(plans[0].paths), ['cycles.data.a', 'cycles.data.ta'], testCase.label);
  assert.equal(plans[1].planningError.code, 'invalid-capability-contract', testCase.label);
  assert.deepEqual(plain(plans[1].paths), [], testCase.label);
  const chart = makeChart();
  const drawn = core.drawFinancialPane(sandbox.LightweightCharts, chart, result, pane);
  assert.equal(chart.records.length, 1, testCase.label);
  assert(drawn.diagnostics.some((item) => (
    item.visualizerId === pane.visualizers[1].id && item.code === 'invalid-capability-contract'
  )), testCase.label);
  drawn.interactionController.dispose();
}

// Mapped parameters can use arbitrary safe ASCII names, but the Definition must
// declare the exact rename consistently in paramsSchema and its UI fields.
const opaqueParameterProvider = definitionClone('series.line');
opaqueParameterProvider.capabilities.provides[0].attributes = {
  'price-scale': 'scaleParam', 'time-domain': 'domainParam',
};
opaqueParameterProvider.paramsSchema.properties.scaleParam =
  opaqueParameterProvider.paramsSchema.properties.priceScaleId;
opaqueParameterProvider.paramsSchema.properties.domainParam =
  opaqueParameterProvider.paramsSchema.properties.timeDomainId;
delete opaqueParameterProvider.paramsSchema.properties.priceScaleId;
delete opaqueParameterProvider.paramsSchema.properties.timeDomainId;
opaqueParameterProvider.paramsSchema.required = opaqueParameterProvider.paramsSchema.required.map((name) => (
  name === 'priceScaleId' ? 'scaleParam' : name === 'timeDomainId' ? 'domainParam' : name
));
opaqueParameterProvider.params.forEach((field) => {
  if (field.name === 'priceScaleId') field.name = 'scaleParam';
  if (field.name === 'timeDomainId') field.name = 'domainParam';
});
const opaqueParameterConsumer = definitionClone('drawing.horizontalLine');
opaqueParameterConsumer.capabilities.requires[0].bindingParam = 'anchorParam';
opaqueParameterConsumer.paramsSchema.properties.anchorParam =
  opaqueParameterConsumer.paramsSchema.properties.targetVisualizerId;
delete opaqueParameterConsumer.paramsSchema.properties.targetVisualizerId;
opaqueParameterConsumer.paramsSchema.required = opaqueParameterConsumer.paramsSchema.required.map((name) => (
  name === 'targetVisualizerId' ? 'anchorParam' : name
));
opaqueParameterConsumer.params.find((field) => field.name === 'targetVisualizerId').name = 'anchorParam';
core.setVisualizerDefinitions([opaqueParameterProvider, opaqueParameterConsumer]);
const opaqueParameterPane = { visualizers: [
  { id: 'opaque-provider', callback: 'series.line', params: {
    dataKey: 'a', timeKey: 'ta', scaleParam: 'opaque-scale', domainParam: 'opaque-domain',
  } },
  { id: 'opaque-consumer', callback: 'drawing.horizontalLine', params: {
    anchorParam: 'opaque-provider', price: 10,
  } },
] };
const opaqueParameterPlans = core.visualizerDependencyPlan(result, opaqueParameterPane);
assert.equal(opaqueParameterPlans.some((plan) => plan.planningError), false);
const opaqueParameterChart = makeChart();
const opaqueParameterDraw = core.drawFinancialPane(
  sandbox.LightweightCharts, opaqueParameterChart, result, opaqueParameterPane,
);
assert.equal(opaqueParameterDraw.diagnostics.length, 0);
assert.equal(opaqueParameterChart.records.length, 1);
assert.equal(opaqueParameterChart.records[0].options.priceScaleId, 'opaque-scale');
assert.equal(opaqueParameterChart.records[0].priceLines.length, 1);
opaqueParameterDraw.interactionController.dispose();
core.setVisualizerDefinitions(definitions);

// DataKeys named like JavaScript prototype members are ordinary legal keys.
// Catalog/planner/binder lookups must remain own-property-only at every level.
const reservedDataKeys = ['__proto__', 'constructor', 'prototype'];
const defineOwnValue = (target, key, value) => Object.defineProperty(target, key, {
  value, enumerable: true, configurable: true, writable: true,
});
const reservedDeclarations = Object.create(null);
reservedDeclarations.ta = declaration(timestamp, 'cycles.data.ta', 'data.ta');
reservedDataKeys.forEach((key) => {
  reservedDeclarations[key] = declaration(scalar, `cycles.data.${key}`, `data.${key}`);
});
const inheritedReservedValues = Object.create(null);
reservedDataKeys.forEach((key, index) => defineOwnValue(inheritedReservedValues, key, 900 + index));
const explicitReservedValues = Object.create(inheritedReservedValues);
reservedDataKeys.forEach((key, index) => defineOwnValue(explicitReservedValues, key, 11 + index));
explicitReservedValues.ta = '2026-01-01T14:30:00Z';
const inheritedOnlyReservedValues = Object.create(inheritedReservedValues);
inheritedOnlyReservedValues.ta = '2026-01-02T14:30:00Z';
const reservedResult = {
  dataKeys: reservedDeclarations,
  cycles: [{ data: explicitReservedValues }, { data: inheritedOnlyReservedValues }],
};
const resultReservedCatalog = core.dataKeyDeclarations(reservedResult);
assert.equal(Object.getPrototypeOf(resultReservedCatalog), null);
reservedDataKeys.forEach((key) => {
  assert.equal(Object.prototype.hasOwnProperty.call(resultReservedCatalog, key), true);
  assert.equal(core.resolveDataKeyDeclaration(reservedResult, {}, key).source.path, `cycles.data.${key}`);
  assert.equal(core.resolveDataKeyDeclaration({ dataKeys: Object.create(null) }, {}, key), null);
});
const reservedContracts = Object.create(null);
reservedDataKeys.forEach((key) => {
  reservedContracts[key] = scalar;
  reservedContracts[`root.${key}`] = scalar;
});
const reservedExpanded = core.expandSchemaPaths(reservedContracts);
assert.equal(Object.getPrototypeOf(reservedExpanded), null);
reservedDataKeys.forEach((key) => {
  assert.equal(Object.prototype.hasOwnProperty.call(reservedExpanded, key), true);
  assert.equal(Object.prototype.hasOwnProperty.call(reservedExpanded.root.properties, key), true);
});
const resultReservedPane = { visualizers: reservedDataKeys.map((key, index) => ({
  id: `result-reserved-${index}`, callback: 'series.line', params: {
    dataKey: key, timeKey: 'ta', timeDomainId: 'reserved-result', priceScaleId: `result-${index}`,
  },
})) };
assert.deepEqual(plain(core.visualizerDependencyPlan(reservedResult, resultReservedPane).map((plan) => ({
  visualizerId: plan.visualizerId, paths: plan.paths,
}))), reservedDataKeys.map((key, index) => ({
  visualizerId: `result-reserved-${index}`,
  paths: [`cycles.data.${key}`, 'cycles.data.ta'],
})));
const resultReservedChart = makeChart();
const drawnResultReserved = core.drawFinancialPane(
  sandbox.LightweightCharts, resultReservedChart, reservedResult, resultReservedPane,
);
assert.equal(drawnResultReserved.diagnostics.length, 0);
assert.deepEqual(resultReservedChart.records.map((record) => record.data), [
  [{ time: core.chartTime('2026-01-01T14:30:00Z'), value: 11 }],
  [{ time: core.chartTime('2026-01-01T14:30:00Z'), value: 12 }],
  [{ time: core.chartTime('2026-01-01T14:30:00Z'), value: 13 }],
]);

const reservedTempDefinition = {
  kind: 'analysis', moduleId: 'reserved-outputs', version: 1,
  ports: { outputs: {
    protoOut: { schema: scalar }, constructorOut: { schema: scalar }, prototypeOut: { schema: scalar },
  } },
};
core.setTemporaryModuleDefinitions([reservedTempDefinition]);
const reservedTempModule = {
  instanceId: 'reserved-temp', kind: 'analysis', moduleId: 'reserved-outputs', version: 1,
  config: {}, inputs: {}, outputs: {
    protoOut: '__proto__', constructorOut: 'constructor', prototypeOut: 'prototype',
  },
};
const reservedTempResult = {
  dataKeys: { ta: declaration(timestamp, 'cycles.data.ta', 'data.ta') },
  cycles: reservedResult.cycles,
};
const reservedTempSpec = { temporaryModules: [reservedTempModule] };
const tempReservedCatalog = core.dataKeyDeclarations(reservedTempResult, reservedTempSpec);
assert.equal(Object.getPrototypeOf(tempReservedCatalog), null);
reservedDataKeys.forEach((key) => {
  assert.equal(Object.prototype.hasOwnProperty.call(tempReservedCatalog, key), true);
});
const tempReservedPane = { visualizers: reservedDataKeys.map((key, index) => ({
  id: `temp-reserved-${index}`, callback: 'series.line', params: {
    dataKey: key, timeKey: 'ta', timeDomainId: 'reserved-temp', priceScaleId: `temp-${index}`,
  },
})) };
const reservedTempPlans = core.visualizerDependencyPlan(
  reservedTempResult, tempReservedPane, reservedTempSpec,
);
assert.deepEqual(plain(reservedTempPlans.map((plan) => ({
  visualizerId: plan.visualizerId,
  paths: plan.paths,
  modules: plan.temporaryModules.map((module) => module.instanceId),
}))), reservedDataKeys.map((key, index) => ({
  visualizerId: `temp-reserved-${index}`,
  paths: [`cycles.data.${key}`, 'cycles.data.ta'],
  modules: ['reserved-temp'],
})));
const tempReservedChart = makeChart();
const drawnTempReserved = core.drawFinancialPane(
  sandbox.LightweightCharts, tempReservedChart, reservedTempResult, tempReservedPane, reservedTempSpec,
);
assert.equal(drawnTempReserved.diagnostics.length, 0);
assert.deepEqual(tempReservedChart.records.map((record) => record.data), [
  [{ time: core.chartTime('2026-01-01T14:30:00Z'), value: 11 }],
  [{ time: core.chartTime('2026-01-01T14:30:00Z'), value: 12 }],
  [{ time: core.chartTime('2026-01-01T14:30:00Z'), value: 13 }],
]);

// Two Candles share only their explicit domain; styles and price scales remain instance-owned.
const dualPane = { visualizers: [
  { id: 'ca', callback: 'ohlc.candles', params: {
    dataKey: 'candleA', timeDomainId: 'market', priceScaleId: 'right', upColor: '#111111', downColor: '#222222',
  } },
  { id: 'cb', callback: 'ohlc.candles', params: {
    dataKey: 'candleB', timeDomainId: 'market', priceScaleId: 'left', upColor: '#333333', downColor: '#444444',
  } },
] };
const dualChart = makeChart();
const dual = core.drawFinancialPane(sandbox.LightweightCharts, dualChart, result, dualPane);
assert.equal(dual.diagnostics.length, 0);
assert.deepEqual(dualChart.records.map((item) => item.options.priceScaleId), ['right', 'left']);
assert.deepEqual(dualChart.records.map((item) => item.options.upColor), ['#111111', '#333333']);
assert.deepEqual(dualChart.records.map((item) => item.options.title), ['Candles 1', 'Candles 2']);
assert(!JSON.stringify(dualChart.records.map((item) => item.options.title)).includes('ca'));
assert(!JSON.stringify(dualChart.records.map((item) => item.options.title)).includes('cb'));
assert.equal(dualChart.records[0].data[0].time, core.chartTime('2026-01-01T14:30:00Z'));
assert.deepEqual(Object.keys(dual).sort(), [
  'cleanups', 'diagnostics', 'interactionController', 'paneId', 'reconcile',
  'styleNormalizationChanged',
]);
for (const name of ['chart', 'library', 'pane', 'result', 'seriesByDataKey', 'seriesByLayerId', 'spec']) assert.equal(name in dual, false);
assert.deepEqual(plain(dual.interactionController.listTools()), { tools: [] });
const dualWithLine = {
  ...dualPane,
  visualizers: [...dualPane.visualizers, {
    id: 'line-added', callback: 'series.line', params: {
      dataKey: 'a', timeKey: 'ta', timeDomainId: 'market', priceScaleId: 'right',
    },
  }],
};
const lineReconcile = dual.reconcile(result, dualWithLine, {});
assert.equal(lineReconcile.updated, true);
assert.equal(dualChart.records.length, 3);
assert.equal(dualChart.removed.length, 0, 'unchanged candles must remain mounted');
const lineRemove = dual.reconcile(result, dualPane, {});
assert.equal(lineRemove.updated, true);
assert.equal(dualChart.removed.length, 1);
assert.equal(dualChart.removed[0].family, 'line');

// Bar offsets use the explicit bound time array by index. They neither assume
// a fixed duration nor synthesize a future time beyond the available domain.
const offsetChart = makeChart();
const offsetPane = { visualizers: [
  { id: 'leading-line', callback: 'series.line', params: {
    dataKey: 'a', timeKey: 'ta', timeDomainId: 'market', priceScaleId: 'line',
    color: '#2563eb', lineWidth: 2, offsetBars: 1,
  } },
  { id: 'lagging-histogram', callback: 'series.histogram', params: {
    dataKey: 'a', timeKey: 'ta', timeDomainId: 'market', priceScaleId: 'histogram',
    color: '#64748b', positiveColor: '#089981', negativeColor: '#f23645', offsetBars: -1,
  } },
] };
const offsetContext = core.drawFinancialPane(sandbox.LightweightCharts, offsetChart, result, offsetPane);
assert.equal(offsetContext.diagnostics.length, 0);
const leadingLine = offsetChart.records.find((item) => item.options.priceScaleId === 'line');
const laggingHistogram = offsetChart.records.find((item) => item.options.priceScaleId === 'histogram');
assert.deepEqual(leadingLine.data, [{ time: core.chartTime('2026-01-02T14:30:00Z'), value: 10 }]);
assert.deepEqual(laggingHistogram.data, [{
  time: core.chartTime('2026-01-01T14:30:00Z'), value: 12, color: '#089981',
}]);

// Bad source/schema and time-domain failures are instance-local.
const isolatedChart = makeChart();
const isolated = core.drawFinancialPane(sandbox.LightweightCharts, isolatedChart, result, { visualizers: [
  { id: 'legacy', callback: 'ohlc.candles', params: {
    dataKey: 'legacy', timeDomainId: 'market', priceScaleId: 'right', upColor: '#089981', downColor: '#f23645',
  } },
  { id: 'good', callback: 'series.line', params: {
    dataKey: 'a', timeKey: 'ta', timeDomainId: 'market', priceScaleId: 'right', color: '#2563eb', lineWidth: 2,
  } },
  { id: 'wrong-domain', callback: 'series.line', params: {
    dataKey: 'b', timeKey: 'tb', timeDomainId: 'other', priceScaleId: 'left', color: '#2563eb', lineWidth: 2,
  } },
] });
assert(isolated.diagnostics.some((item) => item.visualizerId === 'legacy' && item.code === 'input-schema-mismatch'));
assert(isolated.diagnostics.some((item) => item.visualizerId === 'wrong-domain' && item.code === 'time-domain-mismatch'));
assert.equal(isolatedChart.records.length, 1);

// Series creation is rolled back if setData throws, and the next independent
// instance still renders.
const rollbackChart = makeChart();
const addRollbackLine = rollbackChart.addLineSeries;
let throwFirstSetData = true;
rollbackChart.addLineSeries = (options) => {
  const item = addRollbackLine(options);
  if (throwFirstSetData) {
    throwFirstSetData = false;
    item.setData = () => { throw new Error('setData failed'); };
  }
  return item;
};
const rollback = core.drawFinancialPane(sandbox.LightweightCharts, rollbackChart, result, { visualizers: [
  { id: 'broken-set-data', callback: 'series.line', params: {
    dataKey: 'a', timeKey: 'ta', timeDomainId: 'market', priceScaleId: 'broken', color: '#111111', lineWidth: 2,
  } },
  { id: 'after-set-data', callback: 'series.line', params: {
    dataKey: 'b', timeKey: 'tb', timeDomainId: 'market', priceScaleId: 'after', color: '#222222', lineWidth: 2,
  } },
] });
assert(rollback.diagnostics.some((item) => item.visualizerId === 'broken-set-data' && item.code === 'renderer-draw-error'));
assert.equal(rollbackChart.removed.length, 1);
assert.equal(rollbackChart.removed[0].options.priceScaleId, 'broken');
assert(rollbackChart.records.some((item) => item.options.priceScaleId === 'after' && item.data.length === 2));

// Explicit instance targeting: same DataKey, different series; no primary/DataKey fallback.
const overlayChart = makeChart();
const overlayPane = { visualizers: [
  { id: 'markers', callback: 'overlay.markers', params: {
    dataKey: 'events', timeKey: 'ta', timeDomainId: 'market', targetVisualizerId: 'left',
  } },
  { id: 'right', callback: 'series.line', params: {
    dataKey: 'a', timeKey: 'ta', timeDomainId: 'market', priceScaleId: 'right', color: '#111111', lineWidth: 2,
  } },
  { id: 'left', callback: 'series.line', params: {
    dataKey: 'a', timeKey: 'ta', timeDomainId: 'market', priceScaleId: 'left', color: '#222222', lineWidth: 2,
  } },
  { id: 'latest', callback: 'overlay.priceLine', params: {
    dataKey: 'threshold', timeKey: 'tb', timeDomainId: 'market', targetVisualizerId: 'right',
    reducer: 'latest', color: '#475569', lineWidth: 1,
  } },
] };
const overlay = core.drawFinancialPane(sandbox.LightweightCharts, overlayChart, result, overlayPane);
assert.equal(overlay.diagnostics.length, 0);
const right = overlayChart.records.find((item) => item.options.priceScaleId === 'right');
const left = overlayChart.records.find((item) => item.options.priceScaleId === 'left');
assert.equal(right.markers, undefined);
assert.equal(left.markers.length, 1);
assert.equal(right.priceLines[0].price, 11); // explicit timeKey sort + reducer=latest
assert.equal(right.priceLines[0].title, 'Price Line');
const orphanChart = makeChart();
const orphan = core.drawFinancialPane(sandbox.LightweightCharts, orphanChart, result, { visualizers: [
  overlayPane.visualizers[1],
  { ...overlayPane.visualizers[0], id: 'orphan', params: { ...overlayPane.visualizers[0].params, targetVisualizerId: 'missing' } },
] });
assert(orphan.diagnostics.some((item) => item.visualizerId === 'orphan' && item.code === 'missing-overlay-target'));
assert.equal(orphanChart.records[0].markers, undefined);

// A wide compatible source is attenuated to the original port schema before
// adapter preparation; undeclared object fields are never even read.
let secretReads = 0;
const guardedEvent = { side: 'buy', reason: 'allowed' };
Object.defineProperty(guardedEvent, 'secret', {
  enumerable: true,
  get() { secretReads += 1; throw new Error('secret field was read'); },
});
const wideEventSchema = {
  type: ['object', 'null'],
  properties: {
    side: { type: 'string' }, reason: { type: ['string', 'null'] }, secret: { type: 'string' },
  },
  required: ['side', 'secret'], additionalProperties: false,
};
const guardedResult = {
  dataKeys: {
    ...result.dataKeys,
    guardedEvents: declaration(wideEventSchema, 'cycles.data.guardedEvents', 'data.guardedEvents'),
  },
  cycles: result.cycles.map((cycle, index) => ({
    ...cycle,
    data: { ...cycle.data, guardedEvents: index === 0 ? guardedEvent : null },
  })),
};
const guardedChart = makeChart();
const guarded = core.drawFinancialPane(sandbox.LightweightCharts, guardedChart, guardedResult, { visualizers: [
  overlayPane.visualizers[1],
  { id: 'guarded', callback: 'overlay.markers', params: {
    dataKey: 'guardedEvents', timeKey: 'ta', timeDomainId: 'market', targetVisualizerId: 'right',
  } },
] });
assert.equal(guarded.diagnostics.length, 0);
assert.equal(secretReads, 0);
assert.equal(guardedChart.records[0].markers[0].text, 'allowed');

// Composition attenuation selects only matching branches. A getter declared
// exclusively by a non-matching oneOf branch must not be read.
const composedEventPort = {
  allOf: [
    { oneOf: [
      {
        type: 'object',
        properties: {
          kind: { const: 'entry' }, side: { type: 'string' }, reason: { type: 'string' },
        },
        required: ['kind', 'side'], additionalProperties: false,
      },
      {
        type: 'object',
        properties: { kind: { const: 'exit' }, exitOnly: { type: 'string' } },
        required: ['kind', 'exitOnly'], additionalProperties: false,
      },
      { type: 'null' },
    ] },
    { anyOf: [
      { type: 'object', properties: { reason: { type: 'string' } }, required: ['reason'] },
      { type: 'object', properties: { note: { type: 'string' } }, required: ['note'] },
    ] },
  ],
};
const composedSourceSchema = {
  type: 'object',
  properties: {
    kind: { const: 'entry' }, side: { type: 'string' }, reason: { type: 'string' },
    exitOnly: { type: 'string' }, secret: { type: 'string' },
  },
  required: ['kind', 'side', 'reason', 'secret'], additionalProperties: false,
};
assert(core.visualizerSchemasCompatible(composedSourceSchema, composedEventPort));
let wrongBranchReads = 0;
let composedSecretReads = 0;
const composedEvent = { kind: 'entry', side: 'buy', reason: 'composed' };
Object.defineProperties(composedEvent, {
  exitOnly: { enumerable: true, get() { wrongBranchReads += 1; throw new Error('wrong branch read'); } },
  secret: { enumerable: true, get() { composedSecretReads += 1; throw new Error('composed secret read'); } },
});
const composedDefinitions = structuredClone(definitions);
composedDefinitions.find((item) => item.id === 'overlay.markers').inputPorts.dataKey.schema = composedEventPort;
core.setVisualizerDefinitions(composedDefinitions);
const composedResult = {
  dataKeys: {
    ...result.dataKeys,
    composedEvents: declaration(composedSourceSchema, 'cycles.data.composedEvents', 'data.composedEvents'),
  },
  cycles: [{ ...result.cycles[0], data: { ...result.cycles[0].data, composedEvents: composedEvent } }],
};
const composedChart = makeChart();
const composed = core.drawFinancialPane(sandbox.LightweightCharts, composedChart, composedResult, { visualizers: [
  overlayPane.visualizers[1],
  { id: 'composed', callback: 'overlay.markers', params: {
    dataKey: 'composedEvents', timeKey: 'ta', timeDomainId: 'market', targetVisualizerId: 'right',
  } },
] });
assert.equal(composed.diagnostics.length, 0);
assert.equal(wrongBranchReads, 0);
assert.equal(composedSecretReads, 0);
assert.equal(composedChart.records[0].markers[0].text, 'composed');
core.setVisualizerDefinitions(definitions);

// Executable renderer adapters are host-owned. Unknown or incompatible pure
// Definitions fail per instance and cannot install same-realm JavaScript.
assert.equal(core.registerRenderer, undefined);
assert.equal(core.registerDrawCallback, undefined);
const customPorts = { dataKey: { schema: scalar }, timeKey: { schema: timestamp } };
const customDefs = [
  def('custom.alias', { inputPorts: customPorts, fields: primaryFields, provides: [priceCoordinateProvider()], rendererId: 'series.line' }),
  def('custom.v2', { inputPorts: customPorts, fields: primaryFields, provides: [priceCoordinateProvider()], apiVersion: 2 }),
  def('custom.unknown', { inputPorts: customPorts, fields: primaryFields, provides: [priceCoordinateProvider()] }),
];
core.setVisualizerDefinitions([...definitions, ...customDefs]);
const customChart = makeChart();
const custom = core.drawFinancialPane(sandbox.LightweightCharts, customChart, result, { visualizers: [
  { id: 'safe', callback: 'series.line', params: { dataKey: 'a', timeKey: 'ta', timeDomainId: 'market', priceScaleId: 'custom' } },
  { id: 'bad-alias', callback: 'custom.alias', params: { dataKey: 'b', timeKey: 'tb', timeDomainId: 'market', priceScaleId: 'alias' } },
  { id: 'bad-version', callback: 'custom.v2', params: { dataKey: 'b', timeKey: 'tb', timeDomainId: 'market', priceScaleId: 'v2' } },
  { id: 'unknown', callback: 'custom.unknown', params: { dataKey: 'b', timeKey: 'tb', timeDomainId: 'market', priceScaleId: 'unknown' } },
] });
assert(customChart.records.some((item) => item.options.priceScaleId === 'custom'));
assert(custom.diagnostics.some((item) => item.visualizerId === 'bad-alias' && item.code === 'invalid-renderer-contract'));
assert(custom.diagnostics.some((item) => item.visualizerId === 'bad-version' && item.code === 'invalid-renderer-contract'));
assert(custom.diagnostics.some((item) => item.visualizerId === 'unknown' && item.code === 'unknown-renderer'));

// Each instance may receive an independent Result slice/load error.
const wrapperChart = makeChart();
const resultWrapper = {
  instanceResults: { safe: result },
  errors: {
    failed: { message: 'isolated transport failed' },
    planned: { code: 'instance-planning-error', message: 'invalid isolated dependency plan' },
  },
};
const wrapperPane = { visualizers: [
  { id: 'failed', callback: 'series.line', params: { dataKey: 'b', timeKey: 'tb', timeDomainId: 'market', priceScaleId: 'left' } },
  { id: 'planned', callback: 'series.line', params: { dataKey: 'b', timeKey: 'tb', timeDomainId: 'market', priceScaleId: 'left' } },
  { id: 'safe', callback: 'series.line', params: { dataKey: 'a', timeKey: 'ta', timeDomainId: 'market', priceScaleId: 'right' } },
] };
const wrapper = core.drawFinancialPane(sandbox.LightweightCharts, wrapperChart, resultWrapper, wrapperPane);
assert(wrapper.diagnostics.some((item) => item.visualizerId === 'failed' && item.code === 'instance-load-error'));
assert(wrapper.diagnostics.some((item) => item.visualizerId === 'planned' && item.code === 'instance-planning-error'));
assert.equal(wrapperChart.records.length, 1);
const wrapperTime = core.paneTimeInfo(resultWrapper, wrapperPane);
assert(wrapperTime.diagnostics.some((item) => item.visualizerId === 'failed' && item.code === 'instance-load-error'));
assert(wrapperTime.diagnostics.some((item) => item.visualizerId === 'planned' && item.code === 'instance-planning-error'));
assert.equal(wrapperTime.start, core.chartTime('2026-01-01T14:30:00Z'));
const unsafePane = { visualizers: [{
  id: '__proto__', callback: 'series.line',
  params: { dataKey: 'b', timeKey: 'tb', timeDomainId: 'market', priceScaleId: 'left' },
}] };
assert.deepEqual(plain(core.visualizerDependencyPlan(result, unsafePane)), [{
  visualizerId: '__proto__', paths: [], temporaryModules: [],
  planningError: { code: 'invalid-instance-id', message: 'The data display could not be rendered.' },
}]);
const unsafe = core.drawFinancialPane(sandbox.LightweightCharts, makeChart(), result, unsafePane);
assert(unsafe.diagnostics.some((item) => item.code === 'invalid-instance-id'));

// Generic resource-bound gestures and Canvas scenes: click sequence, stroke,
// text/IME, transactional preview, and raw-event attenuation.
core.setVisualizerDefinitions(definitions);
class FakeElement {
  constructor() {
    this.listeners = new Map(); this.style = {}; this.value = ''; this.className = '';
    this.parent = null; this.focused = false;
  }
  addEventListener(type, fn) {
    if (!this.listeners.has(type)) this.listeners.set(type, new Set());
    this.listeners.get(type).add(fn);
  }
  removeEventListener(type, fn) { this.listeners.get(type)?.delete(fn); }
  emit(type, event = {}) { [...(this.listeners.get(type) || [])].forEach((fn) => fn(event)); }
  setAttribute() {}
  focus() { this.focused = true; }
  remove() {
    if (this.parent) this.parent.children = this.parent.children.filter((child) => child !== this);
    this.parent = null;
  }
}
class Container {
  constructor() {
    this.clientWidth = 800; this.clientHeight = 400; this.listeners = new Map();
    this.listenerOptions = new Map(); this.children = []; this.classes = new Set();
    this.capturedPointers = new Set();
    this.classList = { add: (value) => this.classes.add(value), remove: (value) => this.classes.delete(value) };
  }
  addEventListener(type, fn, options) {
    if (!this.listeners.has(type)) this.listeners.set(type, new Set());
    this.listeners.get(type).add(fn);
    this.listenerOptions.set(type, options);
  }
  removeEventListener(type, fn) { this.listeners.get(type)?.delete(fn); }
  emit(type, event) {
    if (type === 'lostpointercapture') this.capturedPointers.delete(event.pointerId);
    [...(this.listeners.get(type) || [])].forEach((fn) => fn(event));
  }
  getBoundingClientRect() { return { left: 0, top: 0 }; }
  setPointerCapture(pointerId) { this.capturedPointers.add(pointerId); }
  releasePointerCapture(pointerId) { this.capturedPointers.delete(pointerId); }
  appendChild(child) { child.parent = this; this.children.push(child); return child; }
}
sandbox.document = { createElement: () => new FakeElement() };
const contextOperations = (primitive) => {
  const operations = [];
  const context = {
    save() { operations.push(['save']); }, restore() { operations.push(['restore']); },
    beginPath() { operations.push(['beginPath']); }, moveTo(x, y) { operations.push(['moveTo', x, y]); },
    lineTo(x, y) { operations.push(['lineTo', x, y]); }, closePath() { operations.push(['closePath']); },
    fill() { operations.push(['fill', context.fillStyle]); }, stroke() { operations.push(['stroke', context.strokeStyle, context.lineWidth]); },
    fillText(text, x, y) { operations.push(['fillText', text, x, y, context.fillStyle, context.font]); },
  };
  primitive.paneViews()[0].renderer().draw({ useMediaCoordinateSpace(callback) { callback({ context, mediaSize: { width: 800, height: 400 } }); } });
  return operations;
};
let preventedPointers = 0;
let stoppedPointers = 0;
let rawSecretReads = 0;
const pointer = (clientY, pointerId, clientX, extra = {}) => {
  const event = {
    clientX, clientY, pointerId, pointerType: 'mouse', isPrimary: true,
    button: 0, buttons: 1, width: 1, height: 1, pressure: 0.5,
    tangentialPressure: 0, tiltX: 0, tiltY: 0, twist: 0,
    altitudeAngle: 1, azimuthAngle: 0, timeStamp: 1,
    altKey: false, ctrlKey: false, metaKey: false, shiftKey: false,
    preventDefault() { preventedPointers += 1; },
    stopPropagation() { stoppedPointers += 1; },
    ...extra,
  };
  Object.defineProperty(event, 'secretPayload', {
    enumerable: true, get() { rawSecretReads += 1; throw new Error('raw pointer payload leaked'); },
  });
  return event;
};
const duplicateInteractiveChart = makeChart();
const duplicateInteractiveContainer = new Container();
sandbox.LightweightCharts.createChart = () => duplicateInteractiveChart;
const duplicateCreatedChart = core.createFinancialChart(
  duplicateInteractiveContainer, { timeZone: 'UTC', showTime: true },
);
const duplicateInteractiveDraw = core.drawFinancialPane(
  sandbox.LightweightCharts, duplicateCreatedChart, result, duplicateProviderPane,
);
const duplicateHorizontalTool = duplicateInteractiveDraw.interactionController.listTools().tools.find((tool) => (
  tool.id === 'horizontal-line'
));
assert.deepEqual(plain(duplicateHorizontalTool.bindings[0].candidates), []);
assert.equal(duplicateInteractiveDraw.interactionController.activate(
  'horizontal-line', { bindings: { target: 'duplicate-provider' } },
), false);
duplicateInteractiveDraw.interactionController.dispose();
const trendPoints = [
  { time: '2026-01-01T14:30:00Z', price: 8 }, { time: '2026-01-02T14:30:00Z', price: 12 },
];
const interactionPane = { visualizers: [
  { id: 'target', callback: 'series.line', params: {
    dataKey: 'a', timeKey: 'ta', timeDomainId: 'market', priceScaleId: 'right', color: '#2563eb', lineWidth: 2,
  } },
  { id: 'horizontal', callback: 'drawing.horizontalLine', params: {
    targetVisualizerId: 'target', price: 5, color: '#475569', lineWidth: 1,
  } },
  { id: 'trend', callback: 'drawing.trendLine', params: {
    targetVisualizerId: 'target', points: trendPoints, color: '#475569', lineWidth: 1,
  } },
] };
const firstX = core.chartTime('2026-01-01T14:30:00Z');
const secondX = core.chartTime('2026-01-02T14:30:00Z');
const mountInteraction = (pane = interactionPane) => {
  const chart = makeChart();
  const container = new Container();
  sandbox.LightweightCharts.createChart = () => chart;
  const createdChart = core.createFinancialChart(container, { timeZone: 'UTC', showTime: true });
  const draw = core.drawFinancialPane(sandbox.LightweightCharts, createdChart, result, pane);
  const events = [];
  draw.interactionController.subscribe((event) => events.push(plain(event)));
  return {
    chart, container, draw, events,
    controller: draw.interactionController,
    targetSeries: chart.records[0],
  };
};
const applyCanonicalEvent = (pane, event) => {
  const next = structuredClone(pane);
  if (event.type === 'commit') {
    const index = next.visualizers.findIndex((item) => item.id === event.instance.id);
    if (index < 0) next.visualizers.push(structuredClone(event.instance));
    else next.visualizers[index] = structuredClone(event.instance);
  } else if (event.type === 'delete') {
    next.visualizers = next.visualizers.filter((item) => item.id !== event.visualizerId);
  } else {
    assert.fail(`Unexpected persistence event: ${event.type}`);
  }
  return next;
};
const disposeInteraction = (interaction) => {
  interaction.draw.cleanups.forEach((cleanup) => cleanup());
};

// Interaction metadata remains semantic and pointer listeners are capture-phase.
const metadataInteraction = mountInteraction();
assert.equal(metadataInteraction.draw.diagnostics.length, 0);
assert.equal(metadataInteraction.chart.clickSubscriptions, 0);
assert.equal(metadataInteraction.targetSeries.priceLines[0].title, 'Horizontal Line');
assert.deepEqual(plain(metadataInteraction.container.listenerOptions.get('pointerdown')), { capture: true, passive: false });
const listed = plain(metadataInteraction.controller.listTools());
assert.equal(Object.prototype.hasOwnProperty.call(listed, 'targets'), false);
assert.deepEqual(listed.tools.map((tool) => tool.id), [
  'select', 'horizontal-line', 'trend-line', 'rectangle', 'brush', 'text',
]);
assert.deepEqual(listed.tools.find((tool) => tool.id === 'rectangle').bindings, [{
  name: 'target', label: 'target', kind: 'series.price-coordinate',
  candidates: [{ visualizerId: 'target', label: 'Line' }],
}]);
disposeInteraction(metadataInteraction);

// Rectangle click-click keeps a live hover preview and promotes it into the
// controller-owned presentation on commit. A second drawing can commit in the
// same chart/controller before any canonical remount.
const clickRectangle = mountInteraction();
assert(clickRectangle.controller.activate('rectangle', { bindings: { target: 'target' } }));
clickRectangle.container.emit('pointerdown', pointer(8, 1, firstX));
clickRectangle.container.emit('pointerup', pointer(8, 1, firstX));
const clickRectanglePrimitive = clickRectangle.targetSeries.primitives[
  clickRectangle.targetSeries.primitives.length - 1
];
clickRectangle.container.emit('pointermove', pointer(12, 1, secondX));
const rectangleOps = contextOperations(clickRectanglePrimitive);
assert(rectangleOps.some((item) => item[0] === 'fill'));
assert(rectangleOps.some((item) => item[0] === 'closePath'));
clickRectangle.container.emit('pointerdown', pointer(12, 2, secondX));
clickRectangle.container.emit('pointerup', pointer(12, 2, secondX));
const rectangleCommit = clickRectangle.events.find((event) => event.type === 'commit');
assert.equal(rectangleCommit.instance.callback, 'drawing.rectangle');
assert.equal(rectangleCommit.instance.params.color, '#475569');
assert.equal(rectangleCommit.instance.params.lineWidth, 1);
assert.deepEqual(rectangleCommit.instance.params.corners, [
  { time: '2026-01-01T14:30:00.000Z', price: 8 },
  { time: '2026-01-02T14:30:00.000Z', price: 12 },
]);
assert.equal(clickRectangle.targetSeries.primitives.includes(clickRectanglePrimitive), true);
assert.equal(clickRectangle.controller.activate('brush', { bindings: { target: 'target' } }), true);
clickRectangle.container.emit('pointerdown', pointer(10, 3, firstX));
clickRectangle.container.emit('pointermove', pointer(11, 3, secondX));
clickRectangle.container.emit('pointerup', pointer(12, 3, secondX));
const consecutiveCommits = clickRectangle.events.filter((event) => event.type === 'commit');
assert.equal(consecutiveCommits.length, 2);
assert.equal(consecutiveCommits[1].instance.callback, 'drawing.brush');
assert.equal(clickRectangle.targetSeries.primitives.length, 3);
disposeInteraction(clickRectangle);
assert.equal(clickRectangle.targetSeries.primitives.includes(clickRectanglePrimitive), false);
const consecutivePane = consecutiveCommits.reduce(applyCanonicalEvent, interactionPane);
const canonicalClickRectangle = mountInteraction(consecutivePane);
assert.equal(canonicalClickRectangle.targetSeries.primitives.length, 3);
assert(contextOperations(canonicalClickRectangle.targetSeries.primitives[1]).some((item) => item[0] === 'fill'));
disposeInteraction(canonicalClickRectangle);

// Trend and Rectangle also support a conventional single down-drag-up gesture.
const dragTrend = mountInteraction();
assert(dragTrend.controller.activate('trend-line', { bindings: { target: 'target' } }));
dragTrend.container.emit('pointerdown', pointer(8, 4, firstX));
const dragTrendPrimitive = dragTrend.targetSeries.primitives[
  dragTrend.targetSeries.primitives.length - 1
];
dragTrend.container.emit('pointermove', pointer(12, 4, secondX));
dragTrend.container.emit('pointerup', pointer(12, 4, secondX));
const trendCommit = dragTrend.events.find((event) => event.type === 'commit');
assert.equal(trendCommit.instance.callback, 'drawing.trendLine');
assert.deepEqual(trendCommit.instance.params.points, [
  { time: '2026-01-01T14:30:00.000Z', price: 8 },
  { time: '2026-01-02T14:30:00.000Z', price: 12 },
]);
assert.equal(dragTrend.targetSeries.primitives.includes(dragTrendPrimitive), true);
disposeInteraction(dragTrend);
assert.equal(dragTrend.targetSeries.primitives.includes(dragTrendPrimitive), false);
const canonicalDragTrend = mountInteraction(applyCanonicalEvent(interactionPane, trendCommit));
assert.equal(canonicalDragTrend.targetSeries.primitives.length, 2);
disposeInteraction(canonicalDragTrend);

const dragRectangle = mountInteraction();
assert(dragRectangle.controller.activate('rectangle', { bindings: { target: 'target' } }));
dragRectangle.container.emit('pointerdown', pointer(8, 5, firstX));
const dragRectanglePrimitive = dragRectangle.targetSeries.primitives[
  dragRectangle.targetSeries.primitives.length - 1
];
dragRectangle.container.emit('pointermove', pointer(12, 5, secondX));
dragRectangle.container.emit('pointerup', pointer(12, 5, secondX));
const dragRectangleCommit = dragRectangle.events.find((event) => event.type === 'commit');
assert.equal(dragRectangleCommit.instance.callback, 'drawing.rectangle');
assert.deepEqual(dragRectangleCommit.instance.params.corners, [
  { time: '2026-01-01T14:30:00.000Z', price: 8 },
  { time: '2026-01-02T14:30:00.000Z', price: 12 },
]);
assert.equal(dragRectangle.targetSeries.primitives.includes(dragRectanglePrimitive), true);
disposeInteraction(dragRectangle);
assert.equal(dragRectangle.targetSeries.primitives.includes(dragRectanglePrimitive), false);
const canonicalDragRectangle = mountInteraction(applyCanonicalEvent(interactionPane, dragRectangleCommit));
assert.equal(canonicalDragRectangle.targetSeries.primitives.length, 2);
disposeInteraction(canonicalDragRectangle);

// Brush keeps input order exactly, including repeated and backwards x values.
const brushInteraction = mountInteraction();
assert(brushInteraction.controller.activate('brush', { bindings: { target: 'target' } }));
brushInteraction.container.emit('pointerdown', pointer(10, 6, firstX));
const brushPrimitive = brushInteraction.targetSeries.primitives[
  brushInteraction.targetSeries.primitives.length - 1
];
brushInteraction.container.emit('pointermove', pointer(11, 6, firstX));
brushInteraction.container.emit('pointermove', pointer(12, 6, firstX - 3600));
brushInteraction.container.emit('pointerup', pointer(13, 6, firstX));
const brushCommit = brushInteraction.events.find((event) => event.type === 'commit');
assert.equal(brushCommit.instance.callback, 'drawing.brush');
assert.deepEqual(brushCommit.instance.params.points.map((point) => core.chartTime(point.time)), [
  firstX, firstX, firstX - 3600, firstX,
]);
const brushLines = contextOperations(brushPrimitive).filter((item) => item[0] === 'lineTo');
assert.deepEqual(brushLines.map((item) => item[1]), [firstX, firstX - 3600, firstX]);
assert.equal(brushInteraction.targetSeries.primitives.includes(brushPrimitive), true);
disposeInteraction(brushInteraction);
assert.equal(brushInteraction.targetSeries.primitives.includes(brushPrimitive), false);
const canonicalBrush = mountInteraction(applyCanonicalEvent(interactionPane, brushCommit));
assert.equal(canonicalBrush.targetSeries.primitives.length, 2);
disposeInteraction(canonicalBrush);

// Text is chart-scoped, composition-safe, live-previewed, explicit-commit,
// and blur cancels rather than committing.
const textInteraction = mountInteraction();
assert(textInteraction.controller.activate('text', { bindings: { target: 'target' } }));
textInteraction.container.emit('pointerdown', pointer(22, 7, secondX));
textInteraction.container.emit('pointerup', pointer(22, 7, secondX));
let editor = textInteraction.container.children.find((child) => child.className === 'chart-interaction-text-input');
const cancelledTextPrimitive = textInteraction.targetSeries.primitives[
  textInteraction.targetSeries.primitives.length - 1
];
editor.value = 'cancel me';
editor.emit('input', { data: 'cancel me', inputType: 'insertText' });
editor.emit('blur', {});
assert.equal(textInteraction.events.some((event) => event.type === 'commit'), false);
assert.equal(textInteraction.targetSeries.primitives.includes(cancelledTextPrimitive), false);

assert(textInteraction.controller.activate('text', { bindings: { target: 'target' } }));
textInteraction.container.emit('pointerdown', pointer(20, 8, firstX));
textInteraction.container.emit('pointerup', pointer(20, 8, firstX));
editor = textInteraction.container.children.find((child) => child.className === 'chart-interaction-text-input');
assert(editor && editor.focused);
const textPrimitive = textInteraction.targetSeries.primitives[
  textInteraction.targetSeries.primitives.length - 1
];
editor.emit('compositionstart');
editor.value = '文';
editor.emit('input', { data: '文', inputType: 'insertCompositionText', isComposing: true });
editor.value = '文字';
editor.emit('compositionend', { data: '字', inputType: 'insertCompositionText' });
assert(contextOperations(textPrimitive).some((item) => item[0] === 'fillText' && item[1] === '文字'));
editor.emit('keydown', { key: 'Enter', shiftKey: false, preventDefault() {}, stopPropagation() {} });
const textCommit = textInteraction.events.find((event) => event.type === 'commit');
assert.equal(textCommit.instance.callback, 'drawing.text');
assert.equal(textCommit.instance.params.text, '文字');
assert.equal(textInteraction.targetSeries.primitives.includes(textPrimitive), true);
disposeInteraction(textInteraction);
assert.equal(textInteraction.targetSeries.primitives.includes(textPrimitive), false);
const canonicalText = mountInteraction(applyCanonicalEvent(interactionPane, textCommit));
assert.equal(canonicalText.targetSeries.primitives.length, 2);
disposeInteraction(canonicalText);

// Selection is a pure click below the drag threshold. Crossing the threshold
// starts a transactional edit; pointercancel and lost capture both restore the
// canonical drawing, while pointerup commits the final edit.
const editInteraction = mountInteraction();
assert(editInteraction.controller.activate('select'));
const horizontalPrimitive = editInteraction.targetSeries.priceLines[0];
editInteraction.container.emit('pointerdown', pointer(5, 9, firstX - 86400));
editInteraction.container.emit('pointermove', pointer(8, 9, firstX - 86400));
editInteraction.container.emit('pointerup', pointer(8, 9, firstX - 86400));
assert.equal(horizontalPrimitive.price, 5);
assert.equal(editInteraction.events.some((event) => event.type === 'commit'), false);

editInteraction.container.emit('pointerdown', pointer(5, 10, firstX - 86400));
editInteraction.container.emit('pointermove', pointer(9, 10, firstX - 86400));
assert.equal(horizontalPrimitive.price, 9);
editInteraction.container.emit('pointercancel', pointer(9, 10, firstX - 86400));
assert.equal(horizontalPrimitive.price, 5);

editInteraction.container.emit('pointerdown', pointer(5, 11, firstX - 86400));
editInteraction.container.emit('pointermove', pointer(11, 11, firstX - 86400));
assert.equal(horizontalPrimitive.price, 11);
assert.equal(editInteraction.container.capturedPointers.has(11), true);
editInteraction.container.emit('lostpointercapture', pointer(11, 11, firstX - 86400));
assert.equal(horizontalPrimitive.price, 5);
assert.equal(editInteraction.container.capturedPointers.has(11), false);

editInteraction.container.emit('pointerdown', pointer(5, 12, firstX - 86400));
editInteraction.container.emit('pointermove', pointer(12, 12, firstX - 86400));
editInteraction.container.emit('pointerup', pointer(12, 12, firstX - 86400));
const horizontalCommit = editInteraction.events.find((event) => event.type === 'commit');
assert.equal(horizontalCommit.instance.id, 'horizontal');
assert.equal(horizontalCommit.instance.params.price, 12);
assert.equal(horizontalPrimitive.price, 12);
editInteraction.container.emit('pointerdown', pointer(12, 14, firstX - 86400));
editInteraction.container.emit('pointermove', pointer(17, 14, firstX - 86400));
assert.equal(horizontalPrimitive.price, 17);
editInteraction.container.emit('pointercancel', pointer(17, 14, firstX - 86400));
assert.equal(horizontalPrimitive.price, 12);
disposeInteraction(editInteraction);
const canonicalEdit = mountInteraction(applyCanonicalEvent(interactionPane, horizontalCommit));
assert.equal(canonicalEdit.targetSeries.priceLines[0].price, 12);
disposeInteraction(canonicalEdit);

// A pure Select click must not edit or save. It leaves the drawing selected so
// either the application button or keyboard handler can call deleteSelected().
const deleteInteraction = mountInteraction();
assert(deleteInteraction.controller.activate('select'));
const trendMidX = (firstX + secondX) / 2;
const selectedTrendPrimitive = deleteInteraction.targetSeries.primitives[0];
const trendBeforeSelection = contextOperations(selectedTrendPrimitive);
deleteInteraction.container.emit('pointerdown', pointer(10, 13, trendMidX));
deleteInteraction.container.emit('pointerup', pointer(10, 13, trendMidX));
assert.equal(deleteInteraction.events.some((event) => event.type === 'commit'), false);
assert(deleteInteraction.events.some((event) => event.type === 'selection' && event.visualizerId === 'trend'));
assert.deepEqual(contextOperations(selectedTrendPrimitive), trendBeforeSelection);
assert.deepEqual(interactionPane.visualizers.find((item) => item.id === 'trend').params.points, trendPoints);
assert.equal(deleteInteraction.controller.deleteSelected(), true);
const deleteEvent = deleteInteraction.events.find((event) => event.type === 'delete');
assert.equal(deleteEvent.visualizerId, 'trend');
assert.equal(deleteInteraction.targetSeries.primitives.includes(selectedTrendPrimitive), false);
assert.equal(deleteInteraction.controller.deleteSelected(), false);
assert.equal(deleteInteraction.controller.activate('brush', { bindings: { target: 'target' } }), true);
disposeInteraction(deleteInteraction);
const canonicalDelete = mountInteraction(applyCanonicalEvent(interactionPane, deleteEvent));
assert.equal(canonicalDelete.targetSeries.primitives.length, 0);
disposeInteraction(canonicalDelete);

// Presentation reconciliation is capability-neutral and synchronous. It
// updates the live primitive and hit-test together, removes one instance
// without remounting the chart, and leaves the prior scene untouched when any
// member of the requested presentation is invalid.
const reconciledInteraction = mountInteraction();
const reconciledTrendPrimitive = reconciledInteraction.targetSeries.primitives[0];
const updatedPresentation = structuredClone(interactionPane.visualizers);
updatedPresentation.find((item) => item.id === 'trend').params.points = [
  { time: '2026-01-01T14:30:00Z', price: 20 },
  { time: '2026-01-02T14:30:00Z', price: 22 },
];
assert.equal(reconciledInteraction.controller.reconcilePresentation({
  visualizers: updatedPresentation,
}), true);
assert.equal(reconciledInteraction.targetSeries.primitives.includes(reconciledTrendPrimitive), true);
assert(contextOperations(reconciledTrendPrimitive).some((item) => (
  item[0] === 'lineTo' && item[2] === 22
)));
const defaultlessPresentation = structuredClone(updatedPresentation);
defaultlessPresentation.find((item) => item.id === 'horizontal').params = {
  targetVisualizerId: 'target', price: 9,
};
assert.equal(reconciledInteraction.controller.reconcilePresentation({
  visualizers: defaultlessPresentation,
}), true);
assert.equal(reconciledInteraction.targetSeries.priceLines[0].price, 9);
assert(reconciledInteraction.controller.activate('select'));
reconciledInteraction.container.emit('pointerdown', pointer(9, 32, firstX - 86400));
reconciledInteraction.container.emit('pointermove', pointer(14, 32, firstX - 86400));
reconciledInteraction.container.emit('pointerup', pointer(14, 32, firstX - 86400));
const defaultlessEditCommit = reconciledInteraction.events.find((event) => (
  event.type === 'commit' && event.instance.id === 'horizontal'
));
assert.equal(defaultlessEditCommit.instance.params.price, 14);
assert.equal(defaultlessEditCommit.instance.params.color, '#475569');
assert.equal(defaultlessEditCommit.instance.params.lineWidth, 1);
assert(reconciledInteraction.controller.activate('select'));
reconciledInteraction.container.emit('pointerdown', pointer(21, 30, trendMidX));
reconciledInteraction.container.emit('pointerup', pointer(21, 30, trendMidX));
assert(reconciledInteraction.events.some((event) => (
  event.type === 'selection' && event.visualizerId === 'trend'
)));

const sceneBeforeRejectedReconcile = contextOperations(reconciledTrendPrimitive);
const priceBeforeRejectedReconcile = reconciledInteraction.targetSeries.priceLines[0].price;
const invalidPresentation = structuredClone(updatedPresentation);
invalidPresentation.find((item) => item.id === 'horizontal').params.price = 7;
invalidPresentation.find((item) => item.id === 'trend').params.points = [
  { time: '2026-01-01T14:30:00Z', price: 100 },
];
assert.equal(reconciledInteraction.controller.reconcilePresentation({
  visualizers: invalidPresentation,
}), false);
assert.equal(reconciledInteraction.targetSeries.priceLines[0].price, priceBeforeRejectedReconcile);
assert.deepEqual(contextOperations(reconciledTrendPrimitive), sceneBeforeRejectedReconcile);
assert.equal(reconciledInteraction.controller.reconcilePresentation({
  visualizers: updatedPresentation,
  fallback: true,
}), false);

assert.equal(reconciledInteraction.controller.reconcilePresentation({
  visualizers: updatedPresentation.filter((item) => item.id !== 'trend'),
}), true);
assert.equal(reconciledInteraction.targetSeries.primitives.includes(reconciledTrendPrimitive), false);
assert.equal(reconciledInteraction.controller.reconcilePresentation({
  visualizers: updatedPresentation,
}), true);
assert.equal(reconciledInteraction.targetSeries.primitives.length, 1);

assert(reconciledInteraction.controller.activate('brush', { bindings: { target: 'target' } }));
reconciledInteraction.container.emit('pointerdown', pointer(30, 31, firstX));
const cancelledByReconcile = reconciledInteraction.targetSeries.primitives[
  reconciledInteraction.targetSeries.primitives.length - 1
];
assert.equal(reconciledInteraction.controller.reconcilePresentation({
  visualizers: updatedPresentation,
}), true);
assert.equal(reconciledInteraction.targetSeries.primitives.includes(cancelledByReconcile), false);
disposeInteraction(reconciledInteraction);

assert.equal(rawSecretReads, 0);
assert(preventedPointers > 0 && stoppedPointers > 0);
assert.equal(editInteraction.container.listeners.get('lostpointercapture').size, 0);
assert.equal(editInteraction.container.classes.has('chart-interaction-active'), false);
assert.equal(editInteraction.container.capturedPointers.size, 0);

// Fault injection stays product-owned: an invalid scene, an unknown reducer
// effect, and a throwing hit-test each fail only their own layer/session. The
// pure Definition still cannot install executable code.
const injectedAdapters = `
  const passiveTestInteraction = (toolId) => ({
    toolId, label: toolId, consumes: ["chart.pointer"], coordinateResource: "target",
    start() { return null; }, reduce() { return null; }, hitTest() { return null; },
    editStart() { return null; }, editReduce() { return null; },
  });
  installHostRenderer("test.badScene", {
    apiVersion: 1, label: "Bad Scene",
    draw({ host }) {
      host.attachCanvasScene("target", { apiVersion: 1, nodes: [{ kind: "unknown" }] });
    },
  });
  installHostRenderer("test.badEffect", {
    apiVersion: 1, label: "Bad Effect",
    interaction: {
      toolId: "bad-effect", label: "Bad Effect", consumes: ["chart.pointer"], coordinateResource: "target",
      start() {
        const effect = { status: "active", state: {} };
        Object.defineProperty(effect, "unexpected", {
          enumerable: true,
          get() { window.__unknownEffectReads += 1; throw new Error("unknown effect getter read"); },
        });
        return effect;
      },
      reduce() { return null; }, hitTest() { return null; }, editStart() { return null; }, editReduce() { return null; },
    },
    draw() {},
  });
  installHostRenderer("test.badHit", {
    apiVersion: 1, label: "Bad Hit",
    interaction: {
      toolId: "bad-hit", label: "Bad Hit", consumes: ["chart.pointer"], coordinateResource: "target",
      start() { return null; }, reduce() { return null; },
      hitTest() { throw new Error("isolated hit-test failed"); },
      editStart() { return null; }, editReduce() { return null; },
    },
    draw({ host }) {
      host.attachCanvasScene("target", {
        apiVersion: 1,
        nodes: [{
          kind: "path",
          points: [{ x: "2026-01-01T14:30:00Z", y: 1 }, { x: "2026-01-02T14:30:00Z", y: 2 }],
          closed: false,
          stroke: { color: "#000000", width: 1 },
        }],
      });
    },
  });
  installHostRenderer("test.rollbackUpdateA", {
    apiVersion: 1, label: "Rollback Update A",
    interaction: passiveTestInteraction("rollback-update-a"),
    draw({ params, host }) {
      host.attachPriceLine("target", { price: params.price, title: "Rollback Update A" });
    },
  });
  installHostRenderer("test.rollbackUpdateB", {
    apiVersion: 1, label: "Rollback Update B",
    interaction: passiveTestInteraction("rollback-update-b"),
    draw({ params, host }) {
      host.attachPriceLine("target", { price: params.price, title: "Rollback Update B" });
    },
  });
  installHostRenderer("test.stagedCleanupA", {
    apiVersion: 1, label: "Staged Cleanup A",
    interaction: passiveTestInteraction("staged-cleanup-a"),
    draw({ params, host }) {
      host.attachPriceLine("target", { price: params.price, title: "Staged Cleanup A" });
    },
  });
  installHostRenderer("test.stagedMountFailure", {
    apiVersion: 1, label: "Staged Mount Failure",
    interaction: passiveTestInteraction("staged-mount-failure"),
    draw() { throw new Error("forced staged mount failure"); },
  });
`;
const injectionPoint = '  window.TradeChartCore = {';
assert(coreSource.includes(injectionPoint));
const isolationSandbox = {
  console, structuredClone, Intl, Date, setTimeout, clearTimeout,
  __unknownEffectReads: 0,
  LightweightCharts: {
    PriceScaleMode: { Normal: 0, Logarithmic: 1 },
    LineStyle: { Solid: 0, Dotted: 1, Dashed: 2 },
  },
};
isolationSandbox.window = isolationSandbox;
vm.runInNewContext(coreSource.replace(injectionPoint, `${injectedAdapters}\n${injectionPoint}`), isolationSandbox);
const isolationCore = isolationSandbox.TradeChartCore;
const isolationDefinitions = [
  definitions.find((item) => item.id === 'series.line'),
  definitions.find((item) => item.id === 'drawing.horizontalLine'),
  def('test.badScene', { fields: [{ name: 'targetVisualizerId', required: true }], requires: [priceCoordinateTarget()] }),
  def('test.badEffect', {
    fields: [{ name: 'targetVisualizerId', required: true }],
    requires: [priceCoordinateTarget()], interactions: ['chart.pointer'],
  }),
  def('test.badHit', {
    fields: [{ name: 'targetVisualizerId', required: true }],
    requires: [priceCoordinateTarget()], interactions: ['chart.pointer'],
  }),
  ...[
    'test.rollbackUpdateA', 'test.rollbackUpdateB',
    'test.stagedCleanupA', 'test.stagedMountFailure',
  ].map((id) => def(id, {
    fields: [
      { name: 'targetVisualizerId', required: true },
      { name: 'price', required: true },
    ],
    requires: [priceCoordinateTarget()], interactions: ['chart.pointer'],
  })),
];
isolationCore.setVisualizerDefinitions(isolationDefinitions);
const isolationChart = makeChart();
const isolationContainer = new Container();
isolationSandbox.LightweightCharts.createChart = () => isolationChart;
const isolatedCreatedChart = isolationCore.createFinancialChart(isolationContainer, { timeZone: 'UTC', showTime: true });
const isolationPane = { visualizers: [
  { id: 'target', callback: 'series.line', params: {
    dataKey: 'a', timeKey: 'ta', timeDomainId: 'market', priceScaleId: 'right', color: '#2563eb', lineWidth: 2,
  } },
  { id: 'horizontal', callback: 'drawing.horizontalLine', params: {
    targetVisualizerId: 'target', price: 5, color: '#475569', lineWidth: 1,
  } },
  { id: 'bad-scene', callback: 'test.badScene', params: { targetVisualizerId: 'target' } },
  { id: 'bad-hit', callback: 'test.badHit', params: { targetVisualizerId: 'target' } },
] };
const isolatedInteraction = isolationCore.drawFinancialPane(
  isolationSandbox.LightweightCharts, isolatedCreatedChart, result, isolationPane,
);
assert(isolatedInteraction.diagnostics.some((item) => (
  item.visualizerId === 'bad-scene' && item.code === 'invalid-canvas-scene'
)));
assert.equal(isolationChart.records[0].data.length, 2);
const isolatedEvents = [];
isolatedInteraction.interactionController.subscribe((event) => isolatedEvents.push(plain(event)));
assert(isolatedInteraction.interactionController.activate('select'));
isolationContainer.emit('pointerdown', pointer(5, 20, firstX - 86400));
assert(isolatedEvents.some((event) => event.type === 'diagnostic' && event.message === 'isolated hit-test failed'));
assert(isolatedEvents.some((event) => event.type === 'selection' && event.visualizerId === 'horizontal'));
isolatedInteraction.interactionController.cancel();
isolatedEvents.length = 0;
assert(isolatedInteraction.interactionController.activate('bad-effect', { bindings: { target: 'target' } }));
isolationContainer.emit('pointerdown', pointer(14, 21, firstX));
assert.equal(isolationSandbox.__unknownEffectReads, 0);
assert(isolatedEvents.some((event) => event.type === 'diagnostic' && /unknown effect shape/.test(event.message)));
isolatedEvents.length = 0;
assert(isolatedInteraction.interactionController.activate('horizontal-line', { bindings: { target: 'target' } }));
isolationContainer.emit('pointerdown', pointer(15, 22, firstX));
isolationContainer.emit('pointerup', pointer(15, 22, firstX));
assert(isolatedEvents.some((event) => event.type === 'commit' && event.instance.callback === 'drawing.horizontalLine'));
isolatedInteraction.interactionController.dispose();

// If a later incremental update fails and an earlier update cannot roll back,
// reconciliation reports false and permanently locks this controller instead
// of claiming that the old scene was restored.
const rollbackUpdateChart = makeChart();
const rollbackUpdateContainer = new Container();
isolationSandbox.LightweightCharts.createChart = () => rollbackUpdateChart;
const rollbackUpdateCreatedChart = isolationCore.createFinancialChart(
  rollbackUpdateContainer, { timeZone: 'UTC', showTime: true },
);
const rollbackUpdatePane = { visualizers: [
  isolationPane.visualizers[0],
  { id: 'rollback-a', callback: 'test.rollbackUpdateA', params: {
    targetVisualizerId: 'target', price: 1,
  } },
  { id: 'rollback-b', callback: 'test.rollbackUpdateB', params: {
    targetVisualizerId: 'target', price: 2,
  } },
] };
const rollbackUpdateDraw = isolationCore.drawFinancialPane(
  isolationSandbox.LightweightCharts, rollbackUpdateCreatedChart, result, rollbackUpdatePane,
);
const rollbackUpdateEvents = [];
rollbackUpdateDraw.interactionController.subscribe((event) => rollbackUpdateEvents.push(plain(event)));
const [rollbackLineA, rollbackLineB] = rollbackUpdateChart.records[0].priceLines;
const applyRollbackLineA = rollbackLineA.applyOptions.bind(rollbackLineA);
let rollbackLineAUpdated = false;
rollbackLineA.applyOptions = (next) => {
  if (next.price === 1 && rollbackLineAUpdated) throw new Error('forced update rollback failure');
  applyRollbackLineA(next);
  if (next.price === 11) rollbackLineAUpdated = true;
};
const applyRollbackLineB = rollbackLineB.applyOptions.bind(rollbackLineB);
rollbackLineB.applyOptions = (next) => {
  if (next.price === 22) throw new Error('forced later update failure');
  applyRollbackLineB(next);
};
const rollbackUpdateTarget = structuredClone(rollbackUpdatePane.visualizers);
rollbackUpdateTarget.find((item) => item.id === 'rollback-a').params.price = 11;
rollbackUpdateTarget.find((item) => item.id === 'rollback-b').params.price = 22;
assert.equal(rollbackUpdateDraw.interactionController.reconcilePresentation({
  visualizers: rollbackUpdateTarget,
}), false);
assert.equal(rollbackLineA.price, 11);
assert(rollbackUpdateEvents.some((event) => (
  event.type === 'diagnostic' && /rollback failure/.test(event.message)
)));
assert.equal(rollbackUpdateDraw.interactionController.activate('select'), false);
rollbackUpdateDraw.interactionController.dispose();

// The same fail-closed rule covers staged additions: if a later mount fails
// and cleanup of an earlier staged primitive also fails, the orphan remains
// observable but the controller cannot accept another gesture.
const stagedRollbackChart = makeChart();
const stagedRollbackContainer = new Container();
isolationSandbox.LightweightCharts.createChart = () => stagedRollbackChart;
const stagedRollbackCreatedChart = isolationCore.createFinancialChart(
  stagedRollbackContainer, { timeZone: 'UTC', showTime: true },
);
const stagedRollbackPane = { visualizers: [isolationPane.visualizers[0]] };
const stagedRollbackDraw = isolationCore.drawFinancialPane(
  isolationSandbox.LightweightCharts, stagedRollbackCreatedChart, result, stagedRollbackPane,
);
const stagedRollbackEvents = [];
stagedRollbackDraw.interactionController.subscribe((event) => stagedRollbackEvents.push(plain(event)));
const stagedTargetSeries = stagedRollbackChart.records[0];
const removeStagedPriceLine = stagedTargetSeries.removePriceLine.bind(stagedTargetSeries);
stagedTargetSeries.removePriceLine = (line) => {
  if (line.title === 'Staged Cleanup A') throw new Error('forced staged cleanup failure');
  removeStagedPriceLine(line);
};
const stagedRollbackTarget = [
  ...stagedRollbackPane.visualizers,
  { id: 'staged-a', callback: 'test.stagedCleanupA', params: {
    targetVisualizerId: 'target', price: 31,
  } },
  { id: 'staged-b', callback: 'test.stagedMountFailure', params: {
    targetVisualizerId: 'target', price: 32,
  } },
];
assert.equal(stagedRollbackDraw.interactionController.reconcilePresentation({
  visualizers: stagedRollbackTarget,
}), false);
assert(stagedTargetSeries.priceLines.some((line) => line.title === 'Staged Cleanup A'));
assert(stagedRollbackEvents.some((event) => (
  event.type === 'diagnostic' && /cleanup failure/.test(event.message)
)));
assert.equal(stagedRollbackDraw.interactionController.activate('select'), false);
stagedRollbackDraw.interactionController.dispose();

assert.deepEqual(Object.getOwnPropertyDescriptors(Object.prototype), hostObjectPrototypeSnapshot);
assert.deepEqual(Object.getOwnPropertyDescriptors(sandboxObjectPrototype), sandboxObjectPrototypeSnapshot);
console.log('chart sparse-point smoke passed; least-privilege runtime verified');
