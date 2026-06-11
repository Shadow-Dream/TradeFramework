const state = {
  summary: null,
  modules: {},
  packages: {},
  instances: {},
  artifacts: {},
  dataSources: [],
  dataProxy: null,
  datasets: [],
  backtests: [],
  selectedBacktest: null,
  resultCharts: [],
  history: [],
  manifest: null,
  attachment: null,
  pipelineDraft: null,
  pipelineSignalModules: {},
  pipelineModules: {},
  resultModules: {},
  moduleCacheByKind: {},
  moduleTotalsByKind: {},
  lanes: {},
  laneId: "main",
  selectedModuleKind: "Input",
  totals: {},
};
window.__tradeState = state;

const $ = (id) => document.getElementById(id);
const forms = window.TradeModuleForms;

let currentView = "overview";
let currentPipelineSection = "composer";
const loadedViews = new Set();
let visualizationSaveTimer = null;
let visualizationSaveSeq = 0;
let uploadCsvValidationSeq = 0;
let uploadCsvValidationState = { pending: false, error: "" };
let pendingModuleLoad = null;
let healthState = { ok: false, text: "Checking" };
const NO_FALLBACK = Symbol("no-fallback");
const LOCAL_UI_ERROR = Symbol("local-ui-error");

const VIEW_PATHS = {
  overview: "/overview",
  pipeline: "/pipeline",
  modules: "/modules",
  data: "/data",
  backtests: "/backtests",
  results: "/results",
  artifacts: "/artifacts",
  manifest: "/manifest",
};

const PIPELINE_DRAFT_STORAGE_PREFIX = "trade.pipeline.draft.v2:";

function purgeLegacyPipelineStorage() {
  try {
    Object.keys(localStorage)
      .filter((key) => key.startsWith("trade.pipeline.draft.v1:"))
      .forEach((key) => localStorage.removeItem(key));
  } catch {}
}

function normalizedViewFromPath(pathname) {
  const path = String(pathname || "/").toLowerCase();
  if (path === "/blueprint") {
    currentPipelineSection = "alpha";
    return "pipeline";
  }
  const matched = Object.entries(VIEW_PATHS).find(([, value]) => value === path);
  return matched?.[0] || "overview";
}

function pathForView(viewId) {
  if (viewId === "pipeline") return "/pipeline";
  return VIEW_PATHS[viewId] || "/overview";
}

const PIPELINE_STAGES = [
  { stage: "inputs", kind: "Input", title: "Inputs" },
  { stage: "universe", kind: "Universe", title: "Universe" },
  { stage: "signal", kind: "Signal", title: "Signals" },
  { stage: "target", kind: "Target", title: "Targets" },
  { stage: "constraint", kind: "Constraint", title: "Constraints" },
  { stage: "execution", kind: "Execution", title: "Execution" },
  { stage: "analyzer", kind: "Analyzer", title: "Analyzers" },
];

const MARKET_SLOTS = [
  { slot: "brokerageModel", kind: "MarketRule", title: "Brokerage Model" },
  { slot: "feeModel", kind: "FeeModel", title: "Fee Model" },
  { slot: "slippageModel", kind: "SlippageModel", title: "Slippage Model" },
  { slot: "fillModel", kind: "FillModel", title: "Fill Model" },
  { slot: "buyingPowerModel", kind: "BuyingPowerModel", title: "Buying Power Model" },
  { slot: "settlementModel", kind: "SettlementModel", title: "Settlement Model" },
];

const MULTI_STAGE = new Set(["inputs", "signal", "constraint", "analyzer"]);
const GRAPH_NODE_SIZE = { width: 210, height: 188 };
const GRAPH_POSITIONS_KEY = "trade.pipeline.graph.positions.v1";

const MODULE_KINDS = [
  "Input",
  "Universe",
  "Signal",
  "Target",
  "Constraint",
  "Execution",
  "MarketRule",
  "Analyzer",
  "OrderSubmitRule",
  "OrderUpdateRule",
  "OrderExecutionRule",
  "LeverageRule",
  "FeeModel",
  "SlippageModel",
  "FillModel",
  "BuyingPowerModel",
  "SettlementModel",
  "MarginInterestModel",
  "ShortableProvider",
  "BenchmarkProvider",
];

async function getJson(path) {
  const response = await fetch(path, { headers: { Accept: "application/json" } });
  if (!response.ok) {
    throw new Error(`${path} returned ${response.status}`);
  }
  return response.json();
}

async function postJson(path, payload) {
  const response = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: JSON.stringify(payload),
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok || data.accepted === false) {
    throw new Error(data.error || `${path} returned ${response.status}`);
  }
  return data;
}

function shortHash(value) {
  return value ? `${value.slice(0, 12)}...` : "";
}

function formatTime(value) {
  if (!value) return "-";
  const date = typeof value === "number" ? new Date(value * 1000) : new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return date.toLocaleString();
}

function stageCounts(manifest) {
  const stages = ["inputs", "universe", "signal", "target", "constraint", "execution", "analyzer"];
  return Object.fromEntries(stages.map((stage) => [stage, (manifest?.[stage] || []).length]));
}

function pipelineOverviewRows(manifest) {
  return Object.entries(stageCounts(manifest)).map(([stage, count]) => ({
    label: stage,
    value: (manifest?.[stage] || []).join(", ") || "None",
    badge: `${count} active`,
  }));
}

function brokerageOverviewRows(manifest) {
  const rows = [];
  const marketRule = manifest?.marketRule || "";
  if (manifest?.marketRule) {
    rows.push({
      label: "Brokerage Model",
      value: manifest.marketRule,
      badge: "marketRule",
    });
  }
  const labels = {
    brokerageModel: "Brokerage Model Slot",
    feeModel: "Fee Model",
    slippageModel: "Slippage Model",
    fillModel: "Fill Model",
    buyingPowerModel: "Buying Power Model",
    settlementModel: "Settlement Model",
  };
  Object.entries(labels).forEach(([slot, label]) => {
    const value = (manifest?.market || {})[slot];
    const ids = Array.isArray(value) ? value : [value];
    const configured = ids.filter(Boolean).join(", ");
    rows.push({
      label,
      value: configured || (marketRule ? `Default from ${marketRule}` : "Not configured"),
      badge: configured ? `market.${slot}` : "Inherited",
    });
  });
  Object.entries(manifest?.market || {})
    .filter(([slot]) => !labels[slot])
    .forEach(([slot, value]) => {
      const ids = Array.isArray(value) ? value : [value];
      rows.push({
        label: slot,
        value: ids.filter(Boolean).join(", ") || "None",
        badge: `market.${slot}`,
      });
    });
  if (!manifest?.marketRule) {
    rows.push({
      label: "Brokerage Model",
      value: "No market model attached",
      badge: "Missing",
    });
  }
  return rows;
}

function setHealth(ok, text) {
  const node = $("health");
  healthState = {
    ok: !!ok,
    text: String(text || ""),
  };
  node.textContent = text;
  node.classList.toggle("ok", ok);
}

async function runUiAction(label, action) {
  const busyMessage = pipelineBlueprintBusyMessage();
  if (busyMessage && label !== "Attaching") {
    setHealth(healthState.ok, healthState.text);
    return;
  }
  const previousHealth = { ...healthState };
  try {
    setHealth(previousHealth.ok, label);
    await action();
    setHealth(previousHealth.ok, previousHealth.text);
  } catch (error) {
    if (error?.[LOCAL_UI_ERROR]) {
      setHealth(previousHealth.ok, previousHealth.text);
      return;
    }
    setHealth(false, error.message);
  }
}

function localUiError(message, code = "LOCAL_UI_ERROR") {
  const error = new Error(message || "Invalid input");
  error.code = code;
  error[LOCAL_UI_ERROR] = true;
  return error;
}

function setVisualizationSpecError(message = "") {
  const node = $("visualizationSpecError");
  if (!node) return;
  node.textContent = message;
  node.hidden = !message;
}

function setResultsActionError(message = "") {
  const node = $("resultsActionError");
  if (!node) return;
  node.textContent = message;
  node.hidden = !message;
}

function setPipelineAlphaGraphError(message = "") {
  const node = $("pipelineAlphaGraphError");
  if (!node) return;
  node.textContent = message;
  node.hidden = !message;
}

function setPipelineAttachError(message = "") {
  const node = $("pipelineAttachError");
  if (!node) return;
  node.textContent = message;
  node.hidden = !message;
}

function setPipelineLoadError(message = "") {
  const node = $("pipelineLoadError");
  if (!node) return;
  node.textContent = message;
  node.hidden = !message;
}

function setPipelineBlueprintError(message = "") {
  const node = $("pipelineBlueprintError");
  if (!node) return;
  node.textContent = message;
  node.hidden = !message;
}

function syncPipelineBlueprintErrorState() {
  const hasAttachment = !!state.attachment;
  const strategyId = pipelineField("StrategyId")?.value?.trim() || "";
  const version = pipelineField("Version")?.value?.trim() || "";
  const busyMessage = pipelineBlueprintBusyMessage();
  const messages = [];
  if (busyMessage) {
    messages.push(`Reload unavailable: ${busyMessage}`);
    messages.push(`Attach unavailable: ${busyMessage}`);
  } else if (!hasAttachment) {
    messages.push("Reload unavailable: No current lane attachment available");
  }
  if (!busyMessage && (!strategyId || !version)) {
    messages.push("Attach unavailable: Strategy and Version are required");
  }
  setPipelineBlueprintError(messages.join(" | "));
}

function syncVisualizationSpecInputState() {
  try {
    JSON.parse($("visualizationSpec").value || "{}");
    setVisualizationSpecError("");
  } catch (error) {
    setVisualizationSpecError(error?.message || "Invalid visualization spec");
  }
  syncResultsActionState();
}

function syncPipelineAlphaGraphInputState() {
  try {
    parsePipelineAlphaGraphValue({ fallback: NO_FALLBACK, reportError: true });
  } catch {}
  syncPipelineAttachActionState();
}

function pipelineBlueprintBusyState() {
  if (!window.__tradePipelineBlueprintBusyState) {
    window.__tradePipelineBlueprintBusyState = {
      attachInFlight: false,
      reloadInFlight: false,
    };
  }
  return window.__tradePipelineBlueprintBusyState;
}

function syncPipelineBusyUiState() {
  window.__syncPipelineComposerActionState?.();
  document.querySelector("#alphaGraphBuilder")?.__syncAttachState?.();
}

function setPipelineBlueprintBusyState(next = {}) {
  const busyState = pipelineBlueprintBusyState();
  if (Object.prototype.hasOwnProperty.call(next, "attachInFlight")) {
    busyState.attachInFlight = Boolean(next.attachInFlight);
  }
  if (Object.prototype.hasOwnProperty.call(next, "reloadInFlight")) {
    busyState.reloadInFlight = Boolean(next.reloadInFlight);
  }
  syncPipelineBusyUiState();
}

function pipelineBlueprintBusyMessage() {
  const busyState = pipelineBlueprintBusyState();
  if (busyState.reloadInFlight) return "Reload in progress";
  if (busyState.attachInFlight) return "Attach in progress";
  return "";
}

function syncPipelineComposerEditorState() {
  const grid = $("pipelineStageGrid");
  if (!grid) return;
  const busyMessage = pipelineBlueprintBusyMessage();
  grid.querySelectorAll("select[data-load-stage], select[data-load-market-slot], select[data-load-market-rule]").forEach((select) => {
    select.disabled = Boolean(busyMessage);
    select.title = busyMessage || "";
  });
  grid.querySelectorAll(
    "[data-load-stage-button], [data-load-market-button], [data-load-market-rule-button], [data-unload-stage], [data-unload-market-slot], [data-unload-market-rule]",
  ).forEach((button) => {
    const fallbackDisabled = button.dataset.defaultDisabled === "1";
    const fallbackTitle = button.dataset.defaultTitle || "";
    button.disabled = Boolean(busyMessage) || fallbackDisabled;
    button.title = busyMessage || fallbackTitle;
  });
}

function syncPipelineDialogActionState() {
  const busyMessage = pipelineBlueprintBusyMessage();
  const unloadConfirm = $("confirmUnloadBtn");
  if (unloadConfirm) {
    unloadConfirm.disabled = Boolean(busyMessage);
    unloadConfirm.title = busyMessage || "";
  }
  const moduleConfirm = $("confirmModuleLoadBtn");
  const moduleDialog = $("moduleLoadDialog");
  if (moduleConfirm) {
    if (busyMessage) {
      moduleConfirm.disabled = true;
      moduleConfirm.title = busyMessage;
      if (moduleDialog?.open) setModuleLoadDialogError(busyMessage);
    } else {
      syncModuleLoadDialogActionState();
    }
  }
}

function syncPipelineDraftFieldState() {
  const busyMessage = pipelineBlueprintBusyMessage();
  ["LaneId", "StrategyId", "Version", "Name", "AlphaGraph"].forEach((fieldId) => {
    const field = pipelineField(fieldId);
    if (!field) return;
    field.disabled = Boolean(busyMessage);
    field.title = busyMessage || "";
  });
}

function syncGlobalRefreshActionState() {
  const button = $("refreshBtn");
  if (!button) return;
  const busyMessage = pipelineBlueprintBusyMessage();
  button.disabled = Boolean(busyMessage);
  button.title = busyMessage || "";
}

function syncGlobalNavActionState() {
  const busyMessage = pipelineBlueprintBusyMessage();
  document.querySelectorAll(".nav-btn").forEach((button) => {
    button.disabled = Boolean(busyMessage);
    button.title = busyMessage || "";
  });
}

function syncPipelineSubnavActionState() {
  const busyMessage = pipelineBlueprintBusyMessage();
  document.querySelectorAll(".pipeline-subnav-btn").forEach((button) => {
    button.disabled = Boolean(busyMessage);
    button.title = busyMessage || "";
  });
}

function syncActiveViewBusyState() {
  const busyMessage = pipelineBlueprintBusyMessage();
  document.querySelectorAll(".view button, .view input, .view select, .view textarea").forEach((element) => {
    if (!("disabled" in element)) return;
    if (busyMessage) {
      if (!element.dataset.activeViewBusyCaptured) {
        element.dataset.activeViewBusyCaptured = "1";
        element.dataset.activeViewBusyDisabled = element.disabled ? "1" : "0";
        element.dataset.activeViewBusyTitle = element.title || "";
      }
      const activeView = element.closest(".view.active");
      if (activeView) {
        element.disabled = true;
        element.title = busyMessage;
      }
      return;
    }
    if (!element.dataset.activeViewBusyCaptured) return;
    element.disabled = element.dataset.activeViewBusyDisabled === "1";
    element.title = element.dataset.activeViewBusyTitle || "";
    delete element.dataset.activeViewBusyCaptured;
    delete element.dataset.activeViewBusyDisabled;
    delete element.dataset.activeViewBusyTitle;
  });
}

function syncGlobalLaneActionState() {
  const select = $("laneSelect");
  if (!select) return;
  const busyMessage = pipelineBlueprintBusyMessage();
  select.disabled = Boolean(busyMessage);
  select.title = busyMessage || "";
}

function syncPipelineLoadActionState() {
  const button = $("loadPipelineBtn");
  if (!button) return;
  const busyMessage = pipelineBlueprintBusyMessage();
  const hasAttachment = !!state.attachment;
  const disabled = Boolean(busyMessage) || !hasAttachment;
  const title = busyMessage || (hasAttachment ? "" : "No current lane attachment available");
  button.disabled = disabled;
  button.title = title;
  setPipelineLoadError(title || "");
}

function syncPipelineAttachActionState() {
  const button = $("attachPipelineBtn");
  if (!button) return;
  const strategyId = pipelineField("StrategyId")?.value?.trim() || "";
  const version = pipelineField("Version")?.value?.trim() || "";
  let disabled = false;
  let title = "";
  const busyMessage = pipelineBlueprintBusyMessage();
  if (busyMessage) {
    disabled = true;
    title = busyMessage;
  } else if (!strategyId || !version) {
    disabled = true;
    title = "Strategy and Version are required";
  } else {
    try {
      parsePipelineAlphaGraphValue({ fallback: NO_FALLBACK, reportError: false });
    } catch {
      disabled = true;
      title = "Fix Alpha Graph JSON first";
    }
  }
  button.disabled = disabled;
  button.title = title;
  setPipelineAttachError(disabled ? title : "");
  syncPipelineBlueprintErrorState();
  document.querySelector("#alphaGraphBuilder")?.__syncAttachState?.();
}

window.__syncPipelineComposerActionState = function syncPipelineComposerActionState() {
  syncGlobalNavActionState();
  syncPipelineSubnavActionState();
  syncGlobalRefreshActionState();
  syncGlobalLaneActionState();
  syncPipelineDialogActionState();
  syncPipelineDraftFieldState();
  syncPipelineLoadActionState();
  syncPipelineAttachActionState();
  syncPipelineComposerEditorState();
  syncActiveViewBusyState();
};

function setDataUploadError(message = "") {
  const node = $("dataUploadError");
  if (!node) return;
  node.textContent = message;
  node.hidden = !message;
}

function resetUploadCsvValidationState() {
  uploadCsvValidationSeq += 1;
  uploadCsvValidationState = { pending: false, error: "" };
}

function parseCsvHeaderRow(text) {
  const firstLine = String(text || "")
    .split(/\r?\n/)
    .map((line) => line.trim())
    .find(Boolean);
  if (!firstLine) return [];
  const values = [];
  let current = "";
  let inQuotes = false;
  for (let index = 0; index < firstLine.length; index += 1) {
    const char = firstLine[index];
    if (char === '"') {
      if (inQuotes && firstLine[index + 1] === '"') {
        current += '"';
        index += 1;
      } else {
        inQuotes = !inQuotes;
      }
      continue;
    }
    if (char === "," && !inQuotes) {
      values.push(current.trim());
      current = "";
      continue;
    }
    current += char;
  }
  values.push(current.trim());
  return values;
}

function normalizeDatasetId(value) {
  return Array.from(String(value || "").trim())
    .map((char) => (/[a-z0-9]/i.test(char) ? char.toLowerCase() : "-"))
    .join("")
    .split("-")
    .filter(Boolean)
    .join("-");
}

