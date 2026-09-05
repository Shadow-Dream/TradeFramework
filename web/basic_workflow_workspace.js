"use strict";

const BASIC_PROTOCOL_ID = "trade.basic-workflow";
const PROJECTION_CACHE_STORAGE_KEY = "trade.basic-workflow.projections.v1";
const PROJECTION_CACHE_MAX_ENTRIES = 24;
const PROJECTION_CACHE_MAX_BYTES = 4_000_000;
const indicatorDescriptor = (typeId, label, shortLabel, category, moduleId, inputBindings, outputPorts, placement, configFields, series) => Object.freeze({
  typeId, label, shortLabel, category, moduleId, inputBindings: Object.freeze(inputBindings), outputPorts: Object.freeze(outputPorts), placement,
  configFields: Object.freeze(configFields.map((field) => Object.freeze(field))),
  series: Object.freeze(series.map((item) => Object.freeze(item))),
});
const line = (outputPort, color = "#2563eb", lineWidth = 2, extra = {}) => ({ outputPort, renderer: "series.line", color, lineWidth, ...extra });
const BASIC_INDICATORS = Object.freeze([
  indicatorDescriptor("sma", "Moving Average", "SMA", "Trend", "sma-indicator", { value: "close" }, ["sma"], "overlay", [{ name: "period", label: "Length" }], [line("sma")]),
  indicatorDescriptor("ema", "Exponential Moving Average", "EMA", "Trend", "ema-indicator", { value: "close" }, ["ema"], "overlay", [{ name: "period", label: "Length" }], [line("ema", "#f59e0b")]),
  indicatorDescriptor("wma", "Weighted Moving Average", "WMA", "Trend", "wma-indicator", { value: "close" }, ["wma"], "overlay", [{ name: "period", label: "Length" }], [line("wma", "#0f766e")]),
  indicatorDescriptor("vwma", "Volume Weighted Moving Average", "VWMA", "Trend", "vwma-indicator", { price: "close", volume: "volume" }, ["vwma"], "overlay", [{ name: "period", label: "Length" }], [line("vwma", "#0891b2")]),
  indicatorDescriptor("bb", "Bollinger Bands", "BB", "Volatility", "bollinger-bands-indicator", { price: "close" }, ["middle", "upper", "lower"], "overlay", [{ name: "period", label: "Length" }, { name: "k", label: "StdDev" }], [line("upper", "#7c3aed", 1), line("middle", "#8b5cf6", 1), line("lower", "#7c3aed", 1)]),
  indicatorDescriptor("rsi", "Relative Strength Index", "RSI", "Momentum", "rsi-indicator", { price: "close" }, ["rsi"], "oscillator", [{ name: "period", label: "Length" }], [line("rsi", "#7c3aed")]),
  indicatorDescriptor("macd", "Moving Average Convergence Divergence", "MACD", "Momentum", "macd-indicator", { price: "close" }, ["macd", "signal", "histogram"], "oscillator", [{ name: "fastPeriod", label: "Fast length" }, { name: "slowPeriod", label: "Slow length" }, { name: "signalPeriod", label: "Signal smoothing" }], [{ outputPort: "histogram", renderer: "series.histogram", color: "#64748b", positiveColor: "#089981", negativeColor: "#f23645" }, line("macd"), line("signal", "#f59e0b")]),
  indicatorDescriptor("atr", "Average True Range", "ATR", "Volatility", "atr-indicator", { high: "high", low: "low", close: "close" }, ["atr"], "oscillator", [{ name: "period", label: "Length" }], [line("atr", "#ea580c")]),
  indicatorDescriptor("stochastic", "Stochastic", "Stoch", "Momentum", "stochastic-indicator", { high: "high", low: "low", close: "close" }, ["k", "d"], "oscillator", [{ name: "period", label: "Length" }, { name: "dPeriod", label: "%D length" }, { name: "smoothKPeriod", label: "%K smoothing" }], [line("k"), line("d", "#f59e0b")]),
  indicatorDescriptor("obv", "On-Balance Volume", "OBV", "Volume", "obv-indicator", { close: "close", volume: "volume" }, ["obv"], "oscillator", [], [line("obv", "#16a34a")]),
  indicatorDescriptor("roc", "Rate of Change", "ROC", "Momentum", "roc-indicator", { price: "close" }, ["roc"], "oscillator", [{ name: "period", label: "Length" }], [line("roc", "#9333ea")]),
  indicatorDescriptor("cci", "Commodity Channel Index", "CCI", "Momentum", "cci-indicator", { source: "hlc3" }, ["cci"], "oscillator", [{ name: "period", label: "Length" }], [line("cci", "#2563eb")]),
  indicatorDescriptor("williams-r", "Williams %R", "Williams %R", "Momentum", "williams-r-indicator", { high: "high", low: "low", close: "close" }, ["williamsR"], "oscillator", [{ name: "period", label: "Length" }], [line("williamsR", "#db2777")]),
  indicatorDescriptor("dmi", "Directional Movement Index", "DMI/ADX", "Trend", "dmi-indicator", { high: "high", low: "low", close: "close" }, ["plusDI", "minusDI", "adx"], "oscillator", [{ name: "diPeriod", label: "DI length" }, { name: "adxSmoothing", label: "ADX smoothing" }], [line("plusDI"), line("minusDI", "#f59e0b"), line("adx", "#7c3aed")]),
  indicatorDescriptor("supertrend", "Supertrend", "Supertrend", "Trend", "supertrend-indicator", { high: "high", low: "low", close: "close" }, ["supertrend", "direction"], "overlay", [{ name: "atrPeriod", label: "ATR length" }, { name: "multiplier", label: "Multiplier" }], [line("supertrend", "#16a34a")]),
  indicatorDescriptor("mfi", "Money Flow Index", "MFI", "Volume", "mfi-indicator", { high: "high", low: "low", close: "close", volume: "volume" }, ["mfi"], "oscillator", [{ name: "period", label: "Length" }], [line("mfi", "#0891b2")]),
  indicatorDescriptor("parabolic-sar", "Parabolic SAR", "PSAR", "Trend", "parabolic-sar-indicator", { high: "high", low: "low", close: "close" }, ["sar", "direction"], "overlay", [{ name: "start", label: "Start" }, { name: "increment", label: "Increment" }, { name: "maximum", label: "Maximum" }], [{ outputPort: "sar", renderer: "series.scatter", color: "#2563eb" }]),
  indicatorDescriptor("volume", "Volume", "Volume", "Volume", "volume-indicator", { volume: "volume" }, ["volume"], "oscillator", [], [{ outputPort: "volume", renderer: "series.histogram", color: "#64748b" }]),
  indicatorDescriptor("anchored-vwap", "Anchored VWAP", "VWAP", "Volume", "anchored-vwap-indicator", { source: "hlc3", volume: "volume", reset: "resetMonth" }, ["vwap"], "overlay", [{ name: "anchor", label: "Anchor" }], [line("vwap", "#f59e0b")]),
  indicatorDescriptor("ichimoku", "Ichimoku Cloud", "Ichimoku", "Trend", "ichimoku-indicator", { high: "high", low: "low", close: "close" }, ["conversion", "base", "spanA", "spanB", "lagging"], "overlay", [{ name: "conversionPeriod", label: "Conversion" }, { name: "basePeriod", label: "Base" }, { name: "spanBPeriod", label: "Span B" }], [line("conversion"), line("base", "#f59e0b"), line("spanA", "#16a34a", 1, { offsetConfig: "basePeriod" }), line("spanB", "#dc2626", 1, { offsetConfig: "basePeriod" }), line("lagging", "#7c3aed", 1, { offsetConfig: "negativeBasePeriod" })]),
]);
const BASIC_INDICATOR_MODULES = new Map(BASIC_INDICATORS.map((indicator) => (
  [indicator.moduleId, "Signal"]
)));
const BASIC_INDICATOR_SOURCE = Object.freeze({
  moduleId: "basic-price-bar-selector",
  instanceId: "basic-indicator-price-close",
  outputKey: "indicator.source.close",
  outputPorts: Object.freeze(["eventTime", "open", "high", "low", "close", "hlc3", "volume", "resetDataset", "resetWeek", "resetMonth", "resetYear"]),
});

const state = {
  csrfToken: "",
  catalog: null,
  catalogPromise: null,
  market: null,
  selectedInstrumentId: "",
  period: "day",
  watchlistBusy: false,
  openBusy: false,
  openSeq: 0,
  activeOpen: null,
  resultView: null,
  visualizationSaveRequest: null,
  visualizationCanonical: null,
  visualizationPresentationSpec: null,
  pendingDrawingMutations: [],
  drawingSaveInFlight: null,
  drawingPersistenceFailure: null,
  drawingMutationSequence: 0,
  chart: null,
  chartContext: null,
  paneCharts: [],
  paneContexts: [],
  paneTimeSubscriptions: [],
  chartObserver: null,
  chartSubscription: null,
  selectedDrawingId: "",
  activeDrawingToolId: "",
  drawingBusy: false,
  indicatorBusy: false,
  indicatorDialogOpen: false,
  indicatorCatalogView: "technicals",
  indicatorSearch: "",
  indicatorDraft: null,
  indicatorFavorites: new Set(),
  projectionCache: new Map(),
  projectionCacheOwner: "",
  projectionControllers: new Set(),
  cacheEvidence: {
    sampleResultHit: false,
    browserProjectionHits: 0,
    serverProjectionHits: 0,
  },
};

const byId = (id) => document.getElementById(id);
const protocolOwned = (record) => Boolean(record) && record.protocolId === BASIC_PROTOCOL_ID;
const basicCatalogModule = (record) => Boolean(record) && (
  protocolOwned(record)
  || (
    !Object.hasOwn(record, "protocolId")
    && record.builtin === true
    && BASIC_INDICATOR_MODULES.get(record.moduleId) === record.kind
  )
);

function requireObject(value, label) {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new Error(`${label} must be an object.`);
  }
  return value;
}

function requireString(value, label) {
  if (typeof value !== "string" || !value || value !== value.trim()) {
    throw new Error(`${label} must be a non-empty canonical string.`);
  }
  return value;
}

function requireProtocol(value, label) {
  requireString(value, label);
  if (value !== BASIC_PROTOCOL_ID) throw new Error(`${label} is outside ${BASIC_PROTOCOL_ID}.`);
  return value;
}

