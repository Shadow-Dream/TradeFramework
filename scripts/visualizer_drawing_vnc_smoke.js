const assert = require('node:assert/strict');
const { execFileSync } = require('node:child_process');
const fs = require('node:fs');
const http = require('node:http');
const os = require('node:os');
const path = require('node:path');

const puppeteer = require('/tmp/trade-browser/node_modules/puppeteer-core');

const WEB_ROOT = path.resolve(__dirname, '..', 'web');
const SCREENSHOT = process.env.TRADE_VNC_SCREENSHOT || '/tmp/trade-visualizer-drawing-vnc.png';
const PREVIEW_SCREENSHOT = process.env.TRADE_VNC_PREVIEW_SCREENSHOT
  || '/tmp/trade-visualizer-rectangle-preview-vnc.png';
const BACKTEST_ID = 'bt_01M0Z2D7FZACAARVSZPZH34P63';
const DATASET_ID = 'ds_01M0Z2D7FZACAARVSZPZH34P64';
const PANE_ID = 'pane_01M0Z2D7FZACAARVSZPZH34P65';
const TARGET_ID = 'ohlc.candles.m9v0z7q2';
const CONTENT_DIGEST = '9f50db1885723a82d3337232079d9053be73bb44987f62d9569c8a64c2d94ce4';
const OPAQUE_VALUES = [BACKTEST_ID, DATASET_ID, PANE_ID, TARGET_ID, CONTENT_DIGEST];

function definitions() {
  const source = [
    'import json',
    'from builtin_implementations.visualizer_contracts import visualizer_definitions',
    'print(json.dumps(visualizer_definitions()))',
  ].join('\n');
  return JSON.parse(execFileSync('python3', ['-c', source], {
    cwd: path.resolve(__dirname, '..'),
    encoding: 'utf8',
  }));
}

function dataKey(schema, name) {
  return {
    label: name,
    schema,
    required: true,
    source: { path: `cycles.data.${name}` },
    encoding: { value: `data.${name}` },
  };
}

const dataKeys = {
  candle: dataKey({
    type: 'object',
    properties: {
      eventTime: { type: 'string' },
      open: { type: 'number' },
      high: { type: 'number' },
      low: { type: 'number' },
      close: { type: 'number' },
      complete: { type: 'boolean' },
    },
    required: ['eventTime', 'open', 'high', 'low', 'close'],
    additionalProperties: false,
  }, 'candle'),
};
const start = Date.parse('2026-08-24T13:30:00Z');
const cycles = Array.from({ length: 48 }, (_, index) => {
  const eventTime = new Date(start + index * 30 * 60 * 1000).toISOString();
  const middle = 100 + Math.sin(index / 4) * 8 + index / 5;
  const open = middle - Math.sin(index / 2) * 0.8;
  const close = middle + Math.cos(index / 3) * 0.8;
  return {
    decisionTime: eventTime,
    data: {
      candle: {
        eventTime,
        open,
        high: Math.max(open, close) + 0.9,
        low: Math.min(open, close) - 0.9,
        close,
        complete: true,
      },
    },
  };
});
let visualization = {
  schemaVersion: 3,
  datasetId: DATASET_ID,
  timeZone: 'UTC',
  temporaryModules: [],
  panes: [{
    id: PANE_ID,
    title: 'VNC Drawing Acceptance',
    role: 'primary',
    view: { start: null, end: null, logScale: false, controlsCollapsed: true },
    temporaryModules: [],
    visualizers: [{
      id: TARGET_ID,
      callback: 'ohlc.candles',
      params: {
        dataKey: 'candle',
        timeDomainId: 'market-time',
        priceScaleId: 'right',
        upColor: '#089981',
        downColor: '#f23645',
      },
    }],
  }],
};
let visualizationRevision = 0;

const visualizerDefinitions = definitions();
const savedSpecs = [];
const resultRequests = [];

function json(response, status, value) {
  const body = JSON.stringify(value);
  response.writeHead(status, {
    'Content-Type': 'application/json; charset=utf-8',
    'Content-Length': Buffer.byteLength(body),
    'Cache-Control': 'no-store',
  });
  response.end(body);
}

function readJson(request) {
  return new Promise((resolve, reject) => {
    const chunks = [];
    request.on('data', (chunk) => chunks.push(chunk));
    request.on('end', () => {
      try { resolve(JSON.parse(Buffer.concat(chunks).toString('utf8') || '{}')); }
      catch (error) { reject(error); }
    });
    request.on('error', reject);
  });
}