function validateUploadCsvText(csvText) {
  const headers = new Set(parseCsvHeaderRow(csvText).map((value) => value.toLowerCase()));
  const required = ["date", "open", "high", "low", "close", "volume"];
  const valid = required.every((header) => headers.has(header));
  if (valid) return "";
  return "Upload requires OHLCV CSV with Date, Open, High, Low, Close, Volume columns.";
}

async function validateSelectedUploadCsvFile() {
  const file = $("uploadCsv")?.files?.[0] || null;
  if (!file) {
    resetUploadCsvValidationState();
    setDataUploadError("");
    syncDataUploadActionState();
    return;
  }
  const currentSeq = ++uploadCsvValidationSeq;
  uploadCsvValidationState = { pending: true, error: "" };
  setDataUploadError("");
  syncDataUploadActionState();
  try {
    const csvText = await file.text();
    if (currentSeq !== uploadCsvValidationSeq) return;
    const csvError = validateUploadCsvText(csvText);
    uploadCsvValidationState = { pending: false, error: csvError };
    setDataUploadError(csvError);
  } catch (error) {
    if (currentSeq !== uploadCsvValidationSeq) return;
    const message = error?.message || "Unable to read CSV file";
    uploadCsvValidationState = { pending: false, error: message };
    setDataUploadError(message);
  }
  syncDataUploadActionState();
}

function dataDownloadActionState() {
  const sourceId = $("downloadSource")?.value || "";
  const selectedSource = state.dataSources.find((source) => source.source === sourceId);
  const symbol = $("downloadSymbol")?.value?.trim() || "";
  const startDate = $("downloadStart")?.value || "";
  const endDate = $("downloadEnd")?.value || "";
  const apiKey = $("downloadApiKey")?.value?.trim() || "";
  if (!sourceId) return { disabled: true, title: "No data source available" };
  if (!symbol) return { disabled: true, title: "Symbol is required" };
  if (selectedSource?.requiresKey && !apiKey) {
    return { disabled: true, title: "API key is required for the selected source" };
  }
  if (startDate && endDate && startDate > endDate) {
    return { disabled: true, title: "Start date must be on or before end date" };
  }
  return { disabled: false, title: "" };
}

function syncDataDownloadActionState() {
  const button = $("downloadDataBtn");
  if (!button) return;
  const { disabled, title } = dataDownloadActionState();
  button.disabled = disabled;
  button.title = title;
  setDataDownloadError(disabled ? title : "");
}

function dataUploadActionState() {
  const datasetId = $("uploadDatasetId")?.value?.trim() || "";
  const normalizedDatasetId = normalizeDatasetId(datasetId);
  const symbol = $("uploadSymbol")?.value?.trim() || "";
  const file = $("uploadCsv")?.files?.[0] || null;
  if (!datasetId) return { disabled: true, title: "Dataset is required" };
  if (!normalizedDatasetId) return { disabled: true, title: "Dataset must contain letters or numbers" };
  const existingDataset = (state.datasets || []).find((row) => normalizeDatasetId(row.datasetId) === normalizedDatasetId);
  if (existingDataset) {
    return { disabled: true, title: `Dataset ${existingDataset.datasetId} already exists` };
  }
  if (!symbol) return { disabled: true, title: "Symbol is required" };
  if (!file) return { disabled: true, title: "CSV file required" };
  if (uploadCsvValidationState.pending) return { disabled: true, title: "Validating CSV file" };
  if (uploadCsvValidationState.error) return { disabled: true, title: uploadCsvValidationState.error };
  return { disabled: false, title: "" };
}

function syncDataUploadActionState() {
  const button = $("uploadDataBtn");
  if (!button) return;
  const { disabled, title } = dataUploadActionState();
  button.disabled = disabled;
  button.title = title;
  setDataUploadError(disabled ? title : "");
}

function setDataDownloadError(message = "") {
  const node = $("dataDownloadError");
  if (!node) return;
  node.textContent = message;
  node.hidden = !message;
}

function setBacktestEntryError(message = "") {
  const node = $("backtestEntryError");
  if (!node) return;
  node.textContent = message;
  node.hidden = !message;
}

function syncRouteChrome() {
  const isBlueprintRoute = String(location.pathname || "").toLowerCase() === "/blueprint";
  document.body.classList.toggle("route-blueprint", isBlueprintRoute);
  const routeBar = $("blueprintRouteBar");
  if (routeBar) routeBar.hidden = !isBlueprintRoute;
  syncGlobalNavActionState();
  syncPipelineSubnavActionState();
  syncGlobalRefreshActionState();
  syncGlobalLaneActionState();
  syncPipelineDialogActionState();
  syncPipelineDraftFieldState();
  syncPipelineLoadActionState();
  syncPipelineAttachActionState();
  syncPipelineComposerEditorState();
  syncActiveViewBusyState();
}

function alphaGraphBuilderRoot() {
  return $("alphaGraphBuilder");
}

function unmountAlphaGraphBuilder(options = {}) {
  const {
    flushPending = true,
  } = options;
  const root = alphaGraphBuilderRoot();
  if (!root) return;
  if (flushPending) {
    root.__flushPendingEmit?.();
  }
  root.__alphaBlueprintCleanup?.({ flushPending });
  root.innerHTML = "";
}

function ensureAlphaGraphBuilderMounted() {
  const root = alphaGraphBuilderRoot();
  if (!root) return;
  if (!root.__liteGraphGraph) {
    renderAlphaGraphBuilder();
    return;
  }
  root.__refreshLayout?.();
}

function switchView(viewId, { push = true } = {}) {
  if (currentView === "pipeline" && currentPipelineSection === "alpha" && viewId !== "pipeline") {
    unmountAlphaGraphBuilder();
  }
  currentView = viewId;
  if (push) {
    const target = pathForView(viewId);
    if (location.pathname !== target) history.pushState({ viewId }, "", target);
  }
  document.querySelectorAll(".nav-btn").forEach((button) => {
    button.classList.toggle("active", button.dataset.view === viewId);
  });
  document.querySelectorAll(".view").forEach((view) => {
    view.classList.toggle("active", view.id === viewId);
  });
  syncRouteChrome();
  if (viewId === "pipeline") {
    switchPipelineSection(currentPipelineSection);
  }
  ensureViewData(viewId).catch((error) => setHealth(false, error.message));
}

function switchPipelineSection(sectionId) {
  currentPipelineSection = sectionId === "alpha" ? "alpha" : "composer";
  if (currentView === "pipeline") {
    const target = currentPipelineSection === "alpha" ? "/blueprint" : "/pipeline";
    if (location.pathname !== target) history.pushState({ viewId: "pipeline" }, "", target);
  }
  syncRouteChrome();
  document.querySelectorAll(".pipeline-subnav-btn").forEach((button) => {
    button.classList.toggle("active", button.dataset.pipelineSection === currentPipelineSection);
  });
  document.querySelectorAll(".pipeline-section").forEach((section) => {
    section.classList.toggle("active", section.id === (currentPipelineSection === "alpha" ? "pipelineAlphaSection" : "pipelineComposerSection"));
  });
  if (currentPipelineSection === "alpha") {
    requestAnimationFrame(() => {
      requestAnimationFrame(() => {
        ensureAlphaGraphBuilderMounted();
        document.querySelector(".alpha-blueprint-shell")?.__refreshLayout?.();
        window.__syncPipelineComposerActionState?.();
      });
    });
  } else {
    unmountAlphaGraphBuilder();
    window.__syncPipelineComposerActionState?.();
  }
}

function pipelineSignalDetailsSummary() {
  const graph = alphaGraphObject();
  const nodeCount = alphaGraphSignalNodeIds(graph).length;
  const outputCount = Object.keys(graph?.outputs || {}).length;
  if (!nodeCount) return "No alpha graph modules";
  const nodeLabel = nodeCount === 1 ? "module" : "modules";
  const outputLabel = outputCount === 1 ? "output" : "outputs";
  return `Alpha graph: ${nodeCount} ${nodeLabel}, ${outputCount} exposed ${outputLabel}`;
}

function renderSummary() {
  const summary = state.summary;
  if (!summary) return;
  const activeLanes = Object.values(state.lanes || {}).filter((lane) => lane.status === "active");
  const manifest = state.manifest || {};
  const repositories = summary.repositories || {};
  const laneHash = state.lanes?.[state.laneId]?.manifestHash;
  $("manifestHash").textContent = shortHash(laneHash || summary.current.manifestHash);

  const runtimeList = $("overviewRuntimeList");
  runtimeList.innerHTML = "";
  [
    { label: "Selected Lane", value: state.laneId, meta: state.lanes?.[state.laneId]?.status || "active" },
    { label: "Strategy", value: state.lanes?.[state.laneId]?.strategyId || summary.current.manifestName || "-", meta: state.lanes?.[state.laneId]?.version || "-" },
    { label: "Manifest", value: manifest.name || summary.current.manifestName || "-", meta: shortHash(laneHash || summary.current.manifestHash) },
    { label: "Updated", value: formatTime((state.lanes?.[state.laneId]?.updatedAt || "") || summary.serviceTime), meta: "control plane" },
  ].forEach((item) => {
    const row = document.createElement("div");
    row.className = "overview-card";
    row.innerHTML = `<div class="label">${item.label}</div><div class="overview-value">${item.value}</div><div class="muted">${item.meta}</div>`;
    runtimeList.appendChild(row);
  });

  const repoList = $("repoList");
  repoList.innerHTML = "";
  const repos = [
    ["Module definitions", repositories.moduleDefinitions],
    ["Custom definitions", repositories.customModuleDefinitions],
    ["Packages", repositories.packages],
    ["Instances", repositories.instances],
    ["Iterations", repositories.iterations],
    ["Artifacts", repositories.artifacts],
  ];
  repos.forEach(([name, count]) => {
    const row = document.createElement("div");
    row.className = "repo-row";
    row.innerHTML = `<div class="label">${name}</div><div class="value">${count}</div><div></div>`;
    repoList.appendChild(row);
  });

  const dataList = $("overviewDataList");
  dataList.innerHTML = "";
  [
    { label: "Datasets", value: repositories.datasets || 0, meta: "persisted market data" },
    { label: "Backtests", value: repositories.backtests || 0, meta: "saved runs" },
    { label: "Artifacts", value: repositories.artifacts || 0, meta: "snapshots, reports, logs" },
    { label: "Module Kinds", value: Object.keys(repositories.moduleDefinitionsByKind || {}).length, meta: "available categories" },
  ].forEach((item) => {
    const row = document.createElement("div");
    row.className = "overview-card";
    row.innerHTML = `<div class="label">${item.label}</div><div class="overview-value">${item.value}</div><div class="muted">${item.meta}</div>`;
    dataList.appendChild(row);
  });
}

function renderSelectOptions(selectId, rows, value, label) {
  const select = $(selectId);
  const previous = select.value;
  select.innerHTML = "";
  rows.forEach((row) => {
    const option = document.createElement("option");
    option.value = value(row);
    option.textContent = label(row);
    option.selected = option.value === previous;
    select.appendChild(option);
  });
}

function renderLaneSelector() {
  const select = $("laneSelect");
  const lanes = Object.values(state.lanes || {}).sort((a, b) => (a.laneId || "").localeCompare(b.laneId || ""));
  select.innerHTML = "";
  if (!lanes.length) {
    const option = document.createElement("option");
    option.value = "main";
    option.textContent = "main";
    select.appendChild(option);
    state.laneId = "main";
    return;
  }
  if (!lanes.some((lane) => lane.laneId === state.laneId)) {
    state.laneId = lanes[0].laneId || "main";
  }
  lanes.forEach((lane) => {
    const option = document.createElement("option");
    option.value = lane.laneId;
    option.textContent = lane.name ? `${lane.laneId} - ${lane.name}` : lane.laneId;
    option.selected = lane.laneId === state.laneId;
    select.appendChild(option);
  });
}

function renderTable(container, rows, columns) {
  const node = $(container);
  node.innerHTML = "";
  if (!rows.length) {
    const empty = document.createElement("div");
    empty.className = "muted";
    empty.textContent = "No rows";
    node.appendChild(empty);
    return;
  }
  rows.forEach((row) => {
    const item = document.createElement("div");
    item.className = "table-row";
    item.innerHTML = columns.map((column) => `<div><div class="label">${column.label}</div><div class="value">${column.value(row)}</div></div>`).join("");
    node.appendChild(item);
  });
}

function renderModules() {
  renderModuleKindMenu();
  const filter = ($("moduleFilter").value || "").toLowerCase();
  const rows = Object.entries(state.modules)
    .map(([key, value]) => ({ key, ...value }))
    .filter((row) => JSON.stringify(row).toLowerCase().includes(filter));
  $("moduleKindStatus").textContent = `${state.selectedModuleKind} - ${state.totals.modules ?? rows.length} module(s)`;
  const table = $("moduleTable");
  table.innerHTML = "";
  table.classList.add("module-card-grid");
  if (!rows.length) {
    const empty = document.createElement("div");
    empty.className = "muted";
    empty.textContent = "No modules in this type";
    table.appendChild(empty);
    return;
  }
  rows.forEach((row) => {
    const configKeys = Object.keys(row.configSchema?.properties || row.configSchema || {}).filter((key) => key !== "type" && key !== "properties");
    const inputPorts = Object.entries(row.ports?.inputs || {}).map(([name, spec]) => `${name}:${spec.type || "any"}`);
    const outputPorts = Object.entries(row.ports?.outputs || {}).map(([name, spec]) => `${name}:${spec.type || "any"}`);
    const card = document.createElement("article");
    card.className = "module-card";
    card.innerHTML = `
      <div class="module-card-top">
        <div>
          <div class="module-title">${row.moduleId || row.key}</div>
          <div class="module-subtitle">${row.kind || "-"} / ${row.version || "-"}</div>
        </div>
        <span class="pill">${row.activationMode || "-"}</span>
      </div>
      <div class="module-meta">
        <div><span class="label">Entry</span><div class="value">${row.entryPoint || "-"}</div></div>
        <div><span class="label">Swap</span><div class="value">${row.hotSwapMode || "-"}</div></div>
        <div><span class="label">Backend</span><div class="value">${row.parameters?.backend?.deploymentId || "-"}</div></div>
        <div><span class="label">Config</span><div class="value">${configKeys.length ? configKeys.join(", ") : "-"}</div></div>
        <div><span class="label">Inputs</span><div class="value">${inputPorts.length ? inputPorts.join(", ") : "-"}</div></div>
        <div><span class="label">Outputs</span><div class="value">${outputPorts.length ? outputPorts.join(", ") : "-"}</div></div>
      </div>
      ${row.description ? `<p class="module-description">${row.description}</p>` : ""}
    `;
    table.appendChild(card);
  });
}

function renderModuleKindMenu() {
  const menu = $("moduleKindMenu");
  if (!menu) return;
  const counts = state.summary?.repositories?.moduleDefinitionsByKind || {};
  menu.innerHTML = "";
  MODULE_KINDS.forEach((kind) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "kind-btn";
    button.classList.toggle("active", kind === state.selectedModuleKind);
    button.dataset.kind = kind;
    button.innerHTML = `<span>${kind}</span><strong>${counts[kind] || 0}</strong>`;
    button.addEventListener("click", () => {
      if (state.selectedModuleKind === kind) return;
      state.selectedModuleKind = kind;
      $("moduleFilter").value = "";
      loadModules(true).catch((error) => setHealth(false, error.message));
    });
    menu.appendChild(button);
  });
}

function renderInstances() {
  const filter = ($("instanceFilter").value || "").toLowerCase();
  const rows = Object.entries(state.instances)
    .map(([key, value]) => ({ key, ...value }))
    .filter((row) => JSON.stringify(row).toLowerCase().includes(filter));
  renderTable("instanceTable", rows, [
    { label: "Kind", value: (row) => row.kind || "-" },
    { label: "Instance", value: (row) => row.instanceId || row.key },
    { label: "Module", value: (row) => row.moduleId || "-" },
    { label: "Status", value: (row) => row.status || "-" },
  ]);
}

function instancesByKind(kind) {
  const merged = { ...(state.instances || {}), ...(state.pipelineDraft?.instances || {}) };
  return Object.entries(merged)
    .map(([key, value]) => ({ key, instanceId: value.instanceId || key, ...value }))
    .filter((row) => (row.kind || "").toLowerCase() === kind.toLowerCase())
    .sort((a, b) => (a.instanceId || "").localeCompare(b.instanceId || ""));
}

function pipelineField(id) {
  return $(`pipeline${id}`);
}

function moduleDefinitionsByKind(kind) {
  if (Object.keys(state.pipelineModules || {}).length) {
    return Object.entries(state.pipelineModules)
      .map(([key, value]) => ({ key, ...value }))
      .filter((row) => row.kind === kind);
  }
  if (kind === "Signal" && Object.keys(state.pipelineSignalModules || {}).length) {
    return Object.entries(state.pipelineSignalModules).map(([key, value]) => ({ key, ...value }));
  }
  return Object.entries(state.modules)
    .map(([key, value]) => ({ key, ...value }))
    .filter((row) => row.kind === kind);
}

function schemaDefaults(schema = {}) {
  return forms.schemaDefaults(schema);
}

function defaultWireName(portName) {
  const suffix = Date.now().toString(36).slice(-5);
  return `${portName}_${suffix}`;
}

function defaultInputWire(portName) {
  const name = String(portName || "").toLowerCase();
  if (name === "value" || name === "price") return "price";
  if (["open", "high", "low", "close", "volume"].includes(name)) return name === "volume" ? "volume" : `price.${name}`;
  return "";
}

function defaultPortInputs(module) {
  return Object.fromEntries(Object.keys(module?.ports?.inputs || {}).map((name) => [name, defaultInputWire(name)]));
}

function defaultPortOutputs(module, instanceId) {
  return Object.fromEntries(Object.keys(module?.ports?.outputs || {}).map((name) => [name, `${instanceId}.${name}`]));
}

