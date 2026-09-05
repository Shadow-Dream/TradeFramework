const params = new URLSearchParams(location.search);
const backtestId = params.get("backtestId") || "";
const requestedPaneId = String(params.get("paneId") || "").trim();
const legacyPaneValue = String(params.get("pane") || "").trim();

function opaqueClientId(prefix) {
  const random = globalThis.crypto?.randomUUID?.().replaceAll("-", "")
    || `${Date.now().toString(36)}${Math.random().toString(36).slice(2)}`;
  return `${prefix}_${random}`;
}

function semanticDataKeySegment(value, fallback = "module") {
  return String(value || fallback)
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9_]+/g, "_")
    .replace(/^_+|_+$/g, "") || fallback;
}
let paneIndex = 0;
const RESULT_REQUEST_TIMEOUT_MS = 30000;
const pageState = {
  backtest: null,
  result: {},
  spec: null,
  pane: null,
  paneId: "",
  chart: null,
  chartContext: null,
  chartContainer: null,
  chartTimeZone: "",
  chartShowTime: false,
  drawingToolbar: null,
  observer: null,
  saveTimer: null,
  saveSeq: 0,
  saveCompletedSeq: 0,
  saveQueue: Promise.resolve(),
  saveEpoch: 0,
  visualizationId: "",
  visualizationRevision: 0,
  saveLifecycleBound: false,
  lifecycleFlushKey: "",
  viewSaveTimer: null,
  viewRangeUnsubscribe: null,
  suppressViewRangeEvents: false,
  resultStatus: "idle",
  resultError: "",
  projectionCache: { entries: new Map(), generation: 0, planningError: null },
  resultModules: {},
  timeInfo: { start: null, end: null, showTime: false },
  rangeStartInput: null,
  rangeEndInput: null,
  drawingUi: {
    toolId: "",
    bindings: {},
    selectedVisualizerId: "",
  },
  drawingCleanup: null,
};
let authState = { user: null, csrfToken: "", expiresAt: 0 };

const forms = window.TradeModuleForms;

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function semanticDisplayText(candidates, forbiddenValues = [], fallback = "") {
  const forbidden = new Set(forbiddenValues.map((value) => String(value || "").trim()).filter(Boolean));
  for (const candidate of candidates) {
    const original = typeof candidate === "string" ? candidate.trim() : "";
    if (!original || forbidden.has(original)) continue;
    const value = typeof forms?.userFacingText === "function"
      ? forms.userFacingText(original, fallback)
      : original;
    if (value) return value;
  }
  return fallback;
}

function ordinalDisplayLabels(items, identity, baseLabel) {
  const bases = items.map((item) => baseLabel(item));
  const counts = new Map();
  bases.forEach((base) => counts.set(base, (counts.get(base) || 0) + 1));
  const seen = new Map();
  const result = new Map();
  items.forEach((item, index) => {
    const base = bases[index];
    const ordinal = (seen.get(base) || 0) + 1;
    seen.set(base, ordinal);
    result.set(identity(item), counts.get(base) > 1 ? `${base} ${ordinal}` : base);
  });
  return result;
}

function safeVisualizerCatalog() {
  try {
    return window.TradeChartCore.visualizerCatalog(
      { dataKeys: pageState.backtest?.dataKeys || {} }, paneScopedSpec(),
    );
  } catch {
    return [];
  }
}

function visualizerDefinitionDisplayLabel(definition) {
  return semanticDisplayText(
    [definition?.label],
    [definition?.id],
    "Data Display",
  );
}

function visualizerDisplayLabelMap(pane = pageState.pane, catalog = safeVisualizerCatalog()) {
  const definitions = new Map((catalog || []).map((definition) => [definition.id, definition]));
  return ordinalDisplayLabels(
    pane?.visualizers || [],
    (item) => item?.id,
    (item) => {
      const definition = definitions.get(item?.callback);
      return semanticDisplayText(
        [item?.displayName, definition?.label],
        [item?.id, item?.callback, definition?.id],
        "Data Display",
      );
    },
  );
}

function temporaryModuleDisplayLabelMap(pane = pageState.pane) {
  return ordinalDisplayLabels(
    pane?.temporaryModules || [],
    (item) => item?.instanceId,
    (item) => {
      const definition = resultModuleDefinitionForInstance(item);
      return semanticDisplayText(
        [definition?.name],
        [item?.instanceId, item?.moduleId, definition?.moduleId],
        "Temporary Module",
      );
    },
  );
}

function paneDisplayTitle(pane = pageState.pane) {
  return semanticDisplayText([pane?.title], [pane?.id], "Chart");
}

function currentTimeZone() {
  const timeZone = pageState.spec?.timeZone || "UTC";
  return { timeZone };
}

function redirectToLogin() {
  const next = `${location.pathname}${location.search}${location.hash}`;
  location.replace(`/login?next=${encodeURIComponent(next)}`);
}

async function authenticatedFetch(path, options = {}) {
  const response = await fetch(path, { credentials: "same-origin", cache: "no-store", ...options });
  if (response.status === 401) redirectToLogin();
  return response;
}

async function loadBrowserSession() {
  const response = await authenticatedFetch("/auth/session", { headers: { Accept: "application/json" } });
  if (!response.ok) throw new Error("Authentication required.");
  const session = await response.json();
  authState = { user: session.user || null, csrfToken: session.csrfToken || "", expiresAt: session.expiresAt || 0 };
}

async function getJson(path) {
  const response = await authenticatedFetch(path, { headers: { Accept: "application/json" } });
  if (!response.ok) throw new Error(`${path} returned ${response.status}`);
  return response.json();
}

async function postJson(path, payload, {
  keepalive = false, signal = null, projection = false,
} = {}) {
  const response = await authenticatedFetch(path, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Accept: "application/json",
      "X-CSRF-Token": authState.csrfToken,
    },
    body: JSON.stringify(payload),
    ...(keepalive ? { keepalive: true } : {}),
    ...(signal ? { signal } : {}),
  });
  const data = response.ok
    ? (projection && window.TradeChartCore?.parseProjectionResponse
      ? await window.TradeChartCore.parseProjectionResponse(response)
      : await response.json())
    : await response.json().catch(() => ({}));
  if (!response.ok || data.accepted === false) {
    const error = new Error(data.error || `${path} returned ${response.status}`);
    error.status = response.status;
    error.payload = data;
    throw error;
  }
  return data;
}

async function postResultJson(path, payload, controller, timeoutMessage) {
  let timedOut = false;
  const timeout = setTimeout(() => {
    timedOut = true;
    controller.abort();
  }, RESULT_REQUEST_TIMEOUT_MS);
  try {
    return await postJson(path, payload, { signal: controller.signal, projection: true });
  } catch (error) {
    if (timedOut) throw new Error(timeoutMessage);
    throw error;
  } finally {
    clearTimeout(timeout);
  }
}

function schemaDefaults(schema = {}) {
  return forms.schemaDefaults(schema);
}

function paneScopedSpec() {
  return {
    ...pageState.spec,
    temporaryModules: [
      ...(pageState.spec?.temporaryModules || []),
      ...(pageState.pane?.temporaryModules || []),
    ],
  };
}

function resultMetadataOnly() {
  return { dataKeys: pageState.backtest?.dataKeys || {} };
}

function instanceValueMap(source = null) {
  const result = Object.create(null);
  for (const [key, value] of Object.entries(source || {})) {
    Object.defineProperty(result, key, {
      value, enumerable: true, configurable: true, writable: true,
    });
  }
  return result;
}

function setInstanceValue(target, visualizerId, value) {
  Object.defineProperty(target, String(visualizerId || ""), {
    value, enumerable: true, configurable: true, writable: true,
  });
}

function resolveRequestedPaneIndex(spec) {
  const panes = spec?.panes || [];
  if (requestedPaneId) {
    const index = panes.findIndex((pane) => pane?.id === requestedPaneId);
    if (index < 0) throw new Error("Requested chart pane is unavailable.");
    return index;
  }
  if (legacyPaneValue && !/^\d+$/.test(legacyPaneValue)) {
    const index = panes.findIndex((pane) => pane?.id === legacyPaneValue);
    if (index < 0) throw new Error("Requested chart pane is unavailable.");
    return index;
  }
  const index = legacyPaneValue ? Number(legacyPaneValue) : 0;
  if (!Number.isSafeInteger(index) || index < 0 || !panes[index]) {
    throw new Error("Requested chart pane is unavailable.");
  }
  return index;
}

function syncPaneReference() {
  const panes = pageState.spec?.panes || [];
  if (pageState.paneId) {
    const nextIndex = panes.findIndex((pane) => pane?.id === pageState.paneId);
    if (nextIndex < 0) throw new Error("Requested chart pane is unavailable.");
    paneIndex = nextIndex;
  }
  pageState.pane = panes[paneIndex];
  if (!pageState.pane) throw new Error("Requested chart pane is unavailable.");
  pageState.paneId = pageState.pane.id;
  return pageState.pane;
}

function paneResultRequest() {
  const scoped = paneScopedSpec();
  const plans = window.TradeChartCore.visualizerDependencyPlan(
    resultMetadataOnly(), pageState.pane, scoped,
  ).map((plan) => ({
    visualizerId: String(plan.visualizerId || ""),
    paths: [...(plan.paths || [])].sort(),
    temporaryModules: plan.temporaryModules || [],
    planningError: plan.planningError || null,
  })).filter((plan) => plan.planningError || plan.paths.length || plan.temporaryModules.length)
    .map((plan) => ({
      ...plan,
      dependencyKey: JSON.stringify({
        paths: plan.paths,
        temporaryModules: plan.temporaryModules,
        planningError: plan.planningError,
      }),
    }))
    .sort((left, right) => left.visualizerId.localeCompare(right.visualizerId));
  return { plans };
}

function projectionCache() {
  if (!(pageState.projectionCache?.entries instanceof Map)) {
    pageState.projectionCache = { entries: new Map(), generation: 0, planningError: null };
  }
  return pageState.projectionCache;
}

function abortVisualizerProjection(entry) {
  try { entry?.controller?.abort?.(); } catch { /* best-effort cancellation */ }
}

function abortProjectionCache(cache = projectionCache()) {
  for (const entry of cache.entries.values()) abortVisualizerProjection(entry);
}

function reconcileProjectionCache(cache, request) {
  const plansById = new Map(request.plans.map((plan) => [plan.visualizerId, plan]));
  for (const [visualizerId, entry] of cache.entries) {
    const plan = plansById.get(visualizerId);
    if (plan && entry.dependencyKey === plan.dependencyKey) continue;
    abortVisualizerProjection(entry);
    cache.entries.delete(visualizerId);
  }
}

function currentPaneProjection() {
  const cache = projectionCache();
  try {
    const request = paneResultRequest();
    cache.planningError = null;
    reconcileProjectionCache(cache, request);
    return { cache, request };
  } catch (error) {
    console.error("Standalone chart dependency planning failed", error);
    cache.planningError = {
      message: "Chart data dependencies could not be planned.",
    };
    return { cache, request: null };
  }
}

function paneResultWrapper(cache, request = null) {
  const instanceResults = instanceValueMap();
  const errors = instanceValueMap();
  const plans = request?.plans || [...cache.entries].map(([visualizerId, entry]) => ({
    visualizerId, dependencyKey: entry.dependencyKey,
  }));
  for (const plan of plans) {
    const entry = cache.entries.get(plan.visualizerId);
    if (!entry || entry.dependencyKey !== plan.dependencyKey) continue;
    if (entry.status === "ready") {
      setInstanceValue(instanceResults, plan.visualizerId, entry.result || {});
    } else if (entry.status === "error") {
      setInstanceValue(errors, plan.visualizerId, entry.error || {
        message: "Chart data failed to load.",
      });
    }
  }
  return { ...resultMetadataOnly(), instanceResults, errors };
}

function pendingVisualizerIds(cache, request) {
  return new Set((request?.plans || []).filter((plan) => {
    const entry = cache.entries.get(plan.visualizerId);
    return !entry || (
      entry.dependencyKey === plan.dependencyKey && entry.status === "loading"
    );
  }).map((plan) => plan.visualizerId));
}