function contentType(file) {
  if (file.endsWith('.html')) return 'text/html; charset=utf-8';
  if (file.endsWith('.js')) return 'text/javascript; charset=utf-8';
  if (file.endsWith('.css')) return 'text/css; charset=utf-8';
  return 'application/octet-stream';
}

function serveStatic(requestPath, response) {
  const relative = requestPath === '/' ? 'chart.html' : requestPath.replace(/^\/+/, '');
  const file = path.resolve(WEB_ROOT, relative);
  if (file !== WEB_ROOT && !file.startsWith(`${WEB_ROOT}${path.sep}`)) {
    response.writeHead(403).end();
    return;
  }
  if (!fs.existsSync(file) || !fs.statSync(file).isFile()) {
    response.writeHead(404).end();
    return;
  }
  response.writeHead(200, {
    'Content-Type': contentType(file),
    'Cache-Control': 'no-store',
  });
  fs.createReadStream(file).pipe(response);
}

async function handler(request, response) {
  const url = new URL(request.url, 'http://127.0.0.1');
  if (url.pathname === '/favicon.ico') {
    response.writeHead(204).end();
    return;
  }
  if (url.pathname === '/auth/session') {
    json(response, 200, {
      user: { userId: 'vnc' },
      csrfToken: 'vnc-csrf',
      expiresAt: Math.floor(Date.now() / 1000) + 3600,
    });
    return;
  }
  if (url.pathname === '/api/modules') {
    json(response, 200, { modules: {} });
    return;
  }
  if (url.pathname === '/api/visualizers') {
    json(response, 200, { visualizers: visualizerDefinitions });
    return;
  }
  if (url.pathname === '/api/visualizations' && request.method === 'GET') {
    json(response, 200, {
      currentVisualizationId: `${BACKTEST_ID}-current`,
      visualizations: visualizationRevision === 0 ? [] : [{
        visualizationId: `${BACKTEST_ID}-current`,
        backtestId: BACKTEST_ID,
        contentDigest: CONTENT_DIGEST,
        name: 'current',
        createdAt: '2026-08-26T00:00:00Z',
        revision: visualizationRevision,
        spec: visualization,
      }],
    });
    return;
  }
  if (url.pathname === `/api/backtests/${BACKTEST_ID}/view`) {
    json(response, 200, {
      backtestId: BACKTEST_ID,
      datasetId: DATASET_ID,
      contentDigest: CONTENT_DIGEST,
      dataKeys,
      visualization,
    });
    return;
  }
  if (url.pathname === `/api/backtests/${BACKTEST_ID}/result` && request.method === 'POST') {
    const payload = await readJson(request);
    resultRequests.push(payload);
    json(response, 200, { accepted: true, result: { dataKeys, cycles } });
    return;
  }
  if (url.pathname === '/api/visualizations' && request.method === 'POST') {
    const payload = await readJson(request);
    assert.equal(payload.expectedRevision, visualizationRevision);
    visualizationRevision += 1;
    visualization = structuredClone(payload.spec);
    savedSpecs.push(structuredClone(payload.spec));
    json(response, 200, {
      accepted: true,
      visualization: {
        visualizationId: `${BACKTEST_ID}-current`,
        backtestId: BACKTEST_ID,
        contentDigest: CONTENT_DIGEST,
        name: 'current',
        createdAt: '2026-08-26T00:00:00Z',
        revision: visualizationRevision,
        spec: visualization,
      },
    });
    return;
  }
  serveStatic(url.pathname, response);
}

function listen(server) {
  return new Promise((resolve, reject) => {
    server.once('error', reject);
    server.listen(0, '127.0.0.1', () => resolve(server.address().port));
  });
}

function waitFor(check, label, timeoutMs = 10000) {
  const deadline = Date.now() + timeoutMs;
  return new Promise((resolve, reject) => {
    const poll = () => {
      let value;
      try { value = check(); } catch (error) { reject(error); return; }
      if (value) { resolve(value); return; }
      if (Date.now() >= deadline) { reject(new Error(`Timed out waiting for ${label}`)); return; }
      setTimeout(poll, 50);
    };
    poll();
  });
}

function drawingInstances() {
  return visualization.panes[0].visualizers.filter((item) => item.callback.startsWith('drawing.'));
}

