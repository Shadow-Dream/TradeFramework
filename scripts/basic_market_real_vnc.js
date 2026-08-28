#!/usr/bin/env node
"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");

const puppeteer = require("/tmp/trade-browser/node_modules/puppeteer-core");

const ORIGIN = process.env.TRADE_BASIC_REAL_ORIGIN;
const SESSION_COOKIE = process.env.TRADE_BASIC_REAL_SESSION_COOKIE;
const SESSION_TOKEN = process.env.TRADE_BASIC_REAL_SESSION_TOKEN;
const CSRF_COOKIE = process.env.TRADE_BASIC_REAL_CSRF_COOKIE;
const CSRF_TOKEN = process.env.TRADE_BASIC_REAL_CSRF_TOKEN;
const HOME_SCREENSHOT = process.env.TRADE_BASIC_REAL_SCREENSHOT;
const WORKSPACE_SCREENSHOT = process.env.TRADE_BASIC_REAL_RESULT_SCREENSHOT;
const RETURNED_SCREENSHOT = process.env.TRADE_BASIC_REAL_RETURNED_SCREENSHOT;
const MODE = process.env.TRADE_BASIC_REAL_MODE || "full";
const DRAWING_DIAGNOSTIC = process.env.TRADE_BASIC_DRAWING_DIAGNOSTIC === "1";
const DRAWING_PREVIEW_SCREENSHOT = "/tmp/trade-basic-drawing-commit-preview-fixed.png";
const DRAWING_GAP_SCREENSHOT = "/tmp/trade-basic-drawing-commit-gap-fixed.png";
const DRAWING_RESTORED_SCREENSHOT = "/tmp/trade-basic-drawing-commit-restored-fixed.png";
const DRAWING_DELETE_SCREENSHOT = "/tmp/trade-basic-drawing-select-delete-fixed.png";
const GENERIC_RESULT_SCREENSHOT = process.env.TRADE_IDENTITY_RESULT_SCREENSHOT
  || "/tmp/trade-generic-result-identity-vnc.png";
const OVERVIEW_SCREENSHOT = process.env.TRADE_IDENTITY_OVERVIEW_SCREENSHOT
  || "/tmp/trade-overview-identity-vnc.png";
const RESOURCE_SCREENSHOT = process.env.TRADE_IDENTITY_RESOURCE_SCREENSHOT
  || "/tmp/trade-resource-detail-identity-vnc.png";
const PIPELINE_RESOURCE_SCREENSHOT = process.env.TRADE_IDENTITY_PIPELINE_RESOURCE_SCREENSHOT
  || "/tmp/trade-pipeline-detail-identity-vnc.png";
const BACKTEST_RESOURCE_SCREENSHOT = process.env.TRADE_IDENTITY_BACKTEST_RESOURCE_SCREENSHOT
  || "/tmp/trade-backtest-detail-identity-vnc.png";

function required(value, label) {
  assert.equal(typeof value, "string", `${label} is required`);
  assert(value, `${label} is required`);
  return value;
}

function containsField(value, field) {
  if (Array.isArray(value)) return value.some((item) => containsField(item, field));
  if (!value || typeof value !== "object") return false;
  return Object.prototype.hasOwnProperty.call(value, field)
    || Object.values(value).some((item) => containsField(item, field));
}

function collectOpaqueValues(value, key = "", output = new Set()) {
  if (Array.isArray(value)) {
    value.forEach((item) => collectOpaqueValues(item, key, output));
    return output;
  }
  if (!value || typeof value !== "object") {
    if (typeof value === "string"
        && /(?:Ids?|Digest|Hash)$/i.test(key)
        && value.length >= 8) output.add(value);
    return output;
  }
  Object.entries(value).forEach(([childKey, child]) => (
    collectOpaqueValues(child, childKey, output)
  ));
  return output;
}

