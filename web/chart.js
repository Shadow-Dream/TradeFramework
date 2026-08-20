const params = new URLSearchParams(location.search);
const backtestId = params.get("backtestId") || "";

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
const paneIndex = Number(params.get("pane") || "0");
const pageState = {
  backtest: null,
  result: {},
  spec: null,
  pane: null,
  chart: null,
  observer: null,
  saveTimer: null,
  saveSeq: 0,
  resultModules: {},
  timeInfo: { start: null, end: null, showTime: false },
  rangeStartInput: null,
  rangeEndInput: null,
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

function currentTimeZone() {
  const timeZone = pageState.spec?.timeZone || "UTC";
  return { timeZone, label: timeZone };
}

function syncTimezoneButton() {
  const button = document.getElementById("chartTimezoneBtn");
  if (!button) return;
  const zone = currentTimeZone();
  button.textContent = `TZ: ${zone.label}`;
  button.title = zone.timeZone;
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

async function postJson(path, payload) {
  const response = await authenticatedFetch(path, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Accept: "application/json",
      "X-CSRF-Token": authState.csrfToken,
    },
    body: JSON.stringify(payload),
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok || data.accepted === false) throw new Error(data.error || `${path} returned ${response.status}`);
  return data;
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

function uiState() {
  pageState.ui ||= { selectedTempModuleId: "", selectedVisualizerId: "" };
  return pageState.ui;
}

function setActionButtonLabels() {
  const tempButton = document.querySelector('[data-add-temp-module="1"]');
  if (tempButton) tempButton.textContent = applyButtonLabel("Template", uiState().selectedTempModuleId);
  const visualizerButton = document.querySelector('[data-add-visualizer="1"]');
  if (visualizerButton) visualizerButton.textContent = applyButtonLabel("Visualizer", uiState().selectedVisualizerId);
}

function syncSpec() {
  pageState.pane = pageState.spec.panes[paneIndex];
  drawPane();
  scheduleSpecSave();
}

function scheduleSpecSave() {
  const saveSeq = ++pageState.saveSeq;
  clearTimeout(pageState.saveTimer);
  document.getElementById("chartStatus").textContent = `${pageState.backtest.backtestId} saving`;
  pageState.saveTimer = setTimeout(async () => {
    try {
      await saveCurrentSpec();
      if (saveSeq === pageState.saveSeq) {
        document.getElementById("chartStatus").textContent = `${pageState.backtest.backtestId} saved`;
      }
    } catch (error) {
      if (saveSeq === pageState.saveSeq) document.getElementById("chartStatus").textContent = error.message;
    }
  }, 350);
}

async function saveCurrentSpec() {
  return postJson("/api/visualizations", {
    backtestId,
    visualizationId: `${backtestId}-current`,
    name: "current",
    spec: pageState.spec,
  });
}

function resultModuleDefinitions() {
  return Object.entries(pageState.resultModules || {})
    .map(([key, value]) => ({ key, ...value }))
    .filter((row) => Object.keys(row.ports?.outputs || {}).length)
    .sort((a, b) => `${a.kind}.${a.moduleId}`.localeCompare(`${b.kind}.${b.moduleId}`));
}

function selectedResultModule() {
  const select = document.querySelector("[data-temp-module-select]");
  if (!select) return null;
  return resultModuleDefinitions().find((row) => row.key === select.value);
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

function fillTemporaryModuleDraft() {
  const module = selectedResultModule();
  if (!module) return;
  const instanceInput = document.querySelector("[data-temp-instance]");
  instanceInput.value = opaqueClientId("tmp");
  forms.renderSchemaFields(document.querySelector("[data-temp-config-fields]"), module.configSchema, schemaDefaults(module.configSchema));
  forms.renderParamFields(
    document.querySelector("[data-temp-inputs-fields]"),
    Object.keys(module.ports?.inputs || {}).map((name) => ({
      name,
      label: forms.humanizeName(name),
      type: "dataKey",
      description: JSON.stringify(module.ports.inputs[name]?.schema || {}),
    })),
    {},
    Object.fromEntries(Object.keys(module.ports?.inputs || {}).map((name) => [
      name,
      dataKeyOptions(module.ports.inputs[name]?.schema || {}),
    ])),
  );
  forms.renderParamFields(
    document.querySelector("[data-temp-outputs-fields]"),
    Object.keys(module.ports?.outputs || {}).map((name) => ({
      name,
      label: forms.humanizeName(name),
      type: "string",
      description: JSON.stringify(module.ports.outputs[name]?.schema || {}),
      default: nextUniqueDataKey(`${semanticDataKeySegment(module.name || module.kind)}.${semanticDataKeySegment(name, "output")}`),
    })),
    {},
  );
}

function addPaneTemporaryModule() {
  const module = selectedResultModule();
  if (!module) return;
  const instanceId = document.querySelector("[data-temp-instance]").value.trim();
  if (!instanceId) throw new Error("Temporary instance id is required");
  const config = forms.readSchemaFields(document.querySelector("[data-temp-config-fields]"), module.configSchema);
  const inputs = forms.readParamFields(
    document.querySelector("[data-temp-inputs-fields]"),
    Object.keys(module.ports?.inputs || {}).map((name) => ({ name, type: "dataKey" })),
  );
  validateDataKeyBindings(inputs, module.ports?.inputs || {});
  const outputs = forms.readParamFields(
    document.querySelector("[data-temp-outputs-fields]"),
    Object.keys(module.ports?.outputs || {}).map((name) => ({ name, type: "string" })),
  );
  const selectedId = uiState().selectedTempModuleId;
  const nextItem = window.TradeChartCore.createTemporaryModuleInstance(module, {
    instanceId, config, inputs, outputs,
  });
  pageState.pane.temporaryModules ||= [];
  if (selectedId) {
    pageState.pane.temporaryModules = window.TradeChartCore.upsertIdentity(
      pageState.pane.temporaryModules, selectedId, nextItem, "instanceId",
    );
    uiState().selectedTempModuleId = instanceId;
  } else {
    pageState.pane.temporaryModules = window.TradeChartCore.upsertIdentity(
      pageState.pane.temporaryModules, "", nextItem, "instanceId",
    );
    uiState().selectedTempModuleId = "";
  }
  syncSpec();
}

function removePaneTemporaryModule(instanceId) {
  const module = (pageState.pane.temporaryModules || []).find((item) => item.instanceId === instanceId);
  const outputs = Object.values(module?.outputs || {});
  pageState.pane.temporaryModules = (pageState.pane.temporaryModules || []).filter((item) => item.instanceId !== instanceId);
  pageState.pane.visualizers = (pageState.pane.visualizers || []).filter((item) => !Object.values(item.params || {}).some((value) => outputs.includes(value)));
  if (uiState().selectedTempModuleId === instanceId) uiState().selectedTempModuleId = "";
  syncSpec();
}

function selectedVisualizerDefinition() {
  const select = document.querySelector("[data-visualizer-select]");
  return window.TradeChartCore.visualizerCatalog({ dataKeys: pageState.backtest.dataKeys || {} }, paneScopedSpec())
    .find((item) => item.id === select?.value);
}

function fillVisualizerDraft() {
  const definition = selectedVisualizerDefinition();
  if (!definition) return;
  forms.renderParamFields(document.querySelector("[data-visualizer-fields]"), definition.params || [], {}, definition.optionMap || {});
}

function addPaneVisualizer() {
  const definition = selectedVisualizerDefinition();
  if (!definition) return;
  const params = forms.readParamFields(document.querySelector("[data-visualizer-fields]"), definition.params || []);
  validateDataKeyBindings(params, definition.inputPorts || {});
  pageState.pane.visualizers ||= [];
  const selectedId = uiState().selectedVisualizerId;
  const nextItem = window.TradeChartCore.createVisualizerInstance(definition, {
    id: selectedId || `${definition.id}.${Date.now().toString(36)}`,
    params,
  });
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
  pageState.pane.visualizers = (pageState.pane.visualizers || []).filter((item) => item.id !== visualizerId);
  if (uiState().selectedVisualizerId === visualizerId) uiState().selectedVisualizerId = "";
  syncSpec();
}

function visualizerSummary(visualizer) {
  if (visualizer.displayName) return visualizer.displayName;
  const definition = window.TradeChartCore.visualizerCatalog({ dataKeys: pageState.backtest.dataKeys || {} }, paneScopedSpec())
    .find((item) => item.id === visualizer.callback);
  const summary = Object.entries(visualizer.params || {})
    .filter(([, value]) => value !== undefined && value !== "")
    .map(([key, value]) => `${forms.humanizeName(key)}=${value}`)
    .join(", ");
  return `${definition?.label || visualizer.callback}${summary ? ` (${summary})` : ""}`;
}

function applyButtonLabel(base, selected) {
  return selected ? `Apply ${base}` : `Add ${base}`;
}

function renderChartControls() {
  const visualizers = window.TradeChartCore.visualizerCatalog({ dataKeys: pageState.backtest.dataKeys || {} }, paneScopedSpec());
  const controls = document.createElement("div");
  controls.className = "chart-controls";
  controls.innerHTML = `
    <section class="chart-tag-section">
      <h4>Temporary Instance Tags</h4>
      <div class="chart-layer-tags">${
        (pageState.pane.temporaryModules || []).length
          ? (pageState.pane.temporaryModules || []).map((module) => `<button class="chart-layer-tag ${uiState().selectedTempModuleId === module.instanceId ? "active" : ""}" data-select-temp-module="${module.instanceId}" type="button"><span class="layer-key">${forms.humanizeName(module.moduleId)}</span><span class="layer-data-key">${Object.values(module.outputs || {}).join(", ")}</span></button><button class="tag-remove" data-remove-temp-module="${module.instanceId}" type="button">Remove</button>`).join("")
          : '<span class="muted">No temporary modules</span>'
      }</div>
    </section>
    <section class="chart-tag-section">
      <h4>Data Tags</h4>
      <div class="chart-layer-tags">${
        (pageState.pane.visualizers || []).length
          ? (pageState.pane.visualizers || []).map((visualizer) => `<button class="chart-layer-tag ${uiState().selectedVisualizerId === visualizer.id ? "active" : ""}" data-select-visualizer="${visualizer.id}" type="button"><span class="layer-key">${visualizerSummary(visualizer)}</span></button><button class="tag-remove" data-remove-visualizer="${visualizer.id}" type="button">Remove</button>`).join("")
          : '<span class="muted">No visualizers</span>'
      }</div>
    </section>
    <section class="chart-control-zone">
      <div class="chart-control-block">
        <h4>Template</h4>
        <select data-temp-module-select="1">
          <option value=""></option>
          ${resultModuleDefinitions().map((row) => `<option value="${row.key}">${row.kind} / ${row.moduleId} / ${row.version}</option>`).join("")}
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
          ${visualizers.map((item) => `<option value="${item.id}">${item.label}</option>`).join("")}
        </select>
        <div data-visualizer-fields="1" class="structured-fields structured-fields-inline"></div>
        <button data-add-visualizer="1" type="button">${applyButtonLabel("Visualizer", uiState().selectedVisualizerId)}</button>
        <button data-save-chart="1" type="button">Save</button>
      </div>
    </section>
  `;
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
        document.getElementById("chartStatus").textContent = error.message;
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
      uiState().selectedTempModuleId = button.dataset.selectTempModule;
      const module = (pageState.pane.temporaryModules || []).find((item) => item.instanceId === button.dataset.selectTempModule);
      const definition = resultModuleDefinitions().find((row) => row.kind === module?.kind && row.moduleId === module?.moduleId && row.version === module?.version);
      const select = area.querySelector("[data-temp-module-select]");
      if (definition && select) select.value = definition.key;
      fillTemporaryModuleDraft();
      drawPane();
    });
  });
  area.querySelectorAll("[data-select-visualizer]").forEach((button) => {
    button.addEventListener("click", () => {
      uiState().selectedVisualizerId = button.dataset.selectVisualizer;
      const visualizer = (pageState.pane.visualizers || []).find((item) => item.id === button.dataset.selectVisualizer);
      const select = area.querySelector("[data-visualizer-select]");
      if (visualizer && select) select.value = visualizer.callback;
      fillVisualizerDraft();
      drawPane();
    });
  });
  area.querySelectorAll("[data-add-visualizer]").forEach((button) => {
    button.addEventListener("click", () => {
      try {
        addPaneVisualizer();
      } catch (error) {
        document.getElementById("chartStatus").textContent = error.message;
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
        clearTimeout(pageState.saveTimer);
        await saveCurrentSpec();
        document.getElementById("chartStatus").textContent = `${pageState.backtest.backtestId} saved`;
      } catch (error) {
        document.getElementById("chartStatus").textContent = error.message;
      } finally {
        button.disabled = false;
      }
    });
  });
}