async function chartBox(page) {
  await page.$eval('.tv-chart', (element) => element.scrollIntoView({ block: 'center' }));
  await new Promise((resolve) => setTimeout(resolve, 80));
  const element = await page.$('.tv-chart');
  const box = await element.boundingBox();
  assert(box && box.width > 600 && box.height > 300, 'The real chart canvas has invalid dimensions');
  return box;
}

async function activateTool(page, toolId, label) {
  await page.waitForSelector(`[data-drawing-tool="${toolId}"]`);
  await page.click(`[data-drawing-tool="${toolId}"]`);
  await page.waitForSelector('[data-drawing-binding="target"]');
  const values = await page.$$eval('[data-drawing-binding="target"] option', (options) => (
    options.map((option) => option.value)
  ));
  assert(values.includes(TARGET_ID), `${label} did not expose the explicit target binding`);
  const targetOptions = await page.$$eval('[data-drawing-binding="target"] option', (options) => (
    options.map((option) => ({ value: option.value, text: option.textContent.trim() }))
  ));
  assert.equal(targetOptions.find((option) => option.value === TARGET_ID)?.text, 'Candles',
    `${label} exposed the target machine ID instead of its semantic label`);
  await page.select('[data-drawing-binding="target"]', TARGET_ID);
  await page.waitForFunction((expected) => (
    document.querySelector('[data-drawing-status]')?.textContent === `${expected} active.`
  ), {}, label);
}