function draftInstanceIdSet() {
  const used = new Set(Object.keys(state.pipelineDraft?.instances || {}));
  const alphaGraph = state.pipelineDraft?.alphaGraph || alphaGraphObject();
  (alphaGraph?.nodes || []).forEach((instanceId) => used.add(instanceId));
  Object.values(state.pipelineDraft?.market || {}).forEach((instanceId) => instanceId && used.add(instanceId));
  (state.pipelineDraft?.marketRule ? [state.pipelineDraft.marketRule] : []).forEach((instanceId) => used.add(instanceId));
  Object.values(state.pipelineDraft?.stages || {}).flat().forEach((instanceId) => instanceId && used.add(instanceId));
  return used;
}

function uniqueDraftInstanceId(moduleId) {
  const safeModuleId = String(moduleId || "node").replace(/[^a-zA-Z0-9_.-]/g, "-");
  const base = `${safeModuleId}.${Date.now().toString(36)}`;
  const used = draftInstanceIdSet();
  let candidate = base;
  let index = 2;
  while (used.has(candidate)) {
    candidate = `${base}.${index.toString(36)}`;
    index += 1;
  }
  return candidate;
}

function alphaGraphObject() {
  return parsePipelineAlphaGraphValue({ fallback: { nodes: [], outputs: {} } });
}

function alphaGraphNodeIds(source = null) {
  const graph = source?.alphaGraph || source || alphaGraphObject();
  return Array.isArray(graph?.nodes) ? graph.nodes.filter(Boolean) : [];
}

function isAlphaSignalInstance(instance) {
  return !!instance && instance.moduleId !== "graph-output";
}

function alphaGraphSignalNodeIds(source = null) {
  const instancesMap = source?.instances || draftInstances();
  return alphaGraphNodeIds(source).filter((instanceId) => isAlphaSignalInstance(instancesMap?.[instanceId]));
}

function syncSignalStageWithAlphaGraph(target) {
  if (!target) return target;
  target.stages ||= {};
  target.alphaGraph ||= { nodes: [], outputs: {} };
  target.stages.signal = [...alphaGraphSignalNodeIds(target)];
  return target;
}

function alphaGraphNodeEntries(instancesMap = draftInstances(), graph = alphaGraphObject()) {
  return alphaGraphSignalNodeIds({ alphaGraph: graph, instances: instancesMap })
    .map((instanceId) => {
      const instance = (instancesMap || {})[instanceId];
      if (!instance) return null;
      return { instanceId, instance };
    })
    .filter(Boolean);
}

function parsePipelineAlphaGraphValue({ fallback = NO_FALLBACK, reportError = true } = {}) {
  try {
    const parsed = JSON.parse(pipelineField("AlphaGraph").value || '{"nodes":[],"outputs":{}}');
    if (reportError) setPipelineAlphaGraphError("");
    return parsed;
  } catch (error) {
    const message = error?.message || "Invalid alpha graph";
    if (reportError) setPipelineAlphaGraphError(message);
    if (fallback !== NO_FALLBACK) return fallback;
    throw localUiError(message, "PIPELINE_ALPHA_GRAPH_PARSE");
  }
}

function clonePipelineDraft(attachment = {}) {
  const stages = {};
  PIPELINE_STAGES.forEach(({ stage }) => {
    stages[stage] = [...(attachment.stages?.[stage] || [])];
  });
  return syncSignalStageWithAlphaGraph({
    stages,
    instances: { ...(attachment.instances || {}) },
    market: { ...(attachment.market || {}) },
    marketRule: attachment.marketRule || "",
    alphaGraph: JSON.parse(JSON.stringify(attachment.alphaGraph || { nodes: [], outputs: {} })),
    meta: {
      laneId: attachment.laneId || state.laneId || "main",
      strategyId: attachment.strategyId || "",
      version: attachment.version || "",
      name: attachment.name || "",
    },
  });
}

function pipelinePinnedInstanceIds(draft = {}) {
  const ids = new Set();
  PIPELINE_STAGES.forEach(({ stage }) => {
    (draft?.stages?.[stage] || []).forEach((instanceId) => ids.add(instanceId));
  });
  if (draft?.marketRule) ids.add(draft.marketRule);
  Object.values(draft?.market || {}).forEach((value) => {
    const idsForSlot = Array.isArray(value) ? value : [value];
    idsForSlot.filter(Boolean).forEach((instanceId) => ids.add(instanceId));
  });
  return ids;
}

function sanitizePipelineDraft(draft, attachment = {}) {
  const base = clonePipelineDraft(attachment || {});
  const attachmentGraph = attachment.alphaGraph || { nodes: [], outputs: {} };
  const next = {
    ...base,
    ...(draft || {}),
    stages: { ...base.stages, ...((draft || {}).stages || {}) },
    market: { ...base.market, ...((draft || {}).market || {}) },
    instances: { ...((draft || {}).instances || {}) },
    alphaGraph: JSON.parse(JSON.stringify((draft || {}).alphaGraph || attachmentGraph)),
    meta: {
      ...base.meta,
      ...((draft || {}).meta || {}),
    },
  };
  const graph = next.alphaGraph || { nodes: [], outputs: {} };
  const validNodeIds = new Set(Object.keys(next.instances));
  graph.nodes = (graph.nodes || []).filter((nodeId) => validNodeIds.has(nodeId));
  next.alphaGraph = graph;
  const graphNodeSet = new Set(graph.nodes || []);
  next.instances = Object.fromEntries(
    Object.entries(next.instances || {}).filter(([instanceId, instance]) => {
      if (instance?.kind !== "Signal") return true;
      return graphNodeSet.has(instanceId);
    }),
  );
  syncSignalStageWithAlphaGraph(next);
  return next;
}

function pipelineDraftStorageKey(laneId) {
  return `${PIPELINE_DRAFT_STORAGE_PREFIX}${laneId || "main"}`;
}

function savePipelineDraft(laneId, attachment, draft) {
  try {
    const payload = {
      attachmentVersion: `${attachment?.strategyId || ""}:${attachment?.version || ""}`,
      draft,
    };
    localStorage.setItem(pipelineDraftStorageKey(laneId), JSON.stringify(payload));
  } catch {}
}

function loadPipelineDraft(laneId, attachment) {
  purgeLegacyPipelineStorage();
  try {
    const raw = localStorage.getItem(pipelineDraftStorageKey(laneId));
    if (!raw) return sanitizePipelineDraft(clonePipelineDraft(attachment || {}), attachment || {});
    const payload = JSON.parse(raw);
    const expectedVersion = `${attachment?.strategyId || ""}:${attachment?.version || ""}`;
    if ((payload?.attachmentVersion || "") !== expectedVersion) return sanitizePipelineDraft(clonePipelineDraft(attachment || {}), attachment || {});
    return sanitizePipelineDraft(payload?.draft || clonePipelineDraft(attachment || {}), attachment || {});
  } catch {
    return sanitizePipelineDraft(clonePipelineDraft(attachment || {}), attachment || {});
  }
}

function persistPipelineDraft() {
  const laneId = pipelineField("LaneId").value.trim() || state.attachment?.laneId || state.laneId || "main";
  if (!state.pipelineDraft) return;
  state.pipelineDraft.meta = {
    laneId: pipelineField("LaneId").value.trim() || state.attachment?.laneId || state.laneId || "main",
    strategyId: pipelineField("StrategyId").value.trim(),
    version: pipelineField("Version").value.trim(),
    name: pipelineField("Name").value.trim(),
  };
  savePipelineDraft(laneId, state.attachment || {}, state.pipelineDraft);
}

function clearPipelineDraftStorage(laneId) {
  try {
    localStorage.removeItem(pipelineDraftStorageKey(laneId));
  } catch {}
}

function loadPipelineFormFromAttachment(options = {}) {
  const {
    preferDraft = true,
    discardDraft = false,
  } = options;
  document.querySelector("#alphaGraphBuilder")?.__flushPendingEmit?.();
  const attachment = state.attachment || {};
  const laneId = attachment.laneId || state.laneId || "main";
  if (discardDraft) {
    clearPipelineDraftStorage(laneId);
  }
  state.pipelineDraft = preferDraft
    ? loadPipelineDraft(laneId, attachment)
    : sanitizePipelineDraft(clonePipelineDraft(attachment), attachment);
  const meta = state.pipelineDraft.meta || {};
  pipelineField("LaneId").value = meta.laneId || attachment.laneId || state.laneId || "main";
  pipelineField("StrategyId").value = meta.strategyId || attachment.strategyId || "";
  pipelineField("Version").value = meta.version || attachment.version || "";
  pipelineField("Name").value = meta.name || attachment.name || "";
  pipelineField("AlphaGraph").value = JSON.stringify(state.pipelineDraft.alphaGraph || { nodes: [], outputs: {} }, null, 2);
  setPipelineAttachError("");
  setPipelineAlphaGraphError("");
  syncPipelineAttachActionState();
  const shouldRenderAlphaBuilder = currentPipelineSection === "alpha"
    || String(location.pathname || "").toLowerCase() === "/blueprint";
  if (shouldRenderAlphaBuilder) {
    renderAlphaGraphBuilder({ flushBeforeCleanup: preferDraft });
  } else {
    unmountAlphaGraphBuilder({ flushPending: false });
  }
  renderBlueprintMeta();
  renderPipelineBuilder();
  if (String(location.pathname || "").toLowerCase() === "/blueprint") {
    currentPipelineSection = "alpha";
  }
  switchPipelineSection(currentPipelineSection);
}

function selectedAlphaModule() {
  const select = $("alphaGraphModuleSelect");
  if (!select) return null;
  return moduleDefinitionsByKind("Signal").find((row) => row.moduleId === select.value);
}

function fillAlphaGraphNodeDraft() {
  const module = selectedAlphaModule();
  if (!module) return;
  const instanceId = uniqueDraftInstanceId(module.moduleId);
  $("alphaGraphInstanceId").value = instanceId;
  forms.renderSchemaFields($("alphaGraphConfigFields"), module.configSchema, schemaDefaults(module.configSchema));
  forms.renderPortFields($("alphaGraphInputsFields"), module.ports?.inputs || {}, {});
  forms.renderPortFields(
    $("alphaGraphOutputsFields"),
    module.ports?.outputs || {},
    {},
    Object.fromEntries(Object.keys(module.ports?.outputs || {}).map((name) => [name, defaultWireName(name)])),
  );
}

function draftInstances() {
  state.pipelineDraft ||= clonePipelineDraft(state.attachment || {});
  state.pipelineDraft.instances ||= {};
  return state.pipelineDraft.instances;
}

function renderAlphaGraphBuilder(options = {}) {
  const {
    flushBeforeCleanup = true,
  } = options;
  const root = $("alphaGraphBuilder");
  if (!root) return;
  if (flushBeforeCleanup) {
    root.__flushPendingEmit?.();
  }
  root.__alphaBlueprintCleanup?.({ flushPending: flushBeforeCleanup });
  const modules = moduleDefinitionsByKind("Signal")
    .filter((row) => Object.keys(row.ports?.inputs || {}).length || Object.keys(row.ports?.outputs || {}).length)
    .sort((a, b) => (a.moduleId || "").localeCompare(b.moduleId || ""));
  if (!modules.length) {
    root.innerHTML = '<div class="muted">No Signal graph modules available</div>';
    return;
  }
  const graph = alphaGraphObject();
  const blueprintImpl = window.AlphaBlueprintLiteGraph || window.AlphaBlueprintX6 || window.AlphaBlueprint;
  blueprintImpl?.mount({
    root,
    modules,
    instances: draftInstances(),
    alphaGraph: graph,
    meta: {
      laneId: pipelineField("LaneId")?.value?.trim() || state.pipelineDraft?.meta?.laneId || state.attachment?.laneId || state.laneId || "main",
      strategyId: pipelineField("StrategyId")?.value?.trim() || state.pipelineDraft?.meta?.strategyId || state.attachment?.strategyId || "",
      version: pipelineField("Version")?.value?.trim() || state.pipelineDraft?.meta?.version || state.attachment?.version || "",
      name: pipelineField("Name")?.value?.trim() || state.pipelineDraft?.meta?.name || state.attachment?.name || "",
    },
    onMetaChange(nextMeta) {
      state.pipelineDraft ||= clonePipelineDraft(state.attachment || {});
      state.pipelineDraft.meta = {
        ...(state.pipelineDraft.meta || {}),
        ...(nextMeta || {}),
      };
      pipelineField("LaneId").value = state.pipelineDraft.meta.laneId || "";
      pipelineField("StrategyId").value = state.pipelineDraft.meta.strategyId || "";
      pipelineField("Version").value = state.pipelineDraft.meta.version || "";
      pipelineField("Name").value = state.pipelineDraft.meta.name || "";
      setPipelineAttachError("");
      syncPipelineAttachActionState();
      persistPipelineDraft();
      renderBlueprintMeta();
    },
    onChange(next) {
      state.pipelineDraft ||= clonePipelineDraft(state.attachment || {});
      syncSignalStageWithAlphaGraph(state.pipelineDraft);
      const pinnedIds = pipelinePinnedInstanceIds(state.pipelineDraft);
      const mergedInstances = Object.fromEntries(
        Object.entries(state.pipelineDraft.instances || {}).filter(([instanceId, instance]) => {
          if (pinnedIds.has(instanceId)) return true;
          return instance?.kind !== "Signal";
        }),
      );
      Object.assign(mergedInstances, next.instances || {});
      state.pipelineDraft.instances = mergedInstances;
      state.pipelineDraft.alphaGraph = next.alphaGraph || { nodes: [], outputs: {} };
      syncSignalStageWithAlphaGraph(state.pipelineDraft);
      pipelineField("AlphaGraph").value = JSON.stringify(next.alphaGraph || { nodes: [], outputs: {} }, null, 2);
      setPipelineAttachError("");
      setPipelineAlphaGraphError("");
      syncPipelineAttachActionState();
      persistPipelineDraft();
      renderBlueprintMeta();
    },
  });
}

function renderBlueprintMeta() {
  const node = $("blueprintMeta");
  if (!node) return;
  const attachment = state.attachment || {};
  const laneId = pipelineField("LaneId")?.value?.trim() || attachment.laneId || state.laneId || "main";
  const strategyId = pipelineField("StrategyId")?.value?.trim() || attachment.strategyId || "-";
  const version = pipelineField("Version")?.value?.trim() || attachment.version || "-";
  const graph = alphaGraphObject();
  const graphNodeIds = new Set(graph.nodes || []);
  const signalInstances = Object.entries(draftInstances() || {})
    .filter(([instanceId]) => graphNodeIds.has(instanceId))
    .map(([, instance]) => instance);
  const outputGroups = Object.keys(graph.outputs || {});
  const signalCount = signalInstances.length;
  const outputCount = outputGroups.length;
  node.innerHTML = "";
  [
    { label: "Lane", value: laneId, meta: "runtime target" },
    { label: "Strategy", value: strategyId, meta: version },
    { label: "Graph Nodes", value: signalCount, meta: "draft instances" },
    { label: "Graph Outputs", value: outputCount, meta: outputGroups.join(", ") || "none" },
  ].forEach((item) => {
    const card = document.createElement("div");
    card.className = "overview-card";
    card.innerHTML = `<div class="label">${item.label}</div><div class="overview-value">${item.value}</div><div class="muted">${item.meta}</div>`;
    node.appendChild(card);
  });
}

function loadStageModuleTemplate(stage, kind, moduleKey) {
  if (!moduleKey) return;
  const module = moduleDefinitionsByKind(kind).find((row) => row.key === moduleKey);
  if (!module) return;
  openModuleLoadDialog({ type: "stage", stage }, kind, module);
}

function loadMarketRuleModuleTemplate(moduleKey) {
  if (!moduleKey) return;
  const module = moduleDefinitionsByKind("MarketRule").find((row) => row.key === moduleKey);
  if (!module) return;
  openModuleLoadDialog({ type: "marketRule" }, "MarketRule", module);
}

function loadMarketModuleTemplate(slot, kind, moduleKey) {
  if (!moduleKey) return;
  const module = moduleDefinitionsByKind(kind).find((row) => row.key === moduleKey);
  if (!module) return;
  openModuleLoadDialog({ type: "marketSlot", slot }, kind, module);
}

function setModuleLoadDialogError(message = "") {
  const node = $("moduleLoadDialogError");
  if (!node) return;
  node.textContent = message;
  node.hidden = !message;
}

function moduleLoadDialogActionState() {
  const instanceInput = $("moduleLoadInstanceId");
  const instanceId = instanceInput?.value?.trim() || "";
  if (!instanceId) {
    return { disabled: true, title: "Instance is required" };
  }
  if (draftInstanceIdSet().has(instanceId)) {
    return { disabled: true, title: `Instance ${instanceId} already exists` };
  }
  if (pendingModuleLoad?.module) {
    try {
      forms.readSchemaFields($("moduleLoadConfigFields"), pendingModuleLoad.module.configSchema);
    } catch (error) {
      return { disabled: true, title: error?.message || "Invalid module fields" };
    }
  }
  return { disabled: false, title: "" };
}

function syncModuleLoadDialogActionState() {
  const confirm = $("confirmModuleLoadBtn");
  const dialog = $("moduleLoadDialog");
  if (!confirm) return;
  const { disabled, title } = moduleLoadDialogActionState();
  confirm.disabled = disabled;
  confirm.title = title;
  if (dialog?.open) {
    setModuleLoadDialogError(disabled ? title : "");
  }
}

function openModuleLoadDialog(target, kind, module) {
  if (pipelineBlueprintBusyMessage()) return;
  pendingModuleLoad = { target, kind, module };
  const instanceId = uniqueDraftInstanceId(module.moduleId);
  const dialog = $("moduleLoadDialog");
  const instanceInput = $("moduleLoadInstanceId");
  $("moduleLoadDialogTitle").textContent = `Load ${kind}: ${module.moduleId}`;
  instanceInput.value = instanceId;
  setModuleLoadDialogError("");
  forms.renderSchemaFields($("moduleLoadConfigFields"), module.configSchema, schemaDefaults(module.configSchema));
  forms.renderPortFields($("moduleLoadInputsFields"), module.ports?.inputs || {}, {}, defaultPortInputs(module));
  forms.renderPortFields($("moduleLoadOutputsFields"), module.ports?.outputs || {}, {}, defaultPortOutputs(module, instanceId));
  dialog.oninput = (event) => {
    if (!event.target.closest(".dialog-form")) return;
    setModuleLoadDialogError("");
    if (event.target === instanceInput) {
      forms.syncPortDefaults($("moduleLoadOutputsFields"), defaultPortOutputs(module, instanceInput.value.trim() || instanceId));
    }
    syncModuleLoadDialogActionState();
  };
  dialog.onchange = (event) => {
    if (!event.target.closest(".dialog-form")) return;
    setModuleLoadDialogError("");
    syncModuleLoadDialogActionState();
  };
  syncModuleLoadDialogActionState();
  if (typeof dialog.showModal === "function") dialog.showModal();
}