function publishPaneProjection(projection = currentPaneProjection()) {
  const { cache, request } = projection;
  pageState.result = paneResultWrapper(cache, request);
  if (!request) {
    pageState.resultStatus = "error";
    pageState.resultError = cache.planningError?.message
      || "Chart data dependencies could not be planned.";
    const status = document.getElementById("chartStatus");
    if (status) status.textContent = pageState.resultError;
  } else {
    const pending = pendingVisualizerIds(cache, request);
    const hasProjectedResult = Object.keys(pageState.result.instanceResults).length > 0
      || Object.keys(pageState.result.errors).length > 0;
    pageState.resultStatus = pending.size && !hasProjectedResult ? "loading" : "ready";
    pageState.resultError = "";
  }
  drawPane();
  return pageState.resultStatus === "ready";
}

async function loadPaneResult({ force = false, visualizerId = "" } = {}) {
  const projection = currentPaneProjection();
  const { cache, request } = projection;
  if (!request) {
    publishPaneProjection(projection);
    return false;
  }
  const selectedPlans = visualizerId
    ? request.plans.filter((plan) => plan.visualizerId === visualizerId)
    : request.plans;
  if (visualizerId && !selectedPlans.length) {
    publishPaneProjection(projection);
    return false;
  }
  const tasks = [];
  for (const plan of selectedPlans) {
    const existing = cache.entries.get(plan.visualizerId);
    const retryExisting = force && (
      visualizerId === plan.visualizerId
      || (!visualizerId && existing?.status === "error")
    );
    if (existing?.dependencyKey === plan.dependencyKey && !retryExisting) continue;
    if (existing) abortVisualizerProjection(existing);
    if (plan.planningError) {
      cache.entries.set(plan.visualizerId, {
        dependencyKey: plan.dependencyKey,
        generation: ++cache.generation,
        status: "error",
        controller: null,
        result: null,
        error: {
          code: "instance-planning-error",
          message: plan.planningError.message || "Chart data dependencies could not be planned.",
          planningCode: plan.planningError.code || "dependency-planning-error",
        },
      });
      continue;
    }
    const controller = new AbortController();
    const entry = {
      dependencyKey: plan.dependencyKey,
      generation: ++cache.generation,
      status: "loading",
      controller,
      result: null,
      error: null,
    };
    cache.entries.set(plan.visualizerId, entry);
    const generation = entry.generation;
    tasks.push((async () => {
      try {
        const response = await postResultJson(
          `/api/backtests/${encodeURIComponent(backtestId)}/result`,
          {
            paths: plan.paths,
            temporaryModules: plan.temporaryModules,
            projectionFormat: "columns-v2",
            window: null,
          },
          controller,
          "Chart data request timed out. Retry when the Result service is available.",
        );
        const current = cache.entries.get(plan.visualizerId);
        if (
          projectionCache() !== cache
          || current !== entry
          || current.generation !== generation
          || current.dependencyKey !== plan.dependencyKey
        ) return;
        entry.status = "ready";
        entry.result = response.result || {};
        entry.error = null;
      } catch (error) {
        console.error("Standalone chart Result slice failed", error);
        const current = cache.entries.get(plan.visualizerId);
        if (
          projectionCache() !== cache
          || current !== entry
          || current.generation !== generation
          || current.dependencyKey !== plan.dependencyKey
        ) return;
        entry.status = "error";
        entry.result = null;
        entry.error = { message: error?.message || "Chart data could not be loaded." };
      } finally {
        const current = cache.entries.get(plan.visualizerId);
        if (current === entry) {
          entry.controller = null;
          publishPaneProjection();
        }
      }
    })());
  }
  publishPaneProjection(projection);
  if (!tasks.length) return true;
  await Promise.all(tasks);
  return true;
}

function uiState() {
  pageState.ui ||= { selectedTempModuleId: "", selectedVisualizerId: "" };
  return pageState.ui;
}

function temporaryModuleActionState() {
  const module = selectedResultModule();
  if (!module) return { disabled: true, title: "Select a Template first" };
  try {
    forms.readSchemaFields(document.querySelector("[data-temp-config-fields]"), module.configSchema);
    const inputs = forms.readParamFields(
      document.querySelector("[data-temp-inputs-fields]"),
      Object.keys(module.ports?.inputs || {}).map((name) => ({ name, type: "dataKey" })),
    );
    const outputs = forms.readParamFields(
      document.querySelector("[data-temp-outputs-fields]"),
      Object.keys(module.ports?.outputs || {}).map((name) => ({ name, type: "string" })),
    );
    validateTemporaryModuleBindings(inputs, outputs, module);
    return { disabled: false, title: "" };
  } catch (error) {
    return {
      disabled: true,
      title: typeof forms?.userFacingText === "function"
        ? forms.userFacingText(error?.message, "Complete the required Template fields")
        : "Complete the required Template fields",
    };
  }
}

function visualizerActionState() {
  const definition = selectedVisualizerDefinition();
  if (!definition) return { disabled: true, title: "Select a Data Display first" };
  try {
    const selectedItem = visualizerById(uiState().selectedVisualizerId);
    const editorDefinition = visualizerDefinitionForEditor(
      definition, selectedItem?.params || {},
    );
    const params = forms.readParamFields(
      document.querySelector("[data-visualizer-fields]"),
      editorDefinition?.params || [],
    );
    const missing = (definition.params || []).filter((field) => (
      field.required && (params[field.name] === undefined || params[field.name] === "")
    ));
    if (missing.length) {
      throw new Error(`Missing visualizer params: ${missing.map((field) => field.label || field.name).join(", ")}`);
    }
    validateDataKeyBindings(params, definition.inputPorts || {});
    validateVisualizerOverlayDependencies(definition, params);
    return { disabled: false, title: "" };
  } catch (error) {
    return {
      disabled: true,
      title: typeof forms?.userFacingText === "function"
        ? forms.userFacingText(error?.message, "Complete the required Data Display fields")
        : "Complete the required Data Display fields",
    };
  }
}

function setActionButtonLabels() {
  const tempButton = document.querySelector('[data-add-temp-module="1"]');
  if (tempButton) {
    const action = temporaryModuleActionState();
    tempButton.textContent = applyButtonLabel("Template", uiState().selectedTempModuleId);
    tempButton.disabled = action.disabled;
    tempButton.title = action.title;
  }
  const visualizerButton = document.querySelector('[data-add-visualizer="1"]');
  if (visualizerButton) {
    const action = visualizerActionState();
    visualizerButton.textContent = applyButtonLabel("Visualizer", uiState().selectedVisualizerId);
    visualizerButton.disabled = action.disabled;
    visualizerButton.title = action.title;
  }
}

function syncSpec() {
  syncPaneReference();
  scheduleSpecSave();
  void loadPaneResult();
}

function scheduleSpecSave() {
  const saveSeq = ++pageState.saveSeq;
  pageState.lifecycleFlushKey = "";
  clearTimeout(pageState.saveTimer);
  document.getElementById("chartStatus").textContent = "Saving";
  pageState.saveTimer = setTimeout(() => {
    pageState.saveTimer = null;
    void enqueueSpecSave(saveSeq, structuredClone(pageState.spec)).catch(() => {});
  }, 350);
}

function enqueueSpecSave(saveSeq, spec, { keepalive = false } = {}) {
  const epoch = pageState.saveEpoch;
  const operation = pageState.saveQueue.then(async () => {
    if (epoch !== pageState.saveEpoch) {
      throw new Error("Visualization save was superseded by a newer read.");
    }
    const expectedRevision = pageState.visualizationRevision;
    if (!Number.isSafeInteger(expectedRevision) || expectedRevision < 0) {
      throw new Error("Visualization revision is unavailable; read it again before saving.");
    }
    if (typeof pageState.visualizationId !== "string" || !pageState.visualizationId.trim()) {
      throw new Error("Visualization current identity is unavailable; read it again before saving.");
    }
    try {
      const response = await saveCurrentSpec(spec, {
        keepalive,
        expectedRevision,
      });
      if (epoch !== pageState.saveEpoch) return response;
      const saved = response.visualization;
      if (
        saved?.backtestId !== backtestId
        || saved?.visualizationId !== pageState.visualizationId
        || saved?.revision !== expectedRevision + 1
      ) {
        throw new Error("Visualization save returned invalid revision evidence.");
      }
      pageState.visualizationRevision = saved.revision;
      return response;
    } catch (error) {
      if (visualizationRevisionConflict(error) && epoch === pageState.saveEpoch) {
        await reloadVisualizationAfterConflict();
        const conflict = new Error(
          "Visualization changed elsewhere. The current saved revision was reloaded."
        );
        conflict.code = "visualization-reloaded";
        throw conflict;
      }
      throw error;
    }
  });
  pageState.saveQueue = operation.catch(() => null);
  return operation.then(
    (value) => {
      pageState.saveCompletedSeq = Math.max(pageState.saveCompletedSeq, saveSeq);
      if (saveSeq === pageState.saveSeq) {
        document.getElementById("chartStatus").textContent = "Saved";
      }
      return value;
    },
    (error) => {
      if (saveSeq === pageState.saveSeq) {
        console.error("Standalone Visualization save failed", error);
        document.getElementById("chartStatus").textContent = error?.code === "visualization-reloaded"
          ? "The saved chart changed elsewhere and was reloaded."
          : "Visualization failed to save.";
      }
      throw error;
    },
  );
}

function flushSpecSave({ keepalive = false } = {}) {
  clearTimeout(pageState.viewSaveTimer);
  pageState.viewSaveTimer = null;
  clearTimeout(pageState.saveTimer);
  pageState.saveTimer = null;
  if (!pageState.backtest || !pageState.spec) return Promise.resolve(null);
  const saveSeq = ++pageState.saveSeq;
  document.getElementById("chartStatus").textContent = "Saving";
  return enqueueSpecSave(saveSeq, structuredClone(pageState.spec), { keepalive });
}

function flushSpecSaveForLifecycle() {
  if (!pageState.backtest || !pageState.spec) return;
  const hasUnsettledSave = (
    pageState.saveTimer !== null
    || pageState.viewSaveTimer !== null
    || pageState.saveCompletedSeq < pageState.saveSeq
  );
  if (!hasUnsettledSave) return;
  const snapshotKey = JSON.stringify(pageState.spec);
  if (pageState.lifecycleFlushKey === snapshotKey) return;
  pageState.lifecycleFlushKey = snapshotKey;
  const clearLifecycleFlush = () => {
    if (pageState.lifecycleFlushKey === snapshotKey) pageState.lifecycleFlushKey = "";
  };
  void flushSpecSave({ keepalive: true }).then(clearLifecycleFlush, clearLifecycleFlush);
}

function bindSpecSaveLifecycle() {
  if (pageState.saveLifecycleBound) return;
  pageState.saveLifecycleBound = true;
  window.addEventListener("pagehide", flushSpecSaveForLifecycle);
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "hidden") flushSpecSaveForLifecycle();
  });
}

function currentVisualizationRecord(records, visualizationId) {
  if (typeof visualizationId !== "string" || !visualizationId.trim()) {
    throw new Error("Visualization repository response has no currentVisualizationId.");
  }
  const matches = (records || []).filter((record) => (
    record?.visualizationId === visualizationId
    && record?.backtestId === backtestId
  ));
  if (matches.length > 1) {
    throw new Error("The current Visualization repository record is not unique.");
  }
  const record = matches[0] || null;
  if (record && (!Number.isSafeInteger(record.revision) || record.revision < 1)) {
    throw new Error("The current Visualization has an invalid revision.");
  }
  return record;
}

function applyCurrentVisualizationRecord(response) {
  if (!response || typeof response !== "object" || Array.isArray(response)) {
    throw new Error("Visualization repository response must be an object.");
  }
  const visualizationId = response.currentVisualizationId;
  const record = currentVisualizationRecord(
    response.visualizations || [], visualizationId,
  );
  pageState.visualizationId = visualizationId;
  pageState.visualizationRevision = record?.revision || 0;
  if (record) pageState.backtest.visualization = structuredClone(record.spec);
  return record;
}

function visualizationRevisionConflict(error) {
  return error?.status === 409
    && error?.payload?.code === "visualization_revision_conflict";
}

