const params = new URLSearchParams(location.search);
const backtestId = params.get("backtestId") || "";
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
};

const forms = window.TradeModuleForms;

async function getJson(path) {
  const response = await fetch(path, { headers: { Accept: "application/json" } });
  if (!response.ok) throw new Error(`${path} returned ${response.status}`);
  return response.json();
}

async function postJson(path, payload) {
  const response = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "application/json" },
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
    name: "current",
    visualizationId: `${backtestId}-current`,
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

function dataKeyOptions() {
  return window.TradeChartCore.chartLayerCatalog({ dataKeys: pageState.backtest.dataKeys || {} }, paneScopedSpec())
    .map((item) => ({ value: item.dataKey, label: `${item.label || item.dataKey} (${item.dataKey})` }));
}

function fillTemporaryModuleDraft() {
  const module = selectedResultModule();
  if (!module) return;
  const instanceInput = document.querySelector("[data-temp-instance]");
  const safeModuleId = (module.moduleId || "tmp").replace(/[^a-zA-Z0-9_.-]/g, "-");
  instanceInput.value = `tmp.${safeModuleId}.${Date.now().toString(36)}`;
  forms.renderSchemaFields(document.querySelector("[data-temp-config-fields]"), module.configSchema, schemaDefaults(module.configSchema));
  forms.renderParamFields(
    document.querySelector("[data-temp-inputs-fields]"),
    Object.keys(module.ports?.inputs || {}).map((name) => ({
      name,
      label: forms.humanizeName(name),
      type: "dataKey",
      description: module.ports.inputs[name]?.type || "input data key",
    })),
    {},
    Object.fromEntries(Object.keys(module.ports?.inputs || {}).map((name) => [name, dataKeyOptions()])),
  );
  forms.renderParamFields(
    document.querySelector("[data-temp-outputs-fields]"),
    Object.keys(module.ports?.outputs || {}).map((name) => ({
      name,
      label: forms.humanizeName(name),
      type: "string",
      description: module.ports.outputs[name]?.type || "output data key",
      default: nextUniqueDataKey(`${module.moduleId}.${name}`),
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
  const outputs = forms.readParamFields(
    document.querySelector("[data-temp-outputs-fields]"),
    Object.keys(module.ports?.outputs || {}).map((name) => ({ name, type: "string" })),
  );
  const selectedId = uiState().selectedTempModuleId;
  const nextItem = {
    instanceId,
    kind: module.kind,
    moduleId: module.moduleId,
    version: module.version,
    config,
    inputs,
    outputs,
  };
  pageState.pane.temporaryModules ||= [];
  if (selectedId) {
    pageState.pane.temporaryModules = pageState.pane.temporaryModules.map((item) => item.instanceId === selectedId ? nextItem : item);
    uiState().selectedTempModuleId = instanceId;
  } else {
    pageState.pane.temporaryModules.push(nextItem);
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
  const missing = (definition.params || []).filter((field) => !params[field.name]);
  if (missing.length) {
    throw new Error(`Missing visualizer params: ${missing.map((field) => field.label || field.name).join(", ")}`);
  }
  pageState.pane.visualizers ||= [];
  const selectedId = uiState().selectedVisualizerId;
  const nextItem = {
    id: selectedId || `${definition.id}.${Date.now().toString(36)}`,
    callback: definition.id,
    params,
  };
  if (selectedId) {
    pageState.pane.visualizers = pageState.pane.visualizers.map((item) => item.id === selectedId ? nextItem : item);
  } else {
    pageState.pane.visualizers.push(nextItem);
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
        <input data-temp-instance="1" placeholder="instance id" />
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

function drawPane() {
  const area = document.getElementById("singleChartArea");
  pageState.observer?.disconnect();
  pageState.chart?.remove?.();
  pageState.chart = null;
  pageState.observer = null;
  area.innerHTML = "";

  const panel = document.createElement("div");
  panel.className = "chart-panel";
  const controls = renderChartControls();
  const container = document.createElement("div");
  container.className = "tv-chart";
  container.style.height = "calc(100vh - 230px)";
  panel.appendChild(controls);
  panel.appendChild(container);
  area.appendChild(panel);

  const chart = window.TradeChartCore.createFinancialChart(container);
  window.TradeChartCore.drawFinancialPane(window.LightweightCharts, chart, pageState.result, pageState.pane, paneScopedSpec());
  chart.timeScale().fitContent();
  const observer = new ResizeObserver(() => chart.applyOptions({ width: container.clientWidth, height: container.clientHeight }));
  observer.observe(container);
  pageState.chart = chart;
  pageState.observer = observer;
  bindControls(area);
}

async function main() {
  if (!backtestId) throw new Error("backtestId is required.");
  if (!window.LightweightCharts) throw new Error("Chart library failed to load.");
  if (!window.TradeChartCore) throw new Error("Chart core failed to load.");
  pageState.resultModules = (await getJson("/api/modules?limit=500")).modules || {};
  pageState.backtest = await getJson(`/api/backtests/${encodeURIComponent(backtestId)}/meta`);
  pageState.spec = window.TradeChartCore.normalizeVisualizationSpec({ dataKeys: pageState.backtest.dataKeys || {} }, pageState.backtest.visualization || {});
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

main().catch((error) => {
  document.getElementById("chartStatus").textContent = error.message;
});