function confirmModuleLoad() {
  if (pipelineBlueprintBusyMessage()) return false;
  if (!pendingModuleLoad) return;
  const { target, kind, module } = pendingModuleLoad;
  const instanceId = $("moduleLoadInstanceId").value.trim();
  if (!instanceId) {
    setModuleLoadDialogError("Instance is required");
    $("moduleLoadInstanceId").focus();
    return false;
  }
  if (draftInstanceIdSet().has(instanceId)) {
    setModuleLoadDialogError(`Instance ${instanceId} already exists`);
    $("moduleLoadInstanceId").focus();
    return false;
  }
  let config;
  let inputs;
  let outputs;
  try {
    config = forms.readSchemaFields($("moduleLoadConfigFields"), module.configSchema);
    inputs = forms.readPortFields($("moduleLoadInputsFields"), module.ports?.inputs || {});
    outputs = forms.readPortFields($("moduleLoadOutputsFields"), module.ports?.outputs || {});
  } catch (error) {
    setModuleLoadDialogError(error?.message || "Invalid module fields");
    return false;
  }
  const payload = {
    instanceId,
    kind,
    moduleId: module.moduleId,
    version: module.version,
    config,
    inputs,
    outputs,
    status: "loaded",
  };
  draftInstances()[payload.instanceId] = payload;
  setModuleLoadDialogError("");
  $("moduleLoadDialog").close();
  pendingModuleLoad = null;
  if (target.type === "stage") {
    loadStageInstance(target.stage, payload.instanceId);
  } else if (target.type === "marketRule") {
    loadMarketRule(payload.instanceId);
  } else if (target.type === "marketSlot") {
    loadMarketSlot(target.slot, payload.instanceId);
  }
  return true;
}

function openUnloadDialog(kind, label, onUnload) {
  const dialog = $("unloadDialog");
  $("unloadDialogTitle").textContent = `Unload ${kind}`;
  $("unloadDialogText").textContent = label;
  const unload = $("confirmUnloadBtn");
  unload.onclick = () => {
    if (pipelineBlueprintBusyMessage()) return;
    dialog.close();
    onUnload();
  };
  syncPipelineDialogActionState();
  if (typeof dialog.showModal === "function") {
    dialog.showModal();
  } else if (window.confirm(`Unload ${label}?`)) {
    onUnload();
  }
}

function loadStageInstance(stage, instanceId) {
  if (!instanceId) return;
  state.pipelineDraft ||= clonePipelineDraft(state.attachment || {});
  if (stage === "signal") {
    state.pipelineDraft.alphaGraph ||= { nodes: [], outputs: {} };
    state.pipelineDraft.alphaGraph.nodes = [...new Set([...alphaGraphNodeIds(state.pipelineDraft.alphaGraph), instanceId])];
    syncSignalStageWithAlphaGraph(state.pipelineDraft);
  } else {
    const current = state.pipelineDraft.stages[stage] || [];
    state.pipelineDraft.stages[stage] = MULTI_STAGE.has(stage)
      ? [...new Set([...current, instanceId])]
      : [instanceId];
  }
  persistPipelineDraft();
  renderBlueprintMeta();
  renderPipelineBuilder();
}

function unloadStageInstance(stage, instanceId) {
  state.pipelineDraft ||= clonePipelineDraft(state.attachment || {});
  if (stage === "signal") {
    const instances = state.pipelineDraft.instances || {};
    const removedInstance = instances[instanceId];
    const removedWires = new Set(Object.values(removedInstance?.outputs || {}).filter(Boolean));
    state.pipelineDraft.alphaGraph ||= { nodes: [], outputs: {} };
    state.pipelineDraft.alphaGraph.nodes = (state.pipelineDraft.alphaGraph.nodes || []).filter((value) => value !== instanceId);
    const staleGraphOutputIds = (state.pipelineDraft.alphaGraph.nodes || []).filter((nodeId) => {
      const instance = instances[nodeId];
      return instance?.moduleId === "graph-output" && removedWires.has(instance?.inputs?.value);
    });
    if (staleGraphOutputIds.length) {
      const staleSet = new Set(staleGraphOutputIds);
      state.pipelineDraft.alphaGraph.nodes = (state.pipelineDraft.alphaGraph.nodes || []).filter((value) => !staleSet.has(value));
      staleGraphOutputIds.forEach((nodeId) => {
        if (instances[nodeId]) delete instances[nodeId];
      });
    }
    state.pipelineDraft.alphaGraph.outputs = Object.fromEntries(
      Object.entries(state.pipelineDraft.alphaGraph.outputs || {})
        .map(([name, wires]) => [name, (Array.isArray(wires) ? wires : []).filter((wire) => !removedWires.has(wire))])
        .filter(([, wires]) => wires.length),
    );
    syncSignalStageWithAlphaGraph(state.pipelineDraft);
  } else {
    state.pipelineDraft.stages[stage] = (state.pipelineDraft.stages[stage] || []).filter((value) => value !== instanceId);
  }
  if (state.pipelineDraft.instances?.[instanceId]) delete state.pipelineDraft.instances[instanceId];
  persistPipelineDraft();
  renderBlueprintMeta();
  renderPipelineBuilder();
}

function loadMarketSlot(slot, instanceId) {
  if (!instanceId) return;
  state.pipelineDraft ||= clonePipelineDraft(state.attachment || {});
  state.pipelineDraft.market[slot] = instanceId;
  persistPipelineDraft();
  renderBlueprintMeta();
  renderPipelineBuilder();
}

function unloadMarketSlot(slot) {
  state.pipelineDraft ||= clonePipelineDraft(state.attachment || {});
  const instanceId = state.pipelineDraft.market[slot];
  delete state.pipelineDraft.market[slot];
  if (instanceId && state.pipelineDraft.instances?.[instanceId]) delete state.pipelineDraft.instances[instanceId];
  persistPipelineDraft();
  renderBlueprintMeta();
  renderPipelineBuilder();
}

function loadMarketRule(instanceId) {
  if (!instanceId) return;
  state.pipelineDraft ||= clonePipelineDraft(state.attachment || {});
  state.pipelineDraft.marketRule = instanceId;
  pipelineField("MarketRule").value = instanceId;
  persistPipelineDraft();
  renderBlueprintMeta();
  renderPipelineBuilder();
}

function unloadMarketRule() {
  state.pipelineDraft ||= clonePipelineDraft(state.attachment || {});
  const instanceId = state.pipelineDraft.marketRule;
  state.pipelineDraft.marketRule = "";
  if (instanceId && state.pipelineDraft.instances?.[instanceId]) delete state.pipelineDraft.instances[instanceId];
  pipelineField("MarketRule").value = "";
  persistPipelineDraft();
  renderBlueprintMeta();
  renderPipelineBuilder();
}

function graphDefaultPositions() {
  return {
    "stage:inputs": { x: 32, y: 120 },
    "stage:universe": { x: 282, y: 120 },
    "stage:signal": { x: 532, y: 120 },
    "stage:target": { x: 782, y: 120 },
    "stage:constraint": { x: 1032, y: 120 },
    "stage:execution": { x: 1282, y: 120 },
    "stage:analyzer": { x: 1532, y: 120 },
    "market:rule": { x: 1032, y: 370 },
    "market:brokerageModel": { x: 1282, y: 370 },
    "market:feeModel": { x: 782, y: 370 },
    "market:slippageModel": { x: 532, y: 370 },
    "market:fillModel": { x: 1532, y: 370 },
    "market:buyingPowerModel": { x: 282, y: 370 },
    "market:settlementModel": { x: 32, y: 370 },
  };
}

function graphStoredPositions() {
  try {
    return JSON.parse(localStorage.getItem(GRAPH_POSITIONS_KEY) || "{}");
  } catch {
    return {};
  }
}

function graphPosition(nodeId) {
  const defaults = graphDefaultPositions();
  const stored = graphStoredPositions();
  return stored[nodeId] || defaults[nodeId] || { x: 32, y: 32 };
}

function saveGraphPosition(nodeId, position) {
  const stored = graphStoredPositions();
  stored[nodeId] = position;
  localStorage.setItem(GRAPH_POSITIONS_KEY, JSON.stringify(stored));
}

function graphNodeBox(board, nodeId) {
  const node = board.querySelector(`[data-node-id="${nodeId}"]`);
  if (!node) return null;
  const x = parseFloat(node.style.left || "0") + node.offsetWidth / 2;
  const y = parseFloat(node.style.top || "0") + node.offsetHeight / 2;
  return {
    x,
    y,
    left: parseFloat(node.style.left || "0"),
    top: parseFloat(node.style.top || "0"),
    width: node.offsetWidth,
    height: node.offsetHeight,
  };
}

function pointOnNodeEdge(box, toward) {
  const dx = toward.x - box.x;
  const dy = toward.y - box.y;
  if (!dx && !dy) return { x: box.x, y: box.y };
  const halfWidth = box.width / 2;
  const halfHeight = box.height / 2;
  const scale = Math.min(
    Math.abs(halfWidth / (dx || 1e-6)),
    Math.abs(halfHeight / (dy || 1e-6)),
  );
  return {
    x: box.x + dx * scale,
    y: box.y + dy * scale,
  };
}

function drawGraphEdges(board) {
  const svg = board.querySelector(".graph-edges");
  if (!svg) return;
  const edges = [
    ["stage:inputs", "stage:universe", "pipeline"],
    ["stage:universe", "stage:signal", "pipeline"],
    ["stage:signal", "stage:target", "pipeline"],
    ["stage:target", "stage:constraint", "pipeline"],
    ["stage:constraint", "stage:execution", "pipeline"],
    ["stage:execution", "stage:analyzer", "pipeline"],
    ["market:rule", "stage:execution", "market"],
    ["market:brokerageModel", "stage:execution", "market"],
    ["market:feeModel", "stage:execution", "market"],
    ["market:slippageModel", "stage:execution", "market"],
    ["market:fillModel", "stage:execution", "market"],
    ["market:buyingPowerModel", "stage:execution", "market"],
    ["market:settlementModel", "stage:execution", "market"],
  ];
  const paths = edges.map(([fromId, toId, kind]) => {
    const fromBox = graphNodeBox(board, fromId);
    const toBox = graphNodeBox(board, toId);
    if (!fromBox || !toBox) return "";
    const from = pointOnNodeEdge(fromBox, toBox);
    const to = pointOnNodeEdge(toBox, fromBox);
    const dx = Math.max(Math.abs(to.x - from.x) * 0.42, 72);
    const path = `M ${from.x} ${from.y} C ${from.x + dx} ${from.y}, ${to.x - dx} ${to.y}, ${to.x} ${to.y}`;
    return `<path class="graph-edge ${kind}" d="${path}" marker-end="url(#arrow-${kind})"></path>`;
  }).join("");
  svg.innerHTML = `
    <defs>
      <marker id="arrow-pipeline" markerWidth="18" markerHeight="18" refX="16" refY="8" orient="auto" markerUnits="userSpaceOnUse">
        <path d="M 1 1 L 17 8 L 1 15 z" class="graph-arrow pipeline"></path>
      </marker>
      <marker id="arrow-market" markerWidth="18" markerHeight="18" refX="16" refY="8" orient="auto" markerUnits="userSpaceOnUse">
        <path d="M 1 1 L 17 8 L 1 15 z" class="graph-arrow market"></path>
      </marker>
    </defs>
    ${paths}
  `;
}

function enableGraphDrag(board, node) {
  const handle = node.querySelector(".component-head") || node;
  handle.addEventListener("pointerdown", (event) => {
    if (pipelineBlueprintBusyMessage()) return;
    if (event.target.closest("button, select, input, textarea")) return;
    event.preventDefault();
    node.setPointerCapture(event.pointerId);
    node.classList.add("dragging");
    const startX = event.clientX;
    const startY = event.clientY;
    const left = parseFloat(node.style.left || "0");
    const top = parseFloat(node.style.top || "0");
    const onMove = (moveEvent) => {
      const next = {
        x: Math.max(12, left + moveEvent.clientX - startX),
        y: Math.max(12, top + moveEvent.clientY - startY),
      };
      node.style.left = `${next.x}px`;
      node.style.top = `${next.y}px`;
      drawGraphEdges(board);
    };
    const onUp = () => {
      node.classList.remove("dragging");
      saveGraphPosition(node.dataset.nodeId, {
        x: parseFloat(node.style.left || "0"),
        y: parseFloat(node.style.top || "0"),
      });
      node.removeEventListener("pointermove", onMove);
      node.removeEventListener("pointerup", onUp);
      node.removeEventListener("pointercancel", onUp);
    };
    node.addEventListener("pointermove", onMove);
    node.addEventListener("pointerup", onUp);
    node.addEventListener("pointercancel", onUp);
  });
}

function placeGraphNode(board, node, nodeId) {
  const position = graphPosition(nodeId);
  node.dataset.nodeId = nodeId;
  node.style.left = `${position.x}px`;
  node.style.top = `${position.y}px`;
  board.appendChild(node);
  enableGraphDrag(board, node);
}

