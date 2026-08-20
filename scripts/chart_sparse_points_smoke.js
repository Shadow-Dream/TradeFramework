#!/usr/bin/env node

const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const window = {};
vm.runInNewContext(fs.readFileSync('web/chart_core.js', 'utf8'), {
  window,
  console,
  Date,
  Intl,
  Map,
  Number,
  Object,
  String,
  structuredClone,
});

const core = window.TradeChartCore;
core.setVisualizerDefinitions([{
  id: 'ohlc.candles',
  label: 'Candles',
  inputPorts: {
    dataKey: {
      schema: {
        type: 'object',
        properties: {
          open: { type: 'number' }, high: { type: 'number' },
          low: { type: 'number' }, close: { type: 'number' },
        },
        required: ['open', 'high', 'low', 'close'],
      },
    },
  },
  params: [{ name: 'dataKey', label: 'Data', type: 'dataKey' }],
}]);
const sparseResult = {
  dataKeys: {
    'market.day': {
      label: 'market.day',
      schema: {
        type: 'object',
        properties: {
          open: { type: 'number' }, high: { type: 'number' },
          low: { type: 'number' }, close: { type: 'number' },
          complete: { type: 'boolean' },
        },
        required: ['open', 'high', 'low', 'close', 'complete'],
      },
      source: { path: 'cycles' },
      encoding: { time: 'decisionTime', value: 'data.market.day' },
    },
  },
};
const candleCatalog = core.visualizerCatalog(sparseResult, { dataKeys: {} })
  .find((item) => item.id === 'ohlc.candles');
assert(candleCatalog.optionMap.dataKey.some((item) => item.value === 'market.day'));

const rows = [
  { date: '2026-01-01T17:00:00Z', value: 10 },
  { date: '2026-01-02T17:00:00Z', value: null },
  { date: '2026-01-03T17:00:00Z', value: Number.NaN },
  { date: '2026-01-04T17:00:00Z', value: 14 },
  { date: 'not-a-clock', value: 15 },
  { date: '2026-01-06T17:00:00Z', value: Number.POSITIVE_INFINITY },
];
const line = core.sparseLinePoints(rows);
assert.deepEqual(JSON.parse(JSON.stringify(line)), [
  { time: 1767286800, value: 10 },
  { time: 1767546000, value: 14 },
]);

const openOnly = core.sparseLinePoints([
  { date: '2026-01-01T14:30:00Z', open: 10, close: null },
  { date: '2026-01-02T14:30:00Z', open: Number.POSITIVE_INFINITY, close: null },
  { date: '2026-01-03T14:30:00Z', open: 12, close: null },
], { value: 'open' });
assert.deepEqual(JSON.parse(JSON.stringify(openOnly)), [
  { time: 1767277800, value: 10 },
  { time: 1767450600, value: 12 },
]);

const candles = core.completeCandlePoints([
  { date: '2026-01-01T17:00:00Z', open: 10, high: null, low: null, close: null, complete: false },
  { date: '2026-01-02T17:00:00Z', open: 10, high: 12, low: 9, close: 11, complete: true },
  { date: '2026-01-03T17:00:00Z', open: 11, high: Number.NaN, low: 10, close: 12, complete: true },
]);
assert.deepEqual(JSON.parse(JSON.stringify(candles)), [
  { time: 1767373200, open: 10, high: 12, low: 9, close: 11 },
]);

console.log('chart sparse-point smoke passed');
