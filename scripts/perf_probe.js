#!/usr/bin/env node
"use strict";

const assert = require("node:assert/strict");
const puppeteer = require("/tmp/trade-browser/node_modules/puppeteer-core");

const origin = process.env.TRADE_PERF_ORIGIN;
const workspaceUrl = process.env.TRADE_PERF_WORKSPACE_URL;
const sessionCookie = process.env.TRADE_PERF_SESSION_COOKIE;
const sessionToken = process.env.TRADE_PERF_SESSION_TOKEN;
const csrfCookie = process.env.TRADE_PERF_CSRF_COOKIE;
const csrfToken = process.env.TRADE_PERF_CSRF_TOKEN;

function required(value, label) {
  assert.equal(typeof value, "string", `${label} is required`);
  assert(value, `${label} is required`);
  return value;
}

function installProbe() {
  const probe = {
    fetches: [],
    statuses: [],
    chartOps: [],
    longTasks: [],
  };
  window.__tradePerf = probe;
  const epoch = () => performance.timeOrigin + performance.now();
  const originalFetch = window.fetch.bind(window);
  window.fetch = async (...args) => {
    const input = args[0];
    const options = args[1] || {};
    const requestUrl = typeof input === "string" ? input : input.url;
    const url = new URL(requestUrl, location.href);
    const record = {
      method: String(options.method || (typeof input === "object" && input.method) || "GET").toUpperCase(),
      path: `${url.pathname}${url.search}`,
      startEpochMs: epoch(),
      startMs: performance.now(),
      headersMs: null,
      bodyMs: null,
      totalMs: null,
      status: null,
      contentLength: null,
    };
    probe.fetches.push(record);
    const response = await originalFetch(...args);
    record.status = response.status;
    record.headersMs = performance.now() - record.startMs;
    record.contentLength = Number(response.headers.get("content-length")) || null;
    for (const method of ["json", "text", "arrayBuffer", "blob", "formData"]) {
      if (typeof response[method] !== "function") continue;
      const original = response[method].bind(response);
      response[method] = async (...bodyArgs) => {
        const bodyStarted = performance.now();
        try {
          return await original(...bodyArgs);
        } finally {
          record.bodyMs = performance.now() - bodyStarted;
          record.totalMs = performance.now() - record.startMs;
          record.endEpochMs = epoch();
        }
      };
    }
    return response;
  };

  function recordStatus(id) {
    const element = document.getElementById(id);
    if (!element) return;
    probe.statuses.push({
      id,
      text: element.textContent || "",
      state: element.dataset.state || "",
      epochMs: epoch(),
    });
  }

  function wrapSeries(series, family) {
    if (!series || series.__tradePerfWrapped) return series;
    try { Object.defineProperty(series, "__tradePerfWrapped", { value: true }); } catch { return series; }
    if (typeof series.setData === "function") {
      const original = series.setData.bind(series);
      series.setData = (points) => {
        const started = performance.now();
        try {
          return original(points);
        } finally {
          probe.chartOps.push({
            operation: "setData",
            family,
            points: Array.isArray(points) ? points.length : null,
            durationMs: performance.now() - started,
            epochMs: epoch(),
          });
        }
      };
    }
    return series;
  }

  function wrapChart(chart) {
    for (const method of ["addSeries", "addLineSeries", "addHistogramSeries", "addCandlestickSeries"]) {
      if (typeof chart?.[method] !== "function") continue;
      const original = chart[method].bind(chart);
      chart[method] = (...args) => wrapSeries(original(...args), method);
    }
    return chart;
  }

  function installChartHooks() {
    const library = window.LightweightCharts;
    if (library?.createChart && !library.createChart.__tradePerfWrapped) {
      const original = library.createChart.bind(library);
      const wrapped = (...args) => {
        const started = performance.now();
        try {
          return wrapChart(original(...args));
        } finally {
          probe.chartOps.push({
            operation: "createChart",
            durationMs: performance.now() - started,
            epochMs: epoch(),
          });
        }
      };
      Object.defineProperty(wrapped, "__tradePerfWrapped", { value: true });
      library.createChart = wrapped;
    }
    const core = window.TradeChartCore;
    for (const method of [
      "normalizeVisualizationSpec",
      "visualizerDependencyPlan",
      "prepareFinancialPane",
      "paneTimeInfo",
      "drawFinancialPane",
    ]) {
      if (typeof core?.[method] !== "function" || core[method].__tradePerfWrapped) continue;
      const original = core[method].bind(core);
      const wrapped = (...args) => {
        const started = performance.now();
        try {
          const result = original(...args);
          if (method === "drawFinancialPane" && typeof result?.reconcile === "function") {
            const reconcile = result.reconcile.bind(result);
            return Object.freeze({
              ...result,
              reconcile(...reconcileArgs) {
                const reconcileStarted = performance.now();
                try { return reconcile(...reconcileArgs); }
                finally {
                  probe.chartOps.push({
                    operation: "reconcileFinancialPane",
                    durationMs: performance.now() - reconcileStarted,
                    epochMs: epoch(),
                  });
                }
              },
            });
          }
          return result;
        } finally {
          probe.chartOps.push({
            operation: method,
            durationMs: performance.now() - started,
            epochMs: epoch(),
          });
        }
      };
      Object.defineProperty(wrapped, "__tradePerfWrapped", { value: true });
      core[method] = wrapped;
    }
  }

  document.addEventListener("DOMContentLoaded", () => {
    installChartHooks();
    for (const id of ["bwOpenStatus", "bwIndicatorStatus"]) {
      recordStatus(id);
      const element = document.getElementById(id);
      if (element) new MutationObserver(() => recordStatus(id)).observe(element, {
        childList: true,
        characterData: true,
        subtree: true,
        attributes: true,
        attributeFilter: ["data-state"],
      });
    }
  });
  try {
    new PerformanceObserver((list) => {
      for (const item of list.getEntries()) {
        probe.longTasks.push({ startMs: item.startTime, durationMs: item.duration });
      }
    }).observe({ type: "longtask", buffered: true });
  } catch { /* Long Task API is optional. */ }
}