function renderPipelineBuilder() {
  if (!$("pipelineStageGrid")) return;
  const attachment = state.attachment || {};
  syncPipelineLoadActionState();
  syncPipelineBlueprintErrorState();
  if (!state.pipelineDraft) state.pipelineDraft = clonePipelineDraft(attachment);
  const stages = state.pipelineDraft.stages || {};
  const market = state.pipelineDraft.market || {};
  if (!pipelineField("LaneId").value) {
    pipelineField("LaneId").value = attachment.laneId || state.laneId || "main";
    pipelineField("StrategyId").value = attachment.strategyId || "";
    pipelineField("Version").value = attachment.version || "";
    pipelineField("Name").value = attachment.name || "";
    pipelineField("AlphaGraph").value = JSON.stringify(attachment.alphaGraph || { nodes: [], outputs: {} }, null, 2);
  }
  const grid = $("pipelineStageGrid");
  grid.innerHTML = "";
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.classList.add("graph-edges");
  grid.appendChild(svg);
  PIPELINE_STAGES.forEach(({ stage, kind, title }) => {
    const signalEntries = stage === "signal" ? alphaGraphNodeEntries(draftInstances(), state.pipelineDraft?.alphaGraph || alphaGraphObject()) : [];
    const selected = stage === "signal" ? signalEntries.map(({ instanceId }) => instanceId) : (stages[stage] || []);
    const available = moduleDefinitionsByKind(kind);
    const helperText = available.length ? `${available.length} template(s)` : "No module template";
    const group = document.createElement("section");
    group.className = "component-group flow-node";
    group.dataset.stage = stage;
    const tags = selected.length
      ? selected.map((instanceId) => {
        const signalEntry = stage === "signal" ? signalEntries.find((entry) => entry.instanceId === instanceId) : null;
        const label = stage === "signal"
          ? forms.humanizeName(signalEntry?.instance?.moduleId || instanceId)
          : instanceId;
        return `<button class="loaded-tag" data-unload-stage="${stage}" data-instance="${instanceId}" type="button" title="${instanceId}">${label}</button>`;
      }).join("")
      : '<span class="muted">No loaded module</span>';
    const detailsButton = stage === "signal"
      ? '<button class="details-btn" data-open-alpha-details="signal" type="button">Details</button>'
      : "";
    const options = [
      `<option value="">Select module</option>`,
      ...available.map((row) => `<option value="${row.key}">${row.moduleId} / ${row.version}</option>`),
    ].join("");
    const loadRow = `
      <div class="load-row">
        <select data-load-stage="${stage}" data-stage-kind="${kind}">${options}</select>
        <button data-load-stage-button="${stage}" type="button">Load</button>
      </div>`;
    const helperRow = `<div class="muted" data-load-helper-stage="${stage}">${helperText}</div>`;
    group.innerHTML = `
      <div class="component-head">
        <h3>${title}</h3>
        <div class="component-head-actions">
          <span>${MULTI_STAGE.has(stage) ? "multi" : "single"} ${kind}</span>
          ${detailsButton}
        </div>
      </div>
      <div class="loaded-tags">${tags}</div>
      ${loadRow}
      ${helperRow}
    `;
    placeGraphNode(grid, group, `stage:${stage}`);
  });

  const selectedMarketRule = state.pipelineDraft.marketRule || "";
  const marketRules = moduleDefinitionsByKind("MarketRule");
  const marketRuleHelperText = marketRules.length ? `${marketRules.length} template(s)` : "No module template";
  const marketRuleGroup = document.createElement("section");
  marketRuleGroup.className = "component-group flow-node";
  const marketRuleOptions = [
    `<option value="">Select module</option>`,
    ...marketRules
      .map((row) => `<option value="${row.key}">${row.moduleId} / ${row.version}</option>`),
  ].join("");
  marketRuleGroup.innerHTML = `
    <div class="component-head"><h3>Market Rule</h3><span>single MarketRule</span></div>
    <div class="loaded-tags">${
      selectedMarketRule
        ? `<button class="loaded-tag" data-unload-market-rule="1" data-instance="${selectedMarketRule}" type="button">${selectedMarketRule}</button>`
        : '<span class="muted">No loaded module</span>'
    }</div>
    <div class="load-row">
      <select data-load-market-rule="1">${marketRuleOptions}</select>
      <button data-load-market-rule-button="1" type="button">Load</button>
    </div>
    <div class="muted" data-load-helper-market-rule="1">${marketRuleHelperText}</div>
  `;
  placeGraphNode(grid, marketRuleGroup, "market:rule");
  MARKET_SLOTS.forEach(({ slot, kind, title }) => {
    const selected = market[slot] || "";
    const available = moduleDefinitionsByKind(kind);
    const helperText = available.length ? `${available.length} template(s)` : "No module template";
    const group = document.createElement("section");
    group.className = "component-group flow-node";
    const options = [
      `<option value="">Select module</option>`,
      ...available.map((row) => `<option value="${row.key}">${row.moduleId} / ${row.version}</option>`),
    ].join("");
    const tag = selected
      ? `<button class="loaded-tag" data-unload-market-slot="${slot}" data-instance="${selected}" type="button">${selected}</button>`
      : '<span class="muted">No loaded module</span>';
    group.innerHTML = `
      <div class="component-head"><h3>${title}</h3><span>${slot}</span></div>
      <div class="loaded-tags">${tag}</div>
      <div class="load-row">
        <select data-load-market-slot="${slot}" data-market-kind="${kind}">${options}</select>
        <button data-load-market-button="${slot}" type="button">Load</button>
      </div>
      <div class="muted" data-load-helper-market-slot="${slot}">${helperText}</div>
    `;
    placeGraphNode(grid, group, `market:${slot}`);
  });
  const syncLoadButtonState = (select, button, emptyTitle, helperNode, defaultText) => {
    if (!select || !button) return;
    const hasTemplates = select.options.length > 1;
    const hasSelection = !!select.value;
    button.dataset.defaultDisabled = hasSelection ? "0" : "1";
    button.dataset.defaultTitle = hasSelection ? "" : (hasTemplates ? "Select module first" : emptyTitle);
    button.disabled = button.dataset.defaultDisabled === "1";
    button.title = button.dataset.defaultTitle || "";
    if (helperNode) {
      helperNode.textContent = hasSelection ? defaultText : (hasTemplates ? "Select module first" : emptyTitle);
    }
  };
  grid.querySelectorAll("[data-load-stage-button]").forEach((button) => {
    const stage = button.dataset.loadStageButton;
    const select = grid.querySelector(`select[data-load-stage="${stage}"]`);
    const helperNode = grid.querySelector(`[data-load-helper-stage="${stage}"]`);
    const defaultText = helperNode?.textContent || "";
    syncLoadButtonState(select, button, "No module template available", helperNode, defaultText);
    select?.addEventListener("change", () => syncLoadButtonState(select, button, "No module template available", helperNode, defaultText));
    button.addEventListener("click", () => {
      if (pipelineBlueprintBusyMessage()) return;
      loadStageModuleTemplate(stage, select.dataset.stageKind, select.value);
    });
  });
  grid.querySelectorAll("[data-unload-stage]").forEach((button) => {
    button.dataset.defaultDisabled = "0";
    button.dataset.defaultTitle = "";
    button.addEventListener("click", () => {
      if (pipelineBlueprintBusyMessage()) return;
      openUnloadDialog(button.dataset.unloadStage, button.dataset.instance, () => {
        unloadStageInstance(button.dataset.unloadStage, button.dataset.instance);
      });
    });
  });
  grid.querySelectorAll("[data-open-alpha-details]").forEach((button) => {
    button.addEventListener("click", () => {
      if (pipelineBlueprintBusyMessage()) return;
      currentView = "pipeline";
      switchPipelineSection("alpha");
    });
  });
  grid.querySelectorAll("[data-load-market-button]").forEach((button) => {
    const slot = button.dataset.loadMarketButton;
    const select = grid.querySelector(`select[data-load-market-slot="${slot}"]`);
    const helperNode = grid.querySelector(`[data-load-helper-market-slot="${slot}"]`);
    const defaultText = helperNode?.textContent || "";
    syncLoadButtonState(select, button, "No module template available", helperNode, defaultText);
    select?.addEventListener("change", () => syncLoadButtonState(select, button, "No module template available", helperNode, defaultText));
    button.addEventListener("click", () => {
      if (pipelineBlueprintBusyMessage()) return;
      loadMarketModuleTemplate(slot, select.dataset.marketKind, select.value);
    });
  });
  grid.querySelectorAll("[data-load-market-rule-button]").forEach((button) => {
    const select = grid.querySelector("select[data-load-market-rule]");
    const helperNode = grid.querySelector("[data-load-helper-market-rule]");
    const defaultText = helperNode?.textContent || "";
    syncLoadButtonState(select, button, "No module template available", helperNode, defaultText);
    select?.addEventListener("change", () => syncLoadButtonState(select, button, "No module template available", helperNode, defaultText));
    button.addEventListener("click", () => {
      if (pipelineBlueprintBusyMessage()) return;
      loadMarketRuleModuleTemplate(select.value);
    });
  });
  grid.querySelectorAll("[data-unload-market-slot]").forEach((button) => {
    button.dataset.defaultDisabled = "0";
    button.dataset.defaultTitle = "";
    button.addEventListener("click", () => {
      if (pipelineBlueprintBusyMessage()) return;
      openUnloadDialog(button.dataset.unloadMarketSlot, button.dataset.instance, () => {
        unloadMarketSlot(button.dataset.unloadMarketSlot);
      });
    });
  });
  grid.querySelectorAll("[data-unload-market-rule]").forEach((button) => {
    button.dataset.defaultDisabled = "0";
    button.dataset.defaultTitle = "";
    button.addEventListener("click", () => {
      if (pipelineBlueprintBusyMessage()) return;
      openUnloadDialog("MarketRule", button.dataset.instance, unloadMarketRule);
    });
  });
  syncPipelineComposerEditorState();
  syncPipelineDraftFieldState();
  syncPipelineLoadActionState();
  syncPipelineAttachActionState();
  requestAnimationFrame(() => drawGraphEdges(grid));

  renderTable("laneTable", Object.values(state.lanes || {}), [
    { label: "Lane", value: (row) => row.laneId },
    { label: "Strategy", value: (row) => row.strategyId || "-" },
    { label: "Version", value: (row) => row.version || "-" },
    { label: "Status", value: (row) => row.status || "-" },
  ]);
}

function buildPipelinePayload() {
  const laneId = pipelineField("LaneId").value.trim() || "main";
  const strategyId = pipelineField("StrategyId").value.trim();
  const version = pipelineField("Version").value.trim();
  if (!strategyId || !version) {
    const message = "Strategy and Version are required";
    setPipelineAttachError(message);
    throw localUiError(message, "PIPELINE_ATTACH_VALIDATION");
  }
  const stages = {};
  PIPELINE_STAGES.forEach(({ stage }) => {
    stages[stage] = [...(state.pipelineDraft?.stages?.[stage] || [])];
  });
  stages.signal = [...alphaGraphSignalNodeIds(state.pipelineDraft?.alphaGraph || alphaGraphObject())];
  const market = { ...(state.pipelineDraft?.market || {}) };
  setPipelineAttachError("");
  return {
    laneId,
    strategyId,
    version,
    name: pipelineField("Name").value.trim() || `${strategyId}-${version}`,
    stages,
    instances: { ...(state.pipelineDraft?.instances || {}) },
    market,
    marketRule: state.pipelineDraft?.marketRule || pipelineField("MarketRule").value || "",
    alphaGraph: parsePipelineAlphaGraphValue({ fallback: NO_FALLBACK }),
  };
}

async function attachCurrentPipeline({ redirect = true } = {}) {
  document.querySelector("#alphaGraphBuilder")?.__flushPendingEmit?.();
  const payload = buildPipelinePayload();
  const response = await postJson(`/api/attach?laneId=${encodeURIComponent(payload.laneId)}`, payload);
  try {
    localStorage.removeItem(pipelineDraftStorageKey(payload.laneId));
  } catch {}
  state.laneId = response.laneId || payload.laneId;
  $("pipelineStatus").textContent = response.iteration?.iterationId || response.liveManifestPath || "attached";
  pipelineField("LaneId").value = "";
  loadedViews.clear();
  await refreshOverview();
  if (redirect) {
    switchView("overview");
  } else {
    await loadPipeline(true);
    currentPipelineSection = "alpha";
    switchPipelineSection(currentPipelineSection);
  }
  return response;
}

window.__tradePipelineActions = {
  buildPayload: buildPipelinePayload,
  attachCurrentPipeline,
  loadPipelineFormFromAttachment,
};

["LaneId", "StrategyId", "Version", "Name"].forEach((fieldId) => {
  pipelineField(fieldId)?.addEventListener("input", () => {
    if (pipelineField(fieldId)?.disabled) return;
    state.pipelineDraft ||= clonePipelineDraft(state.attachment || {});
    state.pipelineDraft.meta = {
      ...(state.pipelineDraft.meta || {}),
      laneId: pipelineField("LaneId").value.trim() || state.attachment?.laneId || state.laneId || "main",
      strategyId: pipelineField("StrategyId").value.trim(),
      version: pipelineField("Version").value.trim(),
      name: pipelineField("Name").value.trim(),
    };
    setPipelineAttachError("");
    syncPipelineAttachActionState();
    persistPipelineDraft();
    renderBlueprintMeta();
  });
});

function renderArtifacts() {
  const rows = Object.entries(state.artifacts).map(([key, value]) => ({ key, ...value }));
  renderTable("artifactTable", rows, [
    { label: "Kind", value: (row) => row.kind || row.key.split("/")[0] },
    { label: "Artifact", value: (row) => row.artifactId || row.key },
    { label: "Version", value: (row) => row.version || "-" },
    { label: "Files", value: (row) => (row.files || []).length },
  ]);
}

function renderData() {
  const sourceSelect = $("downloadSource");
  const previousSource = sourceSelect.value || "robinhood";
  sourceSelect.innerHTML = "";
  state.dataSources
    .filter((source) => source.source !== "upload")
    .filter((source) => !source.requiresProxy || state.dataProxy?.enabled)
    .forEach((source) => {
      const option = document.createElement("option");
      option.value = source.source;
      option.textContent = source.name || source.source;
      option.title = source.description || "";
      option.selected = option.value === previousSource;
      sourceSelect.appendChild(option);
  });
  const selectedSource = state.dataSources.find((source) => source.source === sourceSelect.value);
  $("downloadApiKeyField").hidden = !selectedSource?.requiresKey;
  renderIntervalOptions(selectedSource);
  renderTable("datasetTable", state.datasets, [
    { label: "Dataset", value: (row) => row.datasetId },
    { label: "Symbol", value: (row) => row.symbol },
    { label: "Range", value: (row) => `${row.startDate} -> ${row.endDate}` },
    { label: "Rows", value: (row) => row.rowCount },
  ]);
  renderSelectOptions("backtestDataset", state.datasets, (row) => row.datasetId, (row) => `${row.datasetId} (${row.rowCount})`);
  syncDataDownloadActionState();
  syncDataUploadActionState();
}

function renderIntervalOptions(source) {
  const labels = {
    "1m": "1 Minute",
    "2m": "2 Minute",
    "5m": "5 Minute",
    "10m": "10 Minute",
    "15m": "15 Minute",
    h: "Hourly",
    d: "Daily",
    w: "Weekly",
    m: "Monthly",
  };
  const select = $("downloadInterval");
  const previous = select.value;
  const intervals = source?.intervals?.length ? source.intervals : ["d"];
  select.innerHTML = "";
  intervals.forEach((interval) => {
    const option = document.createElement("option");
    option.value = interval;
    option.textContent = labels[interval] || interval;
    option.selected = interval === previous;
    select.appendChild(option);
  });
  if (![...select.options].some((option) => option.selected) && select.options.length) {
    select.options[0].selected = true;
  }
}

function renderBacktests() {
  const lanes = Object.values(state.lanes || {});
  renderSelectOptions("backtestLane", lanes, (row) => row.laneId, (row) => row.laneId);
  if ($("backtestLane").options.length && !$("backtestLane").value) $("backtestLane").value = state.laneId;
  renderSelectOptions("backtestDataset", state.datasets, (row) => row.datasetId, (row) => `${row.datasetId} (${row.rowCount})`);
  renderTable("backtestTable", state.backtests, [
    { label: "Backtest", value: (row) => row.backtestId },
    { label: "Lane", value: (row) => row.laneId },
    { label: "Dataset", value: (row) => row.datasetId },
    { label: "Return", value: (row) => formatPercent(row.metrics?.totalReturn) },
  ]);
  renderSelectOptions("resultBacktest", state.backtests, (row) => row.backtestId, (row) => row.name || row.backtestId);
  $("resultBacktest").disabled = !state.backtests.length;
  syncResultsActionState();
  syncBacktestActionState();
}

function syncBacktestActionState() {
  const button = $("runBacktestBtn");
  const datasetId = $("backtestDataset").value;
  const dataset = (state.datasets || []).find((row) => row.datasetId === datasetId);
  let disabled = false;
  let title = "";
  if (!datasetId) {
    disabled = true;
    title = "No dataset available";
  } else if (dataset && Number(dataset.rowCount || 0) < 2) {
    disabled = true;
    title = "Dataset must contain at least two bars";
  }
  button.disabled = disabled;
  button.title = title;
  setBacktestEntryError(disabled ? title : "");
}

function syncResultsActionState() {
  const backtestId = $("resultBacktest").value || state.backtests[0]?.backtestId || "";
  const addChartButton = $("addChartBtn");
  const saveSpecButton = $("saveVisualizationBtn");
  const hasBacktest = !!backtestId;
  setResultsActionError(hasBacktest ? "" : "Run or select a backtest to visualize results.");
  if (addChartButton) {
    addChartButton.disabled = !hasBacktest;
    addChartButton.title = hasBacktest ? "" : "Run or select a backtest first";
  }
  if (!saveSpecButton) return;
  if (!hasBacktest) {
    saveSpecButton.disabled = true;
    saveSpecButton.title = "Run or select a backtest first";
    return;
  }
  try {
    JSON.parse($("visualizationSpec").value || "{}");
    saveSpecButton.disabled = false;
    saveSpecButton.title = "";
  } catch {
    saveSpecButton.disabled = true;
    saveSpecButton.title = "Fix visualization JSON first";
  }
}

function formatPercent(value) {
  return typeof value === "number" ? `${(value * 100).toFixed(2)}%` : "-";
}

function formatNumber(value) {
  return typeof value === "number" ? value.toLocaleString(undefined, { maximumFractionDigits: 2 }) : "-";
}

function renderHistory() {
  const node = $("eventList");
  if (!node) return;
  node.innerHTML = "";
  if (!state.history.length) {
    const item = document.createElement("div");
    item.className = "muted";
    item.textContent = "Event log is not loaded in the web UI.";
    node.appendChild(item);
    return;
  }
  state.history.slice(-80).reverse().forEach((event) => {
    const item = document.createElement("div");
    item.className = "event";
    item.innerHTML = `
      <div class="event-title">
        <span>${event.type || "event"}</span>
        <span class="muted">${formatTime(event.timestamp)}</span>
      </div>
      <pre>${JSON.stringify(event.payload || {}, null, 2)}</pre>
    `;
    node.appendChild(item);
  });
}

function renderManifest() {
  $("manifestJson").textContent = JSON.stringify(state.manifest || {}, null, 2);
}

function normalizeVisualizationSpec(result, spec) {
  return window.TradeChartCore.normalizeVisualizationSpec(result, spec);
}

function renderResults() {
  const backtestId = $("resultBacktest").value || state.backtests[0]?.backtestId;
  if (!backtestId) {
    $("metricStrip").innerHTML = "";
    $("chartArea").innerHTML = "";
    $("visualizationSpec").value = "";
    setVisualizationSpecError("");
    setResultsActionError("Run or select a backtest to visualize results.");
    syncResultsActionState();
    return;
  }
  setResultsActionError("");
  if (!state.selectedBacktest || state.selectedBacktest.backtestId !== backtestId) {
    syncResultsActionState();
    return;
  }
  const metrics = state.selectedBacktest.metrics || {};
  $("metricStrip").innerHTML = [
    ["Return", formatPercent(metrics.totalReturn)],
    ["Max DD", formatPercent(metrics.maxDrawdown)],
    ["End Value", formatNumber(metrics.endValue)],
    ["Trades", metrics.tradeCount ?? "-"],
  ].map(([label, value]) => `<div><span>${label}</span><strong>${value}</strong></div>`).join("");
  const spec = normalizeVisualizationSpec({ dataKeys: state.selectedBacktest.dataKeys || {} }, state.selectedBacktest.visualization || {});
  state.selectedBacktest.visualization = spec;
  $("visualizationSpec").value = JSON.stringify(spec, null, 2);
  setVisualizationSpecError("");
  syncResultsActionState();
  drawVisualization(spec);
}

function syncVisualizationSpec(spec) {
  state.selectedBacktest.visualization = spec;
  $("visualizationSpec").value = JSON.stringify(spec, null, 2);
  setVisualizationSpecError("");
  syncResultsActionState();
  drawVisualization(spec);
  scheduleVisualizationSave(spec);
}

function paneResult(pane) {
  const slice = state.selectedBacktest?.paneResults?.[pane.id] || {};
  return {
    dataKeys: state.selectedBacktest?.dataKeys || {},
    ...slice,
  };
}

function paneRequestKey(pane, spec) {
  const baseResult = { dataKeys: state.selectedBacktest?.dataKeys || {} };
  const scoped = paneScopedSpec(spec, pane);
  const paths = window.TradeChartCore.collectPaneSourcePaths(baseResult, pane, scoped);
  return JSON.stringify({
    paths,
    visualizers: pane?.visualizers || [],
    temporaryModules: pane?.temporaryModules || [],
  });
}

function paneHasLoaded(pane, spec) {
  return state.selectedBacktest?.loadedPanes?.[pane.id] === paneRequestKey(pane, spec);
}

async function ensurePaneResultLoaded(pane, spec) {
  if (!state.selectedBacktest?.backtestId || !pane?.id) return;
  state.selectedBacktest.paneResults ||= {};
  state.selectedBacktest.loadingPanes ||= {};
  state.selectedBacktest.loadedPanes ||= {};
  const requestKey = paneRequestKey(pane, spec);
  if (state.selectedBacktest.loadingPanes[pane.id] === requestKey || paneHasLoaded(pane, spec)) return;
  state.selectedBacktest.loadingPanes[pane.id] = requestKey;
  try {
    const parsed = JSON.parse(requestKey);
    if (!parsed.paths.length) {
      if (state.selectedBacktest.loadingPanes[pane.id] === requestKey) {
        state.selectedBacktest.paneResults[pane.id] = {};
        state.selectedBacktest.loadedPanes[pane.id] = requestKey;
      }
      return;
    }
    const response = await postJson(`/api/backtests/${encodeURIComponent(state.selectedBacktest.backtestId)}/result`, {
      paths: parsed.paths,
      temporaryModules: pane.temporaryModules || [],
    });
    if (state.selectedBacktest.loadingPanes[pane.id] !== requestKey) return;
    state.selectedBacktest.paneResults[pane.id] = response.result || {};
    state.selectedBacktest.loadedPanes[pane.id] = requestKey;
  } finally {
    if (state.selectedBacktest.loadingPanes[pane.id] === requestKey) {
      delete state.selectedBacktest.loadingPanes[pane.id];
    }
    renderResults();
  }
}