async function reloadVisualizationAfterConflict() {
  ++pageState.saveEpoch;
  ++pageState.saveSeq;
  clearTimeout(pageState.saveTimer);
  pageState.saveTimer = null;
  clearTimeout(pageState.viewSaveTimer);
  pageState.viewSaveTimer = null;
  abortProjectionCache();
  const response = await getJson(
    `/api/visualizations?backtestId=${encodeURIComponent(backtestId)}`
  );
  const record = applyCurrentVisualizationRecord(response);
  if (!record) {
    throw new Error("Conflicting Visualization disappeared while reloading.");
  }
  pageState.spec = window.TradeChartCore.normalizeVisualizationSpec(
    { dataKeys: pageState.backtest.dataKeys || {} },
    pageState.backtest.visualization,
  );
  syncPaneReference();
  pageState.projectionCache = {
    entries: new Map(), generation: 0, planningError: null,
  };
  pageState.result = resultMetadataOnly();
  await loadPaneResult({ force: true });
}

async function saveCurrentSpec(
  spec = pageState.spec,
  { keepalive = false, expectedRevision = pageState.visualizationRevision } = {},
) {
  return postJson("/api/visualizations", {
    backtestId,
    expectedRevision,
    visualizationId: pageState.visualizationId,
    name: "current",
    spec,
  }, { keepalive });
}

function allResultModuleDefinitions() {
  return Object.entries(pageState.resultModules || {})
    .map(([key, value]) => ({ key, ...value }))
    .filter((row) => Object.keys(row.ports?.outputs || {}).length)
    .sort((a, b) => `${a.kind}.${a.moduleId}`.localeCompare(`${b.kind}.${b.moduleId}`));
}

function resultModuleDefinitions() {
  return window.TradeVersionSelection.currentRows(
    allResultModuleDefinitions(),
    ["kind", "moduleId"],
  );
}

function temporaryModuleById(instanceId) {
  return (pageState.pane?.temporaryModules || []).find((item) => item.instanceId === instanceId) || null;
}

function selectedTemporaryModule() {
  return temporaryModuleById(uiState().selectedTempModuleId);
}

function resultModuleDefinitionForInstance(instance) {
  if (!instance) return null;
  return allResultModuleDefinitions().find((row) => (
    row.kind === instance.kind
    && row.moduleId === instance.moduleId
    && String(row.version) === String(instance.version)
  )) || null;
}

function selectableResultModuleDefinitions() {
  const current = resultModuleDefinitions();
  const selectedDefinition = resultModuleDefinitionForInstance(selectedTemporaryModule());
  if (!selectedDefinition || current.some((row) => row.key === selectedDefinition.key)) return current;
  return [selectedDefinition, ...current];
}

function moduleChoiceLabel(module) {
  const rawDescription = String(module?.description || "").trim();
  const description = typeof forms?.userFacingText === "function"
    ? forms.userFacingText(rawDescription)
    : rawDescription;
  const name = semanticDisplayText(
    [module?.name],
    [module?.moduleId, module?.key],
    "Untitled Module",
  );
  const label = `${name} · v${module?.version || "-"}`;
  return `${label}${description ? ` — ${description}` : ""}`;
}

function hierarchySegment(value, fallback) {
  return String(value || fallback).replaceAll(".", " ").trim() || fallback;
}

function semanticChoiceRecords(items, labelFor, pathFor, detailFor) {
  const bases = items.map((item) => pathFor(item));
  const counts = new Map();
  bases.forEach((base) => counts.set(base, (counts.get(base) || 0) + 1));
  const seen = new Map();
  return items.map((item, index) => {
    const base = bases[index];
    const ordinal = (seen.get(base) || 0) + 1;
    seen.set(base, ordinal);
    return {
      item,
      label: labelFor(item),
      path: counts.get(base) > 1 ? `${base}.Choice ${ordinal}` : base,
      detail: detailFor(item),
    };
  });
}

function selectedResultModule() {
  const select = document.querySelector("[data-temp-module-select]");
  if (!select) return null;
  return selectableResultModuleDefinitions().find((row) => row.key === select.value);
}

function nextUniqueDataKey(baseKey) {
  const declarations = window.TradeChartCore.dataKeyDeclarations({ dataKeys: pageState.backtest.dataKeys || {} }, paneScopedSpec());
  const existing = new Set(Object.keys(declarations));
  let candidate = baseKey;
  let index = 2;
  while (existing.has(candidate)) {
    candidate = `${baseKey}.${index}`;
    index += 1;
  }
  return candidate;
}

function dataKeyOptions(requiredSchema = {}) {
  return window.TradeChartCore.chartLayerCatalog({ dataKeys: pageState.backtest.dataKeys || {} }, paneScopedSpec())
    .filter((item) => window.TradeChartCore.schemasCompatible(item.dataSchema, requiredSchema || {}))
    .map((item) => ({ value: item.dataKey, label: item.dataKey, schema: item.dataSchema }));
}

function dataKeyDeclaration(dataKey) {
  return window.TradeChartCore.resolveDataKeyDeclaration(
    { dataKeys: pageState.backtest.dataKeys || {} },
    paneScopedSpec(),
    dataKey,
  );
}

function validateDataKeyBindings(bindings, ports) {
  for (const [portName, dataKey] of Object.entries(bindings || {})) {
    if (!Object.prototype.hasOwnProperty.call(ports || {}, portName)) continue;
    const declaration = dataKeyDeclaration(dataKey);
    if (!declaration || !window.TradeChartCore.schemasCompatible(
      declaration.schema,
      ports?.[portName]?.schema || {},
    )) {
      throw new Error(`${forms.humanizeName(portName)} must reference a compatible DataKey`);
    }
  }
}

function validateRequiredPortBindings(bindings, ports, direction) {
  for (const [portName, port] of Object.entries(ports || {})) {
    if (port?.required === false) continue;
    const value = bindings?.[portName];
    if (typeof value !== "string" || !value.trim() || value.trim() === "null") {
      throw new Error(`${forms.humanizeName(portName)} ${direction} is required`);
    }
  }
}

function validateTemporaryModuleBindings(inputs, outputs, module) {
  validateRequiredPortBindings(inputs, module.ports?.inputs || {}, "input");
  validateDataKeyBindings(inputs, module.ports?.inputs || {});
  validateRequiredPortBindings(outputs, module.ports?.outputs || {}, "output");
  const boundOutputs = Object.values(outputs || {}).filter((value) => (
    typeof value === "string" && value.trim() && value.trim() !== "null"
  ));
  if (Object.keys(module.ports?.outputs || {}).length && !boundOutputs.length) {
    throw new Error("Temporary Module must bind at least one output");
  }
}

function visualizerCapabilityDescriptors(definition, direction) {
  const descriptors = definition?.capabilities?.[direction];
  return Array.isArray(descriptors)
    ? descriptors.filter((descriptor) => descriptor && typeof descriptor === "object")
    : [];
}

function visualizerReferenceRequirements(definition) {
  return visualizerCapabilityDescriptors(definition, "requires").filter((requirement) => (
    typeof requirement.name === "string" && requirement.name
    && typeof requirement.kind === "string" && requirement.kind
    && typeof requirement.bindingParam === "string" && requirement.bindingParam
    && requirement.matches && typeof requirement.matches === "object"
    && !Array.isArray(requirement.matches)
  ));
}

function visualizerCapabilityValuesEqual(left, right) {
  if (Object.is(left, right)) return true;
  if (Array.isArray(left) || Array.isArray(right)) {
    return Array.isArray(left) && Array.isArray(right)
      && left.length === right.length
      && left.every((value, index) => visualizerCapabilityValuesEqual(value, right[index]));
  }
  if (!left || !right || typeof left !== "object" || typeof right !== "object") return false;
  const leftKeys = Object.keys(left).sort();
  const rightKeys = Object.keys(right).sort();
  return leftKeys.length === rightKeys.length
    && leftKeys.every((key, index) => key === rightKeys[index]
      && visualizerCapabilityValuesEqual(left[key], right[key]));
}

function visualizerProviderMatchesRequirement(
  requirement,
  consumerParams,
  providerDefinition,
  providerParams,
  { allowUnboundMatches = false } = {},
) {
  if (!requirement || typeof requirement.kind !== "string") return false;
  const providers = visualizerCapabilityDescriptors(providerDefinition, "provides")
    .filter((provided) => provided.kind === requirement.kind);
  if (providers.length !== 1) return false;
  const provided = providers[0];
  if (!provided.attributes || typeof provided.attributes !== "object"
      || Array.isArray(provided.attributes)) return false;
  return Object.entries(requirement.matches || {}).every(([attributeName, consumerParam]) => {
    if (typeof consumerParam !== "string" || !consumerParam) return false;
    const providerParam = provided.attributes[attributeName];
    if (typeof providerParam !== "string" || !providerParam) return false;
    if (!Object.prototype.hasOwnProperty.call(providerParams || {}, providerParam)) return false;
    const hasExpected = Object.prototype.hasOwnProperty.call(consumerParams || {}, consumerParam);
    const expected = consumerParams?.[consumerParam];
    if (allowUnboundMatches && (!hasExpected || expected === undefined || expected === "")) return true;
    if (!hasExpected) return false;
    return visualizerCapabilityValuesEqual(expected, providerParams?.[providerParam]);
  });
}

function validateVisualizerOverlayDependencies(definition, params) {
  for (const requirement of visualizerReferenceRequirements(definition)) {
    const candidates = new Set(visualizerReferenceCandidates(
      definition, requirement, params,
    ).map((item) => item.id));
    const referencedId = params?.[requirement.bindingParam];
    if (!referencedId || !candidates.has(referencedId)) {
      const field = (definition.params || []).find((item) => item.name === requirement.bindingParam);
      throw new Error(`${field?.label || forms.humanizeName(requirement.bindingParam)} must reference a visible compatible display`);
    }
  }
}

function visualizerDefinitionMap() {
  const catalog = window.TradeChartCore.visualizerCatalog(
    { dataKeys: pageState.backtest?.dataKeys || {} },
    paneScopedSpec(),
  );
  return new Map(catalog.map((definition) => [definition.id, definition]));
}

function visualizerReferenceCandidates(
  consumerDefinition,
  requirement,
  consumerParams = {},
  { allowUnboundMatches = false } = {},
) {
  if (!requirement) return [];
  const byId = visualizerDefinitionMap();
  const currentId = uiState().selectedVisualizerId;
  return (pageState.pane?.visualizers || []).filter((instance) => {
    if (!instance?.id || instance.id === currentId || instance.visible === false) return false;
    return visualizerProviderMatchesRequirement(
      requirement,
      consumerParams,
      byId.get(instance.callback),
      instance.params || {},
      { allowUnboundMatches },
    );
  });
}

function visualizerDefinitionForEditor(definition, params = {}) {
  if (!definition) return null;
  const requirementsByParam = new Map(visualizerReferenceRequirements(definition).map(
    (requirement) => [requirement.bindingParam, requirement],
  ));
  return {
    ...definition,
    params: (definition.params || []).map((field) => {
      const requirement = requirementsByParam.get(field.name);
      if (!requirement) return field;
      const candidateIds = visualizerReferenceCandidates(
        definition,
        requirement,
        params,
        { allowUnboundMatches: true },
      ).map((item) => item.id);
      const labels = visualizerDisplayLabelMap();
      return {
        ...field,
        type: "visualizerRef",
        options: [
          { value: "", label: "Select…" },
          ...candidateIds.map((value) => ({
            value,
            label: labels.get(value) || "Data Display",
          })),
        ],
      };
    }),
  };
}

function incompatibleVisualizerDependents(
  referencedVisualizerId, providerDefinition, providerParams,
) {
  const byId = visualizerDefinitionMap();
  return visualizerDependents(pageState.pane, referencedVisualizerId, byId).filter((instance) => {
    const consumerDefinition = byId.get(instance.callback);
    return visualizerReferenceRequirements(consumerDefinition).some((requirement) => (
      instance.params?.[requirement.bindingParam] === referencedVisualizerId
      && !visualizerProviderMatchesRequirement(
        requirement,
        instance.params || {},
        providerDefinition,
        providerParams || {},
      )
    ));
  });
}