async function preparePage(browser) {
  const page = await browser.newPage();
  await page.setViewport({ width: 1600, height: 1000, deviceScaleFactor: 1 });
  await page.evaluateOnNewDocument(installProbe);
  await page.setCookie(
    { name: sessionCookie, value: sessionToken, url: origin, httpOnly: true },
    { name: csrfCookie, value: csrfToken, url: origin },
  );
  return page;
}

async function probeSnapshot(page) {
  return page.evaluate(() => structuredClone(window.__tradePerf));
}

async function waitReady(page) {
  await page.waitForSelector("#bwChart canvas", { timeout: 90000 });
  await page.waitForFunction(() => (
    document.querySelector("#bwOpenStatus")?.dataset.state === "ready"
    && document.querySelector("#bwOpenStatus")?.textContent.includes("daily bars")
    && document.querySelector("#bwChart")?.getAttribute("aria-busy") === "false"
  ), { timeout: 90000 });
}

function sliceProbe(probe, startedEpochMs) {
  return {
    fetches: probe.fetches.filter((item) => item.startEpochMs >= startedEpochMs),
    statuses: probe.statuses.filter((item) => item.epochMs >= startedEpochMs),
    chartOps: probe.chartOps.filter((item) => item.epochMs >= startedEpochMs),
    longTasks: probe.longTasks.filter((item) => (
      performance.timeOrigin + item.startMs >= startedEpochMs
    )),
  };
}

async function openWorkspace(page, label, { reload = false } = {}) {
  const startedEpochMs = Date.now();
  if (reload) {
    await page.reload({ waitUntil: "domcontentloaded", timeout: 30000 });
  } else {
    await page.goto(workspaceUrl, { waitUntil: "domcontentloaded", timeout: 30000 });
  }
  await waitReady(page);
  const readyEpochMs = Date.now();
  const probe = sliceProbe(await probeSnapshot(page), startedEpochMs);
  return {
    label,
    startedEpochMs,
    readyEpochMs,
    elapsedMs: readyEpochMs - startedEpochMs,
    status: await page.$eval("#bwOpenStatus", (item) => item.textContent || ""),
    paneCount: await page.$$eval(".bw-chart-pane", (items) => items.length),
    canvasCount: await page.$$eval("#bwChart canvas", (items) => items.length),
    probe,
  };
}