function scheduleVisualizationSave(spec) {
  const backtestId = $("resultBacktest").value || state.selectedBacktest?.backtestId;
  if (!backtestId) return;
  const saveSeq = ++visualizationSaveSeq;
  clearTimeout(visualizationSaveTimer);
  setHealth(false, "Saving visualization");
  visualizationSaveTimer = setTimeout(async () => {
    try {
      await postJson("/api/visualizations", {
        backtestId,
        name: "current",
        visualizationId: `${backtestId}-current`,
        spec,
      });
      if (saveSeq === visualizationSaveSeq) setHealth(true, "Online");
    } catch (error) {
      if (saveSeq === visualizationSaveSeq) setHealth(false, error.message);
    }
  }, 350);
}

function createLayerInstanceId(dataKey) {
  const suffix = `${Date.now().toString(36)}${Math.random().toString(36).slice(2, 7)}`;
  return `${dataKey}.${suffix}`;
}

function paneScopedSpec(spec, pane) {
  return {
    ...spec,
    temporaryModules: [
      ...(spec.temporaryModules || []),
      ...(pane?.temporaryModules || []),
    ],
  };
}

function resultsUiState() {
  if (!state.selectedBacktest) return { selectedTempByPane: {}, selectedVisualizerByPane: {}, selectionHintByPane: {} };
  state.selectedBacktest.ui ||= { selectedTempByPane: {}, selectedVisualizerByPane: {}, selectionHintByPane: {} };
  return state.selectedBacktest.ui;
}

function selectedTempModuleId(paneIndex) {
  return resultsUiState().selectedTempByPane[paneIndex] || "";
}

function selectedVisualizerId(paneIndex) {
  return resultsUiState().selectedVisualizerByPane[paneIndex] || "";
}

function paneSelectionHint(paneIndex) {
  return resultsUiState().selectionHintByPane?.[paneIndex] || "";
}

function setPaneSelectionHint(paneIndex, message = "") {
  resultsUiState().selectionHintByPane[paneIndex] = message || "";
}

function setPaneControlError(paneIndex, message = "") {
  const node = document.querySelector(`[data-chart-control-error="${paneIndex}"]`);
  if (!node) return;
  node.textContent = message;
  node.hidden = !message;
}

function clearPaneErrorForTarget(target) {
  if (!target) return;
  const directTemp = target.closest?.("[data-temp-instance]");
  if (directTemp?.dataset?.tempInstance != null) {
    setPaneSelectionHint(Number(directTemp.dataset.tempInstance), "");
    setPaneControlError(Number(directTemp.dataset.tempInstance), "");
    return;
  }
  const container = target.closest?.("[data-temp-config-fields], [data-temp-inputs-fields], [data-temp-outputs-fields], [data-visualizer-fields]");
  if (!container?.dataset) return;
  const paneIndex = (
    container.dataset.tempConfigFields
    ?? container.dataset.tempInputsFields
    ?? container.dataset.tempOutputsFields
    ?? container.dataset.visualizerFields
  );
  if (paneIndex == null) return;
  setPaneSelectionHint(Number(paneIndex), "");
  setPaneControlError(Number(paneIndex), "");
}

function setSelectedTempModuleId(paneIndex, instanceId) {
  resultsUiState().selectedTempByPane[paneIndex] = instanceId || "";
}

function setSelectedVisualizerId(paneIndex, visualizerId) {
  resultsUiState().selectedVisualizerByPane[paneIndex] = visualizerId || "";
}

function tempModuleActionState(paneIndex) {
  const tempModule = selectedResultModule(paneIndex);
  const tempSelect = document.querySelector(`[data-temp-module-select="${paneIndex}"]`);
  if (!tempModule) {
    return {
      disabled: true,
      title: tempSelect?.options?.length > 1
        ? "Select a template first"
        : "No temporary module templates available",
    };
  }
  const instanceInput = document.querySelector(`[data-temp-instance="${paneIndex}"]`);
  const rawInstanceId = instanceInput?.value?.trim() || "";
  if (!rawInstanceId) {
    return { disabled: true, title: "Temporary instance id is required" };
  }
  try {
    forms.readSchemaFields(document.querySelector(`[data-temp-config-fields="${paneIndex}"]`), tempModule.configSchema);
    forms.readParamFields(
      document.querySelector(`[data-temp-inputs-fields="${paneIndex}"]`),
      Object.keys(tempModule.ports?.inputs || {}).map((name) => ({ name, type: "dataKey" })),
    );
    const outputs = forms.readParamFields(
      document.querySelector(`[data-temp-outputs-fields="${paneIndex}"]`),
      Object.keys(tempModule.ports?.outputs || {}).map((name) => ({ name, type: "string" })),
    );
    const duplicateOutputKey = temporaryModuleOutputConflict(paneIndex, outputs, selectedTempModuleId(paneIndex) || "");
    if (duplicateOutputKey) {
      return { disabled: true, title: `Output data key ${duplicateOutputKey} already exists` };
    }
  } catch (error) {
    return { disabled: true, title: error?.message || "Invalid temporary module fields" };
  }
  return { disabled: false, title: "" };
}

function visualizerActionState(paneIndex) {
  const visualizerDefinition = selectedVisualizerDefinition(paneIndex);
  const visualizerSelect = document.querySelector(`[data-visualizer-select="${paneIndex}"]`);
  if (!visualizerDefinition) {
    return {
      disabled: true,
      title: visualizerSelect?.options?.length > 1
        ? "Select a visualizer first"
        : "No visualizers available for this pane",
    };
  }
  const params = forms.readParamFields(
    document.querySelector(`[data-visualizer-fields="${paneIndex}"]`),
    visualizerDefinition.params || [],
  );
  const missing = (visualizerDefinition.params || []).filter((field) => !params[field.name]);
  if (missing.length) {
    return {
      disabled: true,
      title: `Missing visualizer params: ${missing.map((field) => field.label || field.name).join(", ")}`,
    };
  }
  return { disabled: false, title: "" };
}

function paneValidationMessage(paneIndex) {
  const tempState = tempModuleActionState(paneIndex);
  if (selectedResultModule(paneIndex) && tempState.disabled) {
    return tempState.title;
  }
  const visualizerState = visualizerActionState(paneIndex);
  if (selectedVisualizerDefinition(paneIndex) && visualizerState.disabled) {
    return visualizerState.title;
  }
  const idleMessages = [];
  const tempSelect = document.querySelector(`[data-temp-module-select="${paneIndex}"]`);
  if (!tempSelect?.value && !selectedTempModuleId(paneIndex) && (
    tempState.title === "Select a template first"
    || tempState.title === "No temporary module templates available"
  )) {
    idleMessages.push(tempState.title);
  }
  const visualizerSelect = document.querySelector(`[data-visualizer-select="${paneIndex}"]`);
  if (!visualizerSelect?.value && !selectedVisualizerId(paneIndex) && (
    visualizerState.title === "Select a visualizer first"
    || visualizerState.title === "No visualizers available for this pane"
  )) {
    idleMessages.push(visualizerState.title);
  }
  if (idleMessages.length) {
    return idleMessages.join(" | ");
  }
  return paneSelectionHint(paneIndex);
}

function emptyPaneSelectionMessage(kind, select) {
  const optionCount = select?.options?.length || 0;
  if (kind === "temp") {
    return optionCount > 1 ? "Select a template first" : "No temporary module templates available";
  }
  return optionCount > 1 ? "Select a visualizer first" : "No visualizers available for this pane";
}

function syncInitialPaneSelectionHint(paneIndex, kind, select) {
  if (!select || select.value || (select.options?.length || 0) > 1 || paneSelectionHint(paneIndex)) return;
  setPaneSelectionHint(paneIndex, emptyPaneSelectionMessage(kind, select));
}

function setActionButtonLabels(paneIndex) {
  const tempState = tempModuleActionState(paneIndex);
  const tempButton = document.querySelector(`[data-add-temp-module="${paneIndex}"]`);
  if (tempButton) {
    tempButton.textContent = applyButtonLabel("Template", selectedTempModuleId(paneIndex));
    tempButton.disabled = tempState.disabled;
    tempButton.title = tempState.title;
  }
  const visualizerState = visualizerActionState(paneIndex);
  const visualizerButton = document.querySelector(`[data-add-visualizer="${paneIndex}"]`);
  if (visualizerButton) {
    visualizerButton.textContent = applyButtonLabel("Visualizer", selectedVisualizerId(paneIndex));
    visualizerButton.disabled = visualizerState.disabled;
    visualizerButton.title = visualizerState.title;
  }
  setPaneControlError(paneIndex, paneValidationMessage(paneIndex));
}

function syncResultPaneActionState(scope = document) {
  scope.querySelectorAll("[data-add-temp-module]").forEach((button) => {
    setActionButtonLabels(Number(button.dataset.addTempModule));
  });
  scope.querySelectorAll("[data-add-visualizer]").forEach((button) => {
    setActionButtonLabels(Number(button.dataset.addVisualizer));
  });
}

function removePaneLayer(paneIndex, layerId) {
  const spec = state.selectedBacktest.visualization;
  const pane = spec.panes[paneIndex];
  pane.visualizers = (pane.visualizers || []).filter((item) => item.id !== layerId);
  if (selectedVisualizerId(paneIndex) === layerId) setSelectedVisualizerId(paneIndex, "");
  syncVisualizationSpec(spec);
}

function removePaneTemporaryModule(paneIndex, instanceId) {
  const spec = state.selectedBacktest.visualization;
  const pane = spec.panes[paneIndex];
  const module = (pane.temporaryModules || []).find((item) => item.instanceId === instanceId);
  const outputKeys = Object.values(module?.outputs || {});
  pane.temporaryModules = (pane.temporaryModules || []).filter((item) => item.instanceId !== instanceId);
  pane.visualizers = (pane.visualizers || []).filter((item) => {
    const params = item.params || {};
    return !Object.values(params).some((value) => outputKeys.includes(value));
  });
  if (selectedTempModuleId(paneIndex) === instanceId) setSelectedTempModuleId(paneIndex, "");
  if (state.selectedBacktest?.paneResults) delete state.selectedBacktest.paneResults[pane.id];
  if (state.selectedBacktest?.loadedPanes) delete state.selectedBacktest.loadedPanes[pane.id];
  syncVisualizationSpec(spec);
}

function resultModuleDefinitions() {
  return Object.entries(state.resultModules || {})
    .map(([key, value]) => ({ key, ...value }))
    .filter((row) => Object.keys(row.ports?.outputs || {}).length)
    .sort((a, b) => `${a.kind}.${a.moduleId}`.localeCompare(`${b.kind}.${b.moduleId}`));
}

function selectedResultModule(paneIndex) {
  const select = document.querySelector(`[data-temp-module-select="${paneIndex}"]`);
  if (!select) return null;
  return resultModuleDefinitions().find((row) => row.key === select.value);
}

function temporaryModuleById(paneIndex, instanceId) {
  return state.selectedBacktest?.visualization?.panes?.[paneIndex]?.temporaryModules?.find((item) => item.instanceId === instanceId);
}

function paneTemporaryModuleInstanceIds(paneIndex, currentId = "") {
  const used = new Set();
  const pane = state.selectedBacktest?.visualization?.panes?.[paneIndex];
  (pane?.temporaryModules || []).forEach((item) => {
    if (!item?.instanceId || item.instanceId === currentId) return;
    used.add(item.instanceId);
  });
  return used;
}

function uniqueTemporaryModuleInstanceId(moduleId, paneIndex, preferred = "", currentId = "") {
  const safeModuleId = String(moduleId || "tmp").replace(/[^a-zA-Z0-9_.-]/g, "-");
  const trimmedPreferred = String(preferred || "").trim();
  const base = trimmedPreferred || `tmp.${safeModuleId}.${Date.now().toString(36)}`;
  const used = paneTemporaryModuleInstanceIds(paneIndex, currentId);
  let candidate = base;
  let index = 2;
  while (used.has(candidate)) {
    candidate = `${base}.${index.toString(36)}`;
    index += 1;
  }
  return candidate;
}

function resultModuleDefinitionKey(module) {
  if (!module) return "";
  const definition = resultModuleDefinitions().find((row) => row.kind === module.kind && row.moduleId === module.moduleId && row.version === module.version);
  return definition?.key || "";
}

function nextUniqueDataKey(baseKey, paneIndex) {
  const pane = state.selectedBacktest.visualization.panes[paneIndex];
  const scoped = paneScopedSpec(state.selectedBacktest.visualization, pane);
  const declarations = window.TradeChartCore.dataKeyDeclarations({ dataKeys: state.selectedBacktest.dataKeys || {} }, scoped);
  const existing = new Set(Object.keys(declarations));
  let candidate = baseKey;
  let index = 2;
  while (existing.has(candidate)) {
    candidate = `${baseKey}.${index}`;
    index += 1;
  }
  return candidate;
}

function currentDataKeyOptions(paneIndex) {
  const pane = state.selectedBacktest.visualization.panes[paneIndex];
  const scoped = paneScopedSpec(state.selectedBacktest.visualization, pane);
  return window.TradeChartCore.chartLayerCatalog({ dataKeys: state.selectedBacktest.dataKeys || {} }, scoped)
    .map((item) => ({ value: item.dataKey, label: `${item.label || item.dataKey} (${item.dataKey})` }));
}

function temporaryModuleOutputConflict(paneIndex, outputs = {}, currentId = "") {
  const pane = state.selectedBacktest?.visualization?.panes?.[paneIndex];
  if (!pane) return "";
  const scoped = paneScopedSpec(state.selectedBacktest.visualization, {
    ...pane,
    temporaryModules: (pane.temporaryModules || []).filter((item) => item?.instanceId !== currentId),
  });
  const declarations = window.TradeChartCore.dataKeyDeclarations({ dataKeys: state.selectedBacktest.dataKeys || {} }, scoped);
  const seen = new Set();
  for (const value of Object.values(outputs || {})) {
    const dataKey = String(value || "").trim();
    if (!dataKey) continue;
    if (seen.has(dataKey)) return dataKey;
    if (Object.prototype.hasOwnProperty.call(declarations, dataKey)) return dataKey;
    seen.add(dataKey);
  }
  return "";
}

function fillTemporaryModuleDraft(paneIndex) {
  const module = selectedResultModule(paneIndex);
  const instanceInput = document.querySelector(`[data-temp-instance="${paneIndex}"]`);
  const configFields = document.querySelector(`[data-temp-config-fields="${paneIndex}"]`);
  const inputFields = document.querySelector(`[data-temp-inputs-fields="${paneIndex}"]`);
  const outputFields = document.querySelector(`[data-temp-outputs-fields="${paneIndex}"]`);
  if (!module) {
    if (instanceInput) instanceInput.value = "";
    if (configFields) configFields.innerHTML = "";
    if (inputFields) inputFields.innerHTML = "";
    if (outputFields) outputFields.innerHTML = "";
    return;
  }
  setPaneControlError(paneIndex, "");
  const selectedItem = selectedTempModuleId(paneIndex) ? temporaryModuleById(paneIndex, selectedTempModuleId(paneIndex)) : null;
  instanceInput.value = selectedItem?.instanceId || uniqueTemporaryModuleInstanceId(module.moduleId, paneIndex);
  forms.renderSchemaFields(
    configFields,
    module.configSchema,
    selectedItem?.config || schemaDefaults(module.configSchema),
  );
  forms.renderParamFields(
    inputFields,
    Object.keys(module.ports?.inputs || {}).map((name) => ({
      name,
      label: forms.humanizeName(name),
      type: "dataKey",
      description: module.ports.inputs[name]?.type || "input data key",
    })),
    selectedItem?.inputs || {},
    Object.fromEntries(Object.keys(module.ports?.inputs || {}).map((name) => [name, currentDataKeyOptions(paneIndex)])),
  );
  forms.renderParamFields(
    outputFields,
    Object.keys(module.ports?.outputs || {}).map((name) => ({
      name,
      label: forms.humanizeName(name),
      type: "string",
      description: module.ports.outputs[name]?.type || "output data key",
      default: nextUniqueDataKey(`${module.moduleId}.${name}`, paneIndex),
    })),
    selectedItem?.outputs || {},
  );
}