function fillTemporaryModuleDraft() {
  const module = selectedResultModule();
  const instanceInput = document.querySelector("[data-temp-instance]");
  const configFields = document.querySelector("[data-temp-config-fields]");
  const inputFields = document.querySelector("[data-temp-inputs-fields]");
  const outputFields = document.querySelector("[data-temp-outputs-fields]");
  if (!module) {
    if (instanceInput) instanceInput.value = "";
    if (configFields) configFields.innerHTML = "";
    if (inputFields) inputFields.innerHTML = "";
    if (outputFields) outputFields.innerHTML = "";
    return;
  }
  const selectedItem = selectedTemporaryModule();
  instanceInput.value = selectedItem?.instanceId || opaqueClientId("tmp");
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
      required: module.ports.inputs[name]?.required !== false,
      description: JSON.stringify(module.ports.inputs[name]?.schema || {}),
    })),
    selectedItem?.inputs || {},
    Object.fromEntries(Object.keys(module.ports?.inputs || {}).map((name) => [
      name,
      dataKeyOptions(module.ports.inputs[name]?.schema || {}),
    ])),
  );
  forms.renderParamFields(
    outputFields,
    Object.keys(module.ports?.outputs || {}).map((name) => ({
      name,
      label: forms.humanizeName(name),
      type: "string",
      required: module.ports.outputs[name]?.required !== false,
      description: JSON.stringify(module.ports.outputs[name]?.schema || {}),
      default: nextUniqueDataKey(`${semanticDataKeySegment(module.name || module.kind)}.${semanticDataKeySegment(name, "output")}`),
    })),
    selectedItem?.outputs || {},
  );
}

function remapDataKeyBinding(binding, mappings) {
  if (typeof binding !== "string") return binding;
  const match = mappings.find(({ oldKey }) => (
    binding === oldKey || binding.startsWith(`${oldKey}.`)
  ));
  return match ? `${match.newKey}${binding.slice(match.oldKey.length)}` : binding;
}

function remapTemporaryModuleConsumers(previousItem, outputs) {
  const mappings = Object.entries(previousItem?.outputs || {}).flatMap(([portName, oldKey]) => {
    const newKey = outputs?.[portName];
    return typeof oldKey === "string" && oldKey && typeof newKey === "string" && newKey
      ? [{ oldKey, newKey }]
      : [];
  }).sort((left, right) => right.oldKey.length - left.oldKey.length);
  const removedOutputs = Object.entries(previousItem?.outputs || {}).filter(([portName, oldKey]) => (
    typeof oldKey === "string" && oldKey
    && !(typeof outputs?.[portName] === "string" && outputs[portName])
  ));
  const blockedVisualizers = new Map();
  const blockedModules = new Map();
  removedOutputs.forEach(([portName, oldKey]) => {
    const consumers = temporaryModuleConsumers(
      { ...previousItem, outputs: { [portName]: oldKey } },
    );
    consumers.visualizers.forEach((item) => blockedVisualizers.set(item.id, item));
    consumers.temporaryModules.forEach((item) => blockedModules.set(item.instanceId, item));
  });
  if (blockedVisualizers.size || blockedModules.size) {
    const visualizerLabels = typeof visualizerDisplayLabelMap === "function"
      ? visualizerDisplayLabelMap() : new Map();
    const temporaryLabels = typeof temporaryModuleDisplayLabelMap === "function"
      ? temporaryModuleDisplayLabelMap() : new Map();
    throw new Error(`Cannot remove a bound output; dependent instance(s): ${[
      ...[...blockedVisualizers].map(([id]) => visualizerLabels.get(id) || "Data Display"),
      ...[...blockedModules].map(([id]) => temporaryLabels.get(id) || "Temporary Module"),
    ].join(", ")}`);
  }
  let definitions = new Map();
  try {
    definitions = new Map(window.TradeChartCore.visualizerCatalog(
      { dataKeys: pageState.backtest?.dataKeys || {} }, paneScopedSpec(),
    ).map((definition) => [definition.id, definition]));
  } catch { /* unknown definitions are remapped conservatively below */ }
  const visualizers = (pageState.pane.visualizers || []).map((visualizer) => {
    const params = { ...(visualizer.params || {}) };
    const definition = definitions.get(visualizer.callback);
    const names = definition
      ? Object.keys(definition.inputPorts || {})
      : Object.keys(params);
    names.forEach((name) => {
      params[name] = remapDataKeyBinding(params[name], mappings);
    });
    return { ...visualizer, params };
  });
  const temporaryModules = (pageState.pane.temporaryModules || []).map((candidate) => {
    if (candidate.instanceId === previousItem?.instanceId) return candidate;
    const remappedInputs = Object.fromEntries(Object.entries(candidate.inputs || {}).map(
      ([name, value]) => [name, remapDataKeyBinding(value, mappings)],
    ));
    return { ...candidate, inputs: remappedInputs };
  });
  return { visualizers, temporaryModules };
}

function addPaneTemporaryModule() {
  const module = selectedResultModule();
  if (!module) return;
  const selectedId = uiState().selectedTempModuleId;
  const previousItem = selectedId ? temporaryModuleById(selectedId) : null;
  const instanceId = selectedId || document.querySelector("[data-temp-instance]").value.trim();
  if (!instanceId) throw new Error("Temporary instance id is required");
  const config = forms.readSchemaFields(document.querySelector("[data-temp-config-fields]"), module.configSchema);
  const inputs = forms.readParamFields(
    document.querySelector("[data-temp-inputs-fields]"),
    Object.keys(module.ports?.inputs || {}).map((name) => ({ name, type: "dataKey" })),
  );
  const outputs = forms.readParamFields(
    document.querySelector("[data-temp-outputs-fields]"),
    Object.keys(module.ports?.outputs || {}).map((name) => ({ name, type: "string" })),
  );
  validateTemporaryModuleBindings(inputs, outputs, module);
  const nextItem = window.TradeChartCore.createTemporaryModuleInstance(module, {
    instanceId, config, inputs, outputs,
  });
  pageState.pane.temporaryModules ||= [];
  if (selectedId) {
    const remapped = remapTemporaryModuleConsumers(previousItem, outputs);
    pageState.pane.visualizers = remapped.visualizers;
    pageState.pane.temporaryModules = remapped.temporaryModules;
    pageState.pane.temporaryModules = window.TradeChartCore.upsertIdentity(
      pageState.pane.temporaryModules, selectedId, nextItem, "instanceId",
    );
    uiState().selectedTempModuleId = selectedId;
  } else {
    pageState.pane.temporaryModules = window.TradeChartCore.upsertIdentity(
      pageState.pane.temporaryModules, "", nextItem, "instanceId",
    );
    uiState().selectedTempModuleId = "";
  }
  syncSpec();
}

function dataKeyConsumesOutput(binding, outputKeys) {
  if (typeof binding !== "string" || !binding) return false;
  return outputKeys.some((output) => (
    binding === output || binding.startsWith(`${output}.`)
  ));
}

function temporaryModuleConsumers(module) {
  const outputKeys = Object.values(module?.outputs || {})
    .filter((value) => typeof value === "string" && value);
  if (!outputKeys.length) return { visualizers: [], temporaryModules: [] };
  let definitions = new Map();
  try {
    definitions = new Map(window.TradeChartCore.visualizerCatalog(
      { dataKeys: pageState.backtest?.dataKeys || {} }, paneScopedSpec(),
    ).map((definition) => [definition.id, definition]));
  } catch { /* unknown definitions are checked conservatively below */ }
  const visualizers = (pageState.pane.visualizers || []).filter((visualizer) => {
    const definition = definitions.get(visualizer.callback);
    const bindings = definition
      ? Object.keys(definition.inputPorts || {}).map((name) => visualizer.params?.[name])
      : Object.values(visualizer.params || {});
    return bindings.some((value) => dataKeyConsumesOutput(value, outputKeys));
  });
  const temporaryModules = (pageState.pane.temporaryModules || []).filter((candidate) => (
    candidate.instanceId !== module?.instanceId
    && Object.values(candidate.inputs || {}).some((value) => (
      dataKeyConsumesOutput(value, outputKeys)
    ))
  ));
  return { visualizers, temporaryModules };
}

function removePaneTemporaryModule(instanceId) {
  const module = (pageState.pane.temporaryModules || []).find((item) => item.instanceId === instanceId);
  const consumers = temporaryModuleConsumers(module);
  if (consumers.visualizers.length || consumers.temporaryModules.length) {
    const visualizerLabels = typeof visualizerDisplayLabelMap === "function"
      ? visualizerDisplayLabelMap() : new Map();
    const temporaryLabels = typeof temporaryModuleDisplayLabelMap === "function"
      ? temporaryModuleDisplayLabelMap() : new Map();
    const identities = [
      ...consumers.visualizers.map((item) => visualizerLabels.get(item.id) || "Data Display"),
      ...consumers.temporaryModules.map((item) => temporaryLabels.get(item.instanceId) || "Temporary Module"),
    ];
    document.getElementById("chartStatus").textContent = `Remove dependent instance(s) first: ${identities.join(", ")}`;
    return false;
  }
  pageState.pane.temporaryModules = (pageState.pane.temporaryModules || []).filter((item) => item.instanceId !== instanceId);
  if (uiState().selectedTempModuleId === instanceId) uiState().selectedTempModuleId = "";
  syncSpec();
  return true;
}

function selectedVisualizerDefinition() {
  const select = document.querySelector("[data-visualizer-select]");
  return window.TradeChartCore.visualizerCatalog({ dataKeys: pageState.backtest.dataKeys || {} }, paneScopedSpec())
    .find((item) => item.id === select?.value);
}

function visualizerById(visualizerId) {
  return (pageState.pane?.visualizers || []).find((item) => item.id === visualizerId) || null;
}

function paneVisualizerIds(currentId = "") {
  const used = new Set();
  (pageState.pane?.visualizers || []).forEach((item) => {
    if (!item?.id || item.id === currentId) return;
    used.add(item.id);
  });
  return used;
}

function uniquePaneVisualizerId(definitionId, currentId = "") {
  const base = `${String(definitionId || "visualizer")}.${Date.now().toString(36)}`;
  const used = paneVisualizerIds(currentId);
  let candidate = currentId || base;
  let index = 2;
  while (used.has(candidate)) {
    candidate = `${base}.${index.toString(36)}`;
    index += 1;
  }
  return candidate;
}

function fillVisualizerDraft() {
  const definition = selectedVisualizerDefinition();
  const fields = document.querySelector("[data-visualizer-fields]");
  if (!definition) {
    if (fields) fields.innerHTML = "";
    return;
  }
  const selectedItem = visualizerById(uiState().selectedVisualizerId);
  const editorDefinition = visualizerDefinitionForEditor(
    definition, selectedItem?.params || {},
  );
  forms.renderParamFields(
    fields,
    editorDefinition.params || [],
    selectedItem?.params || {},
    editorDefinition.optionMap || {},
  );
}

function addPaneVisualizer() {
  const definition = selectedVisualizerDefinition();
  if (!definition) return;
  const fields = document.querySelector("[data-visualizer-fields]");
  const selectedId = uiState().selectedVisualizerId;
  const previousItem = selectedId ? visualizerById(selectedId) : null;
  const editorDefinition = visualizerDefinitionForEditor(
    definition, previousItem?.params || {},
  );
  const params = forms.readParamFields(fields, editorDefinition?.params || []);
  validateDataKeyBindings(params, definition.inputPorts || {});
  validateVisualizerOverlayDependencies(definition, params);
  pageState.pane.visualizers ||= [];
  const createdItem = window.TradeChartCore.createVisualizerInstance(definition, {
    id: uniquePaneVisualizerId(definition.id, selectedId || ""),
    params,
  });
  const nextItem = previousItem && Object.prototype.hasOwnProperty.call(previousItem, "visible")
    ? { ...createdItem, visible: previousItem.visible }
    : createdItem;
  if (selectedId) {
    const incompatible = incompatibleVisualizerDependents(
      selectedId, definition, nextItem.params,
    );
    if (incompatible.length) {
      const labels = typeof visualizerDisplayLabelMap === "function"
        ? visualizerDisplayLabelMap() : new Map();
      throw new Error(`This change would break dependent display(s): ${incompatible.map(
        (item) => labels.get(item.id) || "Data Display",
      ).join(", ")}`);
    }
  }
  if (selectedId) {
    pageState.pane.visualizers = window.TradeChartCore.upsertIdentity(
      pageState.pane.visualizers, selectedId, nextItem, "id",
    );
  } else {
    pageState.pane.visualizers = window.TradeChartCore.upsertIdentity(
      pageState.pane.visualizers, "", nextItem, "id",
    );
    uiState().selectedVisualizerId = "";
  }
  syncSpec();
}