async function addBollinger(page, label) {
  await page.click("#bwOpenIndicators");
  await page.waitForSelector('[data-indicator-type-id="bb"]:not(:disabled)', { timeout: 10000 });
  await page.click('[data-indicator-type-id="bb"]');
  await page.waitForFunction(() => !document.querySelector("#bwIndicatorForm")?.hidden);
  const startedEpochMs = Date.now();
  await page.click("#bwIndicatorSubmit");
  await page.waitForFunction(() => (
    document.querySelector("#bwIndicatorStatus")?.textContent.includes("BB added")
    && document.querySelector("#bwChart")?.getAttribute("aria-busy") === "false"
  ), { timeout: 90000 });
  const readyEpochMs = Date.now();
  const probe = sliceProbe(await probeSnapshot(page), startedEpochMs);
  return {
    label,
    startedEpochMs,
    readyEpochMs,
    elapsedMs: readyEpochMs - startedEpochMs,
    status: await page.$eval("#bwIndicatorStatus", (item) => item.textContent || ""),
    active: await page.$$eval(".bw-active-indicator", (items) => items.map((item) => item.textContent)),
    probe,
  };
}

async function removeBollinger(page, label) {
  const startedEpochMs = Date.now();
  const removed = await page.evaluate(() => {
    const item = [...document.querySelectorAll(".bw-active-indicator")]
      .find((candidate) => candidate.textContent.includes("BB"));
    const button = item?.querySelector(".bw-indicator-remove");
    if (!button) return false;
    button.click();
    return true;
  });
  assert.equal(removed, true, "BB remove control is unavailable");
  await page.waitForFunction(() => (
    document.querySelector("#bwIndicatorStatus")?.textContent.includes("removed.")
    && ![...document.querySelectorAll(".bw-active-indicator")]
      .some((item) => item.textContent.includes("BB"))
  ), { timeout: 90000 });
  const readyEpochMs = Date.now();
  return {
    label,
    startedEpochMs,
    readyEpochMs,
    elapsedMs: readyEpochMs - startedEpochMs,
    probe: sliceProbe(await probeSnapshot(page), startedEpochMs),
  };
}

async function main() {
  required(origin, "TRADE_PERF_ORIGIN");
  required(workspaceUrl, "TRADE_PERF_WORKSPACE_URL");
  required(sessionCookie, "TRADE_PERF_SESSION_COOKIE");
  required(sessionToken, "TRADE_PERF_SESSION_TOKEN");
  required(csrfCookie, "TRADE_PERF_CSRF_COOKIE");
  required(csrfToken, "TRADE_PERF_CSRF_TOKEN");
  const browser = await puppeteer.launch({
    headless: true,
    executablePath: "/usr/bin/google-chrome",
    args: ["--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage", "--no-proxy-server"],
    defaultViewport: { width: 1600, height: 1000 },
  });
  try {
    const primary = await preparePage(browser);
    const coldOpen = await openWorkspace(primary, "cold-open");
    const warmBrowserOpen = await openWorkspace(primary, "warm-browser-open", { reload: true });
    const coldBb = await addBollinger(primary, "cold-bb");
    const removeAfterCold = await removeBollinger(primary, "remove-after-cold");
    const warmBrowserBb = await addBollinger(primary, "warm-browser-bb");
    const removeAfterWarm = await removeBollinger(primary, "remove-after-warm");

    const serverWarm = await preparePage(browser);
    const warmServerOpen = await openWorkspace(serverWarm, "warm-server-open");
    const warmServerBb = await addBollinger(serverWarm, "warm-server-bb");
    console.log(`TRADE_PERF_RESULT=${JSON.stringify({
      runs: [
        coldOpen,
        warmBrowserOpen,
        coldBb,
        removeAfterCold,
        warmBrowserBb,
        removeAfterWarm,
        warmServerOpen,
        warmServerBb,
      ],
    })}`);
  } finally {
    await browser.close();
  }
}

main().catch((error) => {
  console.error(error?.stack || error);
  process.exitCode = 1;
});