function addPaneTemporaryModule(paneIndex) {
  const spec = state.selectedBacktest.visualization;
  const pane = spec.panes[paneIndex];
  const module = selectedResultModule(paneIndex);
  if (!module) return;
  const instanceInput = document.querySelector(`[data-temp-instance="${paneIndex}"]`);
  const rawInstanceId = instanceInput.value.trim();
  if (!rawInstanceId) {
    setPaneControlError(paneIndex, "Temporary instance id is required");
    instanceInput.focus();
    return false;
  }
  let config;
  let inputs;
  let outputs;
  try {
    config = forms.readSchemaFields(document.querySelector(`[data-temp-config-fields="${paneIndex}"]`), module.configSchema);
    inputs = forms.readParamFields(
      document.querySelector(`[data-temp-inputs-fields="${paneIndex}"]`),
      Object.keys(module.ports?.inputs || {}).map((name) => ({ name, type: "dataKey" })),
    );
    outputs = forms.readParamFields(
      document.querySelector(`[data-temp-outputs-fields="${paneIndex}"]`),
      Object.keys(module.ports?.outputs || {}).map((name) => ({ name, type: "string" })),
    );
  } catch (error) {
    setPaneControlError(paneIndex, error?.message || "Invalid temporary module fields");
    return false;
  }
  const selectedId = selectedTempModuleId(paneIndex);
  const previousItem = selectedId ? temporaryModuleById(paneIndex, selectedId) : null;
  const duplicateOutputKey = temporaryModuleOutputConflict(paneIndex, outputs, selectedId || "");
  if (duplicateOutputKey) {
    setPaneControlError(paneIndex, `Output data key ${duplicateOutputKey} already exists`);
    return false;
  }
  const instanceId = uniqueTemporaryModuleInstanceId(module.moduleId, paneIndex, rawInstanceId, selectedId || "");
  if (instanceId !== rawInstanceId) instanceInput.value = instanceId;
  const nextItem = {
    instanceId,
    kind: module.kind,
    moduleId: module.moduleId,
    version: module.version,
    config,
    inputs,
    outputs,
  };
  pane.temporaryModules ||= [];
  setPaneControlError(paneIndex, "");
  if (selectedId) {
    pane.temporaryModules = pane.temporaryModules.map((item) => item.instanceId === selectedId ? nextItem : item);
    if (selectedId !== instanceId) setSelectedTempModuleId(paneIndex, instanceId);
    if (previousItem) {
      pane.visualizers = (pane.visualizers || []).map((visualizer) => {
        const params = { ...(visualizer.params || {}) };
        for (const [portName, newKey] of Object.entries(outputs)) {
          const oldKey = previousItem.outputs?.[portName];
          for (const key of Object.keys(params)) {
            if (params[key] === oldKey) params[key] = newKey;
          }
        }
        return { ...visualizer, params };
      });
    }
  } else {
    pane.temporaryModules.push(nextItem);
    setSelectedTempModuleId(paneIndex, "");
  }
  if (state.selectedBacktest?.paneResults) delete state.selectedBacktest.paneResults[pane.id];
  if (state.selectedBacktest?.loadedPanes) delete state.selectedBacktest.loadedPanes[pane.id];
  syncVisualizationSpec(spec);
  return true;
}

function selectedVisualizerDefinition(paneIndex) {
  const select = document.querySelector(`[data-visualizer-select="${paneIndex}"]`);
  const pane = state.selectedBacktest.visualization.panes[paneIndex];
  const scoped = paneScopedSpec(state.selectedBacktest.visualization, pane);
  return window.TradeChartCore.visualizerCatalog({ dataKeys: state.selectedBacktest.dataKeys || {} }, scoped)
    .find((item) => item.id === select?.value);
}

function visualizerById(paneIndex, visualizerId) {
  return state.selectedBacktest?.visualization?.panes?.[paneIndex]?.visualizers?.find((item) => item.id === visualizerId);
}

function paneVisualizerIds(paneIndex, currentId = "") {
  const used = new Set();
  const pane = state.selectedBacktest?.visualization?.panes?.[paneIndex];
  (pane?.visualizers || []).forEach((item) => {
    if (!item?.id || item.id === currentId) return;
    used.add(item.id);
  });
  return used;
}

function uniquePaneVisualizerId(definitionId, paneIndex, currentId = "") {
  const base = `${String(definitionId || "visualizer")}.${Date.now().toString(36)}`;
  const used = paneVisualizerIds(paneIndex, currentId);
  let candidate = currentId || base;
  let index = 2;
  while (used.has(candidate)) {
    candidate = `${base}.${index.toString(36)}`;
    index += 1;
  }
  return candidate;
}

function fillVisualizerDraft(paneIndex) {
  const definition = selectedVisualizerDefinition(paneIndex);
  const visualizerFields = document.querySelector(`[data-visualizer-fields="${paneIndex}"]`);
  if (!definition) {
    if (visualizerFields) visualizerFields.innerHTML = "";
    return;
  }
  setPaneControlError(paneIndex, "");
  const pane = state.selectedBacktest.visualization.panes[paneIndex];
  const scoped = paneScopedSpec(state.selectedBacktest.visualization, pane);
  const refreshed = window.TradeChartCore.visualizerCatalog({ dataKeys: state.selectedBacktest.dataKeys || {} }, scoped)
    .find((item) => item.id === definition.id);
  const selectedItem = selectedVisualizerId(paneIndex) ? visualizerById(paneIndex, selectedVisualizerId(paneIndex)) : null;
  forms.renderParamFields(
    visualizerFields,
    refreshed?.params || [],
    selectedItem?.params || {},
    refreshed?.optionMap || {},
  );
}

function addPaneVisualizer(paneIndex) {
  const spec = state.selectedBacktest.visualization;
  const pane = spec.panes[paneIndex];
  const definition = selectedVisualizerDefinition(paneIndex);
  if (!definition) return;
  const params = forms.readParamFields(
    document.querySelector(`[data-visualizer-fields="${paneIndex}"]`),
    definition.params || [],
  );
  const missing = (definition.params || []).filter((field) => !params[field.name]);
  if (missing.length) {
    setPaneControlError(paneIndex, `Missing visualizer params: ${missing.map((field) => field.label || field.name).join(", ")}`);
    return false;
  }
  pane.visualizers ||= [];
  const selectedId = selectedVisualizerId(paneIndex);
  const nextItem = {
    id: uniquePaneVisualizerId(definition.id, paneIndex, selectedId || ""),
    callback: definition.id,
    params,
  };
  setPaneControlError(paneIndex, "");
  if (selectedId) {
    pane.visualizers = pane.visualizers.map((item) => item.id === selectedId ? nextItem : item);
  } else {
    pane.visualizers.push(nextItem);
    setSelectedVisualizerId(paneIndex, "");
  }
  if (state.selectedBacktest?.paneResults) delete state.selectedBacktest.paneResults[pane.id];
  if (state.selectedBacktest?.loadedPanes) delete state.selectedBacktest.loadedPanes[pane.id];
  syncVisualizationSpec(spec);
  return true;
}

function visualizerSummary(result, spec, pane, visualizer) {
  if (visualizer.displayName) return visualizer.displayName;
  const definition = window.TradeChartCore.visualizerCatalog(result, spec).find((item) => item.id === visualizer.callback);
  const params = visualizer.params || {};
  const summary = Object.entries(params)
    .filter(([, value]) => value !== undefined && value !== "")
    .map(([key, value]) => `${forms.humanizeName(key)}=${value}`)
    .join(", ");
  return `${definition?.label || visualizer.callback}${summary ? ` (${summary})` : ""}`;
}

function applyButtonLabel(base, selected) {
  return selected ? `Apply ${base}` : `Add ${base}`;
}

function addChartPane() {
  if (!state.selectedBacktest) {
    setResultsActionError("Run or select a backtest to visualize results.");
    return false;
  }
  setResultsActionError("");
  const result = { dataKeys: state.selectedBacktest.dataKeys || {} };
  const spec = normalizeVisualizationSpec(result, state.selectedBacktest.visualization || {});
  const index = spec.panes.length + 1;
  spec.panes.push({
    id: `chart-${index}`,
    title: `Custom Chart ${index}`,
    role: "chart",
    visualizers: [],
    temporaryModules: [],
  });
  state.selectedBacktest.loadedPanes ||= {};
  syncVisualizationSpec(spec);
}

function removeChartPane(paneIndex) {
  if (!state.selectedBacktest) return;
  const spec = state.selectedBacktest.visualization;
  const pane = spec.panes?.[paneIndex];
  spec.panes = (spec.panes || []).filter((_, index) => index !== paneIndex);
  if (pane?.id) {
    delete state.selectedBacktest.paneResults?.[pane.id];
    delete state.selectedBacktest.loadingPanes?.[pane.id];
    delete state.selectedBacktest.loadedPanes?.[pane.id];
  }
  syncVisualizationSpec(spec);
}

function toggleChartPaneCollapsed(paneIndex) {
  if (!state.selectedBacktest) return;
  const spec = state.selectedBacktest.visualization;
  const pane = spec.panes?.[paneIndex];
  if (!pane) return;
  pane.collapsed = !pane.collapsed;
  syncVisualizationSpec(spec);
}

function renderChartControls(result, spec, pane, paneIndex) {
  const scoped = paneScopedSpec(spec, pane);
  const controls = document.createElement("div");
  controls.className = "chart-controls";
  const tempTags = document.createElement("section");
  tempTags.className = "chart-tag-section";
  tempTags.innerHTML = `<h4>Temporary Instance Tags</h4><div class="chart-layer-tags">${
    (pane.temporaryModules || []).length
      ? (pane.temporaryModules || []).map((module) => {
        const outputs = Object.values(module.outputs || {}).join(", ");
        return `<button class="chart-layer-tag ${selectedTempModuleId(paneIndex) === module.instanceId ? "active" : ""}" data-select-temp-module="${module.instanceId}" data-pane-index="${paneIndex}" type="button"><span class="layer-key">${forms.humanizeName(module.moduleId)}</span><span class="layer-data-key">${outputs}</span></button><button class="tag-remove" data-remove-temp-module="${module.instanceId}" data-pane-index="${paneIndex}" type="button">Remove</button>`;
      }).join("")
      : '<span class="muted">No temporary modules</span>'
  }</div>`;
  const visualizerTags = document.createElement("section");
  visualizerTags.className = "chart-tag-section";
  visualizerTags.innerHTML = `<h4>Data Tags</h4><div class="chart-layer-tags">${
    (pane.visualizers || []).length
      ? (pane.visualizers || []).map((visualizer) => `<button class="chart-layer-tag ${selectedVisualizerId(paneIndex) === visualizer.id ? "active" : ""}" data-select-visualizer="${visualizer.id}" data-pane-index="${paneIndex}" type="button"><span class="layer-key">${visualizerSummary(result, scoped, pane, visualizer)}</span></button><button class="tag-remove" data-remove-layer="${visualizer.id}" data-pane-index="${paneIndex}" type="button">Remove</button>`).join("")
      : '<span class="muted">No visualizers</span>'
  }</div>`;
  const control = document.createElement("section");
  control.className = "chart-control-zone";
  const visualizers = window.TradeChartCore.visualizerCatalog(result, scoped);
  const selectedTemp = selectedTempModuleId(paneIndex) ? temporaryModuleById(paneIndex, selectedTempModuleId(paneIndex)) : null;
  const selectedTempKey = resultModuleDefinitionKey(selectedTemp);
  const selectedVisualizer = selectedVisualizerId(paneIndex) ? visualizerById(paneIndex, selectedVisualizerId(paneIndex)) : null;
  control.innerHTML = `
    <div class="chart-control-block">
      <h4>Template</h4>
      <select data-temp-module-select="${paneIndex}">
        <option value=""></option>
        ${resultModuleDefinitions().map((row) => `<option value="${row.key}" ${row.key === selectedTempKey ? "selected" : ""}>${row.kind} / ${row.moduleId} / ${row.version}</option>`).join("")}
      </select>
      <input data-temp-instance="${paneIndex}" placeholder="instance id" />
      <div data-temp-config-fields="${paneIndex}" class="structured-fields structured-fields-inline"></div>
      <div data-temp-inputs-fields="${paneIndex}" class="structured-fields structured-fields-inline"></div>
      <div data-temp-outputs-fields="${paneIndex}" class="structured-fields structured-fields-inline"></div>
      <button data-add-temp-module="${paneIndex}" type="button">${applyButtonLabel("Template", selectedTempModuleId(paneIndex))}</button>
    </div>
    <div class="chart-control-block">
      <h4>Data Display</h4>
      <select data-visualizer-select="${paneIndex}">
        <option value=""></option>
        ${visualizers.map((item) => `<option value="${item.id}" ${item.id === selectedVisualizer?.callback ? "selected" : ""}>${item.label}</option>`).join("")}
      </select>
      <div data-visualizer-fields="${paneIndex}" class="structured-fields structured-fields-inline"></div>
      <button data-add-visualizer="${paneIndex}" type="button">${applyButtonLabel("Visualizer", selectedVisualizerId(paneIndex))}</button>
    </div>
  `;
  controls.appendChild(tempTags);
  controls.appendChild(visualizerTags);
  controls.appendChild(control);
  const error = document.createElement("div");
  error.className = "chart-control-error";
  error.dataset.chartControlError = String(paneIndex);
  error.hidden = true;
  controls.appendChild(error);
  return controls;
}

function drawVisualization(spec) {
  const area = $("chartArea");
  state.resultCharts.forEach(({ chart, observer }) => {
    observer?.disconnect();
    chart?.remove?.();
  });
  state.resultCharts = [];
  area.innerHTML = "";
  const library = window.LightweightCharts;
  if (!library?.createChart) {
    const missing = document.createElement("div");
    missing.className = "muted";
    missing.textContent = "Chart library failed to load.";
    area.appendChild(missing);
    return;
  }
  const panes = spec.panes?.length ? spec.panes : [];
  const charts = [];
  panes.forEach((pane, paneIndex) => {
    const panel = document.createElement("div");
    panel.className = "chart-panel";
    panel.classList.toggle("collapsed", !!pane.collapsed);
    const title = document.createElement("div");
    title.className = "chart-title";
    title.innerHTML = `
      <span>${pane.title || pane.id || "Chart"}</span>
      <div class="chart-actions">
        <button class="inline-action" data-toggle-chart="${paneIndex}" type="button">${pane.collapsed ? "Expand" : "Collapse"}</button>
        <button class="inline-action" data-open-chart="${paneIndex}" type="button">Open Chart</button>
        <button class="inline-action danger" data-delete-chart="${paneIndex}" type="button">Delete</button>
      </div>
    `;
    panel.appendChild(title);
    area.appendChild(panel);
    if (pane.collapsed) return;
    const result = paneResult(pane);
    const scoped = paneScopedSpec(spec, pane);
    const controls = renderChartControls(result, scoped, pane, paneIndex);
    panel.appendChild(controls);
    if (!(pane.visualizers || []).length) {
      const empty = document.createElement("div");
      empty.className = "muted";
      empty.textContent = "No visualizers";
      panel.appendChild(empty);
      return;
    }
    const hasLoadedData = Object.keys(result).some((key) => key !== "dataKeys");
    if (!hasLoadedData && !paneHasLoaded(pane, spec)) {
      const loading = document.createElement("div");
      loading.className = "muted";
      loading.textContent = state.selectedBacktest?.loadingPanes?.[pane.id] ? "Loading chart data" : "Queueing chart data";
      panel.appendChild(loading);
      ensurePaneResultLoaded(pane, spec).catch((error) => setHealth(false, error.message));
      return;
    }
    if (!hasLoadedData && paneHasLoaded(pane, spec)) {
      const empty = document.createElement("div");
      empty.className = "muted";
      empty.textContent = "No chart data";
      panel.appendChild(empty);
      return;
    }
    const container = document.createElement("div");
    container.className = "tv-chart";
    container.style.height = "460px";
    panel.appendChild(container);
    const chart = window.TradeChartCore.createFinancialChart(container);
    window.TradeChartCore.drawFinancialPane(library, chart, result, pane, scoped);
    chart.timeScale().fitContent();
    charts.push(chart);
    const observer = new ResizeObserver(() => {
      if (container.isConnected) chart.applyOptions({ width: container.clientWidth });
    });
    observer.observe(container);
    state.resultCharts.push({ chart, observer });
  });
  area.querySelectorAll("[data-open-chart]").forEach((button) => {
    button.addEventListener("click", () => {
      const backtestId = $("resultBacktest").value || state.selectedBacktest.backtestId;
      window.open(`/chart.html?backtestId=${encodeURIComponent(backtestId)}&pane=${button.dataset.openChart}`, "_blank", "noopener,noreferrer");
    });
  });
  area.querySelectorAll("[data-toggle-chart]").forEach((button) => {
    button.addEventListener("click", () => {
      toggleChartPaneCollapsed(Number(button.dataset.toggleChart));
    });
  });
  area.querySelectorAll("[data-delete-chart]").forEach((button) => {
    button.addEventListener("click", () => {
      removeChartPane(Number(button.dataset.deleteChart));
    });
  });
  area.querySelectorAll("[data-temp-module-select]").forEach((select) => {
    const paneIndex = Number(select.dataset.tempModuleSelect);
    fillTemporaryModuleDraft(paneIndex);
    syncInitialPaneSelectionHint(paneIndex, "temp", select);
    setActionButtonLabels(paneIndex);
    select.addEventListener("change", () => {
      const paneIndex = Number(select.dataset.tempModuleSelect);
      setPaneSelectionHint(paneIndex, !select.value ? emptyPaneSelectionMessage("temp", select) : "");
      setPaneControlError(paneIndex, "");
      setSelectedTempModuleId(paneIndex, "");
      fillTemporaryModuleDraft(paneIndex);
      setActionButtonLabels(paneIndex);
    });
  });
  area.querySelectorAll("[data-add-temp-module]").forEach((button) => {
    button.addEventListener("click", () => {
      runUiAction("Adding temporary module", async () => {
        addPaneTemporaryModule(Number(button.dataset.addTempModule));
      });
    });
  });
  area.querySelectorAll("[data-visualizer-select]").forEach((select) => {
    const paneIndex = Number(select.dataset.visualizerSelect);
    fillVisualizerDraft(paneIndex);
    syncInitialPaneSelectionHint(paneIndex, "visualizer", select);
    setActionButtonLabels(paneIndex);
    select.addEventListener("change", () => {
      const paneIndex = Number(select.dataset.visualizerSelect);
      setPaneSelectionHint(paneIndex, !select.value ? emptyPaneSelectionMessage("visualizer", select) : "");
      setPaneControlError(paneIndex, "");
      setSelectedVisualizerId(paneIndex, "");
      fillVisualizerDraft(paneIndex);
      setActionButtonLabels(paneIndex);
    });
  });
  area.querySelectorAll("[data-select-temp-module]").forEach((button) => {
    button.addEventListener("click", () => {
      const paneIndex = Number(button.dataset.paneIndex);
      if (selectedTempModuleId(paneIndex) === button.dataset.selectTempModule) {
        setSelectedTempModuleId(paneIndex, "");
        setPaneSelectionHint(paneIndex, "");
        drawVisualization(state.selectedBacktest.visualization);
        return;
      }
      setPaneSelectionHint(paneIndex, "");
      setSelectedTempModuleId(paneIndex, button.dataset.selectTempModule);
      const module = temporaryModuleById(paneIndex, button.dataset.selectTempModule);
      const definition = resultModuleDefinitions().find((row) => row.kind === module?.kind && row.moduleId === module?.moduleId && row.version === module?.version);
      const select = area.querySelector(`[data-temp-module-select="${paneIndex}"]`);
      if (definition && select) select.value = definition.key;
      fillTemporaryModuleDraft(paneIndex);
      drawVisualization(state.selectedBacktest.visualization);
    });
  });
  area.querySelectorAll("[data-select-visualizer]").forEach((button) => {
    button.addEventListener("click", () => {
      const paneIndex = Number(button.dataset.paneIndex);
      if (selectedVisualizerId(paneIndex) === button.dataset.selectVisualizer) {
        setSelectedVisualizerId(paneIndex, "");
        setPaneSelectionHint(paneIndex, "");
        drawVisualization(state.selectedBacktest.visualization);
        return;
      }
      setPaneSelectionHint(paneIndex, "");
      setSelectedVisualizerId(paneIndex, button.dataset.selectVisualizer);
      const visualizer = visualizerById(paneIndex, button.dataset.selectVisualizer);
      const select = area.querySelector(`[data-visualizer-select="${paneIndex}"]`);
      if (visualizer && select) select.value = visualizer.callback;
      fillVisualizerDraft(paneIndex);
      drawVisualization(state.selectedBacktest.visualization);
    });
  });
  area.querySelectorAll("[data-add-visualizer]").forEach((button) => {
    button.addEventListener("click", () => {
      runUiAction("Adding visualizer", async () => {
        addPaneVisualizer(Number(button.dataset.addVisualizer));
      });
    });
  });
  area.querySelectorAll("[data-remove-layer]").forEach((button) => {
    button.addEventListener("click", () => {
      removePaneLayer(Number(button.dataset.paneIndex), button.dataset.removeLayer);
    });
  });
  area.querySelectorAll("[data-remove-temp-module]").forEach((button) => {
    button.addEventListener("click", () => {
      removePaneTemporaryModule(Number(button.dataset.paneIndex), button.dataset.removeTempModule);
    });
  });
  const clearPaneError = (event) => {
    clearPaneErrorForTarget(event.target);
    syncResultPaneActionState(area);
  };
  area.oninput = clearPaneError;
  area.onchange = clearPaneError;
  syncResultPaneActionState(area);
  window.TradeChartCore.synchronizeTimeScales(charts);
}