function setViewError(message = "") {
  const node = document.querySelector("[data-chart-view-error]");
  if (!node) return;
  node.textContent = message;
  node.hidden = !message;
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
  pageState.chart.timeScale().setVisibleRange({
    from: Math.max(start, pageState.timeInfo.start),
    to: Math.min(end, pageState.timeInfo.end),
  });
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
  pageState.chart.timeScale().fitContent();
  setViewError("");
  persistPaneView();
}

function toggleLogScale(button) {
  if (!pageState.chart) return;
  pageState.pane.view.logScale = !pageState.pane.view.logScale;
  pageState.chart.priceScale("right").applyOptions({ mode: window.TradeChartCore.priceScaleMode(pageState.pane.view.logScale) });
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

function drawPane() {
  const area = document.getElementById("singleChartArea");
  pageState.observer?.disconnect();
  pageState.chart?.remove?.();
  pageState.chart = null;
  pageState.observer = null;
  area.innerHTML = "";

  const panel = document.createElement("div");
  panel.className = "chart-panel";
  pageState.pane.view ||= { start: null, end: null, logScale: false, controlsCollapsed: false };
  const controls = renderChartControls();
  controls.hidden = !!pageState.pane.view.controlsCollapsed;
  pageState.timeInfo = window.TradeChartCore.paneTimeInfo(pageState.result, pageState.pane, paneScopedSpec());
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
    <span class="muted">${escapeHtml(zone.label)} · ${pageState.timeInfo.showTime ? "intraday" : "date"}</span>
    <span class="chart-view-error" data-chart-view-error hidden></span>
  `;
  const container = document.createElement("div");
  container.className = "tv-chart";
  container.style.height = "calc(100vh - 230px)";
  panel.appendChild(controls);
  panel.appendChild(viewToolbar);
  panel.appendChild(container);
  area.appendChild(panel);

  const chart = window.TradeChartCore.createFinancialChart(container, {
    timeZone: zone.timeZone,
    showTime: pageState.timeInfo.showTime,
    logScale: !!pageState.pane.view.logScale,
  });
  window.TradeChartCore.drawFinancialPane(window.LightweightCharts, chart, pageState.result, pageState.pane, paneScopedSpec());
  const savedStart = pageState.timeInfo.start == null
    ? null
    : (typeof storedStart === "number" && Number.isFinite(storedStart) ? Math.max(storedStart, pageState.timeInfo.start) : pageState.timeInfo.start);
  const savedEnd = pageState.timeInfo.end == null
    ? null
    : (typeof storedEnd === "number" && Number.isFinite(storedEnd) ? Math.min(storedEnd, pageState.timeInfo.end) : pageState.timeInfo.end);
  if ((pageState.pane.view.start || pageState.pane.view.end) && savedStart != null && savedEnd != null && savedStart < savedEnd) {
    chart.timeScale().setVisibleRange({ from: savedStart, to: savedEnd });
  } else {
    chart.timeScale().fitContent();
  }
  const observer = new ResizeObserver(() => chart.applyOptions({ width: container.clientWidth, height: container.clientHeight }));
  observer.observe(container);
  pageState.chart = chart;
  pageState.observer = observer;
  pageState.rangeStartInput = viewToolbar.querySelector("[data-chart-start]");
  pageState.rangeEndInput = viewToolbar.querySelector("[data-chart-end]");
  viewToolbar.querySelector("[data-apply-chart-range]")?.addEventListener("click", applyTimeRange);
  viewToolbar.querySelector("[data-fit-chart]")?.addEventListener("click", fitPane);
  const logButton = viewToolbar.querySelector("[data-toggle-chart-log]");
  logButton?.addEventListener("click", () => toggleLogScale(logButton));
  const controlsButton = viewToolbar.querySelector("[data-toggle-chart-controls]");
  controlsButton?.addEventListener("click", () => toggleControls(controls, controlsButton));
  bindControls(area);
}

async function main() {
  await loadBrowserSession();
  if (!backtestId) throw new Error("backtestId is required.");
  if (!window.LightweightCharts) throw new Error("Chart library failed to load.");
  if (!window.TradeChartCore) throw new Error("Chart core failed to load.");
  const [moduleResponse, visualizerResponse] = await Promise.all([
    getJson("/api/modules?limit=500"),
    getJson("/api/visualizers"),
  ]);
  pageState.resultModules = moduleResponse.modules || {};
  window.TradeChartCore.setVisualizerDefinitions(visualizerResponse.visualizers || []);
  window.TradeChartCore.setTemporaryModuleDefinitions(pageState.resultModules);
  pageState.backtest = await getJson(`/api/backtests/${encodeURIComponent(backtestId)}/view`);
  pageState.spec = window.TradeChartCore.normalizeVisualizationSpec({ dataKeys: pageState.backtest.dataKeys || {} }, pageState.backtest.visualization || {});
  syncTimezoneButton();
  pageState.pane = (pageState.spec.panes || [])[paneIndex];
  if (!pageState.pane) throw new Error(`Unknown pane ${paneIndex}.`);
  const paths = window.TradeChartCore.collectPaneSourcePaths({ dataKeys: pageState.backtest.dataKeys || {} }, pageState.pane, paneScopedSpec());
  const resultResponse = await postJson(`/api/backtests/${encodeURIComponent(backtestId)}/result`, {
    paths,
    temporaryModules: pageState.pane.temporaryModules || [],
  });
  pageState.result = resultResponse.result || { dataKeys: pageState.backtest.dataKeys || {} };
  document.getElementById("chartTitle").textContent = pageState.pane.title || pageState.pane.id || "Chart";
  document.getElementById("chartStatus").textContent = pageState.backtest.backtestId;
  drawPane();
}

document.getElementById("chartTimezoneBtn")?.addEventListener("click", () => {
  if (!pageState.spec) return;
  const current = currentTimeZone();
  const candidate = window.prompt("IANA time zone", current.timeZone);
  if (candidate === null) return;
  const timeZone = candidate.trim();
  try {
    new Intl.DateTimeFormat("en-US", { timeZone }).format(0);
  } catch {
    document.getElementById("chartStatus").textContent = "Enter a valid IANA time zone.";
    return;
  }
  pageState.spec.timeZone = timeZone;
  syncTimezoneButton();
  drawPane();
  scheduleSpecSave();
});

main().catch((error) => {
  document.getElementById("chartStatus").textContent = error.message;
});