async function main() {
  assert(process.env.DISPLAY, 'DISPLAY is required; run this smoke in the VNC desktop.');
  const server = http.createServer((request, response) => {
    handler(request, response).catch((error) => json(response, 500, { error: error.message }));
  });
  const port = await listen(server);
  const userDataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'trade-vnc-drawing-'));
  let browser;
  try {
    browser = await puppeteer.launch({
      headless: false,
      executablePath: '/usr/bin/google-chrome',
      userDataDir,
      args: ['--no-sandbox', '--disable-gpu', '--no-proxy-server', '--window-size=1600,1000'],
      defaultViewport: { width: 1600, height: 1000 },
    });
    const page = await browser.newPage();
    await page.setViewport({ width: 1600, height: 1000, deviceScaleFactor: 1 });
    const pageErrors = [];
    const consoleErrors = [];
    const failedResponses = [];
    page.on('pageerror', (error) => pageErrors.push(error.message));
    page.on('console', (message) => {
      if (message.type() === 'error') consoleErrors.push(message.text());
    });
    page.on('response', (response) => {
      if (response.status() >= 500) failedResponses.push(`${response.status()} ${response.url()}`);
    });

    await page.goto(`http://127.0.0.1:${port}/chart.html?backtestId=${BACKTEST_ID}&paneId=${PANE_ID}`, {
      waitUntil: 'networkidle0',
      timeout: 30000,
    });
    await page.waitForSelector('.tv-chart canvas', { timeout: 30000 });
    await new Promise((resolve) => setTimeout(resolve, 250));
    const initialSurface = await page.evaluate(() => ({
      tools: [...document.querySelectorAll('[data-drawing-tool]')].map((button) => button.textContent.trim()),
      diagnostics: [...document.querySelectorAll('.chart-load-error')].map((item) => item.textContent.trim()),
      status: document.getElementById('chartStatus')?.textContent || '',
      pageText: document.getElementById('singleChartArea')?.textContent || '',
    }));
    assert.deepEqual(initialSurface.tools,
      ['Select', 'Horizontal Line', 'Trend Line', 'Rectangle', 'Brush', 'Text'],
      `Unexpected drawing toolbar: ${JSON.stringify(initialSurface)}`);
    assert.deepEqual(initialSurface.diagnostics, [],
      `Initial chart diagnostics: ${JSON.stringify(initialSurface)}`);
    const initialResultRequestCount = resultRequests.length;
    assert.equal(initialResultRequestCount, 1, 'Only the explicit Candles projection may load Result data');
    assert.deepEqual(resultRequests[0].paths,
      ['cycles.data.candle'],
      'Candles did not request exactly its one declared timed-OHLC DataKey');

    await activateTool(page, 'rectangle', 'Rectangle');
    let box = await chartBox(page);
    const rectangleStart = { x: box.x + box.width * 0.28, y: box.y + box.height * 0.30 };
    const rectangleEnd = { x: box.x + box.width * 0.62, y: box.y + box.height * 0.62 };
    await page.mouse.click(rectangleStart.x, rectangleStart.y);
    await page.mouse.move(rectangleEnd.x, rectangleEnd.y, { steps: 8 });
    await page.screenshot({ path: PREVIEW_SCREENSHOT, fullPage: true });
    await page.mouse.click(rectangleEnd.x, rectangleEnd.y);
    await waitFor(() => drawingInstances().some((item) => item.callback === 'drawing.rectangle'), 'Rectangle commit');
    await page.waitForSelector('.tv-chart canvas');

    await activateTool(page, 'brush', 'Brush');
    box = await chartBox(page);
    const brushPoints = [
      [0.24, 0.72], [0.34, 0.57], [0.47, 0.70],
      [0.38, 0.43], [0.58, 0.50], [0.70, 0.32],
    ].map(([x, y]) => ({ x: box.x + box.width * x, y: box.y + box.height * y }));
    await page.mouse.move(brushPoints[0].x, brushPoints[0].y);
    await page.mouse.down();
    for (const point of brushPoints.slice(1)) {
      await page.mouse.move(point.x, point.y, { steps: 4 });
    }
    await page.mouse.up();
    await waitFor(() => drawingInstances().some((item) => item.callback === 'drawing.brush'), 'Brush commit');
    await page.waitForSelector('.tv-chart canvas');

    await activateTool(page, 'text', 'Text');
    box = await chartBox(page);
    await page.mouse.click(box.x + box.width * 0.48, box.y + box.height * 0.24);
    await page.waitForSelector('.chart-interaction-text-input');
    await page.keyboard.type('VNC Text');
    await page.keyboard.press('Enter');
    await waitFor(() => drawingInstances().some((item) => (
      item.callback === 'drawing.text' && item.params.text === 'VNC Text'
    )), 'Text commit');
    await page.waitForFunction(() => !document.querySelector('.chart-interaction-text-input'));
    await page.waitForSelector('.tv-chart canvas');

    const rectangleBeforeDrag = structuredClone(
      drawingInstances().find((item) => item.callback === 'drawing.rectangle'),
    );
    await page.click('[data-drawing-tool="select"]');
    await page.waitForFunction(() => (
      document.querySelector('[data-drawing-status]')?.textContent === 'Select active.'
    ));
    box = await chartBox(page);
    const cornerBefore = { x: box.x + box.width * 0.286, y: box.y + box.height * 0.30 };
    const cornerAfter = { x: box.x + box.width * 0.22, y: box.y + box.height * 0.24 };
    await page.mouse.move(cornerBefore.x, cornerBefore.y);
    await page.mouse.down();
    await new Promise((resolve) => setTimeout(resolve, 100));
    const dragSelection = await page.evaluate(() => ({
      status: document.querySelector('[data-drawing-status]')?.textContent || '',
      deleteDisabled: document.querySelector('[data-drawing-delete]')?.disabled ?? true,
    }));
    assert.equal(dragSelection.deleteDisabled, false,
      `Rectangle was not selected at its visible corner: ${JSON.stringify({ dragSelection, box, cornerBefore })}`);
    await page.mouse.move(cornerAfter.x, cornerAfter.y, { steps: 8 });
    await page.mouse.up();
    await waitFor(() => {
      const current = drawingInstances().find((item) => item.callback === 'drawing.rectangle');
      return current && JSON.stringify(current.params.corners) !== JSON.stringify(rectangleBeforeDrag.params.corners);
    }, 'Rectangle drag commit');
    await page.waitForSelector('.tv-chart canvas');

    await new Promise((resolve) => setTimeout(resolve, 500));
    const finalDrawings = drawingInstances();
    assert.equal(finalDrawings.length, 3, 'Exactly three drawing instances must persist');
    assert(finalDrawings.every((item) => item.params.targetVisualizerId === TARGET_ID),
      'Every drawing must preserve its explicit target binding');
    const rectangle = finalDrawings.find((item) => item.callback === 'drawing.rectangle');
    const brush = finalDrawings.find((item) => item.callback === 'drawing.brush');
    const text = finalDrawings.find((item) => item.callback === 'drawing.text');
    assert.equal(rectangle.params.corners.length, 2, 'Rectangle did not persist two corners');
    assert.equal(rectangle.id, rectangleBeforeDrag.id, 'Rectangle drag replaced the Visualizer identity');
    assert.notDeepEqual(rectangle.params.corners, rectangleBeforeDrag.params.corners,
      'Rectangle drag did not persist its edited corner');
    assert(brush.params.points.length >= brushPoints.length, 'Brush did not persist the pointer path');
    const brushTimes = brush.params.points.map((point) => Date.parse(point.time));
    assert(brushTimes.some((value, index) => index > 0 && value < brushTimes[index - 1]),
      'Brush path order was sorted instead of preserving backwards mouse movement');
    assert.equal(text.params.text, 'VNC Text');
    assert.equal(resultRequests.length, initialResultRequestCount,
      'Drawing-only Visualizers unexpectedly requested Result data');

    const browserAudit = await page.evaluate(() => ({
      tools: [...document.querySelectorAll('[data-drawing-tool]')].map((button) => button.textContent.trim()),
      canvases: document.querySelectorAll('.tv-chart canvas').length,
      diagnostics: [...document.querySelectorAll('.chart-load-error')].map((item) => item.textContent.trim()),
      textEditorPresent: !!document.querySelector('.chart-interaction-text-input'),
      activeGesture: document.querySelector('.tv-chart')?.classList.contains('chart-interaction-active'),
      chartSize: (() => {
        const bounds = document.querySelector('.tv-chart')?.getBoundingClientRect();
        return bounds ? { width: bounds.width, height: bounds.height } : null;
      })(),
      identitySurfaces: {
        documentTitle: document.title,
        bodyText: document.body.innerText,
        titles: [...document.querySelectorAll('[title]')]
          .map((element) => element.getAttribute('title') || ''),
        ariaLabels: [...document.querySelectorAll('[aria-label]')]
          .map((element) => element.getAttribute('aria-label') || ''),
        optionTexts: [...document.querySelectorAll('option')]
          .map((option) => option.textContent || ''),
      },
    }));
    assert.deepEqual(browserAudit.tools, ['Select', 'Horizontal Line', 'Trend Line', 'Rectangle', 'Brush', 'Text']);
    assert(browserAudit.canvases > 0, 'Lightweight Charts did not render canvas layers');
    assert.deepEqual(browserAudit.diagnostics, []);
    assert.equal(browserAudit.textEditorPresent, false);
    assert.equal(browserAudit.activeGesture, false);
    assert(browserAudit.chartSize.width > 600 && browserAudit.chartSize.height > 300);
    const visibleIdentityText = [
      browserAudit.identitySurfaces.documentTitle,
      browserAudit.identitySurfaces.bodyText,
      ...browserAudit.identitySurfaces.titles,
      ...browserAudit.identitySurfaces.ariaLabels,
      ...browserAudit.identitySurfaces.optionTexts,
    ].join('\n');
    const runtimeOpaqueValues = [
      ...OPAQUE_VALUES,
      ...visualization.panes.flatMap((pane) => [
        pane.id,
        ...(pane.temporaryModules || []).map((item) => item.instanceId),
        ...(pane.visualizers || []).map((item) => item.id),
      ]),
    ].filter(Boolean);
    for (const value of runtimeOpaqueValues) {
      assert.equal(visibleIdentityText.includes(value), false,
        `A user-visible identity surface leaked opaque value ${value}`);
    }
    assert.equal(/\b[a-f0-9]{64}\b/i.test(visibleIdentityText), false,
      'A user-visible identity surface leaked a content digest');
    assert(browserAudit.identitySurfaces.optionTexts.includes('Candles'),
      'The target selector did not retain a semantic Candles label');
    assert.deepEqual(pageErrors, []);
    assert.deepEqual(consoleErrors, []);
    assert.deepEqual(failedResponses, []);

    await page.screenshot({ path: SCREENSHOT, fullPage: true });
    console.log(JSON.stringify({
      display: process.env.DISPLAY,
      screenshot: SCREENSHOT,
      previewScreenshot: PREVIEW_SCREENSHOT,
      resultRequestCount: resultRequests.length,
      savedSpecCount: savedSpecs.length,
      drawings: finalDrawings.map((item) => ({
        id: item.id,
        callback: item.callback,
        targetVisualizerId: item.params.targetVisualizerId,
        pointCount: item.params.points?.length || item.params.corners?.length || 1,
      })),
      browserAudit,
      pageErrors,
      consoleErrors,
      failedResponses,
    }, null, 2));
  } finally {
    await browser?.close().catch(() => {});
    await new Promise((resolve) => server.close(resolve));
  }
}

main().catch((error) => {
  console.error(error.stack || error.message);
  process.exitCode = 1;
});