function renderOverview() {
  renderSummary();
  renderLaneSelector();
}

async function loadSummary() {
  const summary = await getJson("/api/summary");
  state.summary = summary;
  $("endpoint").textContent = `${location.origin} -> ${summary.paths.lanesManifestPath || summary.paths.liveManifestPath}`;
}

async function loadLanes() {
  const lanes = await getJson("/api/lanes");
  state.lanes = lanes.lanes || {};
  renderLaneSelector();
}

async function loadCurrent() {
  const current = await getJson(`/api/current?laneId=${encodeURIComponent(state.laneId)}`).catch(() => ({ manifest: null }));
  state.manifest = current.manifest;
  state.attachment = current.attachment || null;
}

async function loadModules(force = false) {
  const kind = state.selectedModuleKind;
  const cacheKey = `modules:${kind}`;
  if (!force && loadedViews.has(cacheKey) && state.moduleCacheByKind[kind]) {
    state.modules = state.moduleCacheByKind[kind];
    state.totals.modules = state.moduleTotalsByKind[kind] ?? Object.keys(state.modules).length;
    renderModules();
    return;
  }
  const modules = await getJson(`/api/modules?kind=${encodeURIComponent(kind)}&limit=80`);
  state.modules = modules.modules || {};
  state.moduleCacheByKind[kind] = state.modules;
  state.moduleTotalsByKind[kind] = modules.total ?? Object.keys(state.modules).length;
  state.totals.modules = state.moduleTotalsByKind[kind];
  loadedViews.add(cacheKey);
  loadedViews.add("modules");
  renderModules();
}

async function loadInstances(force = false) {
  if (!force && loadedViews.has("instances")) return;
  const instances = await getJson("/api/instances?limit=120");
  state.instances = instances.instances || {};
  state.totals.instances = instances.total ?? Object.keys(state.instances).length;
  loadedViews.add("instances");
  renderInstances();
}

async function loadArtifacts(force = false) {
  if (!force && loadedViews.has("artifacts")) return;
  const artifacts = await getJson("/api/artifacts?limit=50");
  state.artifacts = artifacts.artifacts || {};
  state.totals.artifacts = artifacts.total ?? Object.keys(state.artifacts).length;
  loadedViews.add("artifacts");
  renderArtifacts();
}

async function loadDatasets(force = false) {
  if (!force && loadedViews.has("datasets")) return;
  const datasets = await getJson("/api/data/datasets?limit=50");
  state.datasets = datasets.datasets || [];
  state.totals.datasets = datasets.total ?? state.datasets.length;
  loadedViews.add("datasets");
}

async function loadData(force = false) {
  if (!force && loadedViews.has("data")) return;
  const sources = await getJson("/api/data/sources");
  state.dataSources = sources.sources || [];
  state.dataProxy = sources.proxy || null;
  await loadDatasets(force);
  loadedViews.add("data");
  renderData();
}

async function loadBacktests(force = false) {
  if (!force && loadedViews.has("backtests")) return;
  await Promise.all([loadLanes(), loadDatasets(force)]);
  const backtests = await getJson("/api/backtests?limit=50");
  state.backtests = backtests.backtests || [];
  state.totals.backtests = backtests.total ?? state.backtests.length;
  loadedViews.add("backtests");
  renderBacktests();
}

async function loadPipeline(force = false) {
  if (!force && loadedViews.has("pipeline")) return;
  const modules = await getJson("/api/modules?limit=500");
  state.pipelineModules = modules.modules || {};
  state.pipelineSignalModules = Object.fromEntries(
    Object.entries(state.pipelineModules).filter(([, value]) => value.kind === "Signal"),
  );
  await Promise.all([loadLanes(), loadCurrent(), loadInstances(force)]);
  loadedViews.add("pipeline");
  loadPipelineFormFromAttachment();
}

async function loadResults(force = false) {
  if (force || !Object.keys(state.resultModules || {}).length) {
    const modules = await getJson("/api/modules?limit=500");
    state.resultModules = modules.modules || {};
  }
  await loadBacktests(force);
  renderBacktests();
  refreshSelectedBacktest().catch((error) => setHealth(false, error.message));
}

async function refreshOverview() {
  setHealth(false, "Loading");
  await Promise.all([loadSummary(), loadLanes(), loadCurrent()]);
  renderOverview();
  setHealth(true, "Online");
}

async function refreshCurrentView(force = true) {
  setHealth(false, "Loading");
  loadedViews.delete(currentView);
  await ensureViewData(currentView, force);
  setHealth(true, "Online");
}

async function ensureViewData(viewId, force = false) {
  if (viewId === "overview") {
    await refreshOverview();
  } else if (viewId === "pipeline") {
    await loadPipeline(force);
  } else if (viewId === "modules") {
    await loadModules(force);
  } else if (viewId === "instances") {
    await loadInstances(force);
  } else if (viewId === "data") {
    await loadData(force);
  } else if (viewId === "backtests") {
    await loadBacktests(force);
  } else if (viewId === "results") {
    await loadResults(force);
  } else if (viewId === "artifacts") {
    await loadArtifacts(force);
  } else if (viewId === "manifest") {
    await loadCurrent();
    renderManifest();
  } else if (viewId === "history") {
    renderHistory();
  }
}

async function refreshSelectedBacktest() {
  const backtestId = $("resultBacktest").value || state.backtests[0]?.backtestId;
  if (!backtestId) return;
  state.selectedBacktest = await getJson(`/api/backtests/${encodeURIComponent(backtestId)}/meta`);
  state.selectedBacktest.paneResults = {};
  state.selectedBacktest.loadingPanes = {};
  state.selectedBacktest.loadedPanes = {};
  renderResults();
}

document.querySelectorAll(".nav-btn").forEach((button) => {
  button.addEventListener("click", () => {
    if (button.disabled) return;
    if (button.dataset.view === "pipeline") {
      currentPipelineSection = "composer";
    }
    switchView(button.dataset.view);
  });
});

window.addEventListener("popstate", () => {
  switchView(normalizedViewFromPath(location.pathname), { push: false });
});

$("refreshBtn").addEventListener("click", () => {
  if ($("refreshBtn")?.disabled) return;
  refreshCurrentView(true).catch((error) => setHealth(false, error.message));
});
$("laneSelect").addEventListener("change", (event) => {
  if ($("laneSelect")?.disabled) return;
  state.laneId = event.target.value || "main";
  pipelineField("LaneId").value = "";
  loadedViews.delete("pipeline");
  loadedViews.delete("manifest");
  ensureViewData(currentView, true).catch((error) => setHealth(false, error.message));
});
$("moduleFilter").addEventListener("input", renderModules);
$("instanceFilter").addEventListener("input", renderInstances);
$("loadPipelineBtn").addEventListener("click", () => {
  if ($("loadPipelineBtn")?.disabled) return;
  loadPipelineFormFromAttachment({ preferDraft: false, discardDraft: true });
});
document.querySelectorAll(".pipeline-subnav-btn").forEach((button) => {
  button.addEventListener("click", () => {
    if (button.disabled) return;
    switchPipelineSection(button.dataset.pipelineSection);
  });
});
$("blueprintBackToPipelineBtn")?.addEventListener("click", () => {
  currentPipelineSection = "composer";
  switchView("pipeline");
});
$("attachPipelineBtn").addEventListener("click", () => {
  if ($("attachPipelineBtn")?.disabled) return;
  runUiAction("Attaching", async () => {
    setPipelineBlueprintBusyState({ attachInFlight: true });
    try {
      await attachCurrentPipeline();
    } finally {
      setPipelineBlueprintBusyState({ attachInFlight: false });
    }
  });
});
$("dataSearch").addEventListener("input", async (event) => {
  const q = event.target.value.trim();
  if (!q) {
    $("dataSearchList").innerHTML = "";
    renderData();
    return;
  }
  const result = await getJson(`/api/data/search?q=${encodeURIComponent(q)}`);
  $("dataSearchList").innerHTML = (result.candidates || []).map((item) => (
    `<button class="inline-action" data-symbol="${item.symbol}" type="button">${item.symbol}</button>`
  )).join("");
  document.querySelectorAll("#dataSearchList .inline-action").forEach((button) => {
    button.addEventListener("click", () => {
      $("downloadSymbol").value = button.dataset.symbol;
      setDataDownloadError("");
      syncDataDownloadActionState();
    });
  });
});
$("downloadSource").addEventListener("change", renderData);
$("downloadEnd").valueAsDate = new Date();
$("downloadDataBtn").addEventListener("click", () => {
  runUiAction("Downloading", async () => {
    const sourceId = $("downloadSource").value;
    const selectedSource = state.dataSources.find((source) => source.source === sourceId);
    const symbol = $("downloadSymbol").value.trim();
    const startDate = $("downloadStart").value;
    const endDate = $("downloadEnd").value;
    const apiKey = $("downloadApiKey").value.trim();
    if (!sourceId) {
      const message = "No data source available";
      setDataDownloadError(message);
      throw localUiError(message, "DATA_DOWNLOAD_VALIDATION");
    }
    if (!symbol) {
      const message = "Symbol is required";
      setDataDownloadError(message);
      throw localUiError(message, "DATA_DOWNLOAD_VALIDATION");
    }
    if (selectedSource?.requiresKey && !apiKey) {
      const message = "API key is required for the selected source";
      setDataDownloadError(message);
      throw localUiError(message, "DATA_DOWNLOAD_VALIDATION");
    }
    if (startDate && endDate && startDate > endDate) {
      const message = "Start date must be on or before end date";
      setDataDownloadError(message);
      throw localUiError(message, "DATA_DOWNLOAD_VALIDATION");
    }
    setDataDownloadError("");
    await postJson("/api/data/download", {
      source: sourceId,
      symbol,
      apiKey,
      startDate,
      endDate,
      interval: $("downloadInterval").value,
    });
    loadedViews.delete("data");
    await Promise.all([loadSummary(), loadData(true)]);
    setHealth(true, "Online");
  });
});
$("uploadDataBtn").addEventListener("click", () => {
  runUiAction("Uploading", async () => {
    const datasetId = $("uploadDatasetId").value.trim();
    const normalizedDatasetId = normalizeDatasetId(datasetId);
    const symbol = $("uploadSymbol").value.trim();
    const file = $("uploadCsv").files[0];
    if (!datasetId) {
      const message = "Dataset is required";
      setDataUploadError(message);
      throw localUiError(message, "DATA_UPLOAD_VALIDATION");
    }
    if (!normalizedDatasetId) {
      const message = "Dataset must contain letters or numbers";
      setDataUploadError(message);
      throw localUiError(message, "DATA_UPLOAD_VALIDATION");
    }
    const existingDataset = (state.datasets || []).find((row) => normalizeDatasetId(row.datasetId) === normalizedDatasetId);
    if (existingDataset) {
      const message = `Dataset ${existingDataset.datasetId} already exists`;
      setDataUploadError(message);
      throw localUiError(message, "DATA_UPLOAD_VALIDATION");
    }
    if (!symbol) {
      const message = "Symbol is required";
      setDataUploadError(message);
      throw localUiError(message, "DATA_UPLOAD_VALIDATION");
    }
    if (!file) {
      const message = "CSV file required";
      setDataUploadError(message);
      throw localUiError(message, "DATA_UPLOAD_VALIDATION");
    }
    if (uploadCsvValidationState.pending) {
      const message = "Validating CSV file";
      setDataUploadError(message);
      throw localUiError(message, "DATA_UPLOAD_VALIDATION");
    }
    if (uploadCsvValidationState.error) {
      setDataUploadError(uploadCsvValidationState.error);
      throw localUiError(uploadCsvValidationState.error, "DATA_UPLOAD_VALIDATION");
    }
    const csvText = await file.text();
    const csvError = validateUploadCsvText(csvText);
    if (csvError) {
      setDataUploadError(csvError);
      throw localUiError(csvError, "DATA_UPLOAD_VALIDATION");
    }
    setDataUploadError("");
    await postJson("/api/data/upload", {
      datasetId,
      symbol,
      csvText,
    });
    loadedViews.delete("data");
    await Promise.all([loadSummary(), loadData(true)]);
    setHealth(true, "Online");
  });
});
$("runBacktestBtn").addEventListener("click", () => {
  runUiAction("Running", async () => {
    const datasetId = $("backtestDataset").value;
    if (!datasetId) {
      const message = "Dataset is required";
      setBacktestEntryError(message);
      throw localUiError(message, "BACKTEST_ENTRY_VALIDATION");
    }
    const dataset = (state.datasets || []).find((row) => row.datasetId === datasetId);
    if (dataset && Number(dataset.rowCount || 0) < 2) {
      const message = "Dataset must contain at least two bars";
      setBacktestEntryError(message);
      throw localUiError(message, "BACKTEST_ENTRY_VALIDATION");
    }
    setBacktestEntryError("");
    const response = await postJson("/api/backtests", {
      laneId: $("backtestLane").value || state.laneId,
      datasetId,
      name: $("backtestName").value,
      runner: $("backtestRunner").value,
    });
    $("backtestStatus").textContent = response.backtest?.backtestId || "completed";
    loadedViews.delete("backtests");
    await Promise.all([loadSummary(), loadBacktests(true)]);
    $("resultBacktest").value = response.backtest?.backtestId || $("resultBacktest").value;
    await refreshSelectedBacktest();
    switchView("results");
  });
});
$("resultBacktest").addEventListener("change", refreshSelectedBacktest);
$("addChartBtn").addEventListener("click", addChartPane);
$("pipelineAlphaGraph").addEventListener("input", () => {
  if ($("pipelineAlphaGraph")?.disabled) return;
  syncPipelineAlphaGraphInputState();
});
$("downloadSource").addEventListener("change", syncDataDownloadActionState);
$("downloadSymbol").addEventListener("input", syncDataDownloadActionState);
$("downloadStart").addEventListener("change", syncDataDownloadActionState);
$("downloadEnd").addEventListener("change", syncDataDownloadActionState);
$("downloadInterval").addEventListener("change", syncDataDownloadActionState);
$("downloadApiKey").addEventListener("input", syncDataDownloadActionState);
$("uploadDatasetId").addEventListener("input", syncDataUploadActionState);
$("uploadSymbol").addEventListener("input", syncDataUploadActionState);
$("uploadCsv").addEventListener("change", async () => {
  await validateSelectedUploadCsvFile();
});
$("backtestLane").addEventListener("change", syncBacktestActionState);
$("backtestDataset").addEventListener("change", () => {
  syncBacktestActionState();
});
$("backtestName").addEventListener("input", syncBacktestActionState);
$("backtestRunner").addEventListener("change", syncBacktestActionState);
$("visualizationSpec").addEventListener("input", syncVisualizationSpecInputState);
$("saveVisualizationBtn").addEventListener("click", () => {
  runUiAction("Saving", async () => {
    const backtestId = $("resultBacktest").value;
    if (!backtestId) {
      setVisualizationSpecError("Select a backtest before saving.");
      return;
    }
    setResultsActionError("");
    let spec;
    try {
      spec = JSON.parse($("visualizationSpec").value || "{}");
    } catch (error) {
      setVisualizationSpecError(error?.message || "Invalid visualization spec");
      return;
    }
    setVisualizationSpecError("");
    await postJson("/api/visualizations", {
      backtestId,
      name: "current",
      visualizationId: `${backtestId}-current`,
      spec,
    });
    state.selectedBacktest.visualization = spec;
    renderResults();
    setHealth(true, "Online");
  });
});
$("cancelUnloadBtn").addEventListener("click", () => $("unloadDialog").close());
$("cancelModuleLoadBtn").addEventListener("click", () => {
  pendingModuleLoad = null;
  setModuleLoadDialogError("");
  $("moduleLoadDialog").oninput = null;
  $("moduleLoadDialog").onchange = null;
  $("moduleLoadDialog").close();
});
$("confirmModuleLoadBtn").addEventListener("click", () => {
  if ($("confirmModuleLoadBtn")?.disabled) return;
  runUiAction("Loading module", async () => confirmModuleLoad());
});

currentView = normalizedViewFromPath(location.pathname);
switchView(currentView, { push: false });
refreshOverview().catch((error) => setHealth(false, error.message));