function removePaneVisualizer(visualizerId) {
  const dependents = visualizerDependents(
    pageState.pane, visualizerId, visualizerDefinitionMap(),
  );
  if (dependents.length) {
    const labels = typeof visualizerDisplayLabelMap === "function"
      ? visualizerDisplayLabelMap() : new Map();
    document.getElementById("chartStatus").textContent = `Remove dependent display(s) first: ${dependents.map(
      (item) => labels.get(item.id) || "Data Display",
    ).join(", ")}`;
    return false;
  }
  pageState.pane.visualizers = (pageState.pane.visualizers || []).filter((item) => item.id !== visualizerId);
  if (uiState().selectedVisualizerId === visualizerId) uiState().selectedVisualizerId = "";
  syncSpec();
  return true;
}

function isDrawingVisualizerInstance(visualizer) {
  const definition = window.TradeChartCore.visualizerCatalog(
    { dataKeys: pageState.backtest?.dataKeys || {} },
    paneScopedSpec(),
  ).find((item) => item.id === visualizer?.callback);
  return definition?.renderer?.apiVersion === 1
    && (definition.capabilities?.interactions || []).includes("chart.pointer");
}

function visualizerDependents(pane, referencedVisualizerId, definitionsById) {
  if (!definitionsById || typeof definitionsById.get !== "function") return [];
  return (pane?.visualizers || []).filter((item) => {
    if (!item?.id || item.id === referencedVisualizerId) return false;
    const definition = definitionsById.get(item.callback);
    return visualizerReferenceRequirements(definition).some((requirement) => (
      item.params?.[requirement.bindingParam] === referencedVisualizerId
    ));
  });
}

function visualizerTagLabel(visualizer, definition = null, displayLabel = "") {
  definition ||= window.TradeChartCore.visualizerCatalog({ dataKeys: pageState.backtest.dataKeys || {} }, paneScopedSpec())
    .find((item) => item.id === visualizer.callback);
  const label = displayLabel || semanticDisplayText(
    [visualizer.displayName, definition?.label],
    [visualizer.id, visualizer.callback, definition?.id],
    "Data Display",
  );
  const dataKeys = [...new Set(Object.keys(definition?.inputPorts || {})
    .map((name) => visualizer.params?.[name])
    .filter((value) => value !== undefined && value !== "")
    .map((value) => {
      const text = String(value);
      return typeof forms?.userFacingText === "function" ? forms.userFacingText(text, "Data") : text;
    }))];
  return [label, ...dataKeys].join(" · ");
}

function visualizerSummary(visualizer, definition = null, displayLabel = "") {
  return visualizerTagLabel(visualizer, definition, displayLabel);
}

function applyButtonLabel(base, selected) {
  return selected ? `Apply ${base}` : `Add ${base}`;
}

function visualizerCompatibleDataState(visualizer) {
  const ports = Object.keys(visualizer?.inputPorts || {});
  if (!ports.length) return { available: true, count: 0, label: "No DataKey input" };
  const counts = ports.map((name) => visualizer?.optionMap?.[name]?.length || 0);
  const count = Math.min(...counts);
  return {
    available: counts.every((value) => value > 0),
    count,
    label: count === 1 ? "1 compatible DataKey" : `${count} compatible DataKeys`,
  };
}

function renderChartControls() {
  const catalog = window.TradeChartCore.visualizerCatalog({ dataKeys: pageState.backtest.dataKeys || {} }, paneScopedSpec());
  const visualizers = catalog.filter((definition) => !(definition.capabilities?.interactions || []).length);
  const tempDefinitions = selectableResultModuleDefinitions();
  const tempChoices = semanticChoiceRecords(
    tempDefinitions,
    moduleChoiceLabel,
    (row) => `${hierarchySegment(forms.humanizeName(row.kind), "Modules")}.${hierarchySegment(
      semanticDisplayText([row.name], [row.moduleId, row.key], "Untitled Module"),
      "Untitled Module",
    )}.Version ${row.version || "-"}`,
    (row) => `${forms.humanizeName(row.kind)} · Version ${row.version || "-"}`,
  );
  const visualizerChoices = semanticChoiceRecords(
    visualizers,
    visualizerDefinitionDisplayLabel,
    (definition) => `Data Displays.${hierarchySegment(
      visualizerDefinitionDisplayLabel(definition), "Data Display",
    )}`,
    (definition) => visualizerCompatibleDataState(definition).label,
  );
  const selectedTempDefinition = resultModuleDefinitionForInstance(selectedTemporaryModule());
  const selectedVisualizer = (pageState.pane.visualizers || [])
    .find((item) => item.id === uiState().selectedVisualizerId);
  const temporaryLabels = temporaryModuleDisplayLabelMap();
  const visualizerLabels = visualizerDisplayLabelMap(pageState.pane, catalog);
  const controls = document.createElement("div");
  controls.className = "chart-controls";
  controls.innerHTML = `
    <section class="chart-tag-section">
      <h4>Temporary Instance Tags</h4>
      <div class="chart-layer-tags">${
        (pageState.pane.temporaryModules || []).length
          ? (pageState.pane.temporaryModules || []).map((module) => {
            const rawOutputs = Object.values(module.outputs || {}).join(", ");
            const outputs = typeof forms?.userFacingText === "function"
              ? forms.userFacingText(rawOutputs, "Data")
              : rawOutputs;
            const moduleLabel = temporaryLabels.get(module.instanceId) || "Temporary Module";
            const tagTitle = [moduleLabel, outputs].filter(Boolean).join(" · ");
            return `<span class="chart-layer-tag-group"><button class="chart-layer-tag ${uiState().selectedTempModuleId === module.instanceId ? "active" : ""}" data-select-temp-module="${escapeHtml(module.instanceId)}" type="button" title="${escapeHtml(tagTitle)}"><span class="layer-key">${escapeHtml(moduleLabel)}</span><span class="layer-data-key">${escapeHtml(outputs)}</span></button><button class="tag-remove" data-remove-temp-module="${escapeHtml(module.instanceId)}" type="button" aria-label="Remove temporary instance tag ${escapeHtml(moduleLabel)}" title="Remove ${escapeHtml(moduleLabel)}"><span aria-hidden="true">×</span></button></span>`;
          }).join("")
          : '<span class="muted">No temporary modules</span>'
      }</div>
    </section>
    <section class="chart-tag-section">
      <h4>Data Tags</h4>
      <div class="chart-layer-tags">${
        (pageState.pane.visualizers || []).length
          ? (pageState.pane.visualizers || []).map((visualizer) => {
            const definition = catalog.find((item) => item.id === visualizer.callback);
            const displayLabel = visualizerLabels.get(visualizer.id) || "Data Display";
            const summary = visualizerSummary(visualizer, definition, displayLabel);
            const tagLabel = visualizerTagLabel(visualizer, definition, displayLabel);
            return `<span class="chart-layer-tag-group"><button class="chart-layer-tag ${uiState().selectedVisualizerId === visualizer.id ? "active" : ""}" data-select-visualizer="${escapeHtml(visualizer.id)}" type="button" title="${escapeHtml(summary)}"><span class="layer-key">${escapeHtml(tagLabel)}</span></button><button class="tag-remove" data-remove-visualizer="${escapeHtml(visualizer.id)}" type="button" aria-label="Remove data tag ${escapeHtml(tagLabel)}" title="Remove ${escapeHtml(tagLabel)}"><span aria-hidden="true">×</span></button></span>`;
          }).join("")
          : '<span class="muted">No visualizers</span>'
      }</div>
    </section>
    <section class="chart-control-zone">
      <div class="chart-control-block">
        <h4>Template</h4>
        <select data-temp-module-select="1">
          <option value=""></option>
          ${tempChoices.map(({ item: row, label, path, detail }) => `<option value="${escapeHtml(row.key)}" data-combobox-path="${escapeHtml(path)}" data-combobox-meta="${escapeHtml(detail)}" data-combobox-detail="${escapeHtml(detail)}" ${row.key === selectedTempDefinition?.key ? "selected" : ""}>${escapeHtml(label)}</option>`).join("")}
        </select>
        <input data-temp-instance="1" type="hidden" />
        <div data-temp-config-fields="1" class="structured-fields structured-fields-inline"></div>
        <div data-temp-inputs-fields="1" class="structured-fields structured-fields-inline"></div>
        <div data-temp-outputs-fields="1" class="structured-fields structured-fields-inline"></div>
        <button data-add-temp-module="1" type="button">${applyButtonLabel("Template", uiState().selectedTempModuleId)}</button>
      </div>
      <div class="chart-control-block">
        <h4>Data Display</h4>
        <select data-visualizer-select="1">
          <option value=""></option>
          ${visualizerChoices.map(({ item, label, path, detail }) => {
            const dataState = visualizerCompatibleDataState(item);
            const selected = item.id === selectedVisualizer?.callback;
            return `<option value="${escapeHtml(item.id)}" data-combobox-path="${escapeHtml(path)}" data-combobox-meta="${escapeHtml(detail)}" data-combobox-detail="${escapeHtml(detail)}" ${selected ? "selected" : ""} ${!dataState.available && !selected ? "disabled" : ""}>${escapeHtml(label)}</option>`;
          }).join("")}
        </select>
        <div data-visualizer-fields="1" class="structured-fields structured-fields-inline"></div>
        <button data-add-visualizer="1" type="button">${applyButtonLabel("Visualizer", uiState().selectedVisualizerId)}</button>
        <button data-save-chart="1" type="button">Save</button>
      </div>
    </section>
  `;
  forms.enhanceSearchableSelect(
    controls.querySelector("[data-temp-module-select]"),
    {
      placeholder: "Search or browse Templates",
      ariaLabel: "Template",
      rootLabel: "Templates",
      collectionLabel: "Templates",
      choiceLabel: "Template",
      emptyText: "No temporary module Templates are available.",
    },
  );
  forms.enhanceSearchableSelect(
    controls.querySelector("[data-visualizer-select]"),
    {
      placeholder: "Search or browse Data Displays",
      ariaLabel: "Data Display",
      rootLabel: "Data Displays",
      collectionLabel: "Data Displays",
      choiceLabel: "Data Display",
      emptyText: "No Data Display is compatible with this Result.",
    },
  );
  return controls;
}

function bindControls(area) {
  area.querySelectorAll("[data-temp-module-select]").forEach((select) => {
    fillTemporaryModuleDraft();
    select.addEventListener("change", () => {
      uiState().selectedTempModuleId = "";
      fillTemporaryModuleDraft();
      setActionButtonLabels();
    });
  });
  area.querySelectorAll("[data-add-temp-module]").forEach((button) => {
    button.addEventListener("click", () => {
      try {
        addPaneTemporaryModule();
      } catch (error) {
        document.getElementById("chartStatus").textContent = typeof forms?.userFacingText === "function"
          ? forms.userFacingText(error?.message, "Template configuration could not be applied.")
          : "Template configuration could not be applied.";
      }
    });
  });
  area.querySelectorAll("[data-visualizer-select]").forEach((select) => {
    fillVisualizerDraft();
    select.addEventListener("change", () => {
      uiState().selectedVisualizerId = "";
      fillVisualizerDraft();
      setActionButtonLabels();
    });
  });
  area.querySelectorAll("[data-select-temp-module]").forEach((button) => {
    button.addEventListener("click", () => {
      const instanceId = button.dataset.selectTempModule;
      uiState().selectedTempModuleId = uiState().selectedTempModuleId === instanceId ? "" : instanceId;
      drawPane();
    });
  });
  area.querySelectorAll("[data-select-visualizer]").forEach((button) => {
    button.addEventListener("click", () => {
      const visualizerId = button.dataset.selectVisualizer;
      uiState().selectedVisualizerId = uiState().selectedVisualizerId === visualizerId ? "" : visualizerId;
      drawPane();
    });
  });
  area.querySelectorAll("[data-add-visualizer]").forEach((button) => {
    button.addEventListener("click", () => {
      try {
        addPaneVisualizer();
      } catch (error) {
        document.getElementById("chartStatus").textContent = typeof forms?.userFacingText === "function"
          ? forms.userFacingText(error?.message, "Data Display configuration could not be applied.")
          : "Data Display configuration could not be applied.";
      }
    });
  });
  area.querySelectorAll("[data-remove-temp-module]").forEach((button) => {
    button.addEventListener("click", () => removePaneTemporaryModule(button.dataset.removeTempModule));
  });
  area.querySelectorAll("[data-remove-visualizer]").forEach((button) => {
    button.addEventListener("click", () => removePaneVisualizer(button.dataset.removeVisualizer));
  });
  area.querySelectorAll("[data-save-chart]").forEach((button) => {
    button.addEventListener("click", async () => {
      button.disabled = true;
      try {
        await flushSpecSave();
      } catch {
        // enqueueSpecSave reports only the latest save outcome.
      } finally {
        button.disabled = false;
      }
    });
  });
  const controls = area.querySelector(".chart-controls");
  controls?.addEventListener("input", setActionButtonLabels);
  controls?.addEventListener("change", setActionButtonLabels);
  setActionButtonLabels();
}