async function visibleIdentityAudit(page, label, opaqueValues = []) {
  const surfaces = await page.evaluate(() => ({
    documentTitle: document.title,
    bodyText: document.body.innerText,
    titles: [...document.querySelectorAll("[title]")]
      .map((element) => element.getAttribute("title") || ""),
    ariaLabels: [...document.querySelectorAll("[aria-label]")]
      .map((element) => element.getAttribute("aria-label") || ""),
    optionTexts: [...document.querySelectorAll("option")]
      .map((option) => option.textContent || ""),
  }));
  const visible = [
    surfaces.documentTitle,
    surfaces.bodyText,
    ...surfaces.titles,
    ...surfaces.ariaLabels,
    ...surfaces.optionTexts,
  ].join("\n");
  const exactLeaks = [...new Set(opaqueValues)]
    .filter((value) => typeof value === "string" && value && visible.includes(value));
  const patternChecks = [
    ["ULID", /\b[0-9A-HJKMNP-TV-Z]{26}\b/g],
    ["UUID", /\b[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\b/gi],
    ["digest", /\b(?:sha256:)?[0-9a-f]{64}\b/gi],
    ["prefixed identity", /\b(?:bt|job|ds|pipe|pipeline|viz|vis|pane|inst|snapshot)[_-][A-Za-z0-9][A-Za-z0-9._-]{7,}\b/gi],
    ["Visualizer instance suffix", /\b(?:drawing|ohlc|overlay|series)\.[A-Za-z][A-Za-z0-9]*(?:\.[a-z0-9]{6,})+\b/gi],
  ];
  const patternLeaks = patternChecks.flatMap(([kind, pattern]) => (
    [...visible.matchAll(pattern)].map((match) => `${kind}: ${match[0]}`)
  ));
  assert.deepEqual(exactLeaks, [], `${label} exposed canonical identities: ${JSON.stringify(exactLeaks)}`);
  assert.deepEqual(patternLeaks, [], `${label} exposed machine identity patterns: ${JSON.stringify(patternLeaks)}`);
  return {
    label,
    checkedOpaqueValueCount: new Set(opaqueValues).size,
    bodyCharacterCount: surfaces.bodyText.length,
    titleCount: surfaces.titles.length,
    ariaLabelCount: surfaces.ariaLabels.length,
    optionTextCount: surfaces.optionTexts.length,
    exactLeaks,
    patternLeaks,
  };
}

async function selectFirstResource(page, containerId) {
  await page.waitForSelector(`#${containerId} .trade-resource-browser-shell:not([data-loading="true"])`, {
    timeout: 30000,
  });
  await page.waitForFunction((id) => (
    [...document.querySelectorAll(`#${id} .file-item-container`)]
      .some((item) => item.dataset.tradeItemId)
  ), { timeout: 30000 }, containerId);
  await page.evaluate((id) => {
    const item = [...document.querySelectorAll(`#${id} .file-item-container`)]
      .find((candidate) => candidate.dataset.tradeItemId);
    item.click();
  }, containerId);
  await page.waitForFunction((id) => (
    !!document.querySelector(`#${id} .trade-resource-inspector h3`)
  ), { timeout: 30000 }, containerId);
}

async function browserApi(page, method, requestPath, payload = undefined) {
  return page.evaluate(async ({ method: innerMethod, requestPath: innerPath, payload: innerPayload, csrf }) => {
    const response = await fetch(innerPath, {
      method: innerMethod,
      credentials: "same-origin",
      cache: "no-store",
      headers: {
        Accept: "application/json",
        ...(innerPayload === undefined ? {} : {
          "Content-Type": "application/json",
          "X-CSRF-Token": csrf,
        }),
      },
      ...(innerPayload === undefined ? {} : { body: JSON.stringify(innerPayload) }),
    });
    return {
      status: response.status,
      body: await response.json().catch(() => ({})),
    };
  }, { method, requestPath, payload, csrf: CSRF_TOKEN });
}

async function currentVisualization(page, backtestId, visualizationId) {
  const response = await browserApi(
    page,
    "GET",
    `/api/visualizations?backtestId=${encodeURIComponent(backtestId)}`,
  );
  assert.equal(response.status, 200, JSON.stringify(response.body));
  assert.equal(response.body.currentVisualizationId, visualizationId);
  const records = (response.body.visualizations || [])
    .filter((item) => item.visualizationId === visualizationId);
  assert.equal(records.length, 1, `Visualization ${visualizationId} is not unique`);
  return records[0];
}

async function chartBox(page, selector) {
  const box = await page.$eval(selector, (element) => {
    const value = element.getBoundingClientRect();
    return { x: value.x, y: value.y, width: value.width, height: value.height };
  });
  assert(box.width > 500 && box.height > 250, `Invalid chart bounds: ${JSON.stringify(box)}`);
  return box;
}

async function drawTrendLine(page) {
  await page.waitForFunction(() => (
    document.querySelector('[data-drawing-tool="trend-line"]')?.disabled === false
  ), { timeout: 30000 });
  await page.click('[data-drawing-tool="trend-line"]');
  await page.waitForFunction(() => (
    document.querySelector('[data-drawing-tool="trend-line"]')?.classList.contains("active")
  ));
  const box = await chartBox(page, "#bwChart");
  const start = { x: box.x + box.width * 0.30, y: box.y + box.height * 0.68 };
  const end = { x: box.x + box.width * 0.70, y: box.y + box.height * 0.30 };
  await page.mouse.click(start.x, start.y);
  await page.mouse.move(end.x, end.y, { steps: 8 });
  await page.mouse.click(end.x, end.y);
}

async function basicDrawingState(page) {
  return page.evaluate(() => {
    const diagnostic = window.__basicDrawingDiagnostic;
    const canvases = [...document.querySelectorAll("#bwChart canvas")];
    return {
      atMs: diagnostic ? performance.now() - diagnostic.startedAt : null,
      status: document.querySelector("#bwOpenStatus")?.textContent || "",
      canvasCount: canvases.length,
      retainedOriginalCanvases: diagnostic
        ? canvases.filter((canvas) => diagnostic.originalCanvases.includes(canvas)).length
        : null,
      chartChildCount: document.querySelector("#bwChart")?.childElementCount || 0,
      activeGesture: document.querySelector("#bwChart")?.classList.contains("chart-interaction-active") || false,
      activeTool: document.querySelector("[data-drawing-tool].active")?.dataset.drawingTool || "",
      deleteDisabled: document.querySelector("[data-drawing-delete]")?.disabled ?? true,
      activeElement: document.activeElement?.dataset?.drawingTool
        || document.activeElement?.id || document.activeElement?.tagName || "",
      chartTabIndex: document.querySelector("#bwChart")?.getAttribute("tabindex"),
    };
  });
}

async function activateBasicDrawingTool(page, toolId) {
  await page.waitForFunction((id) => (
    document.querySelector(`[data-drawing-tool="${id}"]`)?.disabled === false
  ), { timeout: 30000 }, toolId);
  await page.click(`[data-drawing-tool="${toolId}"]`);
  await page.waitForFunction((id) => (
    document.querySelector(`[data-drawing-tool="${id}"]`)?.classList.contains("active")
  ), {}, toolId);
}

async function waitForDrawingSaved(page) {
  await page.waitForFunction(() => (
    document.querySelector("#bwOpenStatus")?.textContent === "Drawing saved."
  ), { timeout: 30000 });
}

async function installBasicDrawingIdentityProbe(page) {
  await page.evaluateOnNewDocument(() => {
    const probe = {
      chartCreateCount: 0,
      paneDrawCount: 0,
      latestChart: null,
      latestController: null,
      reconcileCalls: [],
      baseline: null,
    };
    Object.defineProperty(window, "__basicDrawingIdentityProbe", {
      configurable: false,
      enumerable: false,
      value: probe,
    });
    Object.defineProperty(window, "TradeChartCore", {
      configurable: true,
      enumerable: true,
      get() { return undefined; },
      set(core) {
        const createFinancialChart = core.createFinancialChart;
        const drawFinancialPane = core.drawFinancialPane;
        core.createFinancialChart = function (...args) {
          const chart = Reflect.apply(createFinancialChart, this, args);
          probe.chartCreateCount += 1;
          probe.latestChart = chart;
          return chart;
        };
        core.drawFinancialPane = function (...args) {
          const context = Reflect.apply(drawFinancialPane, this, args);
          const controller = context.interactionController;
          const instrumentedController = Object.freeze({
            ...controller,
            reconcilePresentation(request) {
              const result = controller.reconcilePresentation(request);
              probe.reconcileCalls.push({
                visualizers: structuredClone(request.visualizers),
                result,
              });
              return result;
            },
          });
          probe.paneDrawCount += 1;
          probe.latestController = instrumentedController;
          return Object.freeze({ ...context, interactionController: instrumentedController });
        };
        Object.defineProperty(window, "TradeChartCore", {
          configurable: true,
          enumerable: true,
          writable: true,
          value: core,
        });
      },
    });
  });
}

async function runLegacyBasicDrawingDiagnostic(page, apiRequests, backtestId, visualizationId) {
  await page.evaluate(() => {
    const diagnostic = {
      startedAt: performance.now(),
      originalCanvases: [...document.querySelectorAll("#bwChart canvas")],
      mutations: [],
      observer: null,
    };
    const snapshot = (reason) => diagnostic.mutations.push({
      reason,
      atMs: performance.now() - diagnostic.startedAt,
      status: document.querySelector("#bwOpenStatus")?.textContent || "",
      canvasCount: document.querySelectorAll("#bwChart canvas").length,
      activeGesture: document.querySelector("#bwChart")?.classList.contains("chart-interaction-active") || false,
      deleteDisabled: document.querySelector("[data-drawing-delete]")?.disabled ?? true,
    });
    diagnostic.observer = new MutationObserver(() => snapshot("mutation"));
    diagnostic.observer.observe(document.querySelector(".bw-workspace-page"), {
      attributes: true,
      childList: true,
      subtree: true,
      characterData: true,
    });
    window.__basicDrawingDiagnostic = diagnostic;
    snapshot("started");
  });

  const visualizationNetwork = [];
  const epoch = Date.now();
  const onResponse = (response) => {
    const url = new URL(response.url());
    if (url.origin === ORIGIN && url.pathname === "/api/visualizations") {
      visualizationNetwork.push({
        type: "response", method: response.request().method(), status: response.status(),
        atMs: Date.now() - epoch,
      });
    }
  };
  page.on("response", onResponse);
  let delayGate = null;
  const armPostDelay = (label, delayMs = 900) => {
    let resolveHit;
    const hit = new Promise((resolve) => { resolveHit = resolve; });
    delayGate = { label, delayMs, resolveHit };
    return hit;
  };
  const onInterceptedRequest = (request) => {
    const url = new URL(request.url());
    if (url.origin === ORIGIN && url.pathname === "/api/visualizations") {
      visualizationNetwork.push({
        type: "request", method: request.method(), atMs: Date.now() - epoch,
        delayedAs: delayGate && request.method() === "POST" ? delayGate.label : "",
      });
    }
    if (delayGate && request.method() === "POST"
        && url.origin === ORIGIN && url.pathname === "/api/visualizations") {
      const gate = delayGate;
      delayGate = null;
      gate.resolveHit({ atMs: Date.now() - epoch, label: gate.label, delayMs: gate.delayMs });
      setTimeout(() => request.continue(), gate.delayMs);
      return;
    }
    request.continue();
  };
  await page.setRequestInterception(true);
  page.on("request", onInterceptedRequest);

  const postCount = () => apiRequests.filter((request) => (
    request.phase === "workspace" && request.method === "POST"
    && request.path === "/api/visualizations"
  )).length;
  const chart = await chartBox(page, "#bwChart");
  const gestures = [];
  await activateBasicDrawingTool(page, "trend-line");
  const lineStart = { x: chart.x + chart.width * 0.28, y: chart.y + chart.height * 0.70 };
  const lineEnd = { x: chart.x + chart.width * 0.70, y: chart.y + chart.height * 0.30 };
  const trendDragRevisionBefore = (await currentVisualization(page, backtestId, visualizationId)).revision;
  const trendDragPostsBefore = postCount();
  await page.mouse.move(lineStart.x, lineStart.y);
  await page.mouse.down();
  await page.mouse.move(lineEnd.x, lineEnd.y, { steps: 10 });
  const beforeCommit = await basicDrawingState(page);
  await page.screenshot({ path: DRAWING_PREVIEW_SCREENSHOT, fullPage: true });
  const commitDelayHit = armPostDelay("trend-drag-commit");
  const commitStartedAt = Date.now();
  await page.mouse.up();
  const commitPost = await commitDelayHit;
  await page.waitForFunction(() => (
    document.querySelector("#bwOpenStatus")?.textContent.includes("Saving drawing")
  ));
  const commitGap = await basicDrawingState(page);
  await page.screenshot({ path: DRAWING_GAP_SCREENSHOT, fullPage: true });
  await waitForDrawingSaved(page);
  const commitElapsedMs = Date.now() - commitStartedAt;
  const afterCommit = await basicDrawingState(page);
  await page.screenshot({ path: DRAWING_RESTORED_SCREENSHOT, fullPage: true });
  const committedRecord = await currentVisualization(page, backtestId, visualizationId);
  assert.equal(committedRecord.revision, 2);
  const committedLine = committedRecord.spec.panes[0].visualizers.find((item) => item.callback === "drawing.trendLine");
  assert(committedLine, "Trend Line did not persist after the delayed canonical save");
  assert.equal(commitGap.canvasCount, beforeCommit.canvasCount);
  assert.equal(commitGap.retainedOriginalCanvases, beforeCommit.canvasCount);
  assert.equal(afterCommit.canvasCount, beforeCommit.canvasCount);
  assert.equal(afterCommit.retainedOriginalCanvases, 0, "Saved drawing did not rebuild the chart canvas set");
  assert.equal(postCount(), trendDragPostsBefore + 1, "Trend drag did not create exactly one save");
  gestures.push({
    toolId: "trend-line", mode: "down-drag-up", committed: true,
    revisionBefore: trendDragRevisionBefore, revisionAfter: committedRecord.revision,
    postsBefore: trendDragPostsBefore, postsAfter: postCount(),
  });

  await activateBasicDrawingTool(page, "select");
  const selectBeforeRecord = await currentVisualization(page, backtestId, visualizationId);
  const selectBeforeLine = selectBeforeRecord.spec.panes[0].visualizers.find((item) => item.id === committedLine.id);
  const midpoint = { x: (lineStart.x + lineEnd.x) / 2, y: (lineStart.y + lineEnd.y) / 2 };
  const selectPostsBefore = postCount();
  await page.mouse.click(midpoint.x, midpoint.y);
  await page.waitForFunction(() => document.querySelector("[data-drawing-delete]")?.disabled === false);
  const selected = await basicDrawingState(page);
  await new Promise((resolve) => setTimeout(resolve, 120));
  const afterPureSelectRecord = await currentVisualization(page, backtestId, visualizationId);
  const afterPureSelectLine = afterPureSelectRecord.spec.panes[0].visualizers.find((item) => item.id === committedLine.id);
  assert.equal(postCount(), selectPostsBefore, "Pure Select click created a Visualization save");
  assert.equal(afterPureSelectRecord.revision, selectBeforeRecord.revision, "Pure Select click changed the revision");
  assert.deepEqual(afterPureSelectLine.params.points, selectBeforeLine.params.points,
    "Pure Select click moved a Trend Line endpoint");
  assert.equal(selected.deleteDisabled, false, "Pure Select click did not leave deletion enabled");
  assert.equal(selected.activeElement, "bwChart", "Selection did not focus the chart keyboard surface");

  const deleteDelayHit = armPostDelay("selected-trend-keyboard-delete");
  const deletePostsBefore = postCount();
  await page.keyboard.press("Delete");
  const deletePost = await deleteDelayHit;
  await page.waitForFunction(() => document.querySelector("#bwOpenStatus")?.textContent.includes("Saving drawing"));
  const deletePending = await basicDrawingState(page);
  assert.equal(postCount(), deletePostsBefore + 1, "Keyboard Delete did not create exactly one deletion save");
  assert.equal(deletePending.deleteDisabled, true, "Pending keyboard deletion did not disable controls");
  await page.screenshot({ path: DRAWING_DELETE_SCREENSHOT, fullPage: true });
  await waitForDrawingSaved(page);
  const afterDeleteRecord = await currentVisualization(page, backtestId, visualizationId);
  assert.equal(afterDeleteRecord.revision, selectBeforeRecord.revision + 1);
  assert.equal(afterDeleteRecord.spec.panes[0].visualizers.some((item) => item.id === committedLine.id), false,
    "Keyboard Delete did not remove the selected Trend Line");

  await activateBasicDrawingTool(page, "rectangle");
  const rectangleStart = { x: chart.x + chart.width * 0.24, y: chart.y + chart.height * 0.64 };
  const rectangleEnd = { x: chart.x + chart.width * 0.62, y: chart.y + chart.height * 0.36 };
  const rectangleRevisionBefore = afterDeleteRecord.revision;
  const rectanglePostsBefore = postCount();
  await page.mouse.move(rectangleStart.x, rectangleStart.y);
  await page.mouse.down();
  await page.mouse.move(rectangleEnd.x, rectangleEnd.y, { steps: 10 });
  await page.mouse.up();
  await waitForDrawingSaved(page);
  const rectangleRecord = await currentVisualization(page, backtestId, visualizationId);
  const rectangle = rectangleRecord.spec.panes[0].visualizers.find((item) => item.callback === "drawing.rectangle");
  assert(rectangle, "Rectangle did not commit on down-drag-up");
  assert.equal(rectangleRecord.revision, rectangleRevisionBefore + 1);
  assert.equal(postCount(), rectanglePostsBefore + 1);
  gestures.push({
    toolId: "rectangle", mode: "down-drag-up", committed: true,
    revisionBefore: rectangleRevisionBefore, revisionAfter: rectangleRecord.revision,
    postsBefore: rectanglePostsBefore, postsAfter: postCount(),
  });

  await activateBasicDrawingTool(page, "select");
  const rectangleSelectPostsBefore = postCount();
  await page.mouse.click((rectangleStart.x + rectangleEnd.x) / 2, rectangleEnd.y);
  await page.waitForFunction(() => document.querySelector("[data-drawing-delete]")?.disabled === false);
  await new Promise((resolve) => setTimeout(resolve, 120));
  const rectangleSelectedRecord = await currentVisualization(page, backtestId, visualizationId);
  assert.equal(rectangleSelectedRecord.revision, rectangleRecord.revision, "Rectangle selection created a save");
  assert.equal(postCount(), rectangleSelectPostsBefore, "Rectangle selection created a POST");
  await page.click("[data-drawing-delete]");
  await waitForDrawingSaved(page);
  const afterRectangleDelete = await currentVisualization(page, backtestId, visualizationId);
  assert.equal(afterRectangleDelete.spec.panes[0].visualizers.some((item) => item.id === rectangle.id), false,
    "Delete button did not remove the selected Rectangle");

  await activateBasicDrawingTool(page, "trend-line");
  const clickLineStart = { x: chart.x + chart.width * 0.32, y: chart.y + chart.height * 0.72 };
  const clickLineEnd = { x: chart.x + chart.width * 0.68, y: chart.y + chart.height * 0.34 };
  const clickRevisionBefore = afterRectangleDelete.revision;
  const clickPostsBefore = postCount();
  await page.mouse.click(clickLineStart.x, clickLineStart.y);
  const clickHover = await basicDrawingState(page);
  assert.equal(clickHover.activeGesture, true, "First Trend click did not enter second-point hover");
  await page.mouse.move(clickLineEnd.x, clickLineEnd.y, { steps: 10 });
  await page.mouse.click(clickLineEnd.x, clickLineEnd.y);
  await waitForDrawingSaved(page);
  const finalRecord = await currentVisualization(page, backtestId, visualizationId);
  assert.equal(finalRecord.revision, clickRevisionBefore + 1);
  assert.equal(finalRecord.spec.panes[0].visualizers.filter((item) => item.callback === "drawing.trendLine").length, 1);
  assert.equal(postCount(), clickPostsBefore + 1);
  gestures.push({
    toolId: "trend-line", mode: "click-click", committed: true,
    revisionBefore: clickRevisionBefore, revisionAfter: finalRecord.revision,
    postsBefore: clickPostsBefore, postsAfter: postCount(), firstClickState: clickHover,
  });

  page.off("request", onInterceptedRequest);
  page.off("response", onResponse);
  await page.setRequestInterception(false);
  const domTimeline = await page.evaluate(() => {
    const diagnostic = window.__basicDrawingDiagnostic;
    diagnostic.observer.disconnect();
    return diagnostic.mutations;
  });
  return {
    gestures,
    commit: {
      beforeCommit, commitGap, afterCommit, commitPost, commitElapsedMs,
      screenshotBeforeCommit: DRAWING_PREVIEW_SCREENSHOT,
      screenshotGap: DRAWING_GAP_SCREENSHOT,
      screenshotRestored: DRAWING_RESTORED_SCREENSHOT,
    },
    selectDelete: {
      selected, deletePost, deletePending,
      revisionBefore: selectBeforeRecord.revision,
      revisionAfterPureSelect: afterPureSelectRecord.revision,
      revisionAfterDelete: afterDeleteRecord.revision,
      pointsBefore: selectBeforeLine.params.points,
      pointsAfterPureSelect: afterPureSelectLine.params.points,
      screenshot: DRAWING_DELETE_SCREENSHOT,
    },
    deleteControls: {
      trendDeletedByKeyboard: true,
      rectangleDeletedByButton: true,
      rectangleRevisionAfterDelete: afterRectangleDelete.revision,
    },
    visualizationNetwork,
    domTimeline,
    finalRecord,
  };
}

async function runBasicDrawingDiagnostic(page, apiRequests, backtestId, visualizationId) {
  const pendingScreenshot = "/tmp/trade-basic-drawing-async-pending.png";
  const successScreenshot = "/tmp/trade-basic-drawing-async-success.png";
  const conflictScreenshot = "/tmp/trade-basic-drawing-async-conflict.png";
  const initialRecord = await currentVisualization(page, backtestId, visualizationId);
  const initialRevision = initialRecord.revision;
  const resultPath = `/api/backtests/${backtestId}/result`;
  const resultSliceCount = () => apiRequests.filter((request) => (
    request.phase === "workspace" && request.method === "POST" && request.path === resultPath
  )).length;
  const resultSlicesBefore = resultSliceCount();
  const requestsBeforeDiagnostic = apiRequests.length;

  await page.evaluate(() => {
    const probe = window.__basicDrawingIdentityProbe;
    if (!probe?.latestChart || !probe?.latestController) {
      throw new Error("Drawing identity probe did not observe the initial chart.");
    }
    const host = document.querySelector("#bwChart");
    const originalCanvases = [...host.querySelectorAll("canvas")];
    const diagnostic = {
      originalCanvases,
      addedCanvases: [],
      removedCanvases: [],
      observer: null,
    };
    probe.baseline = {
      chart: probe.latestChart,
      controller: probe.latestController,
      chartCreateCount: probe.chartCreateCount,
      paneDrawCount: probe.paneDrawCount,
    };
    diagnostic.observer = new MutationObserver((records) => records.forEach((record) => {
      record.addedNodes.forEach((node) => {
        if (node instanceof HTMLCanvasElement) diagnostic.addedCanvases.push(node);
        node.querySelectorAll?.("canvas").forEach((canvas) => diagnostic.addedCanvases.push(canvas));
      });
      record.removedNodes.forEach((node) => {
        if (node instanceof HTMLCanvasElement) diagnostic.removedCanvases.push(node);
        node.querySelectorAll?.("canvas").forEach((canvas) => diagnostic.removedCanvases.push(canvas));
      });
    }));
    diagnostic.observer.observe(host, { childList: true, subtree: true });
    window.__basicAsyncDrawingDiagnostic = diagnostic;
  });

  const identityState = () => page.evaluate(() => {
    const probe = window.__basicDrawingIdentityProbe;
    const diagnostic = window.__basicAsyncDrawingDiagnostic;
    const host = document.querySelector("#bwChart");
    const canvases = [...host.querySelectorAll("canvas")];
    const toolButtons = [...document.querySelectorAll("#bwDrawingTools [data-drawing-tool]")];
    return {
      sameChart: probe.latestChart === probe.baseline.chart,
      sameController: probe.latestController === probe.baseline.controller,
      chartCreateCount: probe.chartCreateCount,
      paneDrawCount: probe.paneDrawCount,
      baselineChartCreateCount: probe.baseline.chartCreateCount,
      baselinePaneDrawCount: probe.baseline.paneDrawCount,
      canvasCount: canvases.length,
      originalCanvasCount: diagnostic.originalCanvases.length,
      sameCanvasNodes: canvases.length === diagnostic.originalCanvases.length
        && canvases.every((canvas, index) => canvas === diagnostic.originalCanvases[index]),
      originalCanvasesConnected: diagnostic.originalCanvases.every((canvas) => canvas.isConnected),
      addedCanvasCount: diagnostic.addedCanvases.length,
      removedCanvasCount: diagnostic.removedCanvases.length,
      ariaBusy: host.getAttribute("aria-busy"),
      persistenceBusyClass: host.classList.contains("drawing-persistence-busy"),
      enabledToolCount: toolButtons.filter((button) => !button.disabled).length,
      disabledToolCount: toolButtons.filter((button) => button.disabled).length,
      openStatus: document.querySelector("#bwOpenStatus")?.textContent || "",
      drawingStatus: document.querySelector("#bwDrawingStatus")?.textContent || "",
      reconcileCallCount: probe.reconcileCalls.length,
      lastReconciledVisualizers: probe.reconcileCalls.at(-1)?.visualizers || null,
    };
  });

  const visualizationNetwork = [];
  const postPlans = [];
  const capturedPosts = new Map();
  const planPost = (kind, label, delayMs = 0) => {
    let resolveHit;
    const hit = new Promise((resolve) => { resolveHit = resolve; });
    postPlans.push({ kind, label, delayMs, resolveHit });
    return hit;
  };
  const onResponse = (response) => {
    const url = new URL(response.url());
    if (url.origin === ORIGIN && url.pathname === "/api/visualizations") {
      visualizationNetwork.push({
        type: "response",
        method: response.request().method(),
        status: response.status(),
        at: Date.now(),
      });
    }
  };
  const onInterceptedRequest = (request) => {
    const url = new URL(request.url());
    if (url.origin !== ORIGIN || url.pathname !== "/api/visualizations"
        || request.method() !== "POST") {
      request.continue();
      return;
    }
    const plan = postPlans.shift();
    if (!plan) {
      visualizationNetwork.push({ type: "unexpected-request", method: "POST", at: Date.now() });
      request.continue();
      return;
    }
    const payload = JSON.parse(request.postData());
    const entry = {
      type: "request",
      method: "POST",
      action: plan.kind,
      label: plan.label,
      expectedRevision: payload.expectedRevision,
      drawingIds: payload.spec.panes.flatMap((pane) => (pane.visualizers || []))
        .filter((item) => item.callback === "drawing.trendLine")
        .map((item) => item.id),
      at: Date.now(),
    };
    capturedPosts.set(plan.label, { request, payload, entry });
    visualizationNetwork.push(entry);
    plan.resolveHit(entry);
    if (plan.kind === "delay") {
      setTimeout(() => request.continue(), plan.delayMs);
    } else if (plan.kind === "hold") {
      // Released after an independent actor advances the repository so the
      // real endpoint returns its authoritative conflict record.
    } else {
      request.continue();
    }
  };
  page.on("response", onResponse);
  await page.setRequestInterception(true);
  page.on("request", onInterceptedRequest);

  const chart = await chartBox(page, "#bwChart");
  const drawLine = async (start, end) => {
    await activateBasicDrawingTool(page, "trend-line");
    await page.mouse.move(start.x, start.y);
    await page.mouse.down();
    await page.mouse.move(end.x, end.y, { steps: 6 });
    await page.mouse.up();
  };
  const firstStart = { x: chart.x + chart.width * 0.22, y: chart.y + chart.height * 0.72 };
  const firstEnd = { x: chart.x + chart.width * 0.55, y: chart.y + chart.height * 0.38 };
  const secondStart = { x: chart.x + chart.width * 0.43, y: chart.y + chart.height * 0.76 };
  const secondEnd = { x: chart.x + chart.width * 0.79, y: chart.y + chart.height * 0.28 };

  const firstSuccessHit = planPost("delay", "success-first", 900);
  const secondSuccessHit = planPost("continue", "success-second");
  await drawLine(firstStart, firstEnd);
  await firstSuccessHit;
  await drawLine(secondStart, secondEnd);
  await page.waitForFunction(() => (
    document.querySelector("#bwOpenStatus")?.textContent === "Saving drawing changes in the background…"
    && document.querySelector("#bwDrawingStatus")?.textContent.startsWith("2 drawing changes saving")
  ));
  const pendingState = await identityState();
  assert.equal(pendingState.ariaBusy, "false", JSON.stringify(pendingState));
  assert.equal(pendingState.persistenceBusyClass, false, JSON.stringify(pendingState));
  assert(pendingState.enabledToolCount > 0, JSON.stringify(pendingState));
  assert.equal(pendingState.sameCanvasNodes, true, JSON.stringify(pendingState));
  assert.equal(pendingState.sameController, true, JSON.stringify(pendingState));
  assert.equal(visualizationNetwork.filter((item) => item.type === "response" && item.method === "POST").length, 0);
  await page.screenshot({ path: pendingScreenshot, fullPage: true });

  await secondSuccessHit;
  await waitForDrawingSaved(page);
  const successState = await identityState();
  assert.deepEqual({
    sameChart: successState.sameChart,
    sameController: successState.sameController,
    sameCanvasNodes: successState.sameCanvasNodes,
    originalCanvasesConnected: successState.originalCanvasesConnected,
    addedCanvasCount: successState.addedCanvasCount,
    removedCanvasCount: successState.removedCanvasCount,
    chartCreateCount: successState.chartCreateCount,
    paneDrawCount: successState.paneDrawCount,
  }, {
    sameChart: true,
    sameController: true,
    sameCanvasNodes: true,
    originalCanvasesConnected: true,
    addedCanvasCount: 0,
    removedCanvasCount: 0,
    chartCreateCount: successState.baselineChartCreateCount,
    paneDrawCount: successState.baselinePaneDrawCount,
  });
  assert.equal(resultSliceCount(), resultSlicesBefore, "Background saves requested another Result slice");
  await page.screenshot({ path: successScreenshot, fullPage: true });
  const successRequests = visualizationNetwork.filter((item) => item.type === "request").slice(0, 2);
  assert.deepEqual(successRequests.map((item) => item.expectedRevision), [initialRevision, initialRevision + 1]);
  assert.deepEqual(successRequests.map((item) => item.action), ["delay", "continue"]);
  assert.equal(successRequests[0].drawingIds.length, 1);
  assert.equal(successRequests[1].drawingIds.length, 2);
  const firstSuccessId = successRequests[0].drawingIds[0];
  const secondSuccessId = successRequests[1].drawingIds.find((id) => id !== firstSuccessId);
  assert(secondSuccessId);

  const requestsBeforeSuccessRead = apiRequests.slice(requestsBeforeDiagnostic);
  assert.equal(requestsBeforeSuccessRead.filter((request) => (
    request.method === "GET" && request.path.startsWith("/api/visualizations")
  )).length, 0, "Successful background save performed a repository GET");
  const successRecord = await currentVisualization(page, backtestId, visualizationId);
  assert.equal(successRecord.revision, initialRevision + 2);
  const successDrawingIds = successRecord.spec.panes[0].visualizers
    .filter((item) => item.callback === "drawing.trendLine").map((item) => item.id);
  assert(successDrawingIds.includes(firstSuccessId) && successDrawingIds.includes(secondSuccessId));

  const conflictRequestsStart = apiRequests.length;
  const conflictNetworkStart = visualizationNetwork.length;
  const conflictBaseRevision = successRecord.revision;
  const firstConflictHit = planPost("delay", "conflict-first", 900);
  const secondConflictHit = planPost("hold", "conflict-second");
  const thirdStart = { x: chart.x + chart.width * 0.26, y: chart.y + chart.height * 0.64 };
  const thirdEnd = { x: chart.x + chart.width * 0.66, y: chart.y + chart.height * 0.22 };
  const fourthStart = { x: chart.x + chart.width * 0.31, y: chart.y + chart.height * 0.79 };
  const fourthEnd = { x: chart.x + chart.width * 0.73, y: chart.y + chart.height * 0.43 };
  await drawLine(thirdStart, thirdEnd);
  await firstConflictHit;
  await drawLine(fourthStart, fourthEnd);
  await page.waitForFunction(() => (
    document.querySelector("#bwDrawingStatus")?.textContent.startsWith("2 drawing changes saving")
  ));
  const conflictPendingState = await identityState();
  assert.equal(conflictPendingState.ariaBusy, "false", JSON.stringify(conflictPendingState));
  assert(conflictPendingState.enabledToolCount > 0, JSON.stringify(conflictPendingState));
  await secondConflictHit;
  const firstConflictCapture = capturedPosts.get("conflict-first");
  const secondConflictCapture = capturedPosts.get("conflict-second");
  assert(firstConflictCapture && secondConflictCapture);
  const firstConflictId = firstConflictCapture.entry.drawingIds
    .find((id) => !successDrawingIds.includes(id));
  const secondConflictId = secondConflictCapture.entry.drawingIds
    .find((id) => !firstConflictCapture.entry.drawingIds.includes(id));
  assert(firstConflictId && secondConflictId);

  const authoritativeSpec = structuredClone(firstConflictCapture.payload.spec);
  const authoritativePane = authoritativeSpec.panes[0];
  const authoritativeTemplate = authoritativePane.visualizers
    .find((item) => item.id === firstConflictId);
  assert(authoritativeTemplate, "Accepted local drawing is missing from the external actor base");
  const serverDrawing = structuredClone(authoritativeTemplate);
  serverDrawing.id = `drawing.trendLine.server${Date.now().toString(36)}`;
  const serverPriceShift = Math.max(
    Math.abs(serverDrawing.params.points[0].price - serverDrawing.params.points[1].price) * 0.3,
    Math.abs(serverDrawing.params.points[0].price) * 0.01,
    0.01,
  );
  serverDrawing.params.points = serverDrawing.params.points.map((point) => ({
    ...point,
    price: point.price + serverPriceShift,
  }));
  serverDrawing.params.color = "#dc2626";
  authoritativePane.visualizers.push(serverDrawing);

  const externalActorHit = planPost("continue", "external-actor");
  const externalActorResponsePromise = browserApi(page, "POST", "/api/visualizations", {
    backtestId,
    visualizationId,
    name: successRecord.name,
    expectedRevision: conflictBaseRevision + 1,
    spec: authoritativeSpec,
  });
  await externalActorHit;
  const externalActorResponse = await externalActorResponsePromise;
  assert.equal(externalActorResponse.status, 200, JSON.stringify(externalActorResponse.body));
  assert.equal(externalActorResponse.body.accepted, true, JSON.stringify(externalActorResponse.body));
  const authoritativeCurrent = externalActorResponse.body.visualization;
  assert.equal(authoritativeCurrent.revision, conflictBaseRevision + 2);
  assert.equal(authoritativeCurrent.visualizationId, visualizationId);
  assert.equal(authoritativeCurrent.spec.panes[0].visualizers.some(
    (item) => item.id === serverDrawing.id,
  ), true);

  await secondConflictCapture.request.continue();
  await page.waitForFunction(() => (
    document.querySelector("#bwChart")?.getAttribute("aria-busy") === "true"
    && document.querySelector("#bwChart")?.classList.contains("drawing-persistence-busy")
  ), { timeout: 30000 });
  await new Promise((resolve) => setTimeout(resolve, 500));
  const conflictState = await identityState();
  assert.equal(conflictState.sameChart, true, JSON.stringify(conflictState));
  assert.equal(conflictState.sameController, true, JSON.stringify(conflictState));
  assert.equal(conflictState.sameCanvasNodes, true, JSON.stringify(conflictState));
  assert.equal(conflictState.ariaBusy, "true", JSON.stringify(conflictState));
  assert.equal(conflictState.persistenceBusyClass, true, JSON.stringify(conflictState));
  assert.equal(conflictState.enabledToolCount, 0, JSON.stringify(conflictState));
  assert.equal(
    conflictState.openStatus,
    "Visualization changed elsewhere. The server version was restored without local changes. Reload this workspace.",
    JSON.stringify(conflictState),
  );
  assert.equal(conflictState.drawingStatus, "Drawing interaction is locked until this workspace is reloaded.");
  assert.deepEqual(
    conflictState.lastReconciledVisualizers,
    authoritativeCurrent.spec.panes[0].visualizers,
    "Live Core presentation was not reconciled to the authoritative server scene",
  );
  assert.equal(resultSliceCount(), resultSlicesBefore, "Conflict handling requested another Result slice");
  await page.screenshot({ path: conflictScreenshot, fullPage: true });

  const conflictRequests = visualizationNetwork.slice(conflictNetworkStart)
    .filter((item) => item.type === "request");
  assert.equal(conflictRequests.length, 3, JSON.stringify(conflictRequests));
  const localConflictRequests = conflictRequests.filter((item) => item.label.startsWith("conflict-"));
  const externalActorRequests = conflictRequests.filter((item) => item.label === "external-actor");
  assert.equal(localConflictRequests.length, 2, JSON.stringify(conflictRequests));
  assert.equal(externalActorRequests.length, 1, JSON.stringify(conflictRequests));
  assert.deepEqual(localConflictRequests.map((item) => item.expectedRevision), [
    conflictBaseRevision,
    conflictBaseRevision + 1,
  ]);
  assert.deepEqual(localConflictRequests.map((item) => item.action), ["delay", "hold"]);
  assert.equal(externalActorRequests[0].expectedRevision, conflictBaseRevision + 1);
  assert.equal(externalActorRequests[0].action, "continue");
  assert.equal(postPlans.length, 0);
  const conflictApiRequests = apiRequests.slice(conflictRequestsStart);
  assert.equal(conflictApiRequests.filter((request) => (
    request.method === "POST" && request.path === "/api/visualizations"
  )).length, 3, "Conflict path made an unexpected Visualization POST");
  assert.equal(conflictApiRequests.filter((request) => (
    request.method === "GET" && request.path.startsWith("/api/visualizations")
  )).length, 0, "Conflict path automatically fetched or merged a Visualization");
  page.off("request", onInterceptedRequest);
  page.off("response", onResponse);
  await page.setRequestInterception(false);
  const finalRecord = await currentVisualization(page, backtestId, visualizationId);
  assert.equal(finalRecord.revision, conflictBaseRevision + 2);
  assert.deepEqual(finalRecord, authoritativeCurrent, "Repository current differs from the actor's accepted record");
  const finalDrawingIds = finalRecord.spec.panes[0].visualizers
    .filter((item) => item.callback === "drawing.trendLine").map((item) => item.id);
  assert(finalDrawingIds.includes(firstSuccessId));
  assert(finalDrawingIds.includes(secondSuccessId));
  assert(finalDrawingIds.includes(firstConflictId));
  assert.equal(finalDrawingIds.includes(secondConflictId), false, "Conflicting local drawing was merged");
  assert(finalDrawingIds.includes(serverDrawing.id), "Authoritative server drawing is absent");
  const observerEvidence = await page.evaluate(() => {
    const diagnostic = window.__basicAsyncDrawingDiagnostic;
    diagnostic.observer.disconnect();
    return {
      originalCanvasCount: diagnostic.originalCanvases.length,
      addedCanvasCount: diagnostic.addedCanvases.length,
      removedCanvasCount: diagnostic.removedCanvases.length,
    };
  });
  return {
    mode: "async-canonical-queue",
    initialRevision,
    success: {
      requestExpectedRevisions: successRequests.map((item) => item.expectedRevision),
      drawingIds: [firstSuccessId, secondSuccessId],
      pendingState,
      settledState: successState,
      canonicalRevision: successRecord.revision,
      screenshotPending: pendingScreenshot,
      screenshotSettled: successScreenshot,
    },
    conflict: {
      requestExpectedRevisions: localConflictRequests.map((item) => item.expectedRevision),
      externalActorExpectedRevision: externalActorRequests[0].expectedRevision,
      externalActorRevision: authoritativeCurrent.revision,
      acceptedDrawingId: firstConflictId,
      rejectedDrawingId: secondConflictId,
      serverDrawingId: serverDrawing.id,
      pendingState: conflictPendingState,
      lockedState: conflictState,
      canonicalRevision: finalRecord.revision,
      screenshot: conflictScreenshot,
      noRetry: true,
      noMerge: true,
    },
    resultSliceCountBefore: resultSlicesBefore,
    resultSliceCountAfter: resultSliceCount(),
    observerEvidence,
    visualizationNetwork,
    finalRecord,
  };
}

async function lightThemeEvidence(page, surfaceSelector) {
  const evidence = await page.evaluate((selector) => {
    const root = getComputedStyle(document.documentElement);
    const surface = document.querySelector(selector);
    const topbar = document.querySelector(".topbar");
    return {
      bodyClass: document.body.className,
      sharedStylesheet: [...document.styleSheets]
        .some((sheet) => new URL(sheet.href || location.href).pathname === "/styles.css"),
      bgToken: root.getPropertyValue("--bg").trim(),
      panelToken: root.getPropertyValue("--panel").trim(),
      textToken: root.getPropertyValue("--text").trim(),
      bodyBackground: getComputedStyle(document.body).backgroundColor,
      topbarBackground: topbar ? getComputedStyle(topbar).backgroundColor : "",
      surfaceBackground: surface ? getComputedStyle(surface).backgroundColor : "",
    };
  }, surfaceSelector);
  assert.equal(evidence.sharedStylesheet, true, JSON.stringify(evidence));
  assert.equal(evidence.bgToken, "#f5f7f8", JSON.stringify(evidence));
  assert.equal(evidence.panelToken, "#ffffff", JSON.stringify(evidence));
  assert.equal(evidence.textToken, "#172026", JSON.stringify(evidence));
  assert.equal(evidence.bodyBackground, "rgb(245, 247, 248)", JSON.stringify(evidence));
  assert.equal(evidence.topbarBackground, "rgb(255, 255, 255)", JSON.stringify(evidence));
  assert.equal(evidence.surfaceBackground, "rgb(255, 255, 255)", JSON.stringify(evidence));
  return evidence;
}

async function basicChromeEvidence(page, expectedBackPath) {
  const evidence = await page.evaluate((backPath) => {
    const heading = document.querySelector(".topbar h1");
    const back = document.querySelector(".topbar .bw-header-link");
    const status = document.querySelector("#bwStatus");
    const statusStyle = status ? getComputedStyle(status) : null;
    return {
      heading: heading?.textContent?.trim() || "",
      backText: back?.textContent?.trim() || "",
      backPath: back ? new URL(back.href).pathname : "",
      statusTag: status?.tagName || "",
      statusRole: status?.getAttribute("role") || "",
      statusClass: status?.className || "",
      statusBorderTopWidth: statusStyle?.borderTopWidth || "",
      statusBackgroundColor: statusStyle?.backgroundColor || "",
      statusCursor: statusStyle?.cursor || "",
      expectedBackPath: backPath,
    };
  }, expectedBackPath);
  assert.equal(evidence.heading, "Trade Engine", JSON.stringify(evidence));
  assert.equal(evidence.backText, "Back", JSON.stringify(evidence));
  assert.equal(evidence.backPath, expectedBackPath, JSON.stringify(evidence));
  assert.equal(evidence.statusTag, "SPAN", JSON.stringify(evidence));
  assert.equal(evidence.statusRole, "status", JSON.stringify(evidence));
  assert(evidence.statusClass.split(/\s+/).includes("bw-plain-status"), JSON.stringify(evidence));
  assert.equal(evidence.statusBorderTopWidth, "0px", JSON.stringify(evidence));
  assert.equal(evidence.statusBackgroundColor, "rgba(0, 0, 0, 0)", JSON.stringify(evidence));
  assert.notEqual(evidence.statusCursor, "pointer", JSON.stringify(evidence));
  return evidence;
}

async function catalogControlEvidence(page) {
  const evidence = await page.evaluate(() => {
    const refresh = document.querySelector("#bwSyncMarket");
    const watchlist = document.querySelector("#bwWatchlistTab");
    const all = document.querySelector("#bwAllStocksTab");
    const panel = document.querySelector("#bwMarketTabPanel");
    const refreshStyle = getComputedStyle(refresh);
    const tabStyle = getComputedStyle(all);
    return {
      refreshText: refresh.textContent.trim(),
      refreshAriaLabel: refresh.getAttribute("aria-label"),
      refreshInCatalogActions: !!refresh.closest(".bw-catalog-actions"),
      refreshInTopbar: !!refresh.closest(".topbar"),
      refreshBorderTopWidth: refreshStyle.borderTopWidth,
      refreshBorderRadius: refreshStyle.borderRadius,
      refreshBackgroundColor: refreshStyle.backgroundColor,
      watchlistRole: watchlist.getAttribute("role"),
      allRole: all.getAttribute("role"),
      watchlistSelected: watchlist.getAttribute("aria-selected"),
      allSelected: all.getAttribute("aria-selected"),
      watchlistTabIndex: watchlist.getAttribute("tabindex"),
      allTabIndex: all.getAttribute("tabindex"),
      panelRole: panel.getAttribute("role"),
      panelLabelledBy: panel.getAttribute("aria-labelledby"),
      tabBorderTopWidth: tabStyle.borderTopWidth,
      tabBorderBottomWidth: tabStyle.borderBottomWidth,
      tabBorderRadius: tabStyle.borderRadius,
      tabBackgroundColor: tabStyle.backgroundColor,
    };
  });
  assert.equal(evidence.refreshText, "Refresh stock list", JSON.stringify(evidence));
  assert.equal(evidence.refreshAriaLabel, "Refresh the complete stock list", JSON.stringify(evidence));
  assert.equal(evidence.refreshInCatalogActions, true, JSON.stringify(evidence));
  assert.equal(evidence.refreshInTopbar, false, JSON.stringify(evidence));
  assert.equal(evidence.refreshBorderTopWidth, "1px", JSON.stringify(evidence));
  assert.notEqual(evidence.refreshBorderRadius, "0px", JSON.stringify(evidence));
  assert.equal(evidence.watchlistRole, "tab", JSON.stringify(evidence));
  assert.equal(evidence.allRole, "tab", JSON.stringify(evidence));
  assert.equal(evidence.watchlistSelected, "false", JSON.stringify(evidence));
  assert.equal(evidence.allSelected, "true", JSON.stringify(evidence));
  assert.equal(evidence.watchlistTabIndex, "-1", JSON.stringify(evidence));
  assert.equal(evidence.allTabIndex, "0", JSON.stringify(evidence));
  assert.equal(evidence.panelRole, "tabpanel", JSON.stringify(evidence));
  assert.equal(evidence.panelLabelledBy, "bwAllStocksTab", JSON.stringify(evidence));
  assert.equal(evidence.tabBorderTopWidth, "0px", JSON.stringify(evidence));
  assert.equal(evidence.tabBorderBottomWidth, "2px", JSON.stringify(evidence));
  assert.equal(evidence.tabBorderRadius, "0px", JSON.stringify(evidence));
  assert.notEqual(evidence.refreshBackgroundColor, evidence.tabBackgroundColor, JSON.stringify(evidence));
  return evidence;
}

function homeForbiddenRequest(request) {
  return request.path === "/api/subsystems/basic/instruments/open"
    || request.path.startsWith("/api/backtests")
    || request.path.startsWith("/api/backtest-jobs")
    || request.path.startsWith("/api/visualizations")
    || request.path.startsWith("/api/data/")
    || request.path.startsWith("/api/pipelines")
    || request.path.startsWith("/api/visualizers")
    || request.path.startsWith("/api/modules")
    || request.path.startsWith("/api/environment-modules")
    || request.path.startsWith("/api/analysis-modules");
}

async function main() {
  required(process.env.DISPLAY, "DISPLAY");
  required(ORIGIN, "TRADE_BASIC_REAL_ORIGIN");
  required(SESSION_COOKIE, "TRADE_BASIC_REAL_SESSION_COOKIE");
  required(SESSION_TOKEN, "TRADE_BASIC_REAL_SESSION_TOKEN");
  required(CSRF_COOKIE, "TRADE_BASIC_REAL_CSRF_COOKIE");
  required(CSRF_TOKEN, "TRADE_BASIC_REAL_CSRF_TOKEN");
  required(HOME_SCREENSHOT, "TRADE_BASIC_REAL_SCREENSHOT");
  required(WORKSPACE_SCREENSHOT, "TRADE_BASIC_REAL_RESULT_SCREENSHOT");
  required(RETURNED_SCREENSHOT, "TRADE_BASIC_REAL_RETURNED_SCREENSHOT");
  assert(["home-only", "full", "drawing-diagnostic"].includes(MODE), `Unsupported mode ${MODE}`);

  const userDataDir = fs.mkdtempSync(path.join(os.tmpdir(), "trade-basic-real-vnc-"));
  let browser;
  try {
    browser = await puppeteer.launch({
      headless: false,
      executablePath: "/usr/bin/google-chrome",
      userDataDir,
      args: [
        "--no-sandbox",
        "--disable-gpu",
        "--disable-dev-shm-usage",
        "--no-proxy-server",
        "--window-size=1600,1000",
      ],
      defaultViewport: { width: 1600, height: 1000 },
    });
    const page = await browser.newPage();
    await page.setViewport({ width: 1600, height: 1000, deviceScaleFactor: 1 });
    if (DRAWING_DIAGNOSTIC) await installBasicDrawingIdentityProbe(page);
    await page.setCookie(
      { name: SESSION_COOKIE, value: SESSION_TOKEN, url: ORIGIN, httpOnly: true },
      { name: CSRF_COOKIE, value: CSRF_TOKEN, url: ORIGIN },
    );

    const pageErrors = [];
    const consoleErrors = [];
    const failedApiResponses = [];
    const failedOriginResponses = [];
    const apiRequests = [];
    let phase = "home";
    page.on("pageerror", (error) => pageErrors.push(error.message));
    page.on("console", (message) => {
      if (message.type() === "error") consoleErrors.push(message.text());
    });
    page.on("framenavigated", (frame) => {
      if (frame !== page.mainFrame()) return;
      const url = new URL(frame.url());
      if (url.origin === ORIGIN && url.pathname === "/basic-workflow/workspace") phase = "workspace";
      if (url.origin === ORIGIN && url.pathname === "/basic-workflow" && phase === "workspace") {
        phase = "returned-home";
      }
    });
    page.on("request", (request) => {
      const url = new URL(request.url());
      if (url.origin !== ORIGIN || !url.pathname.startsWith("/api/")) return;
      let payload = null;
      try { payload = request.postData() ? JSON.parse(request.postData()) : null; } catch { payload = "invalid-json"; }
      apiRequests.push({ phase, method: request.method(), path: `${url.pathname}${url.search}`, payload });
    });
    page.on("response", (response) => {
      const url = new URL(response.url());
      if (url.origin === ORIGIN && response.status() >= 400) {
        failedOriginResponses.push({ status: response.status(), path: url.pathname });
      }
      if (url.origin === ORIGIN && url.pathname.startsWith("/api/") && response.status() >= 400) {
        failedApiResponses.push({ status: response.status(), path: url.pathname });
      }
    });

    await page.goto(`${ORIGIN}/basic-workflow`, { waitUntil: "networkidle0", timeout: 30000 });
    await page.waitForSelector(
      '#bwInstrumentList .bw-market-row[data-instrument-id="US-AAPL"] .bw-market-open',
      { timeout: 30000 },
    );
    await page.waitForFunction(() => (
      document.querySelector("#bwStatus")?.dataset.state === "ready"
      && document.querySelector("#bwInstrumentList")?.children.length > 0
    ), { timeout: 30000 });

    assert.equal(new URL(page.url()).pathname, "/basic-workflow");
    assert.equal(await page.$("#bwChart"), null, "Home unexpectedly contains the workspace chart host");
    assert.equal(await page.$("#bwDrawingTools"), null, "Home unexpectedly contains drawing controls");
    const homeCanvasCount = await page.$$eval("canvas", (items) => items.length);
    assert.equal(homeCanvasCount, 0, "Home instantiated a chart canvas");

    const homeTheme = await lightThemeEvidence(page, ".bw-market-panel");
    assert.equal(homeTheme.bodyClass, "basic-home-route");
    const homeChrome = await basicChromeEvidence(page, "/overview");
    const pageInitialRequests = [...apiRequests];
    if (MODE === "full") {
      assert.deepEqual(pageInitialRequests, [{
        phase: "home",
        method: "GET",
        path: "/api/subsystems/basic/market",
        payload: null,
      }]);
    }
    const market = await browserApi(page, "GET", "/api/subsystems/basic/market");
    assert.equal(market.status, 200, JSON.stringify(market.body));
    assert.equal(market.body.snapshot.instrumentCount, 12000);
    assert.deepEqual(market.body.barSnapshots, []);
    const allInstruments = market.body.snapshot.instruments;
    const firstPageIds = allInstruments.slice(0, 50).map((item) => item.instrumentId);
    const secondPageIds = allInstruments.slice(50, 100).map((item) => item.instrumentId);

    const initialHomeRequests = [...apiRequests];
    if (MODE === "full") {
      assert.equal(
        initialHomeRequests.some((request) => request.path === "/api/subsystems/basic/market/sync"),
        false,
        JSON.stringify(initialHomeRequests),
      );
      assert.equal(initialHomeRequests.some(homeForbiddenRequest), false, JSON.stringify(initialHomeRequests));
    }
    let refreshRequests = [];
    if (MODE === "full") {
      const requestsBeforeRefresh = apiRequests.length;
      const refreshPost = page.waitForResponse((response) => (
        response.request().method() === "POST"
        && new URL(response.url()).pathname === "/api/subsystems/basic/market/sync"
      ), { timeout: 30000 });
      const refreshGet = page.waitForResponse((response) => (
        response.request().method() === "GET"
        && new URL(response.url()).pathname === "/api/subsystems/basic/market"
      ), { timeout: 30000 });
      await page.click("#bwSyncMarket");
      const [refreshPostResponse, refreshGetResponse] = await Promise.all([refreshPost, refreshGet]);
      assert.equal(refreshPostResponse.status(), 200);
      assert.equal(refreshGetResponse.status(), 200);
      await page.waitForFunction(() => document.querySelector("#bwStatus")?.dataset.state === "ready");
      refreshRequests = apiRequests.slice(requestsBeforeRefresh);
      assert.deepEqual(
        refreshRequests.filter((request) => request.path === "/api/subsystems/basic/market/sync"),
        [{
          phase: "home",
          method: "POST",
          path: "/api/subsystems/basic/market/sync",
          payload: { providerId: "nasdaq-us-snapshot" },
        }],
      );
      assert.equal(refreshRequests.some(homeForbiddenRequest), false, JSON.stringify(refreshRequests));
    }

    await page.click('[data-market-tab="all"]');
    await page.waitForFunction(() => document.querySelector("#bwPageRange")?.textContent === "1–50 of 12000");
    const catalogControls = await catalogControlEvidence(page);
    const renderedFirstPageIds = await page.$$eval(
      "#bwInstrumentList .bw-market-row",
      (items) => items.map((item) => item.dataset.instrumentId),
    );
    assert.deepEqual(renderedFirstPageIds, firstPageIds);
    assert.equal(renderedFirstPageIds.length, 50);
    const unopenedDataAsOf = await page.evaluate(() => Object.fromEntries(
      ["US-AAPL", "US-MSFT"].map((instrumentId) => {
        const cell = document.querySelector(
          `#bwInstrumentList [data-instrument-id="${instrumentId}"] .bw-data-as-of`,
        );
        return [instrumentId, { text: cell?.textContent, title: cell?.getAttribute("title") || "" }];
      }),
    ));
    assert.deepEqual(unopenedDataAsOf, {
      "US-AAPL": { text: "", title: "" },
      "US-MSFT": { text: "", title: "" },
    });

    const nextPageStartedAt = Date.now();
    await page.click("#bwNextPage");
    await page.waitForFunction(() => document.querySelector("#bwPageRange")?.textContent === "51–100 of 12000");
    const nextPageMs = Date.now() - nextPageStartedAt;
    assert(nextPageMs < 5000, `Next page render stalled for ${nextPageMs}ms`);
    const renderedSecondPageIds = await page.$$eval(
      "#bwInstrumentList .bw-market-row",
      (items) => items.map((item) => item.dataset.instrumentId),
    );
    assert.deepEqual(renderedSecondPageIds, secondPageIds);
    assert.equal(renderedSecondPageIds.length, 50);

    const searchTarget = allInstruments.at(-1);
    const searchStartedAt = Date.now();
    await page.focus("#bwInstrumentSearch");
    await page.keyboard.type(searchTarget.symbol);
    await page.waitForFunction((instrumentId) => (
      document.querySelector("#bwPageRange")?.textContent === "1–1 of 1"
      && document.querySelectorAll("#bwInstrumentList .bw-market-row").length === 1
      && document.querySelector("#bwInstrumentList .bw-market-row")?.dataset.instrumentId === instrumentId
    ), {}, searchTarget.instrumentId);
    const searchMs = Date.now() - searchStartedAt;
    assert(searchMs < 5000, `Catalog search stalled for ${searchMs}ms`);
    await page.$eval("#bwInstrumentSearch", (item) => {
      item.value = "";
      item.dispatchEvent(new Event("input", { bubbles: true }));
    });
    await page.waitForFunction(() => document.querySelector("#bwPageRange")?.textContent === "1–50 of 12000");
    assert.deepEqual(
      await page.$$eval("#bwInstrumentList .bw-market-row", (items) => items.map((item) => item.dataset.instrumentId)),
      firstPageIds,
      "Clearing search did not reset the catalog to page one",
    );

    const watchSelector = '#bwInstrumentList .bw-market-row[data-instrument-id="US-AAPL"] [data-watch-instrument-id="US-AAPL"]';
    const initiallyWatched = await page.$eval(watchSelector, (item) => item.textContent === "★");
    if (initiallyWatched) {
      await page.click(watchSelector);
      await page.waitForFunction((selector) => document.querySelector(selector)?.textContent === "☆", {}, watchSelector);
    }
    await page.click(watchSelector);
    await page.waitForFunction((selector) => document.querySelector(selector)?.textContent === "★", {}, watchSelector);

    const homeIdentityAudit = await visibleIdentityAudit(
      page,
      "Basic home",
      collectOpaqueValues(market.body),
    );
    await page.screenshot({ path: HOME_SCREENSHOT, fullPage: true });
    const homeRequests = apiRequests.filter((request) => request.phase === "home");
    const forbiddenHomeRequests = homeRequests.filter(homeForbiddenRequest);
    assert.deepEqual(forbiddenHomeRequests, []);
    assert.equal(homeRequests.some((request) => request.path === "/api/subsystems/basic/instruments/open"), false);
    assert.equal(homeRequests.some((request) => request.path.startsWith("/api/backtests")), false);
    assert.equal(homeRequests.some((request) => request.path.startsWith("/api/visualizations")), false);

    const expectedConsoleError = (message) => (
      message.includes("server responded with a status of 404")
      || (DRAWING_DIAGNOSTIC && message.includes("server responded with a status of 409"))
      || (message.includes("Loading the font 'data:font/") && message.includes("Content Security Policy"))
      || message.includes("WebSocket connection to 'ws://127.0.0.1:1/ws/ui' failed")
    );
    assert.deepEqual(pageErrors, []);
    assert.deepEqual(failedApiResponses, []);
    assert.deepEqual(
      failedOriginResponses.filter((item) => !(item.status === 404 && item.path === "/favicon.ico")),
      [],
    );
    assert.deepEqual(consoleErrors.filter((message) => !expectedConsoleError(message)), []);

    const homeAudit = {
      url: page.url(),
      canvasCount: homeCanvasCount,
      requestCount: homeRequests.length,
      forbiddenRequestCount: forbiddenHomeRequests.length,
      catalogInstrumentCount: allInstruments.length,
      pageSize: renderedFirstPageIds.length,
      firstPage: {
        range: "1–50 of 12000",
        firstInstrumentId: renderedFirstPageIds[0],
        lastInstrumentId: renderedFirstPageIds.at(-1),
      },
      secondPage: {
        range: "51–100 of 12000",
        firstInstrumentId: renderedSecondPageIds[0],
        lastInstrumentId: renderedSecondPageIds.at(-1),
        renderMs: nextPageMs,
      },
      search: {
        instrumentId: searchTarget.instrumentId,
        range: "1–1 of 1",
        renderMs: searchMs,
        clearResetRange: "1–50 of 12000",
      },
      unopenedDataAsOf,
      identity: homeIdentityAudit,
      chrome: homeChrome,
      catalogControls,
      refresh: {
        pageInitialRequests,
        initialRequests: initialHomeRequests,
        requestCount: refreshRequests.length,
        requests: refreshRequests,
        forbiddenRequestCount: refreshRequests.filter(homeForbiddenRequest).length,
      },
      theme: homeTheme,
      screenshot: HOME_SCREENSHOT,
      expectedHarnessConsoleErrors: consoleErrors.length,
    };
    if (MODE === "home-only") {
      console.log(`BASIC_HOME_VNC_RESULT=${JSON.stringify({ home: homeAudit })}`);
      return;
    }

    const linkSelector = '#bwInstrumentList .bw-market-row[data-instrument-id="US-AAPL"] .bw-market-open';
    const clickedHref = await page.$eval(linkSelector, (item) => item.href);
    const clickedUrl = new URL(clickedHref);
    assert.equal(clickedUrl.pathname, "/basic-workflow/workspace");
    assert.equal(clickedUrl.searchParams.get("snapshotId"), market.body.snapshot.snapshotId);
    assert.equal(clickedUrl.searchParams.get("instrumentId"), "US-AAPL");
    assert.equal(clickedUrl.searchParams.get("period"), "day");

    const openResponsePromise = page.waitForResponse((response) => (
      response.request().method() === "POST"
      && new URL(response.url()).pathname === "/api/subsystems/basic/instruments/open"
    ), { timeout: 60000 });
    const clickStartedAt = Date.now();
    await Promise.all([
      page.waitForNavigation({ waitUntil: "domcontentloaded", timeout: 30000 }),
      page.click(linkSelector),
    ]);
    const clickNavigationMs = Date.now() - clickStartedAt;
    assert(clickNavigationMs < 5000, `Workspace navigation stalled for ${clickNavigationMs}ms`);
    const openResponse = await openResponsePromise;
    assert.equal(openResponse.status(), 202);
    const opened = await openResponse.json();
    assert.equal(opened.accepted, true);
    assert.equal(opened.instrument.instrumentId, "US-AAPL");
    assert.equal(containsField(opened, "bars"), false, "Open response leaked provider bars");

    const workspaceUrl = new URL(page.url());
    assert.equal(workspaceUrl.pathname, "/basic-workflow/workspace");
    assert.equal(workspaceUrl.searchParams.get("snapshotId"), market.body.snapshot.snapshotId);
    assert.equal(workspaceUrl.searchParams.get("instrumentId"), "US-AAPL");
    assert.equal(workspaceUrl.searchParams.get("period"), "day");
    await page.waitForSelector("#bwChart canvas", { timeout: 60000 });
    await page.waitForFunction(() => (
      document.querySelector("#bwOpenStatus")?.dataset.state === "ready"
      && document.querySelector("#bwOpenStatus")?.textContent.includes("daily bars")
    ), { timeout: 60000 });
    const workspaceReadyMs = Date.now() - clickStartedAt;
    const workspaceTheme = await lightThemeEvidence(page, ".bw-chart-panel");
    assert.equal(workspaceTheme.bodyClass, "basic-workspace-route");
    const workspaceChrome = await basicChromeEvidence(page, "/basic-workflow");

    const saveRequest = opened.materialization.visualizationSaveRequest;
    const backtestId = opened.job.backtestId;
    const initialRecord = await currentVisualization(page, backtestId, saveRequest.visualizationId);
    assert.equal(initialRecord.revision, 1);
    const drawingDiagnostic = DRAWING_DIAGNOSTIC
      ? await runBasicDrawingDiagnostic(page, apiRequests, backtestId, saveRequest.visualizationId)
      : null;
    if (!DRAWING_DIAGNOSTIC) {
      await drawTrendLine(page);
      await waitForDrawingSaved(page);
    }
    const finalRecord = drawingDiagnostic?.finalRecord
      || await currentVisualization(page, backtestId, saveRequest.visualizationId);
    assert.equal(finalRecord.revision, DRAWING_DIAGNOSTIC ? 5 : 2);
    const drawings = finalRecord.spec.panes[0].visualizers
      .filter((item) => item.callback === "drawing.trendLine");
    assert.equal(drawings.length, DRAWING_DIAGNOSTIC ? 4 : 1);
    assert(drawings.every((item) => item.params.targetVisualizerId === "market-candles"));

    const workspaceCanvasCount = await page.$$eval("#bwChart canvas", (items) => items.length);
    assert(workspaceCanvasCount > 0);
    assert.equal(
      await page.$eval("#bwChartDataset", (item) => item.textContent),
      "AAPL · 1D bars · 5 records",
    );
    assert.equal(
      await page.$eval("#bwChartPipeline", (item) => item.textContent),
      "Basic market analysis · 1D",
    );
    assert.equal(await page.$eval("#bwChartBacktest", (item) => item.textContent), "Completed");
    const workspaceIdentityAudit = await visibleIdentityAudit(
      page,
      "Basic workspace",
      collectOpaqueValues(finalRecord, "", collectOpaqueValues(opened, "", collectOpaqueValues(market.body))),
    );
    await page.screenshot({ path: WORKSPACE_SCREENSHOT, fullPage: true });

    const returnedMarketPromise = page.waitForResponse((response) => (
      response.request().method() === "GET"
      && new URL(response.url()).pathname === "/api/subsystems/basic/market"
    ), { timeout: 30000 });
    await Promise.all([
      page.waitForNavigation({ waitUntil: "domcontentloaded", timeout: 30000 }),
      page.click(".topbar .bw-header-link"),
    ]);
    const returnedMarketResponse = await returnedMarketPromise;
    assert.equal(returnedMarketResponse.status(), 200);
    const returnedMarket = await returnedMarketResponse.json();
    assert.equal(returnedMarket.snapshot.snapshotId, market.body.snapshot.snapshotId);
    assert.equal(returnedMarket.barSnapshots.length, 1, JSON.stringify(returnedMarket.barSnapshots));
    const aaplBarSnapshot = returnedMarket.barSnapshots[0];
    assert.equal(aaplBarSnapshot.catalogSnapshotId, market.body.snapshot.snapshotId);
    assert.equal(aaplBarSnapshot.providerId, "nasdaq-us-snapshot");
    assert.equal(aaplBarSnapshot.instrumentId, "US-AAPL");
    assert.equal(aaplBarSnapshot.period, "day");
    assert.equal(aaplBarSnapshot.lastTime, "2026-01-08T21:00:00Z");
    assert.equal(aaplBarSnapshot.datasetId, opened.materialization.dataset.datasetId);
    assert.equal(aaplBarSnapshot.datasetVersionId, opened.materialization.dataset.datasetVersionId);
    assert.equal(aaplBarSnapshot.contentDigest, opened.barSnapshot.contentDigest);
    await page.waitForSelector('#bwInstrumentList [data-instrument-id="US-AAPL"] .bw-data-as-of');
    await page.waitForFunction((lastTime) => {
      const cell = document.querySelector('#bwInstrumentList [data-instrument-id="US-AAPL"] .bw-data-as-of');
      return cell?.title === lastTime && cell.textContent === new Date(lastTime).toLocaleString();
    }, {}, aaplBarSnapshot.lastTime);
    const returnedDataAsOf = await page.evaluate(() => Object.fromEntries(
      ["US-AAPL", "US-MSFT"].map((instrumentId) => {
        const cell = document.querySelector(
          `#bwInstrumentList [data-instrument-id="${instrumentId}"] .bw-data-as-of`,
        );
        return [instrumentId, { text: cell?.textContent, title: cell?.getAttribute("title") || "" }];
      }),
    ));
    assert.equal(returnedDataAsOf["US-AAPL"].title, aaplBarSnapshot.lastTime);
    assert(returnedDataAsOf["US-AAPL"].text);
    assert.deepEqual(returnedDataAsOf["US-MSFT"], { text: "", title: "" });
    assert.equal(new URL(page.url()).pathname, "/basic-workflow");
    const returnedHomeUrl = page.url();
    assert.equal(await page.$("#bwChart"), null);
    const returnedCanvasCount = await page.$$eval("canvas", (items) => items.length);
    assert.equal(returnedCanvasCount, 0);
    const returnedTheme = await lightThemeEvidence(page, ".bw-market-panel");
    const returnedIdentityAudit = await visibleIdentityAudit(
      page,
      "Basic returned home",
      collectOpaqueValues(returnedMarket, "", collectOpaqueValues(opened, "", collectOpaqueValues(market.body))),
    );
    await page.screenshot({ path: RETURNED_SCREENSHOT, fullPage: true });

    phase = "identity-audit";
    const canonicalOpaqueValues = collectOpaqueValues(
      returnedMarket,
      "",
      collectOpaqueValues(finalRecord, "", collectOpaqueValues(opened, "", collectOpaqueValues(market.body))),
    );
    await page.goto(`${ORIGIN}/result?backtestId=${encodeURIComponent(backtestId)}`, {
      waitUntil: "networkidle2",
      timeout: 60000,
    });
    await page.waitForFunction((expected) => (
      window.__tradeState?.selectedBacktest?.backtestId === expected
      && document.querySelector("#results")?.classList.contains("active")
      && document.querySelectorAll("#chartArea canvas").length > 0
    ), { timeout: 60000 }, backtestId);
    const genericResultIdentityAudit = await visibleIdentityAudit(
      page,
      "Generic Result",
      canonicalOpaqueValues,
    );
    await page.screenshot({ path: GENERIC_RESULT_SCREENSHOT, fullPage: true });
    await new Promise((resolve) => setTimeout(resolve, 1200));
    await page.waitForNetworkIdle({ idleTime: 500, timeout: 10000 });
    const genericFinalRecord = await currentVisualization(
      page,
      backtestId,
      saveRequest.visualizationId,
    );
    assert(genericFinalRecord.revision >= finalRecord.revision);
    assert.equal(
      genericFinalRecord.spec.panes[0].visualizers.some((item) => item.id === drawings[0].id),
      true,
      "Generic Result view persistence overwrote the Basic drawing",
    );

    await page.goto(`${ORIGIN}/overview`, { waitUntil: "networkidle2", timeout: 30000 });
    await page.waitForFunction(() => (
      document.querySelector("#overview")?.classList.contains("active")
      && document.querySelectorAll("#repoList .overview-card").length > 0
    ), { timeout: 30000 });
    const overviewIdentityAudit = await visibleIdentityAudit(
      page,
      "Trade Engine overview",
      canonicalOpaqueValues,
    );
    await page.screenshot({ path: OVERVIEW_SCREENSHOT, fullPage: true });

    const resourceIdentityAudits = [];
    for (const resource of [
      {
        route: "data", containerId: "dataRepositoryBrowser", label: "Dataset repository",
        screenshot: RESOURCE_SCREENSHOT,
      },
      {
        route: "pipeline", containerId: "pipelineRepositoryBrowser", label: "Pipeline repository",
        screenshot: PIPELINE_RESOURCE_SCREENSHOT,
      },
      {
        route: "backtests", containerId: "backtestResourceBrowser", label: "Backtest repository",
        screenshot: BACKTEST_RESOURCE_SCREENSHOT,
      },
    ]) {
      await page.goto(`${ORIGIN}/${resource.route}`, { waitUntil: "networkidle2", timeout: 30000 });
      await selectFirstResource(page, resource.containerId);
      const identity = await visibleIdentityAudit(
        page,
        `${resource.label} list and details`,
        canonicalOpaqueValues,
      );
      resourceIdentityAudits.push({ ...identity, screenshot: resource.screenshot });
      await page.screenshot({ path: resource.screenshot, fullPage: true });
    }

    const openRequests = apiRequests.filter((request) => (
      request.path === "/api/subsystems/basic/instruments/open"
    ));
    assert.equal(openRequests.length, 1, JSON.stringify(openRequests));
    assert.equal(openRequests[0].phase, "workspace", JSON.stringify(openRequests));
    const resultRequests = apiRequests.filter((request) => (
      request.method === "POST" && request.path === `/api/backtests/${backtestId}/result`
    ));
    assert(resultRequests.length >= 1, JSON.stringify(resultRequests));
    const exactCandlePaths = ["cycles.data.price.day.US-AAPL"];
    const expectedDiscoveryPaths = [
      "cycles.data.price",
      "cycles.data.execution.orders",
      "cycles.data.intent.approved",
      "cycles.data.intent.requested",
      "cycles.data.signal.scores",
      "cycles.data.universe.selected",
      "cycles.data.portfolio.account.positions",
    ];
    const isExactCandleSlice = (request) => (
      JSON.stringify(request.payload?.paths) === JSON.stringify(exactCandlePaths)
    );
    const workspaceResultRequests = resultRequests.filter((request) => (
      request.phase === "workspace" && isExactCandleSlice(request)
    ));
    const identityResultRequests = resultRequests.filter((request) => (
      request.phase === "identity-audit" && isExactCandleSlice(request)
    ));
    const identityDiscoveryRequests = resultRequests.filter((request) => (
      request.phase === "identity-audit" && !isExactCandleSlice(request)
    ));
    resultRequests.forEach((request) => assert.deepEqual(request.payload.temporaryModules, []));
    assert(workspaceResultRequests.length >= 1, JSON.stringify(resultRequests));
    assert(identityResultRequests.length >= 1, JSON.stringify(resultRequests));
    assert.equal(identityDiscoveryRequests.length, 1, JSON.stringify(resultRequests));
    assert.deepEqual(identityDiscoveryRequests[0].payload.paths, expectedDiscoveryPaths);
    assert.equal(
      resultRequests.length,
      workspaceResultRequests.length + identityResultRequests.length + identityDiscoveryRequests.length,
      JSON.stringify(resultRequests),
    );
    const visualizationRequests = apiRequests.filter((request) => (
      request.path.startsWith("/api/visualizations")
    ));
    assert(visualizationRequests.length >= 4, JSON.stringify(visualizationRequests));
    assert(visualizationRequests.every((request) => (
      ["workspace", "identity-audit"].includes(request.phase)
    )), JSON.stringify(visualizationRequests));
    const workspaceRequests = apiRequests.filter((request) => request.phase === "workspace");
    const returnedHomeRequests = apiRequests.filter((request) => request.phase === "returned-home");
    assert.equal(
      returnedHomeRequests.some((request) => request.path === "/api/subsystems/basic/instruments/open"),
      false,
    );

    assert.deepEqual(pageErrors, []);
    assert.deepEqual(
      failedApiResponses,
      DRAWING_DIAGNOSTIC ? [{ status: 409, path: "/api/visualizations" }] : [],
    );
    assert.deepEqual(
      failedOriginResponses.filter((item) => (
        !(item.status === 404 && item.path === "/favicon.ico")
        && !(DRAWING_DIAGNOSTIC && item.status === 409 && item.path === "/api/visualizations")
      )),
      [],
    );
    const unexpectedConsoleErrors = consoleErrors.filter((message) => !expectedConsoleError(message));
    const expectedConsoleErrorCounts = {
      missingAgentSidecar: consoleErrors.filter((message) => (
        message.includes("WebSocket connection to 'ws://127.0.0.1:1/ws/ui' failed")
      )).length,
      fontCsp: consoleErrors.filter((message) => (
        message.includes("Loading the font 'data:font/") && message.includes("Content Security Policy")
      )).length,
      missingAsset: consoleErrors.filter((message) => (
        message.includes("server responded with a status of 404")
      )).length,
    };
    assert.deepEqual(unexpectedConsoleErrors, []);

    console.log(`BASIC_REAL_VNC_RESULT=${JSON.stringify({
      opened,
      finalRevision: genericFinalRecord.revision,
      audit: {
        clickedHref,
        home: homeAudit,
        workspace: {
          url: workspaceUrl.toString(),
          canvasCount: workspaceCanvasCount,
          requestCount: workspaceRequests.length,
          openRequestCount: openRequests.length,
          resultSliceRequestCount: workspaceResultRequests.length,
          visualizationRequestCount: visualizationRequests.length,
          savedRevision: finalRecord.revision,
          clickNavigationMs,
          readyMs: workspaceReadyMs,
          identity: workspaceIdentityAudit,
          chrome: workspaceChrome,
          theme: workspaceTheme,
          screenshot: WORKSPACE_SCREENSHOT,
          drawingId: drawings[0].id,
          drawingDiagnostic: drawingDiagnostic ? {
            mode: drawingDiagnostic.mode,
            initialRevision: drawingDiagnostic.initialRevision,
            success: drawingDiagnostic.success,
            conflict: drawingDiagnostic.conflict,
            resultSliceCountBefore: drawingDiagnostic.resultSliceCountBefore,
            resultSliceCountAfter: drawingDiagnostic.resultSliceCountAfter,
            observerEvidence: drawingDiagnostic.observerEvidence,
            visualizationNetwork: drawingDiagnostic.visualizationNetwork,
          } : null,
        },
        returnedHome: {
          url: returnedHomeUrl,
          canvasCount: returnedCanvasCount,
          requestCount: returnedHomeRequests.length,
          openRequestCount: returnedHomeRequests.filter(
            (request) => request.path === "/api/subsystems/basic/instruments/open",
          ).length,
          dataAsOf: returnedDataAsOf,
          barSnapshot: aaplBarSnapshot,
          identity: returnedIdentityAudit,
          theme: returnedTheme,
          screenshot: RETURNED_SCREENSHOT,
        },
        identitySurfaces: {
          genericResult: {
            ...genericResultIdentityAudit,
            resultSliceRequestCount: identityResultRequests.length,
            dataKeyDiscoveryRequestCount: identityDiscoveryRequests.length,
            savedRevision: genericFinalRecord.revision,
            screenshot: GENERIC_RESULT_SCREENSHOT,
          },
          overview: {
            ...overviewIdentityAudit,
            screenshot: OVERVIEW_SCREENSHOT,
          },
          resources: resourceIdentityAudits,
        },
        openRequests,
        resultRequests,
        visualizationRequestCount: visualizationRequests.length,
        apiFailureCount: failedApiResponses.length,
        controlledConflictCount: DRAWING_DIAGNOSTIC ? 1 : 0,
        unexpectedApiFailureCount: failedApiResponses.filter((item) => (
          !(DRAWING_DIAGNOSTIC && item.status === 409 && item.path === "/api/visualizations")
        )).length,
        pageErrorCount: pageErrors.length,
        unexpectedOriginFailureCount: failedOriginResponses
          .filter((item) => (
            !(item.status === 404 && item.path === "/favicon.ico")
            && !(DRAWING_DIAGNOSTIC && item.status === 409 && item.path === "/api/visualizations")
          )).length,
        unexpectedConsoleErrorCount: unexpectedConsoleErrors.length,
        expectedHarnessConsoleErrors: consoleErrors.length,
        expectedConsoleErrorCounts,
      },
    })}`);
  } finally {
    if (browser) await browser.close();
    fs.rmSync(userDataDir, { recursive: true, force: true });
  }
}

main().catch((error) => {
  console.error(error?.stack || error);
  process.exitCode = 1;
});