function userFacingMessage(value, fallback) {
  const text = typeof value === "string" ? value.trim() : "";
  if (!text) return fallback;
  const opaqueIdentity = [
    /sha256:/i,
    /\b(?:snapshotId|catalogSnapshotId|contentDigest|datasetId|datasetVersionId|pipelineId|backtestId|visualizationId|visualizerId|instanceId|requestDigest|snapshotHash)\b/i,
    /\b(?:bt|job|pipe|viz|vis)_[0-9a-z_-]{8,}\b/i,
    /\b(?:basic-market-catalog|dataset|pipeline|backtest|visualization|visualizer|instance|snapshot)-[0-9a-z][0-9a-z._-]{5,}\b/i,
    /\b(?:Visualizer|Temporary Module|instance)\s+['"`][^'"`]+['"`]/i,
    /\b[0-9a-f]{24,}\b/i,
    /\b[0-9A-HJKMNP-TV-Z]{20,}\b/,
    /\b[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\b/i,
    /\bhash\b/i,
  ].some((pattern) => pattern.test(text));
  return opaqueIdentity ? fallback : text;
}

function requireContentDigest(value, label) {
  requireString(value, label);
  if (!/^sha256:[0-9a-f]{64}$/.test(value)) {
    throw new Error(`${label} must be a canonical sha256 digest.`);
  }
  return value;
}

function requireInstrument(value, label) {
  const instrument = requireObject(value, label);
  ["instrumentId", "symbol", "name", "exchange", "currency", "assetType"].forEach((field) => {
    requireString(instrument[field], `${label}.${field}`);
  });
  if (!Array.isArray(instrument.availablePeriods)
      || instrument.availablePeriods.some((period) => typeof period !== "string" || !period)) {
    throw new Error(`${label}.availablePeriods must be an array of period IDs.`);
  }
  return instrument;
}

function requireSnapshot(value, label = "Basic market snapshot") {
  const snapshot = requireObject(value, label);
  if (snapshot.schemaVersion !== 1) throw new Error(`${label}.schemaVersion must be 1.`);
  requireString(snapshot.snapshotId, `${label}.snapshotId`);
  requireProtocol(snapshot.protocolId, `${label}.protocolId`);
  ["providerId", "asOf", "contentDigest"].forEach((field) => {
    requireString(snapshot[field], `${label}.${field}`);
  });
  if (!Number.isInteger(snapshot.instrumentCount) || snapshot.instrumentCount < 0) {
    throw new Error(`${label}.instrumentCount must be a non-negative integer.`);
  }
  if (!Array.isArray(snapshot.instruments) || snapshot.instruments.length !== snapshot.instrumentCount) {
    throw new Error(`${label}.instruments does not match instrumentCount.`);
  }
  const ids = new Set();
  snapshot.instruments.forEach((instrument, index) => {
    requireInstrument(instrument, `${label}.instruments[${index}]`);
    if (ids.has(instrument.instrumentId)) throw new Error(`${label} contains duplicate instrument IDs.`);
    ids.add(instrument.instrumentId);
  });
  return snapshot;
}

function requireMarketEnvelope(value) {
  const envelope = requireObject(value, "Basic market response");
  requireProtocol(envelope.protocolId, "Basic market response.protocolId");
  if (envelope.snapshot !== null) requireSnapshot(envelope.snapshot);
  if (!Array.isArray(envelope.watchlist)) throw new Error("Basic market response.watchlist must be an array.");
  const snapshotIds = new Set((envelope.snapshot?.instruments || []).map((item) => item.instrumentId));
  const watchIds = new Set();
  envelope.watchlist.forEach((instrument, index) => {
    requireInstrument(instrument, `Basic market response.watchlist[${index}]`);
    if (!snapshotIds.has(instrument.instrumentId)) {
      throw new Error("Basic market watchlist contains an instrument outside its snapshot.");
    }
    if (watchIds.has(instrument.instrumentId)) throw new Error("Basic market watchlist contains duplicates.");
    watchIds.add(instrument.instrumentId);
  });
  return envelope;
}

function redirectToLogin() {
  const next = `${location.pathname}${location.search}${location.hash}`;
  location.replace(`/login?next=${encodeURIComponent(next)}`);
}

async function authenticatedFetch(path, options = {}) {
  const response = await fetch(path, {
    credentials: "same-origin",
    cache: "no-store",
    ...options,
  });
  if (response.status === 401) redirectToLogin();
  return response;
}

async function getJson(path) {
  const response = await authenticatedFetch(path, { headers: { Accept: "application/json" } });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload.error || `${path} returned ${response.status}`);
  return payload;
}

async function postJson(path, payload, { signal, projection = false } = {}) {
  const response = await authenticatedFetch(path, {
    method: "POST",
    headers: {
      Accept: "application/json",
      "Content-Type": "application/json",
      "X-CSRF-Token": state.csrfToken,
    },
    body: JSON.stringify(payload),
    ...(signal ? { signal } : {}),
  });
  const result = await (projection && window.TradeChartCore?.parseProjectionResponse
    ? window.TradeChartCore.parseProjectionResponse(response)
    : response.json()).catch(() => ({}));
  if (!response.ok || result.accepted === false) {
    const error = new Error(result.error || `${path} returned ${response.status}`);
    error.status = response.status;
    error.payload = result;
    throw error;
  }
  return result;
}

function setStatus(message, error = false) {
  const target = byId("bwStatus");
  target.textContent = userFacingMessage(message, error ? "The workspace action failed." : "Workspace ready");
  target.dataset.state = error ? "error" : "ready";
}

function setOpenStatus(message, error = false) {
  const target = byId("bwOpenStatus");
  target.textContent = userFacingMessage(message, error ? "The workspace action failed." : "Workspace ready");
  target.dataset.state = error ? "error" : "ready";
}

function showChartState(title, detail) {
  const target = byId("bwChartState");
  target.replaceChildren();
  const strong = document.createElement("strong");
  const span = document.createElement("span");
  strong.textContent = userFacingMessage(title, "Workspace unavailable");
  span.textContent = userFacingMessage(detail, "Open the full Result for technical details.");
  target.append(strong, span);
  target.hidden = false;
}

function hideChartState() {
  byId("bwChartState").hidden = true;
}

function formatInstant(value) {
  const instant = new Date(value);
  return Number.isNaN(instant.getTime()) ? value : instant.toLocaleString();
}

function marketInstrument(instrumentId) {
  return (state.market?.snapshot?.instruments || []).find((item) => item.instrumentId === instrumentId) || null;
}

function watchlistIds() {
  return new Set((state.market?.watchlist || []).map((item) => item.instrumentId));
}

function renderInstrumentHeader() {
  const instrument = marketInstrument(state.selectedInstrumentId);
  const favorite = byId("bwFavoriteInstrument");
  favorite.disabled = !instrument || state.watchlistBusy || state.openBusy;
  if (!instrument) {
    byId("bwInstrumentTitle").textContent = "Select a stock";
    byId("bwInstrumentMeta").textContent = "Every chart is backed by an immutable Trade Engine price snapshot.";
    favorite.textContent = "☆";
    favorite.setAttribute("aria-label", "Add selected stock to watchlist");
    return;
  }
  const watched = watchlistIds().has(instrument.instrumentId);
  byId("bwInstrumentTitle").textContent = `${instrument.symbol} · ${instrument.name}`;
  byId("bwInstrumentMeta").textContent = `${instrument.exchange} · ${instrument.assetType} · ${instrument.currency} · daily price snapshot`;
  favorite.textContent = watched ? "★" : "☆";
  favorite.setAttribute("aria-label", `${watched ? "Remove" : "Add"} ${instrument.symbol} ${watched ? "from" : "to"} watchlist`);
}

async function toggleSelectedWatchlist() {
  const snapshot = state.market?.snapshot;
  const instrument = marketInstrument(state.selectedInstrumentId);
  if (!snapshot || !instrument || state.watchlistBusy || state.openBusy) return;
  const ids = watchlistIds();
  if (ids.has(instrument.instrumentId)) ids.delete(instrument.instrumentId);
  else ids.add(instrument.instrumentId);
  state.watchlistBusy = true;
  renderInstrumentHeader();
  try {
    const response = await postJson("/api/subsystems/basic/watchlist", {
      snapshotId: snapshot.snapshotId,
      instrumentIds: [...ids].sort(),
    });
    requireProtocol(response.protocolId, "Watchlist response.protocolId");
    if (response.accepted !== true || response.snapshotId !== snapshot.snapshotId) {
      throw new Error("Watchlist response did not preserve the selected snapshot.");
    }
    if (!Array.isArray(response.watchlist)) {
      throw new Error("Watchlist response.watchlist must be an array.");
    }
    state.market.watchlist = response.watchlist.map((item) => structuredClone(item));
  } catch (error) {
    setStatus(error?.message || "Watchlist update failed.", true);
  } finally {
    state.watchlistBusy = false;
    renderInstrumentHeader();
  }
}

function resetProvenance() {
  byId("bwChartDataset").textContent = "Waiting for data";
  byId("bwChartPipeline").textContent = "Preparing Sampler cache";
  byId("bwChartBacktest").textContent = "Not required";
  byId("bwOpenResult").hidden = true;
}

function jobStatusLabel(value) {
  return ({
    queued: "Queued",
    running: "Running",
    completed: "Completed",
    failed: "Failed",
    cancelled: "Cancelled",
  })[value] || "Processing";
}

function renderProvenance(openResponse) {
  const symbol = openResponse.instrument.symbol;
  const bars = openResponse.barSnapshot;
  byId("bwChartDataset").textContent = `${symbol} · 1D bars · ${bars.barCount.toLocaleString()} records`;
  byId("bwChartPipeline").textContent = "Dataset → Sampler → sealed DataKeys";
  byId("bwChartBacktest").textContent = "Not required for chart";
  const link = byId("bwOpenResult");
  link.hidden = true;
}

function requireResourceRef(value, label, idField, { protocol = false, contentDigest = false } = {}) {
  const ref = requireObject(value, label);
  requireString(ref[idField], `${label}.${idField}`);
  requireString(ref.version, `${label}.version`);
  if (protocol) requireProtocol(ref.protocolId, `${label}.protocolId`);
  if (contentDigest) requireContentDigest(ref.contentDigest, `${label}.contentDigest`);
  return ref;
}

function requireOpenResponse(value, expected) {
  const response = requireObject(value, "Open instrument response");
  if (response.accepted !== true) throw new Error("Open instrument response was not accepted.");
  requireProtocol(response.protocolId, "Open instrument response.protocolId");
  if (response.snapshotId !== expected.snapshotId) throw new Error("Open instrument response changed snapshotId.");
  const instrument = requireInstrument(response.instrument, "Open instrument response.instrument");
  if (instrument.instrumentId !== expected.instrumentId) throw new Error("Open instrument response changed instrumentId.");
  if (!Array.isArray(response.watchlistInstrumentIds)
      || response.watchlistInstrumentIds.some((value) => typeof value !== "string" || !value)
      || new Set(response.watchlistInstrumentIds).size !== response.watchlistInstrumentIds.length) {
    throw new Error("Open instrument response.watchlistInstrumentIds must be unique strings.");
  }
  if (typeof response.ready !== "boolean") throw new Error("Open instrument response.ready must be boolean.");
  if (!response.ready) {
    const cacheJob = requireObject(response.cacheJob, "Open instrument response.cacheJob");
    ["jobId", "status", "instrumentId", "period"].forEach((field) => requireString(cacheJob[field], `Open instrument response.cacheJob.${field}`));
    if (cacheJob.instrumentId !== expected.instrumentId || cacheJob.period !== expected.period) {
      throw new Error("Open instrument cache job changed the selected instrument.");
    }
    return response;
  }
  const bars = requireObject(response.barSnapshot, "Open instrument response.barSnapshot");
  ["providerId", "asOf", "period", "firstTime", "lastTime", "contentDigest"].forEach((field) => {
    requireString(bars[field], `Open instrument response.barSnapshot.${field}`);
  });
  if (bars.period !== expected.period || !Number.isInteger(bars.barCount) || bars.barCount < 1) {
    throw new Error("Open instrument response contains an invalid bar snapshot.");
  }
  const materialization = requireObject(response.materialization, "Open instrument response.materialization");
  const dataset = requireObject(materialization.dataset, "Open instrument response.materialization.dataset");
  requireString(dataset.datasetId, "Materialized Dataset ID");
  requireString(dataset.datasetVersionId, "Materialized Dataset Version ID");
  requireProtocol(dataset.protocolId, "Materialized Dataset protocolId");
  requireResourceRef(materialization.sampler, "Materialized Sampler", "samplerId", { protocol: true });
  const sampleResult = requireObject(materialization.sampleResult, "Materialized Sample Result");
  requireContentDigest(sampleResult.sampleResultId, "Materialized Sample Result ID");
  requireContentDigest(sampleResult.resultContentDigest, "Materialized Sample Result digest");
  requireObject(sampleResult.dataKeys, "Materialized Sample Result dataKeys");
  if (sampleResult.datasetId !== dataset.datasetId || sampleResult.datasetVersionId !== dataset.datasetVersionId) {
    throw new Error("Materialized Sample Result Dataset identity changed.");
  }
  const saveRequest = requireObject(materialization.visualizationSaveRequest, "Materialized Visualization save request");
  ["sampleResultId", "visualizationId", "name"].forEach((field) => requireString(saveRequest[field], `Visualization save request.${field}`));
  if (saveRequest.expectedRevision !== 0) throw new Error("A new market Visualization must start at revision 0.");
  requireObject(saveRequest.spec, "Visualization save request.spec");
  if (saveRequest.sampleResultId !== sampleResult.sampleResultId) throw new Error("Visualization and Sample Result identities differ.");
  const cache = requireObject(response.cache, "Open instrument response.cache");
  if (cache.sampleResultHit !== true || Object.keys(cache).length !== 1) {
    throw new Error("Open instrument response.cache must confirm one Sample Result hit.");
  }
  return response;
}

function installOpenMarketContext(response) {
  const selected = response.instrument;
  state.market = {
    protocolId: BASIC_PROTOCOL_ID,
    snapshot: {
      snapshotId: response.snapshotId,
      instruments: [structuredClone(selected)],
    },
    watchlist: response.watchlistInstrumentIds.map((instrumentId) => (
      instrumentId === selected.instrumentId
        ? structuredClone(selected)
        : { instrumentId }
    )),
    barSnapshots: [],
    snapshotJobs: [],
  };
  renderInstrumentHeader();
}

function wait(milliseconds) {
  return new Promise((resolve) => window.setTimeout(resolve, milliseconds));
}


function canonicalJson(value) {
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(",")}]`;
  if (value && typeof value === "object") {
    return `{${Object.keys(value).sort().map((key) => `${JSON.stringify(key)}:${canonicalJson(value[key])}`).join(",")}}`;
  }
  return JSON.stringify(value);
}

function restoreProjectionCache(ownerId) {
  state.projectionCache.clear();
  state.projectionCacheOwner = ownerId;
  if (typeof sessionStorage === "undefined") return;
  try {
    const raw = sessionStorage.getItem(PROJECTION_CACHE_STORAGE_KEY);
    if (!raw) return;
    const stored = JSON.parse(raw);
    if (stored?.schemaVersion !== 1 || stored.ownerId !== ownerId
        || !Array.isArray(stored.entries)
        || stored.entries.length > PROJECTION_CACHE_MAX_ENTRIES) {
      sessionStorage.removeItem(PROJECTION_CACHE_STORAGE_KEY);
      return;
    }
    stored.entries.forEach((entry) => {
      if (!Array.isArray(entry) || entry.length !== 2 || typeof entry[0] !== "string") {
        throw new Error("Stored projection cache entry is invalid.");
      }
      state.projectionCache.set(entry[0], requireObject(entry[1], "Stored Result slice"));
    });
  } catch {
    state.projectionCache.clear();
    try { sessionStorage.removeItem(PROJECTION_CACHE_STORAGE_KEY); } catch { /* optional cache cleanup */ }
  }
}

function persistProjectionCache() {
  if (!state.projectionCacheOwner || typeof sessionStorage === "undefined") return;
  try {
    const entries = [...state.projectionCache.entries()]
      .filter(([, value]) => !value || typeof value.then !== "function")
      .slice(-PROJECTION_CACHE_MAX_ENTRIES);
    const raw = JSON.stringify({
      schemaVersion: 1,
      ownerId: state.projectionCacheOwner,
      entries,
    });
    if (raw.length <= PROJECTION_CACHE_MAX_BYTES) {
      sessionStorage.setItem(PROJECTION_CACHE_STORAGE_KEY, raw);
    }
  } catch {
    // Browser storage is an optional acceleration; canonical Result projection remains authoritative.
  }
}

function requireExactObjectFields(value, allowed, required, label) {
  const record = requireObject(value, label);
  const keys = Object.keys(record);
  if (keys.some((field) => !allowed.includes(field))
      || required.some((field) => !keys.includes(field))) {
    throw new Error(`${label} has an invalid exact shape.`);
  }
  return record;
}

function requireVisualizationSpecShape(value, label) {
  const spec = requireExactObjectFields(
    value,
    ["schemaVersion", "datasetId", "timeZone", "panes", "temporaryModules"],
    ["schemaVersion", "datasetId", "timeZone", "panes"],
    label,
  );
  if (spec.schemaVersion !== 3) throw new Error(`${label}.schemaVersion must be 3.`);
  requireString(spec.datasetId, `${label}.datasetId`);
  const timeZone = requireString(spec.timeZone, `${label}.timeZone`);
  try {
    new Intl.DateTimeFormat("en-US", { timeZone }).format(0);
  } catch {
    throw new Error(`${label}.timeZone must be a valid IANA time zone.`);
  }
  if (!Array.isArray(spec.panes)) throw new Error(`${label}.panes must be an array.`);
  if (spec.temporaryModules !== undefined && !Array.isArray(spec.temporaryModules)) {
    throw new Error(`${label}.temporaryModules must be an array.`);
  }
  const moduleFields = ["instanceId", "kind", "moduleId", "version", "config", "inputs", "outputs"];
  const requireModules = (modules, owner) => (modules || []).forEach((module, index) => {
    requireExactObjectFields(module, moduleFields, moduleFields, `${owner}[${index}]`);
  });
  requireModules(spec.temporaryModules, `${label}.temporaryModules`);
  const paneIds = new Set();
  spec.panes.forEach((paneValue, paneIndex) => {
    const paneLabel = `${label}.panes[${paneIndex}]`;
    const pane = requireExactObjectFields(
      paneValue,
      ["id", "title", "role", "view", "visualizers", "temporaryModules", "collapsed"],
      ["id", "title", "role", "view", "visualizers", "temporaryModules"],
      paneLabel,
    );
    const paneId = requireString(pane.id, `${paneLabel}.id`);
    if (paneIds.has(paneId)) throw new Error(`${label} Pane IDs must be unique.`);
    paneIds.add(paneId);
    requireString(pane.title, `${paneLabel}.title`);
    requireString(pane.role, `${paneLabel}.role`);
    if (pane.collapsed !== undefined && typeof pane.collapsed !== "boolean") {
      throw new Error(`${paneLabel}.collapsed must be a boolean.`);
    }
    const view = requireExactObjectFields(
      pane.view,
      ["start", "end", "logScale", "controlsCollapsed"],
      ["start", "end", "logScale", "controlsCollapsed"],
      `${paneLabel}.view`,
    );
    if (typeof view.logScale !== "boolean" || typeof view.controlsCollapsed !== "boolean") {
      throw new Error(`${paneLabel}.view flags must be booleans.`);
    }
    ["start", "end"].forEach((field) => {
      const item = view[field];
      if (item !== null && (typeof item === "boolean" || !["string", "number"].includes(typeof item))) {
        throw new Error(`${paneLabel}.view.${field} has an invalid type.`);
      }
    });
    if (!Array.isArray(pane.visualizers) || !Array.isArray(pane.temporaryModules)) {
      throw new Error(`${paneLabel} layers must be arrays.`);
    }
    const visualizerIds = new Set();
    pane.visualizers.forEach((instanceValue, index) => {
      const instanceLabel = `${paneLabel}.visualizers[${index}]`;
      const instance = requireExactObjectFields(
        instanceValue,
        ["id", "callback", "params", "visible"],
        ["id", "callback", "params"],
        instanceLabel,
      );
      const id = requireString(instance.id, `${instanceLabel}.id`);
      requireString(instance.callback, `${instanceLabel}.callback`);
      requireObject(instance.params, `${instanceLabel}.params`);
      if (visualizerIds.has(id)) throw new Error(`${paneLabel} Visualizer IDs must be unique.`);
      visualizerIds.add(id);
      if (instance.visible !== undefined && typeof instance.visible !== "boolean") {
        throw new Error(`${instanceLabel}.visible must be a boolean.`);
      }
    });
    requireModules(pane.temporaryModules, `${paneLabel}.temporaryModules`);
  });
  return spec;
}

function requireVisualizationRecord(value, request, label) {
  const fields = ["visualizationId", "sampleResultId", "name", "createdAt", "revision", "spec"];
  const record = requireExactObjectFields(value, fields, fields, label);
  if (record.visualizationId !== request.visualizationId
      || record.sampleResultId !== request.sampleResultId) {
    throw new Error(`${label} identity differs from the save request.`);
  }
  requireString(record.name, `${label}.name`);
  requireString(record.createdAt, `${label}.createdAt`);
  if (!Number.isSafeInteger(record.revision) || record.revision < 1) {
    throw new Error(`${label}.revision must be a positive safe integer.`);
  }
  const spec = requireVisualizationSpecShape(record.spec, `${label}.spec`);
  if (spec.datasetId !== request.spec?.datasetId) {
    throw new Error(`${label}.spec Dataset identity differs from the save request.`);
  }
  return record;
}

async function saveVisualizationRevision(request, seq) {
  if (!Number.isInteger(request.expectedRevision) || request.expectedRevision < 0) {
    throw new Error("Visualization expectedRevision must be a non-negative integer.");
  }
  let result;
  try {
    result = await postJson("/api/subsystems/basic/sample-visualizations/save", request);
  } catch (error) {
    if (error.status === 409 && error.payload?.code === "sample_visualization_revision_conflict") {
      const conflict = new Error(error.message);
      conflict.code = "sample_visualization_revision_conflict";
      conflict.currentVisualization = null;
      try {
        const current = requireVisualizationRecord(
          error.payload.visualization,
          request,
          "Conflicting Visualization record",
        );
        if (current.revision === request.expectedRevision) {
          throw new Error("Conflicting Visualization revision did not change.");
        }
        conflict.currentVisualization = structuredClone(current);
      } catch {
        // A malformed conflict record cannot replace the last confirmed canonical state.
      }
      throw conflict;
    }
    throw error;
  }
  if (seq !== state.openSeq) throw new Error("Stock selection changed.");
  if (result.accepted !== true) throw new Error("Visualization save was not accepted.");
  const record = requireVisualizationRecord(result.visualization, request, "Saved Visualization record");
  if (record.revision !== request.expectedRevision + 1
      || canonicalJson(record.spec) !== canonicalJson(request.spec)) {
    throw new Error("Visualization save returned invalid revision evidence.");
  }
  return structuredClone(record);
}

async function loadCurrentVisualization(request, seq) {
  const response = requireObject(
    await postJson("/api/subsystems/basic/sample-visualizations/list", {
      sampleResultId: request.sampleResultId,
    }),
    "Visualization repository response",
  );
  if (seq !== state.openSeq) throw new Error("Stock selection changed.");
  if (!Array.isArray(response.visualizations)) {
    throw new Error("Visualization repository response.visualizations must be an array.");
  }
  const matches = response.visualizations.filter((record) => (
    record?.visualizationId === request.visualizationId
  ));
  if (!matches.length) return null;
  if (matches.length !== 1 || response.currentVisualizationId !== request.visualizationId) {
    throw new Error("The current market Visualization identity is ambiguous.");
  }
  return structuredClone(requireVisualizationRecord(
    matches[0],
    request,
    "Current Visualization record",
  ));
}

function installCanonicalVisualization(request, record, { resetPresentation = false } = {}) {
  state.visualizationCanonical = {
    revision: record.revision,
    spec: structuredClone(record.spec),
  };
  state.visualizationSaveRequest = {
    ...structuredClone(request),
    expectedRevision: record.revision,
    spec: structuredClone(record.spec),
  };
  if (resetPresentation) {
    state.visualizationPresentationSpec = structuredClone(record.spec);
  }
}

function candleInstance(spec, instrumentId, period) {
  const expectedKey = `price.${period}.${instrumentId}`;
  const matches = (spec.panes || []).flatMap((pane) => (pane.visualizers || [])
    .filter((item) => item.callback === "ohlc.candles" && item.params?.dataKey === expectedKey)
    .map((item) => ({ pane, item })));
  if (matches.length !== 1) {
    throw new Error(`Visualization must bind exactly one Candles Visualizer to ${expectedKey}.`);
  }
  return matches[0];
}

function requireBasicCandlePreset(spec, instrumentId, period) {
  const candle = candleInstance(spec, instrumentId, period);
  const definitions = Array.isArray(state.catalog?.visualizers)
    ? state.catalog.visualizers.filter((definition) => definition?.id === candle.item.callback)
    : [];
  if (definitions.length !== 1) {
    throw new Error(`Candles callback '${candle.item.callback}' has no unique installed Visualizer definition.`);
  }
  requireProtocol(definitions[0].protocolId, `Visualizer definition '${candle.item.callback}'.protocolId`);
  return candle;
}

function moduleIdentity(instance) {
  return `${instance?.kind || ""}\u0000${instance?.moduleId || ""}\u0000${instance?.version || ""}`;
}

function basicTemporaryModule(instance) {
  const identity = moduleIdentity(instance);
  return (state.catalog?.modules || []).some((definition) => (
    basicCatalogModule(definition) && moduleIdentity(definition) === identity
  ));
}

function basicIndicator(indicatorTypeId) {
  const matches = BASIC_INDICATORS.filter((indicator) => indicator.typeId === indicatorTypeId);
  if (matches.length !== 1) throw new Error("The selected indicator is unavailable.");
  return matches[0];
}

function latestBasicModuleDefinition({ kind, moduleId, label, inputPorts, outputPorts }) {
  const candidates = (state.catalog?.modules || []).filter((definition) => (
    basicCatalogModule(definition)
    && definition.kind === kind
    && definition.moduleId === moduleId
    && typeof definition.version === "string"
    && /^[1-9][0-9]*$/.test(definition.version)
  ));
  if (!candidates.length) {
    throw new Error(`${label} has no installed Module definition.`);
  }
  const latestVersion = Math.max(...candidates.map((definition) => Number(definition.version)));
  const latest = candidates.filter((definition) => Number(definition.version) === latestVersion);
  if (latest.length !== 1) {
    throw new Error(`${label} has no unique latest Module definition.`);
  }
  const definition = latest[0];
  const inputs = definition.ports?.inputs;
  const outputs = definition.ports?.outputs;
  for (const inputPort of inputPorts) {
    if (!inputs || typeof inputs !== "object" || !inputs[inputPort]) {
      throw new Error(`${label} Module input contract is unavailable.`);
    }
  }
  for (const outputPort of outputPorts) {
    if (!outputs || typeof outputs !== "object" || !outputs[outputPort]) {
      throw new Error(`${label} Module output contract is unavailable.`);
    }
  }
  return definition;
}

function basicIndicatorModuleDefinition(indicator) {
  return latestBasicModuleDefinition({
    kind: "Signal",
    moduleId: indicator.moduleId,
    label: indicator.label,
    inputPorts: Object.keys(indicator.inputBindings),
    outputPorts: indicator.outputPorts,
  });
}

function basicResultHasRequiredVolume() {
  const barSchema = state.resultView?.dataKeys?.price?.schema
    ?.additionalProperties?.additionalProperties;
  return !!barSchema?.properties?.volume
    && Array.isArray(barSchema.required)
    && barSchema.required.includes("volume");
}

function basicIndicatorSourceDefinition(indicator) {
  if (basicResultHasRequiredVolume()) {
    return latestBasicModuleDefinition({
      kind: "Signal",
      moduleId: BASIC_INDICATOR_SOURCE.moduleId,
      label: "Basic OHLCV price selector",
      inputPorts: ["price"],
      outputPorts: BASIC_INDICATOR_SOURCE.outputPorts,
    });
  }
  const requiredSources = new Set(Object.values(indicator?.inputBindings || {}));
  if (!requiredSources.size || [...requiredSources].some((source) => source !== "close")) {
    throw new Error(`${indicator?.shortLabel || "Indicator"} requires an OHLCV snapshot.`);
  }
  return latestBasicModuleDefinition({ kind: "Signal", moduleId: "basic-price-close-selector", label: "Legacy Basic price selector", inputPorts: ["price"], outputPorts: ["close"] });
}
function sourceOutputPortsFor(definition) {
  return definition?.moduleId === BASIC_INDICATOR_SOURCE.moduleId ? BASIC_INDICATOR_SOURCE.outputPorts : ["close"];
}

function sourceOutputKey(port) { return port === "close" ? BASIC_INDICATOR_SOURCE.outputKey : `indicator.source.${port}`; }
function sourceInputBinding(source) { return sourceOutputKey(source); }
function indicatorInputBindings(indicator, config) {
  const bindings = { ...(indicator.inputBindings || {}) };
  if (indicator.typeId === "anchored-vwap") bindings.reset = `reset${String(config.anchor || "month").replace(/^./, (c) => c.toUpperCase())}`;
  return bindings;
}

function requireBasicSeriesDefinitions(indicator) {
  const callbacks = new Set(indicator.series.map((series) => series.renderer));
  for (const callback of callbacks) {
    const matches = (state.catalog?.visualizers || []).filter((definition) => (
      definition?.id === callback && protocolOwned(definition)
    ));
    if (matches.length !== 1) {
      throw new Error(`The Basic ${callback} Visualizer definition is unavailable.`);
    }
  }
}

function indicatorTemporaryModules(spec) {
  return [
    ...(spec.temporaryModules || []),
    ...(spec.panes || []).flatMap((pane) => pane.temporaryModules || []),
  ];
}

function indicatorDefinitionForInstance(instance, indicator) {
  const matches = (state.catalog?.modules || []).filter((definition) => (
    basicCatalogModule(definition)
    && moduleIdentity(definition) === moduleIdentity(instance)
  ));
  if (matches.length !== 1) {
    throw new Error(`${indicator.label} no longer has one exact installed Module definition.`);
  }
  return matches[0];
}

function indicatorSchemaDefaults(definition) {
  const properties = definition?.configSchema?.properties || {};
  return Object.fromEntries(Object.entries(properties)
    .filter(([, schema]) => Object.hasOwn(schema || {}, "default"))
    .map(([name, schema]) => [name, structuredClone(schema.default)]));
}

function indicatorFormSchema(indicator, definition) {
  const schema = structuredClone(definition.configSchema || {});
  const properties = schema.properties || {};
  indicator.configFields.forEach((field) => {
    if (!properties[field.name]) return;
    properties[field.name].title = field.label;
  });
  return schema;
}

function normalizeIndicatorConfig(definition, config, { defaults = false } = {}) {
  requireObject(config, "Indicator config");
  const schema = requireObject(definition?.configSchema || {}, "Indicator config schema");
  const properties = requireObject(schema.properties || {}, "Indicator config properties");
  if (schema.type !== "object" || schema.additionalProperties !== false) {
    throw new Error("The indicator config contract is unavailable.");
  }
  const result = defaults ? indicatorSchemaDefaults(definition) : {};
  for (const [name, value] of Object.entries(config)) {
    const property = properties[name];
    if (!property) throw new Error(`Unsupported indicator parameter '${name}'.`);
    if (property.type === "integer" && !Number.isInteger(value)) {
      throw new Error(`${name} must be an integer.`);
    }
    if (property.type === "number" && (typeof value !== "number" || !Number.isFinite(value))) {
      throw new Error(`${name} must be a finite number.`);
    }
    if (property.type === "string" && property.enum && !property.enum.includes(value)) {
      throw new Error(`${name} must be one of the supported values.`);
    }
    if (property.minimum !== undefined && value < property.minimum) {
      throw new Error(`${name} must be at least ${property.minimum}.`);
    }
    if (property.exclusiveMinimum !== undefined && value <= property.exclusiveMinimum) {
      throw new Error(`${name} must be greater than ${property.exclusiveMinimum}.`);
    }
    if (property.maximum !== undefined && value > property.maximum) {
      throw new Error(`${name} must be at most ${property.maximum}.`);
    }
    result[name] = value;
  }
  for (const name of schema.required || []) {
    if (!Object.hasOwn(result, name)) throw new Error(`${name} is required.`);
  }
  return result;
}

function indicatorOutputKeys(indicator, ordinal) {
  return Object.fromEntries(indicator.outputPorts.map((port) => [
    port,
    `indicator.basic.${indicator.typeId}.${ordinal}.${port}`,
  ]));
}

function nextIndicatorOrdinal(spec, indicator) {
  const modules = indicatorTemporaryModules(spec);
  const visualizers = (spec.panes || []).flatMap((pane) => pane.visualizers || []);
  for (let ordinal = 1; ordinal <= 10000; ordinal += 1) {
    const instanceId = `basic-indicator-${indicator.typeId}-${ordinal}`;
    const outputs = Object.values(indicatorOutputKeys(indicator, ordinal));
    const seriesIds = new Set(indicator.series.map((series) => `${instanceId}-${series.outputPort}`));
    const occupied = modules.some((module) => (
      module?.instanceId === instanceId
      || Object.values(module?.outputs || {}).some((key) => outputs.includes(key))
    )) || visualizers.some((item) => seriesIds.has(item?.id));
    if (!occupied) return ordinal;
  }
  throw new Error("No free Basic indicator instance is available.");
}

function addedIndicatorRecords(spec) {
  const seen = new Set();
  const visualizers = (spec.panes || []).flatMap((pane) => (pane.visualizers || [])
    .map((visualizer) => ({ pane, visualizer })));
  return indicatorTemporaryModules(spec).flatMap((module) => {
    if (module?.instanceId === BASIC_INDICATOR_SOURCE.instanceId) return [];
    if (typeof module?.instanceId !== "string" || !module.instanceId.startsWith("basic-indicator-")) return [];
    const matches = BASIC_INDICATORS.filter((indicator) => indicator.moduleId === module.moduleId);
    if (!matches.length) return [];
    if (matches.length !== 1 || module.kind !== "Signal" || seen.has(module.instanceId)) {
      throw new Error("A managed indicator instance is ambiguous.");
    }
    seen.add(module.instanceId);
    const indicator = matches[0];
    const definition = indicatorDefinitionForInstance(module, indicator);
    const config = normalizeIndicatorConfig(definition, module.config || {}, { defaults: true });
    const outputs = requireObject(module.outputs, `${indicator.label} outputs`);
    const outputNames = Object.keys(outputs).sort();
    if (JSON.stringify(outputNames) !== JSON.stringify([...indicator.outputPorts].sort())
        || indicator.outputPorts.some((port) => typeof outputs[port] !== "string" || !outputs[port])) {
      throw new Error(`${indicator.label} output bindings are malformed.`);
    }
    Object.entries(indicatorInputBindings(indicator, config)).forEach(([port, source]) => {
      const expected = sourceOutputKey(source);
      if (module.inputs?.[port] !== expected && !(source === "close" && module.inputs?.[port] === "indicator.source.close")) throw new Error(`${indicator.label} input binding is outside the Basic bar source.`);
    });
    const seriesPanes = new Set();
    for (const series of indicator.series) {
      const matches = visualizers.filter(({ visualizer }) => (
        visualizer?.callback === series.renderer
        && visualizer?.params?.dataKey === outputs[series.outputPort]
      ));
      if (matches.length !== 1) throw new Error(`${indicator.label} chart lines are malformed.`);
      seriesPanes.add(matches[0].pane);
    }
    if (seriesPanes.size !== 1) throw new Error(`${indicator.label} chart series must share one Pane.`);
    const [pane] = seriesPanes;
    if (indicator.placement === "overlay" && !pane.visualizers.some((item) => item.callback === "ohlc.candles")) {
      throw new Error(`${indicator.label} overlay is outside the market Pane.`);
    }
    if (indicator.placement === "oscillator" && pane.id !== `${module.instanceId}-pane`) {
      throw new Error(`${indicator.label} oscillator Pane identity is malformed.`);
    }
    return [{
      instanceId: module.instanceId,
      indicator,
      definition,
      module,
      config,
      outputs,
      pane,
    }];
  });
}

function requireIndicatorReservations(spec) {
  const instanceOwners = new Set();
  const outputOwners = new Set();
  let sourceCount = 0;
  let managedCount = 0;
  for (const module of indicatorTemporaryModules(spec)) {
    if (typeof module?.instanceId === "string") {
      if (instanceOwners.has(module.instanceId)) throw new Error("Temporary Module identities must be unique.");
      instanceOwners.add(module.instanceId);
    }
    for (const output of Object.values(module?.outputs || {})) {
      if (typeof output !== "string" || !output) continue;
      if (outputOwners.has(output)) throw new Error("Temporary Module output DataKeys must be unique.");
      outputOwners.add(output);
    }
    if (module?.instanceId === BASIC_INDICATOR_SOURCE.instanceId
        && (module.kind !== "Signal" || ![BASIC_INDICATOR_SOURCE.moduleId, "basic-price-close-selector"].includes(module.moduleId))) {
      throw new Error("The Basic price selector reserved Module identity is already in use.");
    }
    if (module?.instanceId === BASIC_INDICATOR_SOURCE.instanceId) {
      sourceCount += 1;
      const validLegacy = module.moduleId === "basic-price-close-selector"
        && module.outputs?.close === BASIC_INDICATOR_SOURCE.outputKey
        && Object.keys(module.outputs || {}).length === 1;
      const validBar = module.moduleId === BASIC_INDICATOR_SOURCE.moduleId
        && Object.keys(module.outputs || {}).length === BASIC_INDICATOR_SOURCE.outputPorts.length
        && BASIC_INDICATOR_SOURCE.outputPorts.every((port) => module.outputs?.[port] === sourceOutputKey(port));
      if (!validLegacy && !validBar) {
        throw new Error("The Basic price selector output binding is malformed.");
      }
    }
    if (typeof module?.instanceId === "string"
        && module.instanceId.startsWith("basic-indicator-")
        && BASIC_INDICATORS.some((indicator) => indicator.moduleId === module.moduleId)) {
      managedCount += 1;
    }
    if (module?.instanceId !== BASIC_INDICATOR_SOURCE.instanceId
        && Object.values(module?.outputs || {}).some((value) => BASIC_INDICATOR_SOURCE.outputPorts
          .map((port) => sourceOutputKey(port)).includes(value))) {
      throw new Error("The Basic price selector reserved output DataKey is already in use.");
    }
  }
  if (managedCount > 0 && sourceCount !== 1) {
    throw new Error("Saved indicators require one exact Basic price selector.");
  }
}

function indicatorDisplayLabel(record) {
  const values = record.indicator.configFields
    .map((field) => record.config[field.name])
    .filter((value) => value !== undefined)
    .map(String);
  return `${record.indicator.shortLabel}${values.length ? ` ${values.join(" · ")}` : ""}`;
}

function applyIndicatorMutation(spec, mutation) {
  requireObject(mutation, "Indicator mutation");
  if (!["add", "update", "remove"].includes(mutation.type)) {
    throw new Error("Indicator mutation type is unavailable.");
  }
  const nextSpec = structuredClone(spec);
  const candle = candleInstance(nextSpec, state.selectedInstrumentId, state.period);
  const priceKey = requireString(candle.item.params?.dataKey, "Candles dataKey");
  const timeDomainId = requireString(candle.item.params?.timeDomainId, "Candles timeDomainId");
  const priceScaleId = requireString(candle.item.params?.priceScaleId, "Candles priceScaleId");
  requireIndicatorReservations(nextSpec);
  if (priceKey !== `price.${state.period}.${state.selectedInstrumentId}`) {
    throw new Error("Candles dataKey is outside the selected Basic price map.");
  }
  const records = addedIndicatorRecords(nextSpec);
  let indicator;
  let definition;
  let instanceId;
  let outputs;
  let config;
  let existing = null;
  if (mutation.type === "add") {
    indicator = basicIndicator(mutation.indicatorTypeId);
    definition = basicIndicatorModuleDefinition(indicator);
    requireBasicSeriesDefinitions(indicator);
    const ordinal = nextIndicatorOrdinal(nextSpec, indicator);
    instanceId = `basic-indicator-${indicator.typeId}-${ordinal}`;
    outputs = indicatorOutputKeys(indicator, ordinal);
    config = normalizeIndicatorConfig(definition, mutation.config || {}, { defaults: true });
  } else {
    const matches = records.filter((record) => record.module.instanceId === mutation.instanceId);
    if (matches.length !== 1) throw new Error("The selected indicator instance is unavailable.");
    [existing] = matches;
    ({ indicator, definition, outputs } = existing);
    requireBasicSeriesDefinitions(indicator);
    instanceId = existing.module.instanceId;
    config = mutation.type === "update"
      ? normalizeIndicatorConfig(definition, mutation.config || {}, { defaults: true })
      : existing.config;
  }
  const sourceDefinition = basicIndicatorSourceDefinition(indicator);
  const targetOutputKeys = new Set(Object.values(outputs));
  const targetSeriesIds = new Set(indicator.series.map((series) => `${instanceId}-${series.outputPort}`));
  const conflictingSeries = (nextSpec.panes || []).flatMap((pane) => pane.visualizers || [])
    .some((item) => targetSeriesIds.has(item?.id) && !targetOutputKeys.has(item?.params?.dataKey));
  if (conflictingSeries) throw new Error(`${indicator.label} reserved chart identity is already in use.`);
  const rootTargetIndex = (nextSpec.temporaryModules || [])
    .findIndex((module) => module?.instanceId === instanceId);
  const targetPane = existing?.pane || candle.pane;
  const targetSeriesIndex = (targetPane.visualizers || [])
    .findIndex((item) => targetOutputKeys.has(item?.params?.dataKey));
  nextSpec.temporaryModules = (nextSpec.temporaryModules || [])
    .filter((module) => ![instanceId, BASIC_INDICATOR_SOURCE.instanceId]
      .includes(module?.instanceId));
  nextSpec.panes.forEach((pane) => {
    pane.temporaryModules = (pane.temporaryModules || [])
      .filter((module) => ![instanceId, BASIC_INDICATOR_SOURCE.instanceId]
        .includes(module?.instanceId));
    pane.visualizers = (pane.visualizers || [])
      .filter((visualizer) => !targetOutputKeys.has(visualizer?.params?.dataKey));
  });
  if (mutation.type === "remove" && indicator.placement === "oscillator") {
    nextSpec.panes = nextSpec.panes.filter((pane) => {
      if (pane.id !== `${instanceId}-pane`) return true;
      if ((pane.visualizers || []).length || (pane.temporaryModules || []).length) {
        throw new Error(`${indicator.label} oscillator Pane contains unmanaged layers.`);
      }
      return false;
    });
  }
  const keepSource = mutation.type !== "remove" || addedIndicatorRecords(nextSpec).length > 0;
  if (keepSource) nextSpec.temporaryModules.unshift({
    instanceId: BASIC_INDICATOR_SOURCE.instanceId,
    kind: sourceDefinition.kind,
    moduleId: sourceDefinition.moduleId,
    version: sourceDefinition.version,
    config: {
      decisionPeriod: state.period,
      instrumentId: state.selectedInstrumentId,
    },
    inputs: { price: "price" },
    outputs: Object.fromEntries(sourceOutputPortsFor(sourceDefinition).map((port) => [port, sourceOutputKey(port)])),
  });
  if (mutation.type === "remove") return nextSpec;
  const nextModule = {
    instanceId,
    kind: existing?.module.kind || definition.kind,
    moduleId: existing?.module.moduleId || definition.moduleId,
    version: existing?.module.version || definition.version,
    config,
    inputs: Object.fromEntries(Object.entries(indicatorInputBindings(indicator, config)).map(([port, source]) => [port, sourceInputBinding(source)])),
    outputs: structuredClone(outputs),
  };
  if (mutation.type === "update" && rootTargetIndex >= 0) {
    nextSpec.temporaryModules.splice(Math.min(rootTargetIndex, nextSpec.temporaryModules.length), 0, nextModule);
  } else {
    nextSpec.temporaryModules.push(nextModule);
  }
  let renderPane = candle.pane;
  if (indicator.placement === "oscillator") {
    const paneId = `${instanceId}-pane`;
    const matches = nextSpec.panes.filter((pane) => pane.id === paneId);
    if (matches.length > 1) throw new Error(`${indicator.label} oscillator Pane identity is ambiguous.`);
    if (!matches.length) {
      nextSpec.panes.push({
        id: paneId,
        title: indicator.shortLabel,
        role: "oscillator",
        view: { start: null, end: null, logScale: false, controlsCollapsed: false },
        visualizers: [],
        temporaryModules: [],
      });
    }
    renderPane = nextSpec.panes.find((pane) => pane.id === paneId);
  }
  const priceScaleIdForSeries = indicator.placement === "overlay"
    ? priceScaleId
    : `${instanceId}-scale`;
  const nextSeries = indicator.series.map((series) => ({
    id: `${instanceId}-${series.outputPort}`,
    callback: series.renderer,
    params: {
      dataKey: outputs[series.outputPort],
      timeKey: "time",
      timeDomainId,
      priceScaleId: priceScaleIdForSeries,
      color: series.color,
      ...(series.lineWidth === undefined ? {} : { lineWidth: series.lineWidth }),
      ...(series.positiveColor === undefined ? {} : { positiveColor: series.positiveColor }),
      ...(series.negativeColor === undefined ? {} : { negativeColor: series.negativeColor }),
      ...(series.offsetConfig === "basePeriod" ? { offsetBars: Number(config.basePeriod || 0) } : {}),
      ...(series.offsetConfig === "negativeBasePeriod" ? { offsetBars: -Number(config.basePeriod || 0) } : {}),
    },
  }));
  if (mutation.type === "update" && targetSeriesIndex >= 0) {
    renderPane.visualizers.splice(Math.min(targetSeriesIndex, renderPane.visualizers.length), 0, ...nextSeries);
  } else {
    renderPane.visualizers.push(...nextSeries);
  }
  return nextSpec;
}

function temporaryOutputOwners(instances) {
  const owners = new Map();
  for (const instance of instances || []) {
    for (const dataKey of Object.values(instance?.outputs || {})) {
      if (typeof dataKey !== "string" || !dataKey) continue;
      if (!owners.has(dataKey)) owners.set(dataKey, new Set());
      owners.get(dataKey).add(instance.instanceId);
    }
  }
  return owners;
}

function temporaryOwnersForBinding(binding, owners) {
  if (typeof binding !== "string" || !binding) return null;
  const matches = [...owners].filter(([dataKey]) => (
    binding === dataKey
    || binding.startsWith(`${dataKey}.`)
    || dataKey.startsWith(`${binding}.`)
  ));
  if (!matches.length) return null;
  return new Set(matches.flatMap(([, instanceIds]) => [...instanceIds]));
}

function closeBasicTemporaryModules(canonicalInstances, candidates) {
  const canonicalOwners = temporaryOutputOwners(canonicalInstances);
  const counts = new Map();
  candidates.forEach((instance) => counts.set(instance.instanceId, (counts.get(instance.instanceId) || 0) + 1));
  const kept = new Map(candidates
    .filter((instance) => typeof instance?.instanceId === "string" && counts.get(instance.instanceId) === 1)
    .map((instance) => [instance.instanceId, instance]));
  let changed = true;
  while (changed) {
    changed = false;
    for (const [instanceId, instance] of kept) {
      const blocked = Object.values(instance?.inputs || {}).some((binding) => {
        const owners = temporaryOwnersForBinding(binding, canonicalOwners);
        return owners && (owners.size !== 1 || !kept.has([...owners][0]));
      });
      if (blocked) {
        kept.delete(instanceId);
        changed = true;
      }
    }
  }
  return [...kept.values()];
}

function projectBasicVisualizers(visualizers, canonicalTemporaryModules, projectedTemporaryModules) {
  const definitions = new Map((state.catalog?.visualizers || [])
    .filter(protocolOwned)
    .map((definition) => [definition.id, definition]));
  const canonicalOwners = temporaryOutputOwners(canonicalTemporaryModules);
  const projectedIds = new Set(projectedTemporaryModules.map((instance) => instance.instanceId));
  const counts = new Map();
  visualizers.forEach((instance) => counts.set(instance?.id, (counts.get(instance?.id) || 0) + 1));
  const kept = new Map((visualizers || []).flatMap((instance) => {
    const definition = definitions.get(instance?.callback);
    if (!definition || counts.get(instance?.id) !== 1) return [];
    const readsExcludedTemporaryModule = Object.keys(definition.inputPorts || {}).some((portName) => {
      const owners = temporaryOwnersForBinding(instance?.params?.[portName], canonicalOwners);
      return owners && (owners.size !== 1 || !projectedIds.has([...owners][0]));
    });
    return readsExcludedTemporaryModule ? [] : [[instance.id, instance]];
  }));
  let changed = true;
  while (changed) {
    changed = false;
    for (const [visualizerId, instance] of kept) {
      const definition = definitions.get(instance.callback);
      const requirements = Array.isArray(definition?.capabilities?.requires)
        ? definition.capabilities.requires
        : [];
      const referencesExcludedVisualizer = requirements.some((requirement) => {
        const bindingParam = requirement?.bindingParam;
        return typeof bindingParam !== "string" || !bindingParam
          || typeof instance?.params?.[bindingParam] !== "string"
          || !kept.has(instance.params[bindingParam]);
      });
      if (referencesExcludedVisualizer) {
        kept.delete(visualizerId);
        changed = true;
      }
    }
  }
  return [...kept.values()];
}

function projectBasicVisualizationSpec(canonicalSpec) {
  const projected = structuredClone(canonicalSpec);
  const globalCanonical = canonicalSpec.temporaryModules || [];
  const globalCandidates = globalCanonical.filter(basicTemporaryModule);
  const projectedGlobal = closeBasicTemporaryModules(globalCanonical, globalCandidates);
  projected.temporaryModules = projectedGlobal;
  projected.panes = (canonicalSpec.panes || []).map((canonicalPane) => {
    const pane = structuredClone(canonicalPane);
    const paneCanonical = canonicalPane.temporaryModules || [];
    const combinedCanonical = [...globalCanonical, ...paneCanonical];
    const paneCandidates = paneCanonical.filter(basicTemporaryModule);
    const combinedProjected = closeBasicTemporaryModules(
      combinedCanonical,
      [...projectedGlobal, ...paneCandidates],
    );
    const globalIds = new Set(projectedGlobal.map((instance) => instance.instanceId));
    pane.temporaryModules = combinedProjected.filter((instance) => !globalIds.has(instance.instanceId));
    pane.visualizers = projectBasicVisualizers(
      canonicalPane.visualizers || [],
      combinedCanonical,
      combinedProjected,
    );
    return pane;
  });
  return projected;
}

function abortProjections() {
  state.projectionControllers.forEach((controller) => controller.abort());
  state.projectionControllers.clear();
}

function disposeChart({ clearCache = false } = {}) {
  abortProjections();
  try { state.chartSubscription?.(); } catch { /* chart listener cleanup */ }
  state.chartSubscription = null;
  for (const unsubscribe of state.paneTimeSubscriptions || []) {
    try { unsubscribe?.(); } catch { /* time-range listener cleanup */ }
  }
  state.paneTimeSubscriptions = [];
  try { state.chartObserver?.disconnect?.(); } catch { /* observer cleanup */ }
  state.chartObserver = null;
  for (const cleanup of state.chartContext?.cleanups || []) {
    try { cleanup(); } catch { /* cleanup isolation */ }
  }
  for (const context of state.paneContexts || []) {
    if (context === state.chartContext) continue;
    for (const cleanup of context?.cleanups || []) {
      try { cleanup(); } catch { /* cleanup isolation */ }
    }
  }
  state.chartContext = null;
  const charts = state.paneCharts?.length ? state.paneCharts : [state.chart].filter(Boolean);
  for (const chart of charts) {
    try { chart?.remove?.(); } catch { /* chart cleanup */ }
  }
  state.paneCharts = [];
  state.paneContexts = [];
  state.chart = null;
  byId("bwChart").replaceChildren();
  byId("bwDrawingTools").replaceChildren();
  if (clearCache) state.projectionCache.clear();
  state.selectedDrawingId = "";
}

function synchronizePaneTimeScales(charts) {
  const subscriptions = [];
  let applying = false;
  charts.forEach((source) => {
    const sourceScale = source.timeScale();
    if (typeof sourceScale.subscribeVisibleTimeRangeChange !== "function") return;
    const listener = (range) => {
      if (applying || !range) return;
      applying = true;
      try {
        charts.forEach((target) => {
          if (target !== source) target.timeScale().setVisibleRange?.(range);
        });
      } finally {
        applying = false;
      }
    };
    sourceScale.subscribeVisibleTimeRangeChange(listener);
    subscriptions.push(() => sourceScale.unsubscribeVisibleTimeRangeChange?.(listener));
  });
  return subscriptions;
}

function financialPaneLayout(spec, candle) {
  const panes = (spec.panes || []).filter((pane) => (
    (pane.visualizers || []).some((visualizer) => visualizer?.visible !== false)
  ));
  const mainIndex = panes.indexOf(candle.pane);
  if (mainIndex < 0) throw new Error("The market Pane has no visible chart layers.");
  if (mainIndex > 0) panes.unshift(...panes.splice(mainIndex, 1));
  return panes;
}

async function projectionForPlan(sampleResultId, plan, seq) {
  if (plan.planningError) throw new Error(plan.planningError.message || "Visualizer dependencies are invalid.");
  if (!(plan.paths || []).length && !(plan.temporaryModules || []).length) return null;
  const request = {
    paths: [...(plan.paths || [])].sort(),
    temporaryModules: structuredClone(plan.temporaryModules || []),
    projectionFormat: "columns-v2",
    window: null,
  };
  const cacheKey = `${sampleResultId}:${canonicalJson(request)}`;
  if (state.projectionCache.has(cacheKey)) {
    state.cacheEvidence.browserProjectionHits += 1;
    return state.projectionCache.get(cacheKey);
  }
  const controller = new AbortController();
  state.projectionControllers.add(controller);
  const pending = (async () => {
    const response = await postJson(
      "/api/subsystems/basic/sample-projections",
      { sampleResultId, ...request },
      { signal: controller.signal, projection: true },
    );
    if (seq !== state.openSeq) throw new Error("Stock selection changed.");
    if (response.cache?.hit === true) state.cacheEvidence.serverProjectionHits += 1;
    return requireObject(response.result, `Visualizer ${plan.visualizerId} Result slice`);
  })();
  state.projectionCache.set(cacheKey, pending);
  try {
    const result = await pending;
    state.projectionCache.set(cacheKey, result);
    persistProjectionCache();
    return result;
  } catch (error) {
    if (state.projectionCache.get(cacheKey) === pending) {
      state.projectionCache.delete(cacheKey);
    }
    throw error;
  } finally {
    state.projectionControllers.delete(controller);
  }
}

function projectionGroups(plans) {
  const groups = [];
  const byDependencies = new Map();
  plans.forEach((plan, index) => {
    const isolated = plan.planningError || (!(plan.paths || []).length && !(plan.temporaryModules || []).length);
    const key = isolated
      ? `isolated:${index}`
      : canonicalJson(plan.temporaryModules || []);
    let group = byDependencies.get(key);
    if (!group) {
      group = { members: [], paths: new Set(), temporaryModules: structuredClone(plan.temporaryModules || []) };
      byDependencies.set(key, group);
      groups.push(group);
    }
    group.members.push(plan);
    (plan.paths || []).forEach((path) => group.paths.add(path));
  });
  return groups.map((group) => ({
    members: group.members,
    plan: {
      visualizerId: group.members.map((plan) => plan.visualizerId).join(","),
      paths: [...group.paths].sort(),
      temporaryModules: group.temporaryModules,
      ...(group.members[0].planningError ? { planningError: group.members[0].planningError } : {}),
    },
  }));
}

async function projectPane(view, pane, spec, seq) {
  const core = window.TradeChartCore;
  const plans = core.visualizerDependencyPlan({ dataKeys: view.dataKeys }, pane, spec);
  const groupedSlices = await Promise.all(projectionGroups(plans).map(async (group) => {
    try {
      return { group, result: await projectionForPlan(view.sampleResultId, group.plan, seq), error: null };
    } catch (error) {
      return {
        group,
        result: null,
        error: {
          code: group.plan.planningError ? "instance-planning-error" : "instance-load-error",
          message: error?.message || "Visualizer Result slice could not be loaded.",
        },
      };
    }
  }));
  const instanceResults = Object.create(null);
  const errors = Object.create(null);
  groupedSlices.forEach(({ group, result, error }) => {
    group.members.forEach((plan) => {
      if (error) Object.defineProperty(errors, plan.visualizerId, {
        value: error, enumerable: true, configurable: true, writable: true,
      });
      if (result !== null) Object.defineProperty(instanceResults, plan.visualizerId, {
        value: result, enumerable: true, configurable: true, writable: true,
      });
    });
  });
  return { dataKeys: view.dataKeys, instanceResults, errors };
}

function setDrawingStatus(message, error = false) {
  const target = byId("bwDrawingStatus");
  target.textContent = userFacingMessage(
    message,
    error ? "The drawing action failed." : "Chart drawing controls are ready.",
  );
  target.dataset.state = error ? "error" : "ready";
}

function setIndicatorStatus(message, error = false) {
  const target = byId("bwIndicatorStatus");
  if (!target) return;
  target.textContent = userFacingMessage(
    message,
    error ? "The indicator action failed." : "Indicator overlays are ready.",
  );
  target.dataset.state = error ? "error" : "ready";
}

function syncChartBusyState() {
  const surface = byId("bwChart");
  if (!surface) return;
  surface.classList.toggle("drawing-persistence-busy", state.drawingBusy);
  surface.classList.toggle("indicator-persistence-busy", state.indicatorBusy);
  surface.setAttribute("aria-busy", String(state.drawingBusy || state.indicatorBusy));
}

function indicatorEditingBlocked() {
  return !state.resultView
    || !state.visualizationCanonical
    || !state.visualizationSaveRequest
    || state.indicatorBusy
    || state.drawingBusy
    || state.drawingPersistenceFailure !== null
    || state.drawingSaveInFlight !== null
    || state.pendingDrawingMutations.length > 0;
}

function renderIndicatorCatalog() {
  const root = byId("bwIndicatorCatalog");
  if (!root) return;
  root.replaceChildren();
  const query = state.indicatorSearch.trim().toLocaleLowerCase();
  const indicators = BASIC_INDICATORS.filter((indicator) => (
    (state.indicatorCatalogView !== "favorites" || state.indicatorFavorites.has(indicator.typeId))
    && (!query || [indicator.label, indicator.shortLabel, indicator.category]
      .some((value) => value.toLocaleLowerCase().includes(query)))
  ));
  if (!indicators.length) {
    const empty = document.createElement("p");
    empty.className = "bw-indicator-empty";
    empty.textContent = state.indicatorCatalogView === "favorites"
      ? "No favorite indicators match this search."
      : "No indicators match this search.";
    root.append(empty);
    return;
  }
  indicators.forEach((indicator) => {
    const row = document.createElement("div");
    row.className = "bw-indicator-catalog-row";
    row.setAttribute("role", "listitem");
    const favorite = document.createElement("button");
    favorite.type = "button";
    favorite.className = "bw-indicator-favorite";
    favorite.textContent = state.indicatorFavorites.has(indicator.typeId) ? "★" : "☆";
    favorite.setAttribute("aria-label", `${state.indicatorFavorites.has(indicator.typeId) ? "Unfavorite" : "Favorite"} ${indicator.label}`);
    favorite.addEventListener("click", () => {
      if (state.indicatorFavorites.has(indicator.typeId)) state.indicatorFavorites.delete(indicator.typeId);
      else state.indicatorFavorites.add(indicator.typeId);
      renderIndicatorCatalog();
    });
    const select = document.createElement("button");
    select.type = "button";
    select.className = "bw-indicator-catalog-item";
    select.dataset.indicatorTypeId = indicator.typeId;
    let unavailable = "";
    try {
      basicIndicatorModuleDefinition(indicator);
      basicIndicatorSourceDefinition(indicator);
      requireBasicSeriesDefinitions(indicator);
    } catch (error) {
      unavailable = error?.message || "Indicator definition is unavailable.";
    }
    const name = document.createElement("span");
    name.textContent = `${indicator.shortLabel} · ${indicator.label}`;
    const category = document.createElement("small");
    category.textContent = indicator.category;
    select.append(name, category);
    select.disabled = !!unavailable || indicatorEditingBlocked();
    select.title = unavailable || `Configure ${indicator.label}`;
    select.addEventListener("click", () => openIndicatorEditor(indicator.typeId));
    row.append(favorite, select);
    root.append(row);
  });
}

function renderIndicatorEditor() {
  const form = byId("bwIndicatorForm");
  if (!form) return;
  const draft = state.indicatorDraft;
  form.hidden = !draft;
  if (!draft) return;
  try {
    let indicator;
    let definition;
    let config;
    if (draft.type === "add") {
      indicator = basicIndicator(draft.indicatorTypeId);
      definition = basicIndicatorModuleDefinition(indicator);
      config = draft.config || indicatorSchemaDefaults(definition);
    } else {
      const spec = state.visualizationPresentationSpec || state.visualizationCanonical?.spec;
      const matches = addedIndicatorRecords(spec).filter((record) => record.module.instanceId === draft.instanceId);
      if (matches.length !== 1) throw new Error("The selected indicator instance is unavailable.");
      ({ indicator, definition, config } = matches[0]);
      config = draft.config || config;
    }
    const forms = window.TradeModuleForms;
    if (!forms?.renderSchemaFields) throw new Error("Indicator parameter controls are unavailable.");
    byId("bwIndicatorFormTitle").textContent = `${draft.type === "add" ? "Add" : "Settings"} · ${indicator.shortLabel}`;
    byId("bwIndicatorSubmit").textContent = draft.type === "add" ? "Add" : "Apply";
    byId("bwIndicatorSubmit").disabled = indicatorEditingBlocked();
    forms.renderSchemaFields(
      byId("bwIndicatorFields"),
      indicatorFormSchema(indicator, definition),
      config,
    );
  } catch (error) {
    state.indicatorDraft = null;
    form.hidden = true;
    setIndicatorStatus(error?.message || "Indicator settings are unavailable.", true);
  }
}

function renderIndicatorDialog() {
  const dialog = byId("bwIndicatorDialog");
  if (!dialog) return;
  dialog.hidden = !state.indicatorDialogOpen;
  if (!state.indicatorDialogOpen) return;
  byId("bwIndicatorFavorites").setAttribute("aria-selected", String(state.indicatorCatalogView === "favorites"));
  byId("bwIndicatorTechnicals").setAttribute("aria-selected", String(state.indicatorCatalogView === "technicals"));
  if (byId("bwIndicatorSearch").value !== state.indicatorSearch) {
    byId("bwIndicatorSearch").value = state.indicatorSearch;
  }
  renderIndicatorCatalog();
  renderIndicatorEditor();
}

function renderIndicatorTools() {
  const open = byId("bwOpenIndicators");
  const root = byId("bwActiveIndicators");
  if (!open || !root) return;
  open.disabled = indicatorEditingBlocked();
  open.setAttribute("aria-expanded", String(state.indicatorDialogOpen));
  root.replaceChildren();
  const spec = state.visualizationPresentationSpec || state.visualizationCanonical?.spec;
  if (spec) {
    try {
      addedIndicatorRecords(spec).forEach((record) => {
        const item = document.createElement("span");
        item.className = "bw-active-indicator";
        const label = document.createElement("span");
        label.textContent = indicatorDisplayLabel(record);
        const settings = document.createElement("button");
        settings.type = "button";
        settings.className = "bw-indicator-settings";
        settings.textContent = "⚙";
        settings.disabled = indicatorEditingBlocked();
        settings.setAttribute("aria-label", `Settings for ${indicatorDisplayLabel(record)}`);
        settings.addEventListener("click", () => openIndicatorEditor(null, record.module.instanceId));
        const remove = document.createElement("button");
        remove.type = "button";
        remove.className = "bw-indicator-remove";
        remove.textContent = "×";
        remove.disabled = indicatorEditingBlocked();
        remove.setAttribute("aria-label", `Remove ${indicatorDisplayLabel(record)}`);
        remove.addEventListener("click", () => void persistIndicatorMutation({
          type: "remove",
          instanceId: record.module.instanceId,
        }));
        item.append(label, settings, remove);
        root.append(item);
      });
    } catch (error) {
      open.disabled = true;
      setIndicatorStatus(error?.message || "Saved indicators are unavailable.", true);
    }
  }
  renderIndicatorDialog();
}

function setIndicatorBusy(busy) {
  state.indicatorBusy = !!busy;
  if (state.indicatorBusy) {
    state.chartContext?.interactionController?.cancel?.();
    state.activeDrawingToolId = "";
    state.selectedDrawingId = "";
  }
  syncChartBusyState();
  renderIndicatorTools();
  try { syncDrawingToolAvailability(); } catch { /* current chart diagnostics remain authoritative */ }
}

function openIndicatorDialog() {
  if (indicatorEditingBlocked()) {
    setIndicatorStatus("Finish the current chart change before editing indicators.", true);
    return false;
  }
  state.indicatorDialogOpen = true;
  renderIndicatorTools();
  byId("bwIndicatorSearch")?.focus();
  return true;
}

function closeIndicatorDialog() {
  state.indicatorDialogOpen = false;
  state.indicatorDraft = null;
  renderIndicatorTools();
  byId("bwOpenIndicators")?.focus();
}

function openIndicatorEditor(indicatorTypeId = null, instanceId = null) {
  if (indicatorEditingBlocked()) return false;
  state.indicatorDialogOpen = true;
  state.indicatorDraft = instanceId
    ? { type: "update", instanceId }
    : { type: "add", indicatorTypeId: basicIndicator(indicatorTypeId).typeId };
  renderIndicatorTools();
  byId("bwIndicatorFields")?.querySelector("input, select, textarea")?.focus();
  return true;
}

function cancelIndicatorEditor() {
  state.indicatorDraft = null;
  renderIndicatorTools();
}

async function submitIndicatorForm(event) {
  event?.preventDefault?.();
  const draft = state.indicatorDraft;
  if (!draft || indicatorEditingBlocked()) return false;
  try {
    let definition;
    if (draft.type === "add") {
      definition = basicIndicatorModuleDefinition(basicIndicator(draft.indicatorTypeId));
    } else {
      const matches = addedIndicatorRecords(state.visualizationCanonical.spec)
        .filter((record) => record.module.instanceId === draft.instanceId);
      if (matches.length !== 1) throw new Error("The selected indicator instance is unavailable.");
      definition = matches[0].definition;
    }
    const forms = window.TradeModuleForms;
    if (!forms?.readSchemaFields) throw new Error("Indicator parameter controls are unavailable.");
    const config = forms.readSchemaFields(byId("bwIndicatorFields"), definition.configSchema);
    state.indicatorDraft = { ...draft, config };
    return await persistIndicatorMutation({ ...draft, config });
  } catch (error) {
    setIndicatorStatus(error?.message || "Indicator parameters are invalid.", true);
    return false;
  }
}

async function persistIndicatorMutation(mutation) {
  if (indicatorEditingBlocked()) {
    setIndicatorStatus("Finish the current chart change before editing indicators.", true);
    return false;
  }
  let request;
  let actionLabel;
  try {
    const before = addedIndicatorRecords(state.visualizationCanonical.spec);
    if (mutation.type === "add") {
      actionLabel = basicIndicator(mutation.indicatorTypeId).shortLabel;
    } else {
      const matches = before.filter((record) => record.module.instanceId === mutation.instanceId);
      if (matches.length !== 1) throw new Error("The selected indicator instance is unavailable.");
      actionLabel = indicatorDisplayLabel(matches[0]);
    }
    request = {
      ...structuredClone(state.visualizationSaveRequest),
      expectedRevision: state.visualizationCanonical.revision,
      spec: applyIndicatorMutation(state.visualizationCanonical.spec, mutation),
    };
  } catch (error) {
    setIndicatorStatus(error?.message || "The indicator could not be prepared.", true);
    renderIndicatorTools();
    return false;
  }
  const seq = state.openSeq;
  const action = mutation.type === "add" ? "Adding" : mutation.type === "update" ? "Updating" : "Removing";
  setIndicatorStatus(`${action} ${actionLabel}…`);
  setIndicatorBusy(true);
  const projectionOutcome = prepareCurrentChartProjection(seq, request.spec).then(
    (value) => ({ value, error: null }),
    (error) => ({ value: null, error }),
  );
  let record;
  let conflict = false;
  try {
    record = await saveVisualizationRevision(request, seq);
  } catch (error) {
    if (seq !== state.openSeq) return false;
    if (error?.code === "sample_visualization_revision_conflict" && error.currentVisualization) {
      record = error.currentVisualization;
      conflict = true;
    } else {
      setIndicatorStatus(error?.message || "The indicator change could not be saved.", true);
      return false;
    }
  } finally {
    if (!record && seq === state.openSeq) setIndicatorBusy(false);
  }
  if (seq !== state.openSeq || !record) return false;
  installCanonicalVisualization(request, record, { resetPresentation: true });
  state.indicatorDraft = null;
  try {
    let prepared = null;
    if (!conflict) {
      const outcome = await projectionOutcome;
      if (outcome.error) throw outcome.error;
      prepared = outcome.value;
    }
    await renderCurrentChart(seq, prepared);
    setIndicatorStatus(
      conflict
        ? "Visualization changed elsewhere. Loaded the current server indicators without merging."
        : `${actionLabel} ${mutation.type === "add" ? "added" : mutation.type === "update" ? "updated" : "removed"}.`,
      conflict,
    );
  } catch (error) {
    setIndicatorStatus(
      conflict
        ? "The current server indicators were loaded but could not be displayed."
        : `${actionLabel} was saved but could not be displayed.`,
      true,
    );
    setOpenStatus(error?.message || "Saved indicator layers could not be displayed.", true);
  } finally {
    if (seq === state.openSeq) setIndicatorBusy(false);
  }
  return !conflict;
}

function toolBindings(tool, baseVisualizerId) {
  const result = Object.create(null);
  for (const binding of tool.bindings || []) {
    const exact = (binding.candidates || []).filter((candidate) => candidate.visualizerId === baseVisualizerId);
    if (exact.length !== 1) return null;
    result[binding.name] = exact[0].visualizerId;
  }
  return result;
}

function renderDrawingTools(controller, baseVisualizerId) {
  const root = byId("bwDrawingTools");
  root.replaceChildren();
  const tools = controller?.listTools?.().tools || [];
  const records = tools.map((tool) => ({ tool, bindings: toolBindings(tool, baseVisualizerId) }));
  const available = records.filter((record) => record.bindings !== null);
  if (!available.some((record) => record.tool.id === state.activeDrawingToolId)) {
    state.activeDrawingToolId = available[0]?.tool.id || "";
  }
  const buttons = [];
  const syncActiveButtons = () => buttons.forEach((button) => {
    const active = button.dataset.drawingTool === state.activeDrawingToolId;
    button.classList.toggle("active", active);
    button.setAttribute("aria-pressed", String(active));
  });
  const activate = (record, { announce = true } = {}) => {
    if (state.drawingBusy || state.indicatorBusy || record.bindings === null) return false;
    if (controller.activate(record.tool.id, { bindings: record.bindings }) !== true) {
      if (announce) setDrawingStatus("The selected drawing tool is unavailable for this chart.", true);
      return false;
    }
    state.activeDrawingToolId = record.tool.id;
    syncActiveButtons();
    if (announce) setDrawingStatus(`${record.tool.label} active. Use the chart to draw or select an existing drawing.`);
    return true;
  };
  records.forEach((record) => {
    const { tool, bindings } = record;
    const button = document.createElement("button");
    button.type = "button";
    requireString(tool.id, "Drawing tool ID");
    const label = requireString(tool.label, "Drawing tool label");
    button.textContent = label;
    button.title = label;
    button.setAttribute("aria-label", label);
    button.disabled = state.drawingBusy || state.indicatorBusy || bindings === null;
    button.dataset.drawingTool = tool.id;
    buttons.push(button);
    button.addEventListener("click", () => {
      activate(record);
    });
    root.append(button);
  });
  syncActiveButtons();
  const remove = document.createElement("button");
  remove.type = "button";
  remove.className = "bw-drawing-delete";
  remove.textContent = "Delete";
  remove.title = "Delete selected drawing";
  remove.setAttribute("aria-label", "Delete selected drawing");
  remove.setAttribute("aria-keyshortcuts", "Delete Backspace");
  remove.dataset.drawingDelete = "";
  remove.disabled = state.drawingBusy || state.indicatorBusy || !state.selectedDrawingId;
  remove.addEventListener("click", () => deleteSelectedDrawing());
  root.append(remove);
  if (!state.drawingBusy && !state.indicatorBusy) {
    const active = available.find((record) => record.tool.id === state.activeDrawingToolId);
    if (active) activate(active, { announce: false });
  }
  if (state.drawingBusy) setDrawingStatus("Drawing interaction is locked until this workspace is reloaded.", true);
  else if (state.indicatorBusy) setDrawingStatus("Drawing controls will resume after the indicator change is confirmed.");
  else if (state.pendingDrawingMutations.length) setBackgroundSaveStatus();
  else if (state.selectedDrawingId) setDrawingStatus("Drawing selected. Drag it, use Delete, or press Delete/Backspace.");
  else setDrawingStatus("Choose a drawing tool. Select a drawing to move or delete it.");
}

function deleteSelectedDrawing() {
  if (state.drawingBusy || state.indicatorBusy || !state.selectedDrawingId) return false;
  const accepted = state.chartContext?.interactionController?.deleteSelected?.() === true;
  if (!accepted) setDrawingStatus("Select a drawing before deleting it.", true);
  return accepted;
}

function setDrawingInteractionBusy(busy) {
  state.drawingBusy = !!busy;
  syncChartBusyState();
  if (state.drawingBusy) {
    state.chartContext?.interactionController?.cancel?.();
    state.activeDrawingToolId = "";
    state.selectedDrawingId = "";
  }
  renderIndicatorTools();
  try { syncDrawingToolAvailability(); } catch { /* current chart diagnostics remain authoritative */ }
}

function handleDrawingKeydown(event) {
  const editing = event.target?.matches?.("input, select, textarea, [contenteditable='true']");
  if (editing || !["Delete", "Backspace"].includes(event.key) || !state.selectedDrawingId) return;
  if (deleteSelectedDrawing()) event.preventDefault();
}

function syncDrawingToolAvailability() {
  const controller = state.chartContext?.interactionController;
  if (!controller || !state.visualizationPresentationSpec || !state.selectedInstrumentId) return;
  const projected = projectBasicVisualizationSpec(state.visualizationPresentationSpec);
  const candle = candleInstance(projected, state.selectedInstrumentId, state.period);
  renderDrawingTools(controller, candle.item.id);
}

function applyDrawingMutation(spec, mutation) {
  const nextSpec = structuredClone(spec);
  const pane = (nextSpec.panes || []).find((candidate) => candidate.id === mutation.paneId);
  if (!pane) throw new Error("Chart Pane is unavailable.");
  if (mutation.type === "commit") {
    pane.visualizers = window.TradeChartCore.upsertIdentity(
      pane.visualizers || [],
      (pane.visualizers || []).some((item) => item.id === mutation.instance.id) ? mutation.instance.id : "",
      mutation.instance,
      "id",
    );
  } else if (mutation.type === "delete") {
    pane.visualizers = (pane.visualizers || []).filter((item) => item.id !== mutation.visualizerId);
  } else {
    throw new Error("Drawing mutation type is unavailable.");
  }
  return nextSpec;
}

function drawingMutation(event) {
  const presentation = state.visualizationPresentationSpec;
  const candle = candleInstance(presentation, state.selectedInstrumentId, state.period);
  const sequence = ++state.drawingMutationSequence;
  if (event.type === "commit") {
    const instance = requireObject(event.instance, "Committed drawing instance");
    requireString(instance.id, "Committed drawing instance.id");
    return Object.freeze({
      sequence,
      type: "commit",
      paneId: candle.pane.id,
      instance: structuredClone(instance),
    });
  }
  if (event.type === "delete") {
    return Object.freeze({
      sequence,
      type: "delete",
      paneId: candle.pane.id,
      visualizerId: requireString(event.visualizerId, "Deleted drawing visualizerId"),
    });
  }
  return null;
}

function rebuildDrawingPresentation() {
  let presentation = structuredClone(state.visualizationCanonical.spec);
  state.pendingDrawingMutations.forEach((mutation) => {
    presentation = applyDrawingMutation(presentation, mutation);
  });
  state.visualizationPresentationSpec = presentation;
}

function setBackgroundSaveStatus() {
  const count = state.pendingDrawingMutations.length;
  if (count > 0) {
    setDrawingStatus(`${count} drawing change${count === 1 ? "" : "s"} saving in the background. You can keep drawing.`);
    setOpenStatus("Saving drawing changes in the background…");
  } else {
    setDrawingStatus("Drawing saved. Choose a tool or select an existing drawing.");
    setOpenStatus("Drawing saved.");
  }
  renderIndicatorTools();
}

function restoreCanonicalDrawingPresentation() {
  const canonicalSpec = state.visualizationCanonical?.spec;
  const controller = state.chartContext?.interactionController;
  if (!canonicalSpec || typeof controller?.reconcilePresentation !== "function") return false;
  try {
    const projected = projectBasicVisualizationSpec(canonicalSpec);
    const candle = candleInstance(projected, state.selectedInstrumentId, state.period);
    const restored = controller.reconcilePresentation({
      visualizers: structuredClone(candle.pane.visualizers || []),
    }) === true;
    if (restored) state.visualizationPresentationSpec = structuredClone(canonicalSpec);
    return restored;
  } catch {
    return false;
  }
}

function failDrawingPersistence(inFlight, error) {
  if (state.drawingSaveInFlight !== inFlight || inFlight.openSeq !== state.openSeq) return;
  state.drawingSaveInFlight = null;
  const conflict = error?.code === "sample_visualization_revision_conflict";
  const installedConflictCurrent = conflict && !!error.currentVisualization;
  if (conflict) {
    if (installedConflictCurrent) {
      installCanonicalVisualization(inFlight.request, error.currentVisualization);
    }
    state.pendingDrawingMutations = [];
  }
  state.drawingPersistenceFailure = Object.freeze({
    code: conflict ? "conflict" : "save-failed",
    message: String(error?.message || "Drawing save failed."),
  });
  setDrawingInteractionBusy(true);
  const restored = restoreCanonicalDrawingPresentation();
  if (conflict) {
    setOpenStatus(
      restored && installedConflictCurrent
        ? "Visualization changed elsewhere. The server version was restored without local changes. Reload this workspace."
        : restored
          ? "Visualization changed elsewhere. The last confirmed presentation was restored. Reload this workspace."
          : "Visualization changed elsewhere and its confirmed presentation could not be restored. Reload this workspace.",
      true,
    );
  } else {
    setOpenStatus(
      restored
        ? "Drawing changes could not be confirmed. The last confirmed presentation was restored; reload this workspace."
        : "Drawing changes could not be confirmed or restored. Reload this workspace.",
      true,
    );
  }
  setDrawingStatus("Drawing interaction is locked until this workspace is reloaded.", true);
}

function failDrawingStaging(error) {
  state.drawingPersistenceFailure = Object.freeze({
    code: "stage-failed",
    message: String(error?.message || "The drawing change could not be staged."),
  });
  setDrawingInteractionBusy(true);
  const restored = restoreCanonicalDrawingPresentation();
  setOpenStatus(
    restored
      ? "The drawing change could not be staged. The last confirmed presentation was restored; reload this workspace."
      : "The drawing change could not be staged or restored. Reload this workspace.",
    true,
  );
  setDrawingStatus("Drawing interaction is locked until this workspace is reloaded.", true);
}

function startNextDrawingSave() {
  if (state.drawingSaveInFlight || state.drawingPersistenceFailure || !state.pendingDrawingMutations.length) return;
  const canonical = state.visualizationCanonical;
  const template = state.visualizationSaveRequest;
  if (!canonical || !template) return;
  const mutation = state.pendingDrawingMutations[0];
  const request = {
    ...structuredClone(template),
    expectedRevision: canonical.revision,
    spec: applyDrawingMutation(canonical.spec, mutation),
  };
  const inFlight = {
    openSeq: state.openSeq,
    mutationSequence: mutation.sequence,
    request: structuredClone(request),
    completion: null,
  };
  state.drawingSaveInFlight = inFlight;
  const completion = (async () => {
    try {
      const saved = await saveVisualizationRevision(request, inFlight.openSeq);
      if (state.drawingSaveInFlight !== inFlight || inFlight.openSeq !== state.openSeq) return;
      if (state.pendingDrawingMutations[0]?.sequence !== mutation.sequence) {
        throw new Error("Drawing persistence queue order changed.");
      }
      state.pendingDrawingMutations.shift();
      installCanonicalVisualization(request, saved);
      state.drawingSaveInFlight = null;
      rebuildDrawingPresentation();
      setBackgroundSaveStatus();
      startNextDrawingSave();
    } catch (error) {
      failDrawingPersistence(inFlight, error);
    }
  })();
  inFlight.completion = completion;
}

function persistDrawingEvent(event) {
  if (state.drawingBusy || state.indicatorBusy || state.drawingPersistenceFailure
      || !state.visualizationPresentationSpec || !state.visualizationCanonical
      || !state.visualizationSaveRequest) return false;
  try {
    const mutation = drawingMutation(event);
    if (!mutation) return false;
    state.visualizationPresentationSpec = applyDrawingMutation(
      state.visualizationPresentationSpec,
      mutation,
    );
    state.pendingDrawingMutations.push(mutation);
    setBackgroundSaveStatus();
    startNextDrawingSave();
    return true;
  } catch (error) {
    failDrawingStaging(error);
    return false;
  }
}

async function prepareCurrentChartProjection(
  seq,
  presentationSpec = state.visualizationPresentationSpec,
) {
  const core = window.TradeChartCore;
  if (!core || !window.LightweightCharts?.createChart) throw new Error("TradeChartCore is unavailable.");
  const view = state.resultView;
  const canonicalSpec = core.normalizeVisualizationSpec({ dataKeys: view.dataKeys }, presentationSpec);
  const spec = projectBasicVisualizationSpec(canonicalSpec);
  const candle = candleInstance(spec, state.selectedInstrumentId, state.period);
  const panes = financialPaneLayout(spec, candle);
  const paneResults = await Promise.all(panes.map(async (pane) => ({
    pane,
    result: await projectPane(view, pane, spec, seq),
  })));
  if (seq !== state.openSeq) throw new Error("Stock selection changed.");
  return { canonicalSpec, spec, candle, panes, paneResults };
}

async function renderCurrentChart(seq, preparedProjection = null) {
  const core = window.TradeChartCore;
  if (!core || !window.LightweightCharts?.createChart) throw new Error("TradeChartCore is unavailable.");
  const prepared = preparedProjection || await prepareCurrentChartProjection(seq);
  const { spec, candle, panes, paneResults } = prepared;
  if (seq !== state.openSeq) return;
  const host = byId("bwChart");
  const previousMainContext = state.chartContext;
  const previousSubscription = state.chartSubscription;
  const previousRange = state.chart?.timeScale?.().getVisibleRange?.() || null;
  for (const unsubscribe of state.paneTimeSubscriptions || []) {
    try { unsubscribe?.(); } catch { /* time-range listener cleanup */ }
  }
  state.paneTimeSubscriptions = [];
  try { state.chartObserver?.disconnect?.(); } catch { /* resize observer cleanup */ }
  state.chartObserver = null;
  const existingFrames = new Map(
    [...host.children]
      .filter((item) => item?.dataset?.paneId)
      .map((item) => [item.dataset.paneId, item]),
  );
  const canReconcile = state.paneContexts.length > 0
    && state.paneContexts.length === state.paneCharts.length
    && state.paneContexts.every((context) => (
      typeof context?.paneId === "string"
      && typeof context?.reconcile === "function"
      && existingFrames.has(context.paneId)
    ));
  if (!canReconcile && state.paneContexts.length) disposeChart();
  host.style.setProperty("--bw-oscillator-count", String(Math.max(0, panes.length - 1)));
  const existing = new Map();
  if (canReconcile) {
    state.paneContexts.forEach((context, index) => existing.set(context.paneId, {
      context,
      chart: state.paneCharts[index],
      frame: existingFrames.get(context.paneId),
    }));
  }
  const mounts = panes.map((pane, index) => {
    const retained = existing.get(pane.id);
    if (retained) {
      retained.frame.className = `bw-chart-pane ${index === 0 ? "bw-chart-pane-main" : "bw-chart-pane-oscillator"}`;
      retained.frame.setAttribute("aria-label", `${pane.title} chart Pane`);
      const label = retained.frame.querySelector(".bw-chart-pane-label");
      if (label) label.textContent = pane.title;
      host.append(retained.frame);
      return {
        pane,
        frame: retained.frame,
        container: retained.frame.querySelector(".bw-chart-pane-surface"),
        chart: retained.chart,
        context: retained.context,
      };
    }
    const frame = document.createElement("section");
    frame.className = `bw-chart-pane ${index === 0 ? "bw-chart-pane-main" : "bw-chart-pane-oscillator"}`;
    frame.dataset.paneId = pane.id;
    frame.setAttribute("aria-label", `${pane.title} chart Pane`);
    const label = document.createElement("span");
    label.className = "bw-chart-pane-label";
    label.textContent = pane.title;
    const container = document.createElement("div");
    container.className = "bw-chart-pane-surface";
    frame.append(label, container);
    host.append(frame);
    return { pane, frame, container, chart: null, context: null };
  });
  const diagnostics = [];
  const resizeTargets = [];
  paneResults.forEach(({ pane, result }, index) => {
    const mount = mounts[index];
    const { container } = mount;
    const preparedPane = core.prepareFinancialPane(result, pane, spec);
    const timeInfo = core.paneTimeInfo(result, pane, spec, preparedPane);
    let { chart, context } = mount;
    let paneDiagnostics;
    if (chart && context) {
      chart.applyOptions({
        rightPriceScale: { mode: core.priceScaleMode(!!pane.view.logScale) },
      });
      const reconciled = context.reconcile(result, pane, spec, preparedPane);
      paneDiagnostics = reconciled.diagnostics || [];
    } else {
      chart = core.createFinancialChart(container, {
        timeZone: spec.timeZone,
        showTime: state.period !== "day" && timeInfo.showTime,
        logScale: !!pane.view.logScale,
      });
      context = core.drawFinancialPane(
        window.LightweightCharts, chart, result, pane, spec, preparedPane,
      );
      mount.chart = chart;
      mount.context = context;
      paneDiagnostics = context.diagnostics || [];
    }
    resizeTargets.push({ chart, container });
    diagnostics.push(...(timeInfo.diagnostics || []), ...paneDiagnostics);
  });
  const targetPaneIds = new Set(panes.map((pane) => pane.id));
  for (const [paneId, retained] of existing) {
    if (targetPaneIds.has(paneId)) continue;
    for (const cleanup of retained.context?.cleanups || []) {
      try { cleanup(); } catch { /* obsolete Pane cleanup isolation */ }
    }
    try { retained.chart?.remove?.(); } catch { /* obsolete chart cleanup */ }
    retained.frame?.remove?.();
  }
  state.paneCharts = mounts.map((mount) => mount.chart);
  state.paneContexts = mounts.map((mount) => mount.context);
  state.chart = state.paneCharts[0] || null;
  state.chartContext = state.paneContexts[0] || null;
  diagnostics.splice(0, diagnostics.length, ...diagnostics
    .filter((item, index, items) => item?.message && items.findIndex((candidate) => (
      candidate?.code === item.code
      && candidate?.visualizerId === item.visualizerId
      && candidate?.message === item.message
    )) === index));
  if (previousRange && canReconcile) {
    state.paneCharts.forEach((chart) => chart.timeScale().setVisibleRange?.(previousRange));
  } else {
    state.paneCharts.forEach((chart) => chart.timeScale().fitContent());
  }
  const mainRange = state.chart?.timeScale?.().getVisibleRange?.();
  if (mainRange) {
    state.paneCharts.slice(1).forEach((chart) => chart.timeScale().setVisibleRange?.(mainRange));
  }
  state.paneTimeSubscriptions = synchronizePaneTimeScales(state.paneCharts);
  if (window.ResizeObserver) {
    state.chartObserver = new ResizeObserver(() => {
      resizeTargets.forEach(({ chart, container }) => {
        if (container.isConnected) chart.applyOptions({ width: container.clientWidth, height: container.clientHeight });
      });
    });
    resizeTargets.forEach(({ container }) => state.chartObserver.observe(container));
  }
  const controller = state.chartContext.interactionController;
  if (state.chartContext !== previousMainContext) {
    try { previousSubscription?.(); } catch { /* prior chart listener cleanup */ }
    state.chartSubscription = controller.subscribe((event) => {
    if (event.type === "selection") {
      state.selectedDrawingId = event.visualizerId || "";
      const remove = byId("bwDrawingTools").querySelector("[data-drawing-delete]");
      if (remove) remove.disabled = state.drawingBusy || state.indicatorBusy || !state.selectedDrawingId;
      if (state.selectedDrawingId) {
        byId("bwChart").focus({ preventScroll: true });
        setDrawingStatus("Drawing selected. Drag it, use Delete, or press Delete/Backspace.");
      } else if (state.drawingBusy) {
        setDrawingStatus("Drawing interaction is locked until this workspace is reloaded.", true);
      } else if (state.indicatorBusy) {
        setDrawingStatus("Drawing controls will resume after the indicator change is confirmed.");
      } else if (state.pendingDrawingMutations.length) {
        setBackgroundSaveStatus();
      } else {
        setDrawingStatus("Choose a drawing tool. Select a drawing to move or delete it.");
      }
    } else if (event.type === "diagnostic") {
      setOpenStatus("A chart interaction could not be completed.", true);
    } else if (["commit", "delete"].includes(event.type)) {
      void persistDrawingEvent(event);
    }
    });
  } else {
    state.chartSubscription = previousSubscription;
  }
  renderDrawingTools(controller, candle.item.id);
  renderIndicatorTools();
  hideChartState();
  if (diagnostics.length) {
    setOpenStatus("Some chart layers could not be displayed. Open the full Result for technical details.", true);
  }
  return diagnostics;
}

async function loadMaterializedResult(openResponse, seq) {
  renderProvenance(openResponse);
  setOpenStatus("Sampler cache ready. Verifying DataKey and Visualizer contracts…");
  const view = structuredClone(openResponse.materialization.sampleResult);
  if (!state.catalog) {
    setOpenStatus("Loading Basic Visualizer and temporary Module definitions…");
    await loadCatalog();
  }
  const request = structuredClone(openResponse.materialization.visualizationSaveRequest);
  requireBasicCandlePreset(
    request.spec,
    openResponse.instrument.instrumentId,
    openResponse.barSnapshot.period,
  );
  setOpenStatus("Loading the saved Candles Visualization…");
  let saved = await loadCurrentVisualization(request, seq);
  const loadedSavedVisualization = !!saved;
  try {
    if (!saved) saved = await saveVisualizationRevision(request, seq);
  } catch (error) {
    if (error?.code !== "sample_visualization_revision_conflict") throw error;
    if (!error.currentVisualization) {
      throw new Error("Visualization changed elsewhere, but the conflict response had no valid current record.");
    }
    state.activeOpen = openResponse;
    state.resultView = view;
    installCanonicalVisualization(request, error.currentVisualization, { resetPresentation: true });
    state.pendingDrawingMutations = [];
    state.drawingSaveInFlight = null;
    state.drawingPersistenceFailure = null;
    state.drawingMutationSequence = 0;
    state.drawingBusy = false;
    state.indicatorBusy = false;
    const diagnostics = await renderCurrentChart(seq);
    setIndicatorStatus("Indicator overlays are ready.");
    if (!diagnostics.length) {
      setOpenStatus(
        `${openResponse.barSnapshot.barCount.toLocaleString()} daily bars · Data through ${formatInstant(openResponse.barSnapshot.lastTime)} · Loaded saved Visualization${readyCacheLabel()}`,
      );
    }
    return;
  }
  if (seq !== state.openSeq) return;
  state.activeOpen = openResponse;
  state.resultView = view;
  installCanonicalVisualization(request, saved, { resetPresentation: true });
  state.pendingDrawingMutations = [];
  state.drawingSaveInFlight = null;
  state.drawingPersistenceFailure = null;
  state.drawingMutationSequence = 0;
  state.drawingBusy = false;
  state.indicatorBusy = false;
  const diagnostics = await renderCurrentChart(seq);
  setIndicatorStatus("Indicator overlays are ready.");
  if (!diagnostics.length) {
    setOpenStatus(
      `${openResponse.barSnapshot.barCount.toLocaleString()} daily bars · Data through ${formatInstant(openResponse.barSnapshot.lastTime)}${loadedSavedVisualization ? " · Loaded saved Visualization" : ""}${readyCacheLabel()}`,
    );
  }
}

function readyCacheLabel() {
  const evidence = state.cacheEvidence;
  if (evidence.sampleResultHit) return " · Sampler cache hit";
  if (evidence.serverProjectionHits > 0) return " · Result cache hit";
  if (evidence.browserProjectionHits > 0) return " · Browser render cache hit";
  return " · Cached for next open";
}

async function openInstrument(instrumentId, initialSelection = null) {
  const snapshotId = initialSelection?.snapshotId || state.market?.snapshot?.snapshotId;
  const period = initialSelection?.period || state.period;
  const instrument = marketInstrument(instrumentId);
  if (!snapshotId || state.openBusy) return;
  if (instrument && !instrument.availablePeriods.includes(period)) {
    setOpenStatus(`${instrument.symbol} has no ${state.period} snapshot.`, true);
    return;
  }
  if (state.activeOpen?.snapshotId === snapshotId
      && state.activeOpen?.instrument?.instrumentId === instrumentId
      && state.activeOpen?.barSnapshot?.period === period) return;
  state.openBusy = true;
  state.selectedInstrumentId = instrumentId;
  renderInstrumentHeader();
  const seq = ++state.openSeq;
  state.activeOpen = null;
  state.resultView = null;
  state.visualizationSaveRequest = null;
  state.visualizationCanonical = null;
  state.visualizationPresentationSpec = null;
  state.pendingDrawingMutations = [];
  state.drawingSaveInFlight = null;
  state.drawingPersistenceFailure = null;
  state.drawingMutationSequence = 0;
  state.drawingBusy = false;
  state.indicatorBusy = false;
  state.indicatorDialogOpen = false;
  state.indicatorDraft = null;
  state.cacheEvidence = {
    sampleResultHit: false,
    browserProjectionHits: 0,
    serverProjectionHits: 0,
  };
  disposeChart();
  syncChartBusyState();
  renderIndicatorTools();
  setIndicatorStatus("Indicators become available after the Sampler cache is ready.");
  resetProvenance();
  showChartState(`Opening ${instrument?.symbol || "stock"}…`, "Checking the sealed Sampler timeline cache.");
  setOpenStatus("Checking the chart cache…");
  try {
    const selection = {
      snapshotId,
      instrumentId,
      period,
    };
    let response;
    for (let polls = 0; polls < 600; polls += 1) {
      response = requireOpenResponse(
        await postJson("/api/subsystems/basic/charts/open", selection),
        selection,
      );
      if (seq !== state.openSeq) return;
      installOpenMarketContext(response);
      if (response.ready) break;
      if (response.cacheJob.status === "failed") {
        throw new Error(response.cacheJob.error || "Chart cache preparation failed.");
      }
      setOpenStatus(
        response.cacheJob.status === "running"
          ? "Preparing Dataset → Sampler cache…"
          : "Chart cache queued with interactive priority…",
      );
      await wait(250);
    }
    if (!response?.ready) throw new Error("Chart cache did not become ready in time.");
    if (seq !== state.openSeq) return;
    state.cacheEvidence.sampleResultHit = response.cache.sampleResultHit;
    state.activeOpen = response;
    renderProvenance(response);
    setOpenStatus("Sampler cache hit. Loading the saved Visualization…");
    await loadMaterializedResult(response, seq);
  } catch (error) {
    if (seq !== state.openSeq) return;
    setOpenStatus(error?.message || "Stock snapshot materialization failed.", true);
    showChartState("The chart could not be materialized.", error?.message || "Trade Engine rejected this snapshot composition.");
  } finally {
    if (seq === state.openSeq) {
      state.openBusy = false;
      renderInstrumentHeader();
    }
  }
}

function mapValues(value) {
  return value && typeof value === "object" ? Object.values(value) : [];
}

async function loadCatalog() {
  if (state.catalog) return state.catalog;
  if (state.catalogPromise) return state.catalogPromise;
  state.catalogPromise = (async () => {
    const [visualizers, modules] = await Promise.all([
      getJson("/api/visualizers"),
      getJson("/api/subsystems/basic/chart-catalog"),
    ]);
    state.catalog = {
      visualizers: (visualizers.visualizers || []).filter(protocolOwned),
      modules: mapValues(modules.modules).filter(basicCatalogModule),
    };
    window.TradeChartCore?.setVisualizerDefinitions(state.catalog.visualizers);
    window.TradeChartCore?.setTemporaryModuleDefinitions(state.catalog.modules);
    renderIndicatorTools();
    return state.catalog;
  })();
  try {
    return await state.catalogPromise;
  } finally {
    state.catalogPromise = null;
  }
}

function workspaceSelection() {
  const query = new URLSearchParams(location.search);
  const allowed = new Set(["snapshotId", "instrumentId", "period"]);
  for (const key of query.keys()) {
    if (!allowed.has(key)) throw new Error(`Unsupported workspace parameter '${key}'.`);
    if (query.getAll(key).length !== 1) throw new Error(`Workspace parameter '${key}' must occur exactly once.`);
  }
  const selection = {
    snapshotId: requireString(query.get("snapshotId"), "Workspace snapshotId"),
    instrumentId: requireString(query.get("instrumentId"), "Workspace instrumentId"),
    period: requireString(query.get("period"), "Workspace period"),
  };
  if (selection.period !== "day") throw new Error("Basic workspace currently supports only the day period.");
  return selection;
}

async function main() {
  byId("bwFavoriteInstrument").addEventListener("click", () => void toggleSelectedWatchlist());
  byId("bwOpenIndicators").addEventListener("click", openIndicatorDialog);
  byId("bwCloseIndicators").addEventListener("click", closeIndicatorDialog);
  byId("bwIndicatorCancel").addEventListener("click", cancelIndicatorEditor);
  byId("bwIndicatorForm").addEventListener("submit", (event) => void submitIndicatorForm(event));
  byId("bwIndicatorSearch").addEventListener("input", (event) => {
    state.indicatorSearch = event.target.value;
    renderIndicatorCatalog();
  });
  byId("bwIndicatorFavorites").addEventListener("click", () => {
    state.indicatorCatalogView = "favorites";
    renderIndicatorDialog();
  });
  byId("bwIndicatorTechnicals").addEventListener("click", () => {
    state.indicatorCatalogView = "technicals";
    renderIndicatorDialog();
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && state.indicatorDialogOpen) {
      event.preventDefault();
      closeIndicatorDialog();
      return;
    }
    const editable = ["INPUT", "SELECT", "TEXTAREA"].includes(event.target?.tagName)
      || event.target?.isContentEditable;
    if (event.key === "/" && !editable && !state.indicatorDialogOpen && !indicatorEditingBlocked()) {
      event.preventDefault();
      openIndicatorDialog();
    }
  });
  byId("bwChart").addEventListener("keydown", handleDrawingKeydown);
  byId("bwDrawingTools").addEventListener("keydown", handleDrawingKeydown);
  window.addEventListener("beforeunload", () => {
    disposeChart({ clearCache: true });
  });

  try {
    const session = await getJson("/auth/session");
    state.csrfToken = requireString(session.csrfToken, "Session CSRF token");
    restoreProjectionCache(requireString(session.user?.userId, "Session userId"));
    const selection = workspaceSelection();
    state.selectedInstrumentId = selection.instrumentId;
    state.period = selection.period;
    const catalogPromise = loadCatalog();
    await Promise.all([
      catalogPromise,
      openInstrument(selection.instrumentId, selection),
    ]);
  } catch (error) {
    const message = error?.message || "Basic workspace could not be opened.";
    setStatus(message, true);
    setOpenStatus(message, true);
    showChartState("The workspace could not be opened.", message);
  }
}

void main();