function setViewError(message = "") {
  const node = document.querySelector("[data-chart-view-error]");
  if (!node) return;
  node.textContent = message;
  node.hidden = !message;
}

function visibleRangeTime(value) {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (value && typeof value === "object" && value.year && value.month && value.day) {
    return Math.floor(Date.UTC(value.year, value.month - 1, value.day) / 1000);
  }
  const converted = window.TradeChartCore.chartTime(value);
  return typeof converted === "number" && Number.isFinite(converted) ? converted : null;
}

function cleanupPaneViewTracking({ flush = false } = {}) {
  const unsubscribe = pageState.viewRangeUnsubscribe;
  pageState.viewRangeUnsubscribe = null;
  try { unsubscribe?.(); } catch { /* continue pane cleanup */ }
  if (pageState.viewSaveTimer === null) return;
  clearTimeout(pageState.viewSaveTimer);
  pageState.viewSaveTimer = null;
  if (flush && pageState.backtest && pageState.spec) scheduleSpecSave();
}

function subscribePaneView(chart) {
  const timeScale = chart?.timeScale?.();
  if (!timeScale?.subscribeVisibleTimeRangeChange) return;
  const pane = pageState.pane;
  const onVisibleRange = (range) => {
    if (pageState.suppressViewRangeEvents || pageState.pane !== pane || !range) return;
    const start = visibleRangeTime(range.from);
    const end = visibleRangeTime(range.to);
    if (start === null || end === null || start >= end) return;
    pane.view ||= { start: null, end: null, logScale: false, controlsCollapsed: false };
    if (pane.view.start === start && pane.view.end === end) return;
    pane.view.start = start;
    pane.view.end = end;
    pageState.lifecycleFlushKey = "";
    clearTimeout(pageState.viewSaveTimer);
    pageState.viewSaveTimer = setTimeout(() => {
      pageState.viewSaveTimer = null;
      scheduleSpecSave();
    }, 120);
  };
  timeScale.subscribeVisibleTimeRangeChange(onVisibleRange);
  pageState.viewRangeUnsubscribe = () => {
    timeScale.unsubscribeVisibleTimeRangeChange?.(onVisibleRange);
  };
}

function persistPaneView() {
  pageState.spec.panes[paneIndex] = pageState.pane;
  scheduleSpecSave();
}

function applyTimeRange() {
  if (!pageState.chart) return false;
  const options = { timeZone: currentTimeZone().timeZone, showTime: pageState.timeInfo.showTime };
  const parsedStart = window.TradeChartCore.parseRangeInput(pageState.rangeStartInput?.value, options);
  const parsedEnd = window.TradeChartCore.parseRangeInput(pageState.rangeEndInput?.value, options);
  if (Number.isNaN(parsedStart) || Number.isNaN(parsedEnd)) {
    setViewError(`Use ${pageState.timeInfo.showTime ? "a valid local date and time" : "YYYY-MM-DD"}.`);
    return false;
  }
  const start = parsedStart ?? pageState.timeInfo.start;
  const end = parsedEnd ?? pageState.timeInfo.end;
  if (start == null || end == null) {
    setViewError("This chart has no time range to display.");
    return false;
  }
  if (start >= end) {
    setViewError("Start must be before End.");
    return false;
  }
  if (end < pageState.timeInfo.start || start > pageState.timeInfo.end) {
    setViewError("The selected range does not overlap this chart's data.");
    return false;
  }
  pageState.suppressViewRangeEvents = true;
  try {
    pageState.chart.timeScale().setVisibleRange({
      from: Math.max(start, pageState.timeInfo.start),
      to: Math.min(end, pageState.timeInfo.end),
    });
  } finally {
    pageState.suppressViewRangeEvents = false;
  }
  pageState.pane.view.start = parsedStart == null ? null : (pageState.timeInfo.showTime ? new Date(parsedStart * 1000).toISOString() : pageState.rangeStartInput.value);
  pageState.pane.view.end = parsedEnd == null ? null : (pageState.timeInfo.showTime ? new Date(parsedEnd * 1000).toISOString() : pageState.rangeEndInput.value);
  setViewError("");
  persistPaneView();
  return true;
}

function fitPane() {
  if (!pageState.chart) return;
  pageState.pane.view.start = null;
  pageState.pane.view.end = null;
  if (pageState.rangeStartInput) pageState.rangeStartInput.value = "";
  if (pageState.rangeEndInput) pageState.rangeEndInput.value = "";
  pageState.suppressViewRangeEvents = true;
  try {
    pageState.chart.timeScale().fitContent();
  } finally {
    pageState.suppressViewRangeEvents = false;
  }
  setViewError("");
  persistPaneView();
}

function toggleLogScale(button) {
  if (!pageState.chart) return;
  pageState.pane.view.logScale = !pageState.pane.view.logScale;
  pageState.chart.priceScale("right").applyOptions({
    mode: window.TradeChartCore.priceScaleMode(pageState.pane.view.logScale),
  });
  button.classList.toggle("active", !!pageState.pane.view.logScale);
  button.textContent = pageState.pane.view.logScale ? "Log" : "Linear";
  persistPaneView();
}

function toggleControls(controls, button) {
  pageState.pane.view.controlsCollapsed = !pageState.pane.view.controlsCollapsed;
  controls.hidden = !!pageState.pane.view.controlsCollapsed;
  button.textContent = pageState.pane.view.controlsCollapsed ? "Show Config" : "Hide Config";
  persistPaneView();
}

function mergePaneDiagnostics(...groups) {
  const seen = new Set();
  return groups.flatMap((group) => Array.isArray(group) ? group : []).filter((item) => {
    const key = JSON.stringify([
      item?.visualizerId || item?.instanceId || item?.renderer || "",
      item?.code || "",
      item?.message || "",
    ]);
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

function runChartCleanups(cleanups = []) {
  for (const cleanup of cleanups) {
    try { cleanup?.(); } catch { /* one cleanup must not block the rest */ }
  }
}

function cleanupPaneDrawingResources(drawingToolbar = null, chartContext = null) {
  let toolbarCleanup = null;
  try { toolbarCleanup = drawingToolbar?.cleanup; } catch { /* continue failed draw cleanup */ }
  runChartCleanups([toolbarCleanup]);
  let contextCleanups = [];
  try {
    contextCleanups = Array.isArray(chartContext?.cleanups) ? chartContext.cleanups : [];
  } catch { /* continue failed draw cleanup */ }
  runChartCleanups(contextCleanups);
}

function cleanupPaneDrawAttempt({ chart = null, observer = null, drawingToolbar = null, chartContext = null } = {}) {
  try { cleanupPaneViewTracking({ flush: false }); } catch { /* continue failed draw cleanup */ }
  try { observer?.disconnect?.(); } catch { /* continue failed draw cleanup */ }
  cleanupPaneDrawingResources(drawingToolbar, chartContext);
  try { chart?.remove?.(); } catch { /* continue failed draw cleanup */ }
  pageState.chart = null;
  pageState.chartContext = null;
  pageState.chartContainer = null;
  pageState.drawingToolbar = null;
  pageState.observer = null;
  pageState.drawingCleanup = null;
  pageState.rangeStartInput = null;
  pageState.rangeEndInput = null;
}

function renderPaneDrawFailure(area, panel, controls, error) {
  const message = "Chart rendering failed.";
  for (const child of [...(panel?.children || [])]) {
    if (child === controls) continue;
    try { child.remove(); } catch {
      try { panel?.removeChild?.(child); } catch { /* keep rendering the failure */ }
    }
  }
  const failure = document.createElement("div");
  failure.className = "chart-load-error";
  const detail = document.createElement("span");
  detail.textContent = `Chart rendering failed: ${message}`;
  const retry = document.createElement("button");
  retry.type = "button";
  retry.dataset.retryChartRender = "1";
  retry.textContent = "Retry";
  retry.addEventListener("click", () => drawPane());
  failure.appendChild(detail);
  failure.appendChild(retry);
  panel.appendChild(failure);
  const status = document.getElementById("chartStatus");
  if (status) {
    status.dataset.chartRenderError = "1";
    status.textContent = message;
  }
  console.error("Standalone chart rendering failed", error);
  return failure;
}

function renderPaneDiagnostics(panel, diagnostics = []) {
  if (!diagnostics.length) return;
  const container = document.createElement("div");
  container.className = "chart-load-error";
  const labels = typeof visualizerDisplayLabelMap === "function"
    ? visualizerDisplayLabelMap() : new Map();
  diagnostics.forEach((item) => {
    const display = semanticDisplayText(
      [item.label, labels.get(item.visualizerId)],
      [item.visualizerId, item.instanceId, item.renderer],
      "Data Display",
    );
    const diagnosticCode = semanticDisplayText([item.code], [], "Diagnostic");
    const identity = [display, diagnosticCode]
      .filter(Boolean).join(" · ");
    const rawMessage = item.message || (
      item.code === "missing-overlay-target"
        ? "A visible compatible target display is required."
        : "The display could not be drawn."
    );
    const message = typeof forms?.userFacingText === "function"
      ? forms.userFacingText(rawMessage, "The display could not be drawn.")
      : rawMessage;
    const row = document.createElement("div");
    const text = document.createElement("span");
    text.textContent = `${identity ? `${identity}: ` : ""}${message}`;
    row.appendChild(text);
    if (item.code === "instance-load-error" && item.visualizerId) {
      const retry = document.createElement("button");
      retry.type = "button";
      retry.textContent = "Retry";
      retry.dataset.retryChartData = item.visualizerId;
      retry.addEventListener("click", () => {
        void loadPaneResult({ force: true, visualizerId: item.visualizerId });
      });
      row.appendChild(retry);
    }
    container.appendChild(row);
  });
  panel.appendChild(container);
}

function drawingControllerCapabilities(controller) {
  if (!controller) return { tools: [] };
  if (typeof controller.listTools !== "function") {
    throw new Error("Drawing controller listTools() is required.");
  }
  const capability = controller.listTools();
  if (!capability || typeof capability !== "object" || Array.isArray(capability)
      || !Array.isArray(capability.tools)) {
    throw new Error("Drawing controller listTools() must return a tools array.");
  }
  return { tools: capability.tools };
}

function drawingDescriptorHasExactFields(value, fields) {
  return value && typeof value === "object" && !Array.isArray(value)
    && Object.keys(value).length === fields.length
    && fields.every((field) => Object.prototype.hasOwnProperty.call(value, field));
}

function drawingToolDescriptorError(tool) {
  if (!tool || typeof tool !== "object" || Array.isArray(tool)) return "Drawing tool descriptor must be an object.";
  if (typeof tool.id !== "string" || !tool.id.trim()) return "Drawing tool id is required.";
  if (typeof tool.label !== "string" || !tool.label.trim()) return "Drawing tool label is required.";
  if (!Array.isArray(tool.bindings)) return "Drawing tool bindings must be an array.";
  if (!drawingDescriptorHasExactFields(tool, ["id", "label", "bindings"])) {
    return "Drawing tool descriptor has an invalid shape.";
  }
  const names = new Set();
  for (const binding of tool.bindings) {
    if (!drawingDescriptorHasExactFields(binding, ["name", "label", "kind", "candidates"])
        || typeof binding.name !== "string" || !binding.name.trim()
        || typeof binding.label !== "string" || !binding.label.trim()
        || typeof binding.kind !== "string" || !binding.kind.trim()
        || !Array.isArray(binding.candidates)) {
      return "Drawing tool contains an invalid binding descriptor.";
    }
    if (names.has(binding.name)) return "Drawing tool contains duplicate bindings.";
    names.add(binding.name);
    const candidateIds = new Set();
    for (const candidate of binding.candidates) {
      if (!drawingDescriptorHasExactFields(candidate, ["visualizerId", "label"])
          || typeof candidate.visualizerId !== "string" || !candidate.visualizerId.trim()
          || typeof candidate.label !== "string" || !candidate.label.trim()) {
        return "Drawing tool binding contains an invalid target.";
      }
      if (candidateIds.has(candidate.visualizerId)) {
        return "Drawing tool binding contains duplicate targets.";
      }
      candidateIds.add(candidate.visualizerId);
    }
  }
  return "";
}

function persistDrawingControllerEvent(event) {
  if (!pageState.pane || !event) return false;
  if (event.type === "commit") {
    const instance = event.instance || event.visualizer;
    if (!instance?.id || !instance.params || !isDrawingVisualizerInstance(instance)) return false;
    const existing = visualizerById(instance.id);
    if (existing) {
      const definition = visualizerDefinitionMap().get(instance.callback);
      const incompatible = incompatibleVisualizerDependents(
        instance.id, definition, instance.params,
      );
      if (incompatible.length) {
        const labels = typeof visualizerDisplayLabelMap === "function"
          ? visualizerDisplayLabelMap() : new Map();
        document.getElementById("chartStatus").textContent = `This change would break dependent display(s): ${incompatible.map(
          (item) => labels.get(item.id) || "Data Display",
        ).join(", ")}`;
        return false;
      }
    }
    pageState.pane.visualizers = window.TradeChartCore.upsertIdentity(
      pageState.pane.visualizers || [],
      (pageState.pane.visualizers || []).some((item) => item.id === instance.id) ? instance.id : "",
      structuredClone(instance),
      "id",
    );
    pageState.spec.panes[paneIndex] = pageState.pane;
    pageState.drawingUi.selectedVisualizerId = "";
    syncSpec();
    return true;
  }
  if (event.type === "delete" && event.visualizerId) {
    const instance = visualizerById(event.visualizerId);
    if (!instance || !isDrawingVisualizerInstance(instance)) return false;
    const dependents = visualizerDependents(
      pageState.pane, event.visualizerId, visualizerDefinitionMap(),
    );
    if (dependents.length) {
      const labels = typeof visualizerDisplayLabelMap === "function"
        ? visualizerDisplayLabelMap() : new Map();
      document.getElementById("chartStatus").textContent = `Remove dependent display(s) first: ${dependents.map(
        (item) => labels.get(item.id) || "Data Display",
      ).join(", ")}`;
      return false;
    }
    pageState.pane.visualizers = (pageState.pane.visualizers || [])
      .filter((item) => item.id !== event.visualizerId);
    if (uiState().selectedVisualizerId === event.visualizerId) uiState().selectedVisualizerId = "";
    pageState.spec.panes[paneIndex] = pageState.pane;
    pageState.drawingUi.selectedVisualizerId = "";
    syncSpec();
    return true;
  }
  return false;
}

function createDrawingToolbar(controller, interactionSurface) {
  const element = document.createElement("div");
  element.className = "chart-drawing-toolbar";
  element.setAttribute("aria-label", "Chart drawing controls");
  const ui = pageState.drawingUi;
  const drawingText = (value, internalValue, fallback) => {
    const original = typeof value === "string" ? value.trim() : "";
    if (!original || original === String(internalValue || "").trim()) return fallback;
    return typeof forms !== "undefined" && typeof forms?.userFacingText === "function"
      ? forms.userFacingText(original, fallback)
      : original;
  };
  if (!ui.bindings || typeof ui.bindings !== "object" || Array.isArray(ui.bindings)) ui.bindings = {};
  let capabilities;
  let capabilityError = null;
  try {
    capabilities = drawingControllerCapabilities(controller);
  } catch (error) {
    capabilities = { tools: [] };
    capabilityError = error;
    console.error("Drawing capabilities failed", error);
  }
  const toolIdCounts = new Map();
  capabilities.tools.forEach((tool) => {
    if (typeof tool?.id === "string") toolIdCounts.set(tool.id, (toolIdCounts.get(tool.id) || 0) + 1);
  });
  const toolRecords = capabilities.tools.map((tool) => ({
    tool,
    error: drawingToolDescriptorError(tool) || (
      toolIdCounts.get(tool.id) > 1 ? "Drawing tool is duplicated." : ""
    ),
  }));
  const toolById = new Map(toolRecords.filter((record) => !record.error).map((record) => [record.tool.id, record.tool]));
  if (!toolById.has(ui.toolId)) ui.toolId = toolRecords.find((record) => !record.error)?.tool.id || "";
  element.innerHTML = `
    <div class="chart-drawing-bindings" data-drawing-bindings="1"></div>
    <div class="chart-drawing-tools" role="toolbar" aria-label="Drawing tools">
      ${toolRecords.map(({ tool, error }) => `<button type="button" data-drawing-tool="${escapeHtml(tool?.id || "")}" aria-pressed="${tool?.id === ui.toolId ? "true" : "false"}" ${error ? `disabled title="${escapeHtml(error)}"` : ""}>${escapeHtml(drawingText(tool?.label, tool?.id, "Drawing tool"))}</button>`).join("")}
      <button type="button" data-drawing-delete aria-label="Delete selected drawing" aria-keyshortcuts="Delete Backspace" disabled>Delete</button>
    </div>
    <span class="chart-drawing-status" data-drawing-status role="status" aria-live="polite"></span>
  `;
  const bindingHost = element.querySelector("[data-drawing-bindings]");
  const status = element.querySelector("[data-drawing-status]");
  const deleteButton = element.querySelector("[data-drawing-delete]");
  const toolButtons = [...element.querySelectorAll("[data-drawing-tool]")];
  const setStatus = (message = "", isError = false) => {
    status.textContent = message;
    status.classList.toggle("error", !!message && isError);
  };
  const syncButtons = () => {
    toolButtons.forEach((button) => {
      const tool = toolById.get(button.dataset.drawingTool);
      button.disabled = !tool;
      button.setAttribute("aria-pressed", button.dataset.drawingTool === ui.toolId ? "true" : "false");
    });
    deleteButton.disabled = !ui.selectedVisualizerId;
  };
  const activeBindings = (tool) => Object.fromEntries((tool?.bindings || []).map(
    (binding) => [binding.name, ui.bindings[binding.name] || ""],
  ));
  const normalizeBindings = (tool) => {
    for (const binding of tool?.bindings || []) {
      const candidateIds = new Set(binding.candidates.map((candidate) => candidate.visualizerId));
      if (!candidateIds.has(ui.bindings[binding.name])) ui.bindings[binding.name] = "";
    }
  };
  let applyActiveTool = () => false;
  const renderBindings = (tool) => {
    if (!bindingHost) return;
    normalizeBindings(tool);
    bindingHost.innerHTML = (tool?.bindings || []).map((binding) => `
      <label class="chart-drawing-binding">
        <span>${escapeHtml(drawingText(binding.label, binding.name, "Target display"))}</span>
        <select data-drawing-binding="${escapeHtml(binding.name)}" aria-label="${escapeHtml(drawingText(binding.label, binding.name, "Target display"))}">
          <option value="">Select…</option>
          ${binding.candidates.map((candidate) => `<option value="${escapeHtml(candidate.visualizerId)}" ${candidate.visualizerId === ui.bindings[binding.name] ? "selected" : ""}>${escapeHtml(drawingText(candidate.label, candidate.visualizerId, "Data Display"))}</option>`).join("")}
        </select>
      </label>
    `).join("");
    bindingHost.querySelectorAll("[data-drawing-binding]").forEach((select) => {
      select.addEventListener("change", () => {
        ui.bindings[select.dataset.drawingBinding] = select.value;
        applyActiveTool();
      });
    });
  };
  const activate = (toolId) => {
    const tool = toolById.get(toolId);
    if (!tool) {
      setStatus("The selected drawing tool has an invalid descriptor.", true);
      return false;
    }
    ui.toolId = toolId;
    renderBindings(tool);
    syncButtons();
    const bindings = activeBindings(tool);
    const missing = tool.bindings.filter((binding) => !bindings[binding.name]);
    const toolLabel = drawingText(tool.label, tool.id, "Drawing tool");
    if (missing.length) {
      setStatus(`Select ${missing.map((binding) => drawingText(
        binding.label, binding.name, "target display",
      )).join(", ")} to activate ${toolLabel}.`);
      return false;
    }
    let accepted = false;
    try {
      accepted = controller?.activate?.(toolId, { bindings }) === true;
    } catch (error) {
      console.error("Drawing tool activation failed", error);
      setStatus("Drawing tool activation failed.", true);
      return false;
    }
    if (!accepted) {
      setStatus("The selected drawing tool is unavailable for these bindings.", true);
      return false;
    }
    setStatus(`${toolLabel} active.`);
    return true;
  };
  applyActiveTool = () => activate(ui.toolId);
  toolButtons.forEach((button) => {
    button.addEventListener("click", () => activate(button.dataset.drawingTool));
  });
  deleteButton?.addEventListener("click", () => {
    if (controller?.deleteSelected?.() !== true) {
      setStatus("Select a drawing before deleting it.", true);
    }
  });
  const unsubscribe = controller?.subscribe?.((event) => {
    if (!event || typeof event !== "object") return;
    if (event.type === "selection") {
      ui.selectedVisualizerId = event.visualizerId || "";
      syncButtons();
      setStatus(ui.selectedVisualizerId ? "Drawing selected. Drag to move it or press Delete." : "");
      return;
    }
    if (event.type === "diagnostic" || event.type === "error") {
      console.error("Drawing interaction failed", event);
      setStatus("Drawing interaction failed.", true);
      return;
    }
    if (persistDrawingControllerEvent(event)) {
      if (event.type === "delete") {
        ui.selectedVisualizerId = "";
        setStatus("Drawing deleted.");
      } else {
        setStatus("Drawing saved.");
      }
      syncButtons();
    }
  });
  const onKeyDown = (event) => {
    if (event.key === "Escape") {
      controller?.cancel?.();
      const fallbackTool = toolRecords.find((record) => !record.error)?.tool;
      if (fallbackTool) activate(fallbackTool.id);
      event.preventDefault();
      return;
    }
    const editingControl = event.target?.matches?.("input, select, textarea, [contenteditable='true']");
    if (!editingControl && (event.key === "Delete" || event.key === "Backspace") && ui.selectedVisualizerId) {
      if (controller?.deleteSelected?.() === true) event.preventDefault();
    }
  };
  element.addEventListener("keydown", onKeyDown);
  interactionSurface?.addEventListener?.("keydown", onKeyDown);
  if (capabilityError) setStatus("Drawing capabilities could not be loaded.", true);
  else if (!controller) setStatus("Drawing interaction is unavailable in this chart version.", true);
  else if (!toolRecords.length) setStatus("No drawing tools are available.", true);
  else {
    if (ui.toolId) activate(ui.toolId);
    const invalidTool = toolRecords.find((record) => record.error);
    if (invalidTool) setStatus(invalidTool.error, true);
  }
  syncButtons();
  return {
    element,
    cleanup({ disposeController = true } = {}) {
      unsubscribe?.();
      element.removeEventListener("keydown", onKeyDown);
      interactionSurface?.removeEventListener?.("keydown", onKeyDown);
      if (disposeController) controller?.dispose?.();
    },
  };
}

function drawPane() {
  let area = null;
  let panel = null;
  let controls = null;
  let chart = null;
  let chartContext = null;
  let reconcileDiagnostics = null;
  let drawingToolbar = null;
  let observer = null;
  const retained = pageState.chart && pageState.chartContext && pageState.chartContainer
    ? {
      chart: pageState.chart,
      chartContext: pageState.chartContext,
      container: pageState.chartContainer,
      drawingToolbar: pageState.drawingToolbar,
      timeZone: pageState.chartTimeZone,
      showTime: pageState.chartShowTime,
      range: pageState.chart.timeScale?.().getVisibleRange?.() || null,
    }
    : null;
  const disposeRetained = () => {
    if (!retained) return;
    runChartCleanups(retained.chartContext?.cleanups || []);
    try { retained.chart?.remove?.(); } catch { /* replacement cleanup */ }
  };
  try {
    area = document.getElementById("singleChartArea");
    if (!area) throw new Error("Standalone chart area is unavailable.");
    try { cleanupPaneViewTracking({ flush: true }); } catch { /* continue pane cleanup */ }
    try { retained?.drawingToolbar?.cleanup?.({ disposeController: false }); } catch { /* controller remains Chart Core owned */ }
    pageState.drawingCleanup = null;
    pageState.drawingToolbar = null;
    try { pageState.observer?.disconnect(); } catch { /* continue pane cleanup */ }
    pageState.chart = null;
    pageState.chartContext = null;
    pageState.chartContainer = null;
    pageState.observer = null;
    pageState.rangeStartInput = null;
    pageState.rangeEndInput = null;

    panel = document.createElement("div");
    panel.className = "chart-panel";
    pageState.pane.view ||= { start: null, end: null, logScale: false, controlsCollapsed: false };
    controls = renderChartControls();
    controls.hidden = !!pageState.pane.view.controlsCollapsed;
    panel.appendChild(controls);
    area.innerHTML = "";
    area.appendChild(panel);
    bindControls(area);
    const status = document.getElementById("chartStatus");
    if (status?.dataset?.chartRenderError === "1") {
      delete status.dataset.chartRenderError;
      status.textContent = "Ready";
    }

    if (pageState.resultStatus === "loading" || pageState.resultStatus === "error") {
      disposeRetained();
      const dataState = document.createElement("div");
      dataState.className = "chart-view-toolbar";
      const message = document.createElement("span");
      message.className = "muted";
      message.textContent = pageState.resultStatus === "loading"
        ? "Loading chart data"
        : `Chart data failed to load: ${pageState.resultError}`;
      dataState.appendChild(message);
      if (pageState.resultStatus === "error") {
        const retry = document.createElement("button");
        retry.type = "button";
        retry.dataset.retryChartData = "1";
        retry.textContent = "Retry";
        retry.addEventListener("click", () => { void loadPaneResult({ force: true }); });
        dataState.appendChild(retry);
      }
      panel.appendChild(dataState);
      return;
    }

    const projection = currentPaneProjection();
    const pendingIds = projection.request
      ? pendingVisualizerIds(projection.cache, projection.request)
      : new Set();
    if (pendingIds.size) {
      const loading = document.createElement("div");
      loading.className = "muted";
      const labels = visualizerDisplayLabelMap();
      loading.textContent = `Loading data for ${[...pendingIds].map(
        (id) => labels.get(id) || "Data Display",
      ).join(", ")}`;
      panel.appendChild(loading);
    }

    if (!(pageState.pane.visualizers || []).length) {
      disposeRetained();
      const empty = document.createElement("div");
      empty.className = "muted";
      empty.textContent = "No visualizers";
      panel.appendChild(empty);
      return;
    }

    if (!window.LightweightCharts?.createChart) {
      throw new Error("Chart library failed to load. Configuration remains available above.");
    }

    const scoped = paneScopedSpec();
    const preparedPane = window.TradeChartCore.prepareFinancialPane(
      pageState.result, pageState.pane, scoped,
    );
    pageState.timeInfo = window.TradeChartCore.paneTimeInfo(
      pageState.result, pageState.pane, scoped, preparedPane,
    );
    const zone = currentTimeZone();
    const rangeOptions = { timeZone: zone.timeZone, showTime: pageState.timeInfo.showTime };
    const storedStart = pageState.pane.view.start ? window.TradeChartCore.chartTime(pageState.pane.view.start) : null;
    const storedEnd = pageState.pane.view.end ? window.TradeChartCore.chartTime(pageState.pane.view.end) : null;
    const viewToolbar = document.createElement("div");
    viewToolbar.className = "chart-view-toolbar";
    viewToolbar.innerHTML = `
      <label><span>Start</span><input type="${pageState.timeInfo.showTime ? "datetime-local" : "date"}" data-chart-start value="${escapeHtml(window.TradeChartCore.formatRangeInput(storedStart, rangeOptions))}" /></label>
      <label><span>End</span><input type="${pageState.timeInfo.showTime ? "datetime-local" : "date"}" data-chart-end value="${escapeHtml(window.TradeChartCore.formatRangeInput(storedEnd, rangeOptions))}" /></label>
      <button type="button" data-apply-chart-range>Apply</button>
      <button type="button" data-fit-chart>Fit</button>
      <button type="button" class="${pageState.pane.view.logScale ? "active" : ""}" data-toggle-chart-log>${pageState.pane.view.logScale ? "Log" : "Linear"}</button>
      <button type="button" data-toggle-chart-controls>${pageState.pane.view.controlsCollapsed ? "Show Config" : "Hide Config"}</button>
      <span class="chart-granularity">${pageState.timeInfo.showTime ? "Intraday" : "Date"}</span>
      <span class="chart-view-error" data-chart-view-error hidden></span>
    `;
    const canRetain = retained
      && retained.timeZone === zone.timeZone
      && retained.showTime === pageState.timeInfo.showTime
      && retained.chartContext?.paneId === pageState.pane.id
      && typeof retained.chartContext?.reconcile === "function";
    if (retained && !canRetain) disposeRetained();
    const container = canRetain ? retained.container : document.createElement("div");
    container.className = "tv-chart";
    container.style.height = "calc(100vh - 230px)";
    container.tabIndex = 0;
    container.setAttribute("aria-label", `${paneDisplayTitle()} drawing surface`);
    panel.appendChild(viewToolbar);
    panel.appendChild(container);

    if (canRetain) {
      chart = retained.chart;
      chartContext = retained.chartContext;
      chart.applyOptions({
        rightPriceScale: { mode: window.TradeChartCore.priceScaleMode(!!pageState.pane.view.logScale) },
      });
      const reconciled = chartContext.reconcile(
        pageState.result, pageState.pane, scoped, preparedPane,
      );
      if (reconciled.updated !== true) {
        disposeRetained();
        chart = null;
        chartContext = null;
      } else {
        reconcileDiagnostics = reconciled.diagnostics || [];
      }
    }
    if (!chart || !chartContext) {
      chart = window.TradeChartCore.createFinancialChart(container, {
        timeZone: zone.timeZone,
        showTime: pageState.timeInfo.showTime,
        logScale: !!pageState.pane.view.logScale,
      });
      chartContext = window.TradeChartCore.drawFinancialPane(
        window.LightweightCharts,
        chart,
        pageState.result,
        pageState.pane,
        scoped,
        preparedPane,
      );
    }
    drawingToolbar = createDrawingToolbar(
      chartContext?.interactionController || null,
      container,
    );
    panel.insertBefore(drawingToolbar.element, container);
    const diagnostics = mergePaneDiagnostics(
      pageState.timeInfo?.diagnostics,
      reconcileDiagnostics || chartContext?.diagnostics,
    ).filter((item) => !(
      item?.code === "missing-instance-result"
      && pendingIds.has(item?.visualizerId)
    ));
    renderPaneDiagnostics(panel, diagnostics);
    const savedStart = pageState.timeInfo.start == null
      ? null
      : (typeof storedStart === "number" && Number.isFinite(storedStart) ? Math.max(storedStart, pageState.timeInfo.start) : pageState.timeInfo.start);
    const savedEnd = pageState.timeInfo.end == null
      ? null
      : (typeof storedEnd === "number" && Number.isFinite(storedEnd) ? Math.min(storedEnd, pageState.timeInfo.end) : pageState.timeInfo.end);
    if (canRetain && retained.range && chart === retained.chart) {
      chart.timeScale().setVisibleRange(retained.range);
    } else if ((pageState.pane.view.start || pageState.pane.view.end) && savedStart != null && savedEnd != null && savedStart < savedEnd) {
      chart.timeScale().setVisibleRange({ from: savedStart, to: savedEnd });
    } else {
      chart.timeScale().fitContent();
    }
    subscribePaneView(chart);
    observer = new ResizeObserver(() => chart.applyOptions({ width: container.clientWidth, height: container.clientHeight }));
    observer.observe(container);
    const rangeStartInput = viewToolbar.querySelector("[data-chart-start]");
    const rangeEndInput = viewToolbar.querySelector("[data-chart-end]");
    viewToolbar.querySelector("[data-apply-chart-range]")?.addEventListener("click", applyTimeRange);
    viewToolbar.querySelector("[data-fit-chart]")?.addEventListener("click", fitPane);
    const logButton = viewToolbar.querySelector("[data-toggle-chart-log]");
    logButton?.addEventListener("click", () => toggleLogScale(logButton));
    const controlsButton = viewToolbar.querySelector("[data-toggle-chart-controls]");
    controlsButton?.addEventListener("click", () => toggleControls(controls, controlsButton));
    pageState.chart = chart;
    pageState.chartContext = chartContext;
    pageState.chartContainer = container;
    pageState.chartTimeZone = zone.timeZone;
    pageState.chartShowTime = pageState.timeInfo.showTime;
    pageState.drawingToolbar = drawingToolbar;
    pageState.observer = observer;
    pageState.rangeStartInput = rangeStartInput;
    pageState.rangeEndInput = rangeEndInput;
    pageState.drawingCleanup = () => cleanupPaneDrawingResources(drawingToolbar, chartContext);
  } catch (error) {
    cleanupPaneDrawAttempt({ chart, observer, drawingToolbar, chartContext });
    try {
      area ||= document.getElementById("singleChartArea");
      if (!area) throw error;
      if (!panel?.parentNode) {
        const existingPanel = area.querySelector?.(".chart-panel");
        panel = existingPanel || panel || document.createElement("div");
        panel.className = "chart-panel";
        controls = existingPanel?.querySelector?.(".chart-controls") || controls;
        if (!existingPanel) {
          area.innerHTML = "";
          if (controls && controls.parentNode !== panel) panel.appendChild(controls);
          area.appendChild(panel);
        }
      }
      renderPaneDrawFailure(area, panel, controls, error);
    } catch (failureError) {
      console.error("Standalone chart failure boundary could not render", failureError);
    }
  }
}

function renderStartupError(error) {
  console.error("Standalone chart startup failed", error);
  const message = "Chart failed to load.";
  document.getElementById("chartStatus").textContent = message;
  const area = document.getElementById("singleChartArea");
  if (!area) return;
  area.innerHTML = "";
  const failure = document.createElement("div");
  failure.className = "chart-view-toolbar";
  const detail = document.createElement("span");
  detail.className = "muted";
  detail.textContent = message;
  const retry = document.createElement("button");
  retry.type = "button";
  retry.textContent = "Retry";
  retry.addEventListener("click", () => location.reload());
  failure.appendChild(detail);
  failure.appendChild(retry);
  area.appendChild(failure);
}

async function discoverBacktestDataKeys() {
  const originalDataKeys = pageState.backtest?.dataKeys || {};
  const paths = window.TradeChartCore.discoverySourcePaths(
    { dataKeys: originalDataKeys },
    {},
  );
  if (!paths.length) return;
  const controller = new AbortController();
  const response = await postResultJson(
    `/api/backtests/${encodeURIComponent(backtestId)}/result`,
    { paths, temporaryModules: [] },
    controller,
    "DataKey discovery timed out. Retry when the Result service is available.",
  );
  pageState.backtest.dataKeys = window.TradeChartCore.dataKeyDeclarations({
    ...(response.result || {}),
    dataKeys: originalDataKeys,
  }, {});
}

async function main() {
  await loadBrowserSession();
  if (!backtestId) throw new Error("A chart link is required.");
  if (!window.TradeChartCore) throw new Error("Chart core failed to load.");
  const [moduleResponse, visualizerResponse] = await Promise.all([
    getJson("/api/modules?limit=500"),
    getJson("/api/visualizers"),
  ]);
  pageState.resultModules = moduleResponse.modules || {};
  window.TradeChartCore.setVisualizerDefinitions(visualizerResponse.visualizers || []);
  window.TradeChartCore.setTemporaryModuleDefinitions(pageState.resultModules);
  const [backtest, visualizations] = await Promise.all([
    getJson(`/api/backtests/${encodeURIComponent(backtestId)}/view`),
    getJson(`/api/visualizations?backtestId=${encodeURIComponent(backtestId)}`),
  ]);
  pageState.backtest = backtest;
  applyCurrentVisualizationRecord(visualizations);
  pageState.spec = window.TradeChartCore.normalizeVisualizationSpec(
    { dataKeys: pageState.backtest.dataKeys || {} },
    pageState.backtest.visualization || {},
  );
  bindSpecSaveLifecycle();
  await discoverBacktestDataKeys();
  paneIndex = resolveRequestedPaneIndex(pageState.spec);
  pageState.pane = pageState.spec.panes[paneIndex];
  pageState.paneId = pageState.pane.id;
  pageState.result = resultMetadataOnly();
  document.getElementById("chartTitle").textContent = paneDisplayTitle();
  document.getElementById("chartStatus").textContent = "Ready";
  await loadPaneResult();
}

main().catch((error) => {
  renderStartupError(error);
});
