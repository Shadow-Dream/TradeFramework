const state = {
  summary: null,
  datasets: [],
  datasetVersions: [],
  samplers: [],
  environments: [],
  analyses: [],
  repositoryCatalogs: {},
  selectedRepository: "modules",
  selectedRepositoryFolderId: "*",
  repositoryFolderSelections: {},
  repositoryFilters: {},
  datasetWorkspaces: [],
  datasetWorkspaceScripts: [],
  datasetRecipes: [],
  datasetBuildJobs: [],
  backtests: [],
  backtestJobs: [],
  backtestJobMaxConcurrent: 0,
  selectedBacktest: null,
  resultBacktestId: "",
  resultCharts: [],
  history: [],
  pipelineDraft: null,
  pipelineModules: {},
  analysisModules: {},
  environmentModules: {},
  visualizers: [],
  resultModules: {},
  moduleCacheByKind: {},
  moduleTotalsByKind: {},
  pipelines: {},
  pipelineVersions: [],
  selectedModuleKind: "Universe",
  selectedModuleRepositoryItem: null,
  selectedModuleRepository: "modules",
  uiRepositorySelections: {},
  totals: {},
  pipelineViewport: {
    scale: 1,
    x: 0,
    y: 0,
    fullscreen: false,
    infoCollapsed: false,
    initialized: false,
  },
  backtestViewport: {
    scale: 1,
    x: 0,
    y: 0,
    fullscreen: false,
    initialized: false,
  },
};
window.__tradeState = state;

const pipelineEditorState = {
  pipelineId: "",
  definition: null,
  manifest: null,
  versions: [],
  loadedVersion: "",
  loadedDefinition: null,
};
const backtestEntryState = {
  pipelineId: "",
  pipelineVersion: "",
  samplerKey: "",
  samplerParameters: {},
  environmentKey: "",
  analysisKey: "",
  compositionValidation: "idle",
  compositionMessage: "Select exact resource versions",
  compositionSequence: 0,
  preparedSubmissionToken: "",
  preparedRequestDigest: "",
  preparedRequestFingerprint: "",
  preparedTokenExpiresAt: 0,
  preparedBuildExpiresAt: 0,
  submissionPending: false,
};
window.__tradeBacktestEntryState = backtestEntryState;
const environmentEditorState = {
  environmentKey: "",
  draftsByEnvironment: {},
  returnView: "environment",
};
const analysisEditorState = {
  analysisKey: "",
  draftsByAnalysis: {},
  returnView: "analysis",
};
let pendingRepositoryRename = null;
let pendingDatasetReplace = null;
const datasetWorkspaceSelection = new Set();

const $ = (id) => document.getElementById(id);
const forms = window.TradeModuleForms;

let currentView = "overview";
let currentPipelinePage = "browser";
let currentPipelineSection = "composer";
let currentEnvironmentSection = "browser";
let currentAnalysisSection = "browser";
let currentBacktestSection = "entry";
const loadedViews = new Set();
let visualizationSaveTimer = null;
let visualizationSaveSeq = 0;
let resultSelectionSeq = 0;
let showArchivedBacktests = false;
let showInactivePipelines = false;
let uploadZipValidationSeq = 0;
let uploadZipValidationState = { pending: false, error: "" };
let pendingModuleLoad = null;
let backtestJobPollTimer = null;
let healthState = { ok: false, text: "Checking" };
let serviceRuntimeState = {
  active: false,
  serviceTime: null,
  loading: true,
};
let viewLoadingSequence = 0;
const viewLoadingTokens = new Map();
let authState = { user: null, csrfToken: "", expiresAt: 0 };
const LOCAL_UI_ERROR = Symbol("local-ui-error");
function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

const VIEW_PATHS = {
  overview: "/overview",
  agent: "/agent",
  pipeline: "/pipeline",
  environment: "/environment",
  analysis: "/analysis",
  visualizers: "/visualizers",
  modules: "/modules",
  data: "/data",
  backtests: "/backtests",
  "mining-kline": "/mining/k-line",
  results: "/result",
};

function normalizedViewFromPath(pathname) {
  const path = String(pathname || "/").toLowerCase();
  if (path === "/backtests") currentBacktestSection = "entry";
  if (path === "/pipeline") {
    currentPipelinePage = "browser";
    currentPipelineSection = "composer";
    return "pipeline";
  }
  if (path === "/pipeline/builder") {
    currentPipelinePage = "builder";
    currentPipelineSection = "composer";
    pipelineEditorState.pipelineId = new URLSearchParams(location.search).get("pipelineId") || "";
    return "pipeline";
  }
  if (path === "/signal-blueprint") {
    currentPipelinePage = "builder";
    currentPipelineSection = "signal";
    pipelineEditorState.pipelineId = new URLSearchParams(location.search).get("pipelineId") || "";
    return "pipeline";
  }
  if (path === "/environment-blueprint") {
    environmentEditorState.environmentKey = new URLSearchParams(location.search).get("environment") || "";
    currentEnvironmentSection = environmentEditorState.environmentKey ? "blueprint" : "browser";
    return "environment";
  }
  if (path === "/environment") {
    currentEnvironmentSection = "browser";
    environmentEditorState.returnView = "environment";
  }
  if (path === "/analysis-blueprint") {
    analysisEditorState.analysisKey = new URLSearchParams(location.search).get("analysis") || "";
    currentAnalysisSection = analysisEditorState.analysisKey ? "blueprint" : "browser";
    return "analysis";
  }
  if (path === "/analysis") {
    currentAnalysisSection = "browser";
    analysisEditorState.returnView = "analysis";
  }
  if (path === "/result") {
    state.resultBacktestId = new URLSearchParams(location.search).get("backtestId") || "";
    return "results";
  }
  if (path === "/pipeline/manifest" || path === "/manifest") {
    currentPipelinePage = "builder";
    currentPipelineSection = "manifest";
    pipelineEditorState.pipelineId = new URLSearchParams(location.search).get("pipelineId") || "";
    return "pipeline";
  }
  const matched = Object.entries(VIEW_PATHS).find(([, value]) => value === path);
  return matched?.[0] || "overview";
}

function pathForView(viewId) {
  if (viewId === "pipeline") {
    if (currentPipelinePage !== "builder") return "/pipeline";
    const path = currentPipelineSection === "signal"
      ? "/signal-blueprint"
      : currentPipelineSection === "manifest"
          ? "/pipeline/manifest"
          : "/pipeline/builder";
    const pipelineId = pipelineEditorState.pipelineId;
    return pipelineId ? `${path}?pipelineId=${encodeURIComponent(pipelineId)}` : path;
  }
  if (viewId === "environment" && currentEnvironmentSection === "blueprint") {
    const environmentKey = environmentEditorState.environmentKey;
    return environmentKey
      ? `/environment-blueprint?environment=${encodeURIComponent(environmentKey)}`
      : "/environment-blueprint";
  }
  if (viewId === "analysis" && currentAnalysisSection === "blueprint") {
    const analysisKey = analysisEditorState.analysisKey;
    return analysisKey
      ? `/analysis-blueprint?analysis=${encodeURIComponent(analysisKey)}`
      : "/analysis-blueprint";
  }
  if (viewId === "results") {
    return state.resultBacktestId
      ? `/result?backtestId=${encodeURIComponent(state.resultBacktestId)}`
      : "/result";
  }
  return VIEW_PATHS[viewId] || "/overview";
}

function uiResourceRef(kind, id, version = "", digest = "", label = "") {
  if (!kind || !id) return null;
  return {
    kind: String(kind),
    id: String(id),
    ...(version !== "" && version !== undefined ? { version: String(version) } : {}),
    ...(digest ? { digest: String(digest) } : {}),
    ...(label ? { label: String(label) } : {}),
  };
}

function uiResourceRefForItem(repository, item) {
  if (!item) return null;
  if (item.pipelineId) return uiResourceRef("pipeline", item.pipelineId, item.version, item.contentDigest, item.name || item.label);
  if (item.datasetId) return uiResourceRef("dataset", item.datasetId, item.datasetVersionId || "", item.contentHash, item.name || item.label);
  if (item.samplerId) return uiResourceRef("sampler", item.samplerId, item.version, item.contentDigest, item.name || item.label);
  if (item.environmentId) return uiResourceRef("environment", item.environmentId, item.version, item.contentDigest, item.name || item.label);
  if (item.analysisId) return uiResourceRef("analysis", item.analysisId, item.version, item.contentDigest, item.name || item.label);
  if (item.backtestId) return uiResourceRef(item.sourceRepository === "results" ? "result" : "backtest", item.backtestId, "", item.contentDigest, item.name || item.label);
  if (item.visualizerId) return uiResourceRef("visualizer", item.visualizerId, item.version, item.contentDigest, item.name || item.label);
  if (item.workspaceId) return uiResourceRef("dataset-workspace", item.workspaceId, "", item.contentDigest, item.name || item.label);
  if (item.recipeId) return uiResourceRef("dataset-script", item.recipeId, item.version, item.contentDigest, item.name || item.label);
  if (item.moduleId && item.kind) return uiResourceRef(`module:${repository}:${item.kind}`, item.moduleId, item.version, item.contentDigest, item.name || item.label);
  if (item.itemId) return uiResourceRef(repository, item.itemId, item.version, item.contentDigest, item.name || item.label);
  return null;
}

function uiGraphSelection(rootId) {
  const root = $(rootId);
  const selectedNodes = Object.values(root?.__liteGraphCanvas?.selected_nodes || {});
  if (selectedNodes.length !== 1) return null;
  const node = selectedNodes[0];
  const meta = root?.__liteGraphNodeMeta?.get?.(node.id);
  return {
    kind: "graph-node",
    id: String(meta?.id || node.id),
    ...(node.title ? { label: String(node.title) } : {}),
  };
}

function currentUiSubview() {
  if (currentView === "pipeline") {
    return currentPipelinePage === "builder" ? currentPipelineSection : "browser";
  }
  if (currentView === "environment") return currentEnvironmentSection;
  if (currentView === "analysis") return currentAnalysisSection;
  if (currentView === "backtests") return currentBacktestSection;
  return undefined;
}

function currentUiContext() {
  const resourceRefs = [];
  let selection;
  if (currentView === "pipeline") {
    const pipeline = selectedPipelineRecord();
    const version = pipelineEditorState.loadedVersion || pipeline?.currentVersion || "";
    const summary = (pipelineEditorState.versions || []).find((row) => String(row.version) === String(version));
    const reference = pipeline && uiResourceRef("pipeline", pipeline.pipelineId, version, summary?.contentDigest, pipeline.name);
    if (reference) resourceRefs.push(reference);
    selection = currentPipelineSection === "signal" ? uiGraphSelection("alphaGraphBuilder") : undefined;
  } else if (currentView === "environment" && currentEnvironmentSection === "blueprint") {
    const [environmentId, version] = environmentEditorState.environmentKey.split("::");
    const environment = state.environments.find((row) => row.environmentId === environmentId && String(row.version) === String(version));
    const reference = environment && uiResourceRef("environment", environmentId, version, environment.contentDigest, environment.name);
    if (reference) resourceRefs.push(reference);
    selection = currentEnvironmentSection === "blueprint" ? uiGraphSelection("environmentGraphBuilder") : undefined;
  } else if (currentView === "analysis" && currentAnalysisSection === "blueprint") {
    const [analysisId, version] = analysisEditorState.analysisKey.split("::");
    const analysis = state.analyses.find((row) => row.analysisId === analysisId && String(row.version) === String(version));
    const reference = analysis && uiResourceRef("analysis", analysisId, version, analysis.contentDigest, analysis.name);
    if (reference) resourceRefs.push(reference);
    selection = currentAnalysisSection === "blueprint" ? uiGraphSelection("analysisGraphBuilder") : undefined;
  } else if (currentView === "backtests") {
    const pipeline = state.pipelines?.[backtestEntryState.pipelineId];
    const dataset = selectedBacktestDatasetEvidence();
    const sampler = selectedBacktestSampler();
    const environment = selectedBacktestEnvironment();
    const analysis = selectedBacktestAnalysis();
    [
      pipeline && uiResourceRef("pipeline", pipeline.pipelineId, backtestEntryState.pipelineVersion, "", pipeline.name),
      dataset && uiResourceRef("dataset", dataset.datasetId, dataset.datasetVersionId, dataset.contentHash),
      sampler && uiResourceRef("sampler", sampler.samplerId, sampler.version, sampler.contentDigest, sampler.name),
      environment && uiResourceRef("environment", environment.environmentId, environment.version, environment.contentDigest, environment.name),
      analysis && uiResourceRef("analysis", analysis.analysisId, analysis.version, analysis.contentDigest, analysis.name),
    ].filter(Boolean).forEach((reference) => resourceRefs.push(reference));
  } else if (currentView === "results" && state.resultBacktestId) {
    resourceRefs.push(uiResourceRef("result", state.resultBacktestId, "", "", state.selectedBacktest?.name));
  } else if (currentView === "mining-kline") {
    const job = miningKLine?.state?.jobs?.find((candidate) => candidate.jobId === miningKLine.state.selectedJobId);
    if (job) {
      resourceRefs.push(uiResourceRef("mining-job", job.jobId, "", "", job.name));
      selection = { kind: "mining-job", id: String(job.jobId), label: String(job.name || job.jobId) };
    }
  } else {
    const repository = currentView === "modules"
      ? state.selectedModuleRepository
      : currentView === "data" ? "data"
        : currentView === "visualizers" ? "visualizers"
          : currentView === "environment" ? "environments"
            : currentView === "analysis" ? "analyses"
              : "";
    const entries = state.uiRepositorySelections?.[repository] || [];
    entries.slice(0, 32).map((item) => uiResourceRefForItem(repository, item)).filter(Boolean)
      .forEach((reference) => resourceRefs.push(reference));
    if (entries.length === 1) {
      const item = entries[0];
      selection = {
        kind: "resource",
        id: String(item.itemId || item.sourceItemId || item.moduleId || item.datasetId || item.workspaceId || "selected"),
        ...(item.name || item.label ? { label: String(item.name || item.label) } : {}),
      };
    }
  }
  return {
    route: `${location.pathname}${location.search}`,
    view: currentView,
    ...(currentUiSubview() ? { subview: currentUiSubview() } : {}),
    resourceRefs: resourceRefs.filter(Boolean).slice(0, 32),
    ...(selection ? { selection } : {}),
    ...(activeUiDocumentRecord ? {
      documentId: activeUiDocumentRecord.documentId,
      documentRevision: activeUiDocumentRecord.revision,
    } : {}),
  };
}

let uiContextSyncTimer = 0;
let lastUiContextFingerprint = "";
function scheduleUiContextSync() {
  clearTimeout(uiContextSyncTimer);
  uiContextSyncTimer = setTimeout(() => {
    const context = currentUiContext();
    const fingerprint = JSON.stringify(context);
    if (fingerprint === lastUiContextFingerprint) return;
    lastUiContextFingerprint = fingerprint;
    window.TradeUiSync?.publishContext(context).catch(() => undefined);
  }, 120);
  scheduleUiDocumentSync();
}

let activeUiDocumentRecord = null;
let uiDocumentSyncTimer = 0;
let uiDocumentSyncSequence = 0;

function uiDraftError(code, message) {
  const error = new Error(message);
  error.code = code;
  return error;
}

function exactUiDraft(value, allowed, required = allowed) {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw uiDraftError("invalid_document", "Draft content must be a JSON object");
  }
  const unknown = Object.keys(value).filter((key) => !allowed.includes(key));
  const missing = required.filter((key) => !(key in value));
  if (unknown.length || missing.length) {
    throw uiDraftError("invalid_document", "Draft content does not match the editor contract");
  }
  return value;
}

function uiDraftJson(value) {
  return `${JSON.stringify(value, null, 2)}\n`;
}

function activePipelineUiDocument() {
  if (currentView !== "pipeline" || currentPipelinePage !== "builder"
      || !state.pipelineDraft || currentPipelineSection === "manifest") return null;
  const pipelineId = pipelineField("Id")?.value?.trim() || state.pipelineDraft.meta?.pipelineId || pipelineEditorState.pipelineId;
  if (!pipelineId) return null;
  return {
    documentId: `engine:pipeline:${pipelineId}`,
    kind: "pipeline-draft",
    label: `Pipeline · ${pipelineField("Name")?.value?.trim() || pipelineId}`,
    getContent() {
      $("alphaGraphBuilder")?.__flushPendingEmit?.();
      return uiDraftJson({
        schemaVersion: 1,
        pipelineId: pipelineField("Id")?.value?.trim() || pipelineId,
        name: pipelineField("Name")?.value?.trim() || pipelineId,
        stages: structuredClone(state.pipelineDraft?.stages || {}),
        instances: structuredClone(state.pipelineDraft?.instances || {}),
        signalGraph: structuredClone(state.pipelineDraft?.alphaGraph || { nodes: [], inputs: {}, outputs: {} }),
        config: structuredClone(state.pipelineDraft?.config || { observationInput: { whitelist: [], blacklist: [] } }),
      });
    },
    applyContent(content) {
      const parsed = exactUiDraft(JSON.parse(content), ["schemaVersion", "pipelineId", "name", "stages", "instances", "signalGraph", "config"]);
      if (parsed.schemaVersion !== 1 || typeof parsed.pipelineId !== "string" || !parsed.pipelineId.trim()
          || typeof parsed.name !== "string" || !parsed.stages || typeof parsed.stages !== "object"
          || Array.isArray(parsed.stages) || !parsed.instances || typeof parsed.instances !== "object"
          || Array.isArray(parsed.instances) || !parsed.signalGraph || typeof parsed.signalGraph !== "object"
          || Array.isArray(parsed.signalGraph) || !parsed.config || typeof parsed.config !== "object"
          || Array.isArray(parsed.config)) {
        throw uiDraftError("invalid_document", "Pipeline draft fields are invalid");
      }
      state.pipelineDraft = sanitizePipelineDraft({
        stages: structuredClone(parsed.stages),
        instances: structuredClone(parsed.instances),
        alphaGraph: structuredClone(parsed.signalGraph),
        config: structuredClone(parsed.config),
        meta: { pipelineId: parsed.pipelineId.trim(), name: parsed.name.trim() || parsed.pipelineId.trim() },
      }, pipelineEditorState.definition || {});
      pipelineField("Id").value = state.pipelineDraft.meta.pipelineId;
      pipelineField("Name").value = state.pipelineDraft.meta.name;
      const observation = state.pipelineDraft.config?.observationInput || {};
      renderObservationEditor("Whitelist", observation.whitelist || []);
      renderObservationEditor("Blacklist", observation.blacklist || []);
      pipelineField("AlphaGraph").value = JSON.stringify(state.pipelineDraft.alphaGraph, null, 2);
      renderAlphaGraphBuilder({ flushBeforeCleanup: false });
      renderPipelineBuilder();
      invalidateBacktestBuild("Pipeline draft changed by Agent · Build again before running");
    },
  };
}

function activeEnvironmentUiDocument() {
  if (currentView !== "environment" || currentEnvironmentSection !== "blueprint") return null;
  const key = environmentEditorState.environmentKey;
  const draft = environmentEditorState.draftsByEnvironment[key];
  const [environmentId, version] = key.split("::");
  const source = state.environments.find((row) => row.environmentId === environmentId && String(row.version) === String(version));
  if (!draft || !source) return null;
  return {
    documentId: `engine:environment:${key}`,
    kind: "environment-draft",
    label: `Environment · ${draft.name || environmentId}`,
    getContent() {
      $("environmentGraphBuilder")?.__flushPendingEmit?.();
      return uiDraftJson({
        schemaVersion: 2,
        environmentId: draft.environmentId,
        name: draft.name,
        description: source.description || "",
        instances: structuredClone(draft.instances),
        graph: structuredClone(draft.graph),
      });
    },
    applyContent(content) {
      const parsed = exactUiDraft(JSON.parse(content), ["schemaVersion", "environmentId", "name", "description", "instances", "graph"]);
      if (parsed.schemaVersion !== 2 || typeof parsed.environmentId !== "string" || !parsed.environmentId.trim()
          || typeof parsed.name !== "string" || !parsed.name.trim() || typeof parsed.description !== "string"
          || !parsed.instances || typeof parsed.instances !== "object" || Array.isArray(parsed.instances)
          || !parsed.graph || typeof parsed.graph !== "object" || Array.isArray(parsed.graph)) {
        throw uiDraftError("invalid_document", "Environment draft fields are invalid");
      }
      environmentEditorState.draftsByEnvironment[key] = {
        environmentId: parsed.environmentId,
        name: parsed.name,
        instances: structuredClone(parsed.instances),
        graph: structuredClone(parsed.graph),
      };
      renderEnvironmentDetails();
      invalidateBacktestBuild("Environment draft changed by Agent · Build again before running");
    },
  };
}

function activeAnalysisUiDocument() {
  if (currentView !== "analysis" || currentAnalysisSection !== "blueprint") return null;
  const key = analysisEditorState.analysisKey;
  const draft = analysisEditorState.draftsByAnalysis[key];
  const [analysisId, version] = key.split("::");
  const source = state.analyses.find((row) => row.analysisId === analysisId && String(row.version) === String(version));
  if (!draft || !source) return null;
  return {
    documentId: `engine:analysis:${key}`,
    kind: "analysis-draft",
    label: `Analysis · ${draft.name || analysisId}`,
    getContent() {
      $("analysisGraphBuilder")?.__flushPendingEmit?.();
      return uiDraftJson({
        schemaVersion: 1,
        analysisId: draft.analysisId,
        name: draft.name,
        description: source.description || "",
        instances: structuredClone(draft.instances),
        graph: structuredClone(draft.graph),
      });
    },
    applyContent(content) {
      const parsed = exactUiDraft(JSON.parse(content), ["schemaVersion", "analysisId", "name", "description", "instances", "graph"]);
      if (parsed.schemaVersion !== 1 || typeof parsed.analysisId !== "string" || !parsed.analysisId.trim()
          || typeof parsed.name !== "string" || !parsed.name.trim() || typeof parsed.description !== "string"
          || !parsed.instances || typeof parsed.instances !== "object" || Array.isArray(parsed.instances)
          || !parsed.graph || typeof parsed.graph !== "object" || Array.isArray(parsed.graph)) {
        throw uiDraftError("invalid_document", "Analysis draft fields are invalid");
      }
      analysisEditorState.draftsByAnalysis[key] = {
        analysisId: parsed.analysisId,
        name: parsed.name,
        instances: structuredClone(parsed.instances),
        graph: structuredClone(parsed.graph),
      };
      renderAnalysisDetails();
      invalidateBacktestBuild("Analysis draft changed by Agent · Build again before running");
    },
  };
}

function activeBacktestUiDocument() {
  if (currentView !== "backtests" || currentBacktestSection !== "entry") return null;
  const request = buildBacktestCompositionRequest();
  if (!request) return null;
  return {
    documentId: "engine:backtest:composition",
    kind: "backtest-draft",
    label: "Backtest composition",
    getContent: () => uiDraftJson({ schemaVersion: 1, ...buildBacktestCompositionRequest() }),
    applyContent(content) {
      const parsed = exactUiDraft(JSON.parse(content), ["schemaVersion", "pipeline", "datasetId", "datasetVersionId", "sampler", "environment", "analysis"]);
      if (parsed.schemaVersion !== 1 || typeof parsed.datasetId !== "string"
          || !parsed.pipeline || typeof parsed.pipeline !== "object"
          || !parsed.sampler || typeof parsed.sampler !== "object"
          || !parsed.environment || typeof parsed.environment !== "object"
          || !parsed.analysis || typeof parsed.analysis !== "object") {
        throw uiDraftError("invalid_document", "Backtest composition fields are invalid");
      }
      const values = {
        pipeline: `${parsed.pipeline.pipelineId || ""}::${parsed.pipeline.version || ""}`,
        dataset: parsed.datasetId,
        sampler: `${parsed.sampler.samplerId || ""}::${parsed.sampler.version || ""}`,
        environment: `${parsed.environment.environmentId || ""}::${parsed.environment.version || ""}`,
        analysis: `${parsed.analysis.analysisId || ""}::${parsed.analysis.version || ""}`,
      };
      const controls = {
        pipeline: $("backtestPipelineSelect"), dataset: $("backtestDataset"), sampler: $("backtestSampler"),
        environment: $("backtestEnvironmentSelect"), analysis: $("backtestAnalysisSelect"),
      };
      if (!Object.entries(controls).every(([name, control]) => control && [...control.options].some((option) => option.value === values[name]))) {
        throw uiDraftError("invalid_document", "Backtest composition refers to an unavailable exact resource version");
      }
      Object.entries(controls).forEach(([name, control]) => { control.value = values[name]; });
      backtestEntryState.pipelineId = parsed.pipeline.pipelineId;
      backtestEntryState.pipelineVersion = parsed.pipeline.version;
      backtestEntryState.samplerKey = values.sampler;
      backtestEntryState.samplerParameters = structuredClone(parsed.sampler.parameters || {});
      backtestEntryState.environmentKey = values.environment;
      backtestEntryState.analysisKey = values.analysis;
      invalidateBacktestBuild("Composition changed by Agent · Build again before running");
      renderBacktestChain();
    },
  };
}

function activeVisualizationUiDocument() {
  if (currentView !== "results" || !state.resultBacktestId || !state.selectedBacktest) return null;
  return {
    documentId: `engine:visualization:${state.resultBacktestId}`,
    kind: "visualization-draft",
    label: `Visualization · ${state.selectedBacktest.name || state.resultBacktestId}`,
    getContent: () => `${$("visualizationSpec")?.value || "{}"}\n`,
    applyContent(content) {
      const spec = JSON.parse(content);
      if (!spec || typeof spec !== "object" || Array.isArray(spec)) throw uiDraftError("invalid_document", "Visualization spec must be a JSON object");
      state.selectedBacktest.visualization = structuredClone(spec);
      $("visualizationSpec").value = JSON.stringify(spec, null, 2);
      setVisualizationSpecError("");
      syncResultsActionState();
      drawVisualization(spec);
    },
  };
}

function currentUiDocumentDescriptor() {
  return activePipelineUiDocument()
    || activeEnvironmentUiDocument()
    || activeAnalysisUiDocument()
    || activeBacktestUiDocument()
    || activeVisualizationUiDocument();
}

function uiDocumentPublicState(record) {
  return {
    documentId: record.documentId,
    kind: record.kind,
    label: record.label,
    revision: record.revision,
    savedRevision: record.savedRevision,
    contentDigest: record.contentDigest,
    dirty: record.dirty,
    readOnly: false,
  };
}

async function refreshUiDocumentRecord(record, descriptor) {
  const content = descriptor.getContent();
  if (content === record.content) return record;
  const digest = await window.TradeUiSync.digestText(content);
  if (activeUiDocumentRecord !== record) throw uiDraftError("page_gone", "The selected Engine draft is no longer active");
  const baseRevision = record.revision;
  record.revision += 1;
  record.content = content;
  record.contentDigest = digest;
  record.dirty = true;
  record.label = descriptor.label;
  await window.TradeUiSync.updateDocument(uiDocumentPublicState(record), baseRevision);
  scheduleUiContextSync();
  return record;
}

function engineUiDocumentProvider(record) {
  return {
    async getSnapshot({ includeContent }) {
      const descriptor = currentUiDocumentDescriptor();
      if (!descriptor || descriptor.documentId !== record.documentId) throw uiDraftError("page_gone", "The Engine draft is no longer active");
      await refreshUiDocumentRecord(record, descriptor);
      return {
        revision: record.revision,
        contentDigest: record.contentDigest,
        ...(includeContent ? { content: record.content } : {}),
      };
    },
    async applyPatch(request) {
      const descriptor = currentUiDocumentDescriptor();
      if (!descriptor || descriptor.documentId !== record.documentId) throw uiDraftError("page_gone", "The Engine draft is no longer active");
      await refreshUiDocumentRecord(record, descriptor);
      if (record.revision !== request.baseRevision || record.contentDigest !== request.baseDigest) {
        throw uiDraftError("revision_conflict", "The Engine draft changed; read it again before applying a patch");
      }
      const { start, end, text } = request.patch || {};
      if (!Number.isSafeInteger(start) || !Number.isSafeInteger(end) || start < 0 || end < start || end > record.content.length || typeof text !== "string") {
        throw uiDraftError("invalid_patch", "The text replacement range is invalid");
      }
      const patched = `${record.content.slice(0, start)}${text}${record.content.slice(end)}`;
      let content;
      let digest;
      try {
        content = uiDraftJson(JSON.parse(patched));
        digest = await window.TradeUiSync.digestText(content);
        if (activeUiDocumentRecord !== record || descriptor.getContent() !== record.content) {
          throw uiDraftError("revision_conflict", "The Engine draft changed while applying the patch");
        }
        descriptor.applyContent(content);
      } catch (error) {
        if (!error?.code) error.code = "invalid_document";
        throw error;
      }
      record.revision += 1;
      record.content = content;
      record.contentDigest = digest;
      record.dirty = true;
      scheduleUiContextSync();
      return {
        revision: record.revision,
        savedRevision: record.savedRevision,
        contentDigest: record.contentDigest,
        dirty: true,
      };
    },
  };
}

async function syncActiveUiDocument() {
  const sequence = ++uiDocumentSyncSequence;
  const descriptor = currentUiDocumentDescriptor();
  if (!descriptor) {
    const previous = activeUiDocumentRecord;
    activeUiDocumentRecord = null;
    if (previous) await window.TradeUiSync?.closeDocument(previous.documentId).catch(() => undefined);
    scheduleUiContextSync();
    return;
  }
  const content = descriptor.getContent();
  const digest = await window.TradeUiSync.digestText(content);
  if (sequence !== uiDocumentSyncSequence) return;
  if (!activeUiDocumentRecord || activeUiDocumentRecord.documentId !== descriptor.documentId) {
    const previous = activeUiDocumentRecord;
    const record = {
      documentId: descriptor.documentId,
      kind: descriptor.kind,
      label: descriptor.label,
      revision: 0,
      savedRevision: 0,
      contentDigest: digest,
      content,
      dirty: false,
    };
    activeUiDocumentRecord = record;
    if (previous) await window.TradeUiSync.closeDocument(previous.documentId).catch(() => undefined);
    await window.TradeUiSync.openDocument(uiDocumentPublicState(record), engineUiDocumentProvider(record)).catch(() => undefined);
    scheduleUiContextSync();
    return;
  }
  activeUiDocumentRecord.kind = descriptor.kind;
  activeUiDocumentRecord.label = descriptor.label;
  await refreshUiDocumentRecord(activeUiDocumentRecord, descriptor).catch(() => undefined);
}

function scheduleUiDocumentSync() {
  clearTimeout(uiDocumentSyncTimer);
  uiDocumentSyncTimer = setTimeout(() => {
    void syncActiveUiDocument().catch(() => undefined);
  }, 180);
}

const PIPELINE_STAGES = [
  { stage: "universe", kind: "Universe", title: "Universe" },
  { stage: "signal", kind: "Signal", title: "Signals" },
  { stage: "target", kind: "Target", title: "Targets" },
  { stage: "constraint", kind: "Constraint", title: "Constraints" },
];
const PIPELINE_MODULE_STAGES = PIPELINE_STAGES.filter(({ stage }) => stage !== "signal");


const MULTI_STAGE = new Set(["constraint"]);
const GRAPH_NODE_SIZE = { width: 210, height: 188 };
const GRAPH_POSITIONS_KEY = "trade.pipeline.graph.positions.v1";
const GRAPH_SPACE_VERSION_KEY = "trade.pipeline.graph.space.version";
const GRAPH_SPACE_VERSION = "v2-large-canvas";
const PIPELINE_CANVAS_SIZE = { width: 6000, height: 3600 };
const PIPELINE_CANVAS_ORIGIN = { x: 1800, y: 1100 };
const PIPELINE_VIEWPORT_MIN_SCALE = 0.35;
const PIPELINE_VIEWPORT_MAX_SCALE = 1.8;
const BACKTEST_GRAPH_POSITIONS_KEY = "trade.backtest.graph.positions.v2";
const BACKTEST_CANVAS_SIZE = { width: 3000, height: 1800 };
const BACKTEST_VIEWPORT_MIN_SCALE = 0.35;
const BACKTEST_VIEWPORT_MAX_SCALE = 1.8;
const BACKTEST_GRAPH_DEFAULT_POSITIONS = {
  dataset: { left: 520, top: 760 },
  sampler: { left: 820, top: 750 },
  environment: { left: 1120, top: 760 },
  pipeline: { left: 1430, top: 565 },
  analyzer: { left: 1430, top: 955 },
};

const MODULE_KINDS = [
  "Universe",
  "Signal",
  "Target",
  "Constraint",
];

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
  const response = await fetch("/auth/session", {
    headers: { Accept: "application/json" },
    credentials: "same-origin",
    cache: "no-store",
  });
  if (!response.ok) {
    redirectToLogin();
    throw new Error("Authentication required.");
  }
  const session = await response.json();
  authState = {
    user: session.user || null,
    csrfToken: session.csrfToken || "",
    expiresAt: session.expiresAt || 0,
  };
  window.__tradeAuth = authState;
  $("currentUser").textContent = authState.user?.email || "";
  $("accountMenuBtn").title = authState.user?.email || "Account menu";
  $("accountIdentity").textContent = `${authState.user?.email || ""} · ${authState.user?.role || "user"}`;
}

async function getJson(path) {
  const response = await authenticatedFetch(path, { headers: { Accept: "application/json" } });
  if (!response.ok) {
    throw new Error(`${path} returned ${response.status}`);
  }
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
  if (!response.ok || data.accepted === false) {
    throw new Error(data.error || `${path} returned ${response.status}`);
  }
  publishUiResourceMutation(path, data);
  return data;
}

const uiResourceMutationFingerprints = new Set();
function publishUiResourceMutation(path, data) {
  let kind = "";
  let id = "";
  let change = "published";
  if (/^\/api\/pipelines(?:\/|$)/.test(path)) {
    kind = "pipeline";
    id = data.pipelineId || data.definition?.pipelineId || path.split("/")[3] || "";
    if (path.endsWith("/disable")) change = "archived";
  } else if (path === "/api/environments") {
    kind = "environment";
    id = data.definition?.environmentId || data.environmentId || "";
  } else if (path === "/api/analyses") {
    kind = "analysis";
    id = data.definition?.analysisId || data.analysisId || "";
  } else if (path === "/api/visualizations") {
    kind = "visualization";
    id = data.visualization?.visualizationId || data.visualizationId || "";
  } else if (path === "/api/backtests") {
    kind = "backtest";
    id = data.job?.backtestId || data.job?.jobId || "";
    change = "changed";
  } else if (path === "/api/data/upload" || path === "/api/data/process") {
    kind = "dataset";
    id = data.dataset?.datasetId || data.datasetId || data.job?.datasetId || "";
  }
  if (!kind || !id) return;
  const fingerprint = `${path}:${kind}:${id}:${data.version || data.definition?.version || data.visualization?.contentDigest || ""}`;
  if (uiResourceMutationFingerprints.has(fingerprint)) return;
  uiResourceMutationFingerprints.add(fingerprint);
  if (uiResourceMutationFingerprints.size > 256) uiResourceMutationFingerprints.delete(uiResourceMutationFingerprints.values().next().value);
  window.TradeUiSync?.publishResourceChange({
    eventId: `resource-${Date.now()}-${Math.random().toString(16).slice(2)}`,
    kind,
    id: String(id),
    change,
    ...(data.version || data.definition?.version ? { version: String(data.version || data.definition.version) } : {}),
    ...(data.contentDigest || data.definition?.contentDigest || data.visualization?.contentDigest
      ? { digest: String(data.contentDigest || data.definition?.contentDigest || data.visualization.contentDigest) }
      : {}),
    occurredAt: new Date().toISOString(),
  }).catch(() => undefined);
}

const uiOperationFingerprints = new Map();
function publishUiOperation(operation) {
  const fingerprint = JSON.stringify(operation);
  if (uiOperationFingerprints.get(operation.operationId) === fingerprint) return;
  uiOperationFingerprints.set(operation.operationId, fingerprint);
  window.TradeUiSync?.publishOperation(operation).catch(() => undefined);
}

function publishBacktestOperations(jobs = []) {
  jobs.forEach((job) => {
    const total = Number(job.totalCycles || 0);
    const completed = Number(job.completedCycles || 0);
    const status = job.status === "completed" ? "completed"
      : job.status === "failed" ? "failed"
        : job.phase === "interrupted" ? "interrupted"
          : job.status === "running" ? "progress" : "waiting";
    const updatedAt = [job.updatedAt, job.completedAt, job.startedAt, job.submittedAt]
      .find((value) => value && Number.isFinite(Date.parse(value))) || new Date().toISOString();
    publishUiOperation({
      operationId: `backtest:${job.jobId}`,
      kind: "backtest",
      resourceId: String(job.backtestId || job.jobId),
      status,
      ...(total > 0 ? { progress: Math.max(0, Math.min(1, completed / total)) } : {}),
      message: String(job.phase || job.status || "Backtest").slice(0, 512),
      ...(status === "failed" ? { errorCode: "backtest_failed" } : {}),
      updatedAt: new Date(updatedAt).toISOString(),
    });
  });
}

const miningKLine = window.TradeMiningKLine?.create({
  $,
  escapeHtml,
  getJson,
  postJson,
  publishOperation: publishUiOperation,
  isActive: () => currentView === "mining-kline",
  onError: (error) => console.error(error?.message || String(error)),
});

function setAccountError(message = "") {
  const node = $("accountError");
  node.textContent = message;
  node.hidden = !message;
}

function clearPasswordFields() {
  $("currentPassword").value = "";
  $("newPassword").value = "";
  $("confirmNewPassword").value = "";
}

function setAccountMenuOpen(open) {
  const trigger = $("accountMenuBtn");
  const panel = $("accountMenuPanel");
  if (!trigger || !panel) return;
  const nextOpen = Boolean(open);
  trigger.setAttribute("aria-expanded", nextOpen ? "true" : "false");
  panel.hidden = !nextOpen;
}

function formatTime(value) {
  if (!value) return "-";
  const date = typeof value === "number" ? new Date(value * 1000) : new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return date.toLocaleString();
}

function setHealth(ok, text) {
  const node = $("health");
  healthState = {
    ok: !!ok,
    text: String(text || ""),
  };
  if (node) {
    node.textContent = text;
    node.classList.toggle("ok", ok);
  } else if (!ok && text && text !== "Loading") {
    console.error(text);
  }
}

function beginViewLoading(viewId) {
  const view = $(viewId);
  if (!view) return 0;
  const token = ++viewLoadingSequence;
  viewLoadingTokens.set(viewId, token);
  let indicator = view.querySelector(":scope > .view-loading");
  if (!indicator) {
    indicator = document.createElement("div");
    indicator.className = "view-loading";
    indicator.setAttribute("role", "status");
    indicator.setAttribute("aria-live", "polite");
    const spinner = document.createElement("span");
    spinner.className = "loading-spinner";
    spinner.setAttribute("aria-hidden", "true");
    const label = document.createElement("span");
    label.dataset.loadingLabel = "";
    indicator.append(spinner, label);
    view.prepend(indicator);
  }
  const names = {
    overview: "Overview",
    pipeline: "Pipeline",
    modules: "Modules",
    data: "Dataset",
    environment: "Environment",
    analysis: "Analysis",
    visualizers: "Visualizer",
    backtests: "Backtest",
    results: "Result",
    "mining-kline": "K Line Mining",
    agent: "Agent",
  };
  indicator.querySelector("[data-loading-label]").textContent = `Loading ${names[viewId] || "view"}…`;
  indicator.hidden = false;
  view.setAttribute("aria-busy", "true");
  return token;
}

function endViewLoading(viewId, token) {
  if (viewLoadingTokens.get(viewId) !== token) return;
  viewLoadingTokens.delete(viewId);
  const view = $(viewId);
  if (!view) return;
  const indicator = view.querySelector(":scope > .view-loading");
  if (indicator) indicator.hidden = true;
  view.removeAttribute("aria-busy");
}

async function runUiAction(label, action) {
  const busyMessage = pipelineBlueprintBusyMessage();
  if (busyMessage && label !== "Saving Pipeline Version") {
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
  if (node) {
    node.textContent = message;
    node.hidden = true;
  }
  if (message) {
    document.querySelector("#alphaGraphBuilder")?.__setBlueprintStatus?.(message, true);
  }
}

function setPipelineSaveError(message = "") {
  const node = $("pipelineSaveError");
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

function setCreatePipelineError(message = "") {
  const node = $("createPipelineError");
  if (!node) return;
  node.textContent = message;
  node.hidden = !message;
}

function setClonePipelineError(message = "") {
  const node = $("clonePipelineError");
  if (!node) return;
  node.textContent = message;
  node.hidden = !message;
}

function setArchivePipelineError(message = "") {
  const node = $("disablePipelineError");
  if (!node) return;
  node.textContent = message;
  node.hidden = !message;
}

let moduleUploadManifest = null;
const MODULE_REPOSITORY_IDS = new Set(["modules", "analysis-modules", "environment-modules"]);

function moduleRepositoryKinds(repository = state.selectedModuleRepository) {
  if (repository === "analysis-modules") return ["Analyzer"];
  if (repository === "environment-modules") return ["Environment"];
  return ["Universe", "Signal", "Target", "Constraint"];
}

function selectModuleRepository(repository) {
  state.selectedModuleRepository = MODULE_REPOSITORY_IDS.has(repository) ? repository : "modules";
}

function populateModuleKindSelect(id, selected = "") {
  const select = $(id);
  const kinds = moduleRepositoryKinds();
  select.innerHTML = kinds.map((kind) => `<option>${escapeHtml(kind)}</option>`).join("");
  select.value = kinds.includes(selected) ? selected : kinds[0];
}

function setModuleLifecycleError(id, message = "") {
  const node = $(id);
  if (!node) return;
  node.textContent = message;
  node.hidden = !message;
}

function selectedModuleRepositoryItem() {
  return state.selectedModuleRepositoryItem?.moduleId ? state.selectedModuleRepositoryItem : null;
}

function parseModuleJsonField(id, label) {
  let value;
  try {
    value = JSON.parse($(id).value || "{}");
  } catch (error) {
    throw localUiError(`${label} is invalid JSON: ${error.message}`, "MODULE_DEFINITION_JSON");
  }
  if (!value || Array.isArray(value) || typeof value !== "object") {
    throw localUiError(`${label} must be a JSON object`, "MODULE_DEFINITION_JSON");
  }
  return value;
}

function setModuleUploadDefinition(definition = {}) {
  $("moduleUploadKind").value = definition.kind || "Signal";
  $("moduleUploadName").value = definition.name || definition.moduleId || "";
  $("moduleUploadId").value = definition.moduleId || "";
  const argumentsValue = definition.parameters?.arguments || "";
  const entryMatch = String(argumentsValue).match(/\{\{moduleRoot\}\}\/([^\s]+)/);
  $("moduleUploadEntry").value = definition.activationMode === "ProcessRunner"
    ? (entryMatch?.[1] || definition.entryFile || "runner.py")
    : "module.py";
  $("moduleUploadInputs").value = JSON.stringify(definition.ports?.inputs || {}, null, 2);
  $("moduleUploadOutputs").value = JSON.stringify(definition.ports?.outputs || {}, null, 2);
  $("moduleUploadConfigSchema").value = JSON.stringify(
    definition.configSchema || { type: "object", properties: {}, additionalProperties: false }, null, 2
  );
  $("moduleUploadDescription").value = definition.description || "";
}

function openModuleUploadDialog() {
  moduleUploadManifest = null;
  $("moduleUploadForm").reset();
  $("moduleUploadFiles").value = "";
  setModuleLifecycleError("moduleUploadError", "");
  $("moduleUploadImpact").hidden = true;
  $("moduleUploadDialogTitle").textContent = "Add Module";
  $("moduleUploadSource").textContent = "Select one complete local Module directory.";
  setModuleUploadDefinition({ kind: moduleRepositoryKinds()[0] });
  $("moduleUploadKind").disabled = false;
  $("moduleUploadName").disabled = false;
  $("moduleUploadId").disabled = false;
  $("confirmModuleUploadBtn").textContent = "Add Module";
  populateModuleKindSelect("moduleUploadKind", moduleRepositoryKinds()[0]);
  $("moduleUploadDialog").showModal();
}

async function fileContentBase64(file) {
  if (file.size > 25 * 1024 * 1024) {
    throw localUiError(`File '${file.name}' exceeds the 25 MB Module file limit`, "MODULE_FILE_TOO_LARGE");
  }
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result).split(",", 2)[1] || "");
    reader.onerror = () => reject(reader.error || new Error(`Unable to read ${file.name}`));
    reader.readAsDataURL(file);
  });
}

async function moduleFilesPayload(fileList) {
  const files = [...fileList];
  if (!files.length) throw localUiError("Select one local Module directory", "MODULE_FILES_REQUIRED");
  if (files.some((file) => !file.webkitRelativePath)) {
    throw localUiError("Module files must be selected through the directory picker", "MODULE_DIRECTORY_REQUIRED");
  }
  const roots = new Set(files.map((file) => file.webkitRelativePath.split("/", 1)[0]));
  if (roots.size !== 1) {
    throw localUiError("Select exactly one Module directory", "MODULE_DIRECTORY_REQUIRED");
  }
  const total = files.reduce((sum, file) => sum + file.size, 0);
  if (total > 64 * 1024 * 1024) {
    throw localUiError("Module bundle exceeds the 64 MB total limit", "MODULE_BUNDLE_TOO_LARGE");
  }
  const payload = await Promise.all(files.map(async (file) => ({
    path: file.webkitRelativePath.split("/").slice(1).join("/"),
    contentBase64: await fileContentBase64(file),
    executable: /(^|\/)(runner|main|serve)[^/]*\.py$/i.test(file.webkitRelativePath),
  })));
  if (payload.some((file) => !file.path) || new Set(payload.map((file) => file.path)).size !== payload.length) {
    throw localUiError("Module directory contains invalid or duplicate relative paths", "MODULE_DIRECTORY_INVALID");
  }
  return payload;
}

async function refreshModulesAfterLifecycle() {
  state.selectedModuleRepositoryItem = null;
  loadedViews.delete("modules");
  loadedViews.delete("pipeline");
  state.pipelineModules = {};
  state.resultModules = {};
  await loadModules(true);
}

async function openModuleJupyter(item, repository = "modules") {
  const jupyterWindow = window.open("about:blank", "_blank");
  if (!jupyterWindow) throw localUiError("Allow pop-ups to open the Module Jupyter Workspace.", "JUPYTER_POPUP_BLOCKED");
  jupyterWindow.document.title = "Starting Module JupyterLab";
  jupyterWindow.document.body.textContent = "Creating an isolated Module edit Workspace...";
  try {
    const url = `/api/${encodeURIComponent(repository)}/${encodeURIComponent(item.kind)}/${encodeURIComponent(item.moduleId)}/versions/${encodeURIComponent(item.version)}/jupyter`;
    const result = await postJson(url, {});
    jupyterWindow.opener = null;
    jupyterWindow.location.replace(result.url);
  } catch (error) {
    jupyterWindow.close();
    throw error;
  }
}

async function openSamplerJupyter(item) {
  if (item?.builtin) {
    throw localUiError("Built-in Samplers are read-only.", "BUILTIN_SAMPLER_READ_ONLY");
  }
  if (!["row-map", "python-script"].includes(item?.type)) {
    throw localUiError("This Sampler type cannot be opened in the editor.", "SAMPLER_EDITOR_UNSUPPORTED");
  }
  const jupyterWindow = window.open("about:blank", "_blank");
  if (!jupyterWindow) throw localUiError("Allow pop-ups to open the Sampler Jupyter Workspace.", "JUPYTER_POPUP_BLOCKED");
  jupyterWindow.document.title = "Starting Sampler JupyterLab";
  jupyterWindow.document.body.textContent = "Creating an isolated Sampler edit Workspace...";
  try {
    const url = `/api/data/samplers/${encodeURIComponent(item.samplerId)}/versions/${encodeURIComponent(item.version)}/jupyter`;
    const result = await postJson(url, {});
    jupyterWindow.opener = null;
    jupyterWindow.location.replace(result.url);
  } catch (error) {
    jupyterWindow.close();
    throw error;
  }
}

async function publishModuleWorkspace(item, repository) {
  const url = `/api/${encodeURIComponent(repository)}/${encodeURIComponent(item.kind)}/${encodeURIComponent(item.moduleId)}/versions/${encodeURIComponent(item.version)}/publish`;
  const result = await postJson(url, {});
  await refreshModulesAfterLifecycle();
  setHealth(true, result.unchanged
    ? `Module Workspace has no effective changes; Version ${result.definition.version} remains current.`
    : `Published Module Version ${result.definition.version} from its Workspace.`);
}

async function publishSamplerWorkspace(item) {
  const url = `/api/data/samplers/${encodeURIComponent(item.samplerId)}/versions/${encodeURIComponent(item.version)}/publish`;
  const result = await postJson(url, {});
  await refreshDataFilesystemAfterMutation();
  setHealth(true, result.unchanged
    ? `Sampler Workspace has no effective changes; Version ${result.sampler.version} remains current.`
    : `Published Sampler Version ${result.sampler.version} from its Workspace.`);
}

async function startModuleLifecycleAction(action, item = null, repository = "modules") {
  selectModuleRepository(repository);
  if (item?.moduleId) state.selectedModuleRepositoryItem = item;
  const selected = selectedModuleRepositoryItem();
  if (action === "add") {
    openModuleUploadDialog();
    return;
  }
  if (!selected) throw new Error("Select a Module Version first.");
  if (action === "publish") return publishModuleWorkspace(selected, state.selectedModuleRepository);
  if (action === "edit") await openModuleJupyter(selected, state.selectedModuleRepository);
}

function pipelineModuleLifecycleReport() {
  const errors = [];
  const warnings = [];
  Object.values(state.pipelineDraft?.instances || {}).forEach((instance) => {
    const definition = Object.values(state.pipelineModules || {}).find((candidate) => (
      candidate.kind === instance.kind
      && candidate.moduleId === instance.moduleId
      && String(candidate.version) === String(instance.version)
    ));
    const key = `${instance.kind}/${instance.moduleId}/${instance.version}`;
    if (!definition) errors.push(`${instance.instanceId || key} references missing Module ${key}`);
    else if (definition.status !== "archived") {
      errors.push(`${instance.instanceId || key} references an unavailable Module archive ${key}`);
    }
  });
  return { errors, warnings };
}

function setPipelineBlueprintError(message = "") {
  const node = $("pipelineBlueprintError");
  if (!node) return;
  node.textContent = message;
  // Save/load state belongs on the disabled control and its tooltip.
  // This diagnostic node stays hidden so it cannot shift the graph.
  node.hidden = true;
}

function syncPipelineBlueprintErrorState() {
  const hasDefinition = !!pipelineEditorState.definition;
  const pipelineId = pipelineField("Id")?.value?.trim() || "";
  const busyMessage = pipelineBlueprintBusyMessage();
  const messages = [];
  if (busyMessage) {
    messages.push(`Version load unavailable: ${busyMessage}`);
    messages.push(`Save unavailable: ${busyMessage}`);
  } else if (!hasDefinition) {
    messages.push("Version load unavailable: No saved Pipeline version available");
  }
  if (!busyMessage && !pipelineId) {
    messages.push("Save unavailable: Pipeline metadata is incomplete");
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
    parsePipelineAlphaGraphValue({ reportError: true });
  } catch {}
  invalidateBacktestBuild("Pipeline modules changed · Build again before running");
  syncPipelineSaveActionState();
}

function pipelineBlueprintBusyState() {
  if (!window.__tradePipelineBlueprintBusyState) {
    window.__tradePipelineBlueprintBusyState = {
      saveInFlight: false,
      reloadInFlight: false,
    };
  }
  return window.__tradePipelineBlueprintBusyState;
}

function syncPipelineBusyUiState() {
  window.__syncPipelineComposerActionState?.();
  document.querySelector("#alphaGraphBuilder")?.__syncSaveState?.();
}

function setPipelineBlueprintBusyState(next = {}) {
  const busyState = pipelineBlueprintBusyState();
  if (Object.prototype.hasOwnProperty.call(next, "saveInFlight")) {
    busyState.saveInFlight = Boolean(next.saveInFlight);
  }
  if (Object.prototype.hasOwnProperty.call(next, "reloadInFlight")) {
    busyState.reloadInFlight = Boolean(next.reloadInFlight);
  }
  syncPipelineBusyUiState();
}

function pipelineBlueprintBusyMessage() {
  const busyState = pipelineBlueprintBusyState();
  if (busyState.reloadInFlight) return "Version load in progress";
  if (busyState.saveInFlight) return "Save in progress";
  return "";
}

function syncPipelineComposerEditorState() {
  const grid = $("pipelineStageGrid");
  if (!grid) return;
  const busyMessage = pipelineBlueprintBusyMessage();
  grid.querySelectorAll("select[data-load-stage]").forEach((select) => {
    select.disabled = Boolean(busyMessage);
    select.title = busyMessage || "";
  });
  grid.querySelectorAll(
    "[data-load-stage-button], [data-unload-stage]",
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
  ["Id", "Name", "AlphaGraph"].forEach((fieldId) => {
    const field = pipelineField(fieldId);
    if (!field) return;
    field.disabled = Boolean(busyMessage);
    field.title = busyMessage || "";
  });
  ["Whitelist", "Blacklist"].forEach((fieldId) => {
    const editor = $(`pipelineObservation${fieldId}Editor`);
    if (!editor) return;
    editor.querySelectorAll("button, input").forEach((control) => {
      control.disabled = Boolean(busyMessage);
      control.title = busyMessage || control.dataset.defaultTitle || "";
    });
  });
  const batchDialog = $("pipelineObservationBatchDialog");
  batchDialog?.querySelectorAll("button, textarea").forEach((control) => {
    control.disabled = Boolean(busyMessage);
    control.title = busyMessage || "";
  });
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

function syncPipelineEditorSelectorState() {
  const select = $("pipelineSelect");
  if (!select) return;
  const busyMessage = pipelineBlueprintBusyMessage();
  const hasPipelines = sortedPipelines().length > 0;
  select.disabled = Boolean(busyMessage) || !hasPipelines;
  select.title = busyMessage || (hasPipelines ? "" : "No Pipeline available");
  const addButton = $("addPipelineBtn");
  if (addButton) {
    addButton.disabled = Boolean(busyMessage);
    addButton.title = busyMessage || "";
  }
  syncPipelineLifecycleActionState();
}

function syncPipelineLifecycleActionState() {
  const busyMessage = pipelineBlueprintBusyMessage();
  const pipeline = selectedPipelineRecord();
  const cloneButton = $("clonePipelineBtn");
  if (cloneButton) {
    cloneButton.disabled = Boolean(busyMessage) || !pipeline;
    cloneButton.title = busyMessage || (pipeline ? "" : "Select a Pipeline");
  }
  const disableButton = $("disablePipelineBtn");
  if (disableButton) {
    const inactive = pipeline?.status === "inactive";
    disableButton.disabled = Boolean(busyMessage) || !pipeline || inactive;
    disableButton.title = busyMessage || (!pipeline ? "Select a Pipeline" : (inactive ? "Pipeline is already inactive" : ""));
  }
}

function syncPipelineLoadActionState() {
  const button = $("loadPipelineBtn");
  if (!button) return;
  const busyMessage = pipelineBlueprintBusyMessage();
  const hasVersion = Boolean($("pipelineVersionSelect")?.value);
  const disabled = Boolean(busyMessage) || !hasVersion;
  const title = busyMessage || (hasVersion ? "" : "No saved Pipeline version available");
  button.disabled = disabled;
  button.title = title;
  const versionSelect = $("pipelineVersionSelect");
  if (versionSelect) {
    versionSelect.disabled = Boolean(busyMessage) || !(pipelineEditorState.versions || []).length;
    versionSelect.title = busyMessage || "";
  }
}

function syncPipelineSaveActionState() {
  const button = $("savePipelineVersionBtn");
  if (!button) return;
  const pipelineId = pipelineField("Id")?.value?.trim() || "";
  let disabled = false;
  let title = "";
  const busyMessage = pipelineBlueprintBusyMessage();
  const lifecycle = pipelineModuleLifecycleReport();
  if (busyMessage) {
    disabled = true;
    title = busyMessage;
  } else if (selectedPipelineRecord()?.status === "inactive") {
    disabled = true;
    title = "Inactive Pipelines are read-only; clone it to continue editing";
  } else if (lifecycle.errors.length) {
    disabled = true;
    title = lifecycle.errors.join(" | ");
  } else if (!pipelineId) {
    disabled = true;
    title = "Pipeline metadata is incomplete";
  } else {
    try {
      parsePipelineAlphaGraphValue({ reportError: false });
    } catch {
      disabled = true;
      title = "Fix Alpha Graph JSON first";
    }
  }
  button.disabled = disabled;
  button.title = title || lifecycle.warnings.join(" | ");
  syncPipelineBlueprintErrorState();
  document.querySelector("#alphaGraphBuilder")?.__syncSaveState?.();
}

window.__syncPipelineComposerActionState = function syncPipelineComposerActionState() {
  syncActiveViewBusyState();
  syncGlobalNavActionState();
  syncPipelineSubnavActionState();
  syncPipelineEditorSelectorState();
  syncPipelineDialogActionState();
  syncPipelineDraftFieldState();
  syncPipelineLoadActionState();
  syncPipelineSaveActionState();
  syncPipelineComposerEditorState();
};

function setDataUploadError(message = "") {
  const node = $("dataUploadError");
  if (!node) return;
  node.textContent = message;
  node.hidden = !message;
}

function setDatasetManagementError(id, message = "") {
  const node = $(id);
  if (!node) return;
  node.textContent = message;
  node.hidden = !message;
}

function resetUploadZipValidationState() {
  uploadZipValidationSeq += 1;
  uploadZipValidationState = { pending: false, error: "" };
}

function normalizeDatasetId(value) {
  return Array.from(String(value || "").trim())
    .map((char) => (/[a-z0-9]/i.test(char) ? char.toLowerCase() : "-"))
    .join("")
    .split("-")
    .filter(Boolean)
    .join("-");
}

function isZipSignature(bytes) {
  if (bytes.length < 4 || bytes[0] !== 0x50 || bytes[1] !== 0x4b) return false;
  return (bytes[2] === 0x03 && bytes[3] === 0x04)
    || (bytes[2] === 0x05 && bytes[3] === 0x06)
    || (bytes[2] === 0x07 && bytes[3] === 0x08);
}

async function validateSelectedUploadZipFile() {
  const file = $("uploadZip")?.files?.[0] || null;
  if (!file) {
    resetUploadZipValidationState();
    setDataUploadError("");
    syncDataUploadActionState();
    return;
  }
  const currentSeq = ++uploadZipValidationSeq;
  uploadZipValidationState = { pending: true, error: "" };
  setDataUploadError("");
  syncDataUploadActionState();
  try {
    const signature = new Uint8Array(await file.slice(0, 4).arrayBuffer());
    if (currentSeq !== uploadZipValidationSeq) return;
    const error = isZipSignature(signature) ? "" : "Upload accepts valid ZIP archives only.";
    uploadZipValidationState = { pending: false, error };
    setDataUploadError(error);
  } catch (error) {
    if (currentSeq !== uploadZipValidationSeq) return;
    const message = error?.message || "Unable to read ZIP file";
    uploadZipValidationState = { pending: false, error: message };
    setDataUploadError(message);
  }
  syncDataUploadActionState();
}

function dataUploadActionState() {
  const name = $("uploadDatasetName")?.value?.trim() || "";
  const file = $("uploadZip")?.files?.[0] || null;
  if (!name) return { disabled: true, title: "Dataset name is required" };
  if (!file) return { disabled: true, title: "ZIP file required" };
  if (uploadZipValidationState.pending) return { disabled: true, title: "Validating ZIP file" };
  if (uploadZipValidationState.error) return { disabled: true, title: uploadZipValidationState.error };
  return { disabled: false, title: "" };
}

function downloadDatasetArchive(datasetIds) {
  const ids = [...new Set(datasetIds.filter(Boolean))];
  if (!ids.length) return;
  const query = new URLSearchParams();
  ids.forEach((datasetId) => query.append("datasetId", datasetId));
  const anchor = document.createElement("a");
  anchor.href = `/api/data/datasets/download?${query.toString()}`;
  anchor.download = ids.length === 1 ? `${ids[0]}.zip` : "trade-datasets.zip";
  anchor.hidden = true;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
}

function syncDataUploadActionState() {
  const button = $("uploadDataBtn");
  if (!button) return;
  const { disabled, title } = dataUploadActionState();
  button.disabled = disabled;
  button.title = title;
}

function setBacktestEntryError(message = "") {
  const node = $("backtestEntryError");
  if (!node) return;
  node.textContent = message;
  node.hidden = !message;
}

function syncRouteChrome() {
  const routePath = String(location.pathname || "").toLowerCase();
  const isBlueprintRoute = ["/signal-blueprint", "/environment-blueprint", "/analysis-blueprint"].includes(routePath);
  const pipelineBuilderOpen = currentView === "pipeline" && currentPipelinePage === "builder";
  document.body.classList.toggle("route-blueprint", isBlueprintRoute);
  document.body.classList.toggle("route-pipeline-builder", pipelineBuilderOpen);
  document.body.classList.toggle("route-environment-blueprint", routePath === "/environment-blueprint");
  document.body.classList.toggle("route-analysis-blueprint", routePath === "/analysis-blueprint");
  document.body.classList.toggle("route-result", routePath === "/result");
  const environmentBlueprint = currentView === "environment" && currentEnvironmentSection === "blueprint";
  if ($("environmentRepositorySection")) $("environmentRepositorySection").hidden = environmentBlueprint;
  if ($("environmentBlueprintSection")) $("environmentBlueprintSection").hidden = !environmentBlueprint;
  const analysisBlueprint = currentView === "analysis" && currentAnalysisSection === "blueprint";
  if ($("analysisRepositorySection")) $("analysisRepositorySection").hidden = analysisBlueprint;
  if ($("analysisBlueprintSection")) $("analysisBlueprintSection").hidden = !analysisBlueprint;
  if ($("pipelineRepositoryPage")) $("pipelineRepositoryPage").hidden = pipelineBuilderOpen;
  if ($("pipelineBuilderPage")) $("pipelineBuilderPage").hidden = !pipelineBuilderOpen;
  if ($("pipelineBuilderRouteLabel")) {
    const pipeline = selectedPipelineRecord();
    $("pipelineBuilderRouteLabel").textContent = pipelineBuilderOpen
      ? `${pipeline?.name || pipelineEditorState.pipelineId || "Pipeline"} · Pipeline Builder`
      : "Pipeline Builder";
  }
  const routeBar = $("blueprintRouteBar");
  if (routeBar) routeBar.hidden = !isBlueprintRoute;
  syncActiveViewBusyState();
  syncGlobalNavActionState();
  syncPipelineSubnavActionState();
  syncPipelineEditorSelectorState();
  syncPipelineDialogActionState();
  syncPipelineDraftFieldState();
  syncPipelineLoadActionState();
  syncPipelineSaveActionState();
  syncPipelineComposerEditorState();
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
  root.__moduleGraphCleanup?.({ flushPending });
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

function openPipelineBuilder(pipelineId, { replace = false } = {}) {
  const normalizedPipelineId = String(pipelineId || "").trim();
  if (!normalizedPipelineId) {
    throw localUiError("Select a Pipeline to open the Builder.", "PIPELINE_SELECTION_REQUIRED");
  }
  unmountAlphaGraphBuilder({ flushPending: true });
  currentPipelinePage = "builder";
  currentPipelineSection = "composer";
  pipelineEditorState.pipelineId = normalizedPipelineId;
  pipelineEditorState.definition = null;
  pipelineEditorState.manifest = null;
  pipelineEditorState.versions = [];
  pipelineEditorState.loadedVersion = "";
  pipelineEditorState.loadedDefinition = null;
  state.pipelineDraft = null;
  loadedViews.delete("pipeline");
  const target = pathForView("pipeline");
  history[replace ? "replaceState" : "pushState"](
    { viewId: "pipeline", pipelineId: normalizedPipelineId },
    "",
    target,
  );
  return switchView("pipeline", { push: false });
}

function closePipelineBuilder({ replace = false } = {}) {
  if (state.pipelineViewport.fullscreen) togglePipelineFullscreen(false);
  unmountAlphaGraphBuilder({ flushPending: true });
  currentPipelinePage = "browser";
  currentPipelineSection = "composer";
  history[replace ? "replaceState" : "pushState"](
    { viewId: "pipeline" },
    "",
    "/pipeline",
  );
  return switchView("pipeline", { push: false });
}

function switchView(viewId, { push = true } = {}) {
  if (currentView === "pipeline" && currentPipelineSection === "signal" && viewId !== "pipeline") {
    unmountAlphaGraphBuilder();
  }
  if (currentView === "backtests" && viewId !== "backtests" && state.backtestViewport.fullscreen) {
    toggleBacktestFullscreen(false);
  }
  if (currentView === "environment" && (viewId !== "environment" || currentEnvironmentSection !== "blueprint")) {
    const environmentRoot = $("environmentGraphBuilder");
    environmentRoot?.__flushPendingEmit?.();
    environmentRoot?.__moduleGraphCleanup?.();
    if (environmentRoot) environmentRoot.innerHTML = "";
  }
  if (currentView === "analysis" && (viewId !== "analysis" || currentAnalysisSection !== "blueprint")) {
    const analysisRoot = $("analysisGraphBuilder");
    analysisRoot?.__flushPendingEmit?.();
    analysisRoot?.__moduleGraphCleanup?.();
    if (analysisRoot) analysisRoot.innerHTML = "";
  }
  if (currentView === "results" && viewId !== "results") {
    clearResultCharts();
  }
  currentView = viewId;
  if (push) {
    const target = pathForView(viewId);
    if (`${location.pathname}${location.search}` !== target) history.pushState({ viewId }, "", target);
  }
  document.querySelectorAll(".nav-btn").forEach((button) => {
    button.classList.toggle("active", button.dataset.view === viewId);
  });
  document.querySelectorAll(".view").forEach((view) => {
    view.classList.toggle("active", view.id === viewId);
  });
  syncRouteChrome();
  if (viewId === "pipeline" && currentPipelinePage === "builder") {
    switchPipelineSection(currentPipelineSection);
  } else if (viewId === "pipeline") {
    unmountAlphaGraphBuilder({ flushPending: false });
  }
  if (viewId === "backtests") ensureBacktestViewportReady();
  if (viewId !== "backtests") {
    clearTimeout(backtestJobPollTimer);
    backtestJobPollTimer = null;
  }
  if (viewId !== "mining-kline") miningKLine?.deactivate();
  const loadingToken = beginViewLoading(viewId);
  const loading = ensureViewData(viewId);
  loading.then(
    () => endViewLoading(viewId, loadingToken),
    () => endViewLoading(viewId, loadingToken),
  );
  loading.catch((error) => {
    if (viewId === "pipeline") setPipelineLoadError(error.message);
    if (!["agent", "mining-kline"].includes(viewId)) {
      setHealth(false, error.message);
    }
  });
  scheduleUiContextSync();
  return loading;
}

function switchBacktestSection(sectionId, { push = true } = {}) {
  currentBacktestSection = sectionId === "environment" ? "environment" : "entry";
  return switchView(currentBacktestSection === "environment" ? "environment" : "backtests", { push });
}

function switchPipelineSection(sectionId) {
  const previousSection = currentPipelineSection;
  if (previousSection === "signal" && previousSection !== sectionId) {
    const previousRoot = $("alphaGraphBuilder");
    previousRoot?.__flushPendingEmit?.();
    previousRoot?.__moduleGraphCleanup?.();
    if (previousRoot) previousRoot.innerHTML = "";
  }
  currentPipelinePage = "builder";
  currentPipelineSection = ["signal", "manifest"].includes(sectionId) ? sectionId : "composer";
  if (currentView === "pipeline") {
    const target = pathForView("pipeline");
    if (`${location.pathname}${location.search}` !== target) {
      history.pushState({ viewId: "pipeline", pipelineId: pipelineEditorState.pipelineId }, "", target);
    }
  }
  syncRouteChrome();
  document.querySelectorAll(".pipeline-subnav-btn").forEach((button) => {
    button.classList.toggle("active", button.dataset.pipelineSection === currentPipelineSection);
  });
  const definitionPanel = $("pipelineDefinitionPanel");
  definitionPanel?.classList.toggle("pipeline-definition-hidden", currentPipelineSection === "signal");
  const definitionTitle = $("pipelineDefinitionTitle");
  if (definitionTitle) definitionTitle.textContent = currentPipelineSection === "manifest" ? "Pipeline Manifest" : "Pipeline Builder";
  if ($("pipelineStatus")) $("pipelineStatus").hidden = currentPipelineSection === "manifest";
  if ($("pipelineManifestMeta")) $("pipelineManifestMeta").hidden = currentPipelineSection !== "manifest";
  const sectionIds = {
    composer: "pipelineComposerSection",
    signal: "pipelineAlphaSection",
    manifest: "pipelineManifestSection",
  };
  document.querySelectorAll(".pipeline-section").forEach((section) => {
    section.classList.toggle("active", section.id === sectionIds[currentPipelineSection]);
  });
  if (currentPipelineSection === "signal") {
    requestAnimationFrame(() => {
      requestAnimationFrame(() => {
        ensureAlphaGraphBuilderMounted();
        document.querySelector(".alpha-blueprint-shell")?.__refreshLayout?.();
        window.__syncPipelineComposerActionState?.();
      });
    });
  } else {
    unmountAlphaGraphBuilder();
    if (currentPipelineSection === "manifest") renderManifest();
    window.__syncPipelineComposerActionState?.();
  }
}

function pipelineSignalDetailsSummary() {
  const graph = alphaGraphObject();
  const nodeCount = alphaGraphNodeIds(graph).length;
  const outputCount = Object.keys(graph?.outputs || {}).length;
  if (!nodeCount) return "No Signal Graph modules";
  const nodeLabel = nodeCount === 1 ? "module" : "modules";
  const outputLabel = outputCount === 1 ? "output" : "outputs";
  return `Signal Graph: ${nodeCount} ${nodeLabel}, ${outputCount} exposed ${outputLabel}`;
}

function renderSummary() {
  const summary = state.summary;
  const repositories = summary?.repositories || {};
  const statusIndicator = $("serviceStatusIndicator");
  const loading = !!serviceRuntimeState.loading;
  statusIndicator.classList.toggle("loading", loading);
  statusIndicator.classList.toggle("active", !loading && serviceRuntimeState.active);
  statusIndicator.classList.toggle("down", !loading && !serviceRuntimeState.active);
  $("serviceStatusValue").textContent = loading ? "Loading" : (serviceRuntimeState.active ? "Active" : "Down");
  $("serviceTimeValue").textContent = loading ? "-" : formatTime(serviceRuntimeState.serviceTime);

  if (!summary) return;

  const repoList = $("repoList");
  repoList.innerHTML = "";
  const moduleCounts = [
    repositories.pipelineModuleIdentityCount,
    repositories.analysisModuleIdentityCount,
    repositories.environmentModuleIdentityCount,
  ];
  const moduleCount = moduleCounts.every((count) => Number.isFinite(Number(count)))
    ? moduleCounts.reduce((total, count) => total + Number(count), 0)
    : null;
  const resources = [
    ["Pipelines", repositories.pipelineIdentityCount],
    ["Environments", repositories.environmentIdentityCount],
    ["Analyses", repositories.analysisIdentityCount],
    ["Modules", moduleCount],
    ["Datasets", repositories.datasets],
    ["Backtests", repositories.backtests],
  ];
  resources.forEach(([name, count]) => {
    const hasCount = count !== null && count !== undefined && Number.isFinite(Number(count));
    const normalizedCount = hasCount ? Number(count) : "—";
    const card = document.createElement("div");
    card.className = "overview-card";
    if (!hasCount) card.title = "This service process has not loaded the current Summary schema.";
    card.innerHTML = `<div class="label">${name}</div><div class="overview-value">${normalizedCount}</div>`;
    repoList.appendChild(card);
  });
}

const RESOURCE_SCOPE_BY_REPOSITORY = {
  datasets: "data",
  samplers: "data",
  scripts: "data",
  workspaces: "data",
  backtests: "backtest",
  results: "backtest",
};

function repositoryScope(repository) {
  return RESOURCE_SCOPE_BY_REPOSITORY[repository] || repository;
}

function repositoryCatalog(repository) {
  return state.repositoryCatalogs?.[repositoryScope(repository)] || null;
}

function repositoryPlacement(repository, itemId) {
  const sourceItemId = String(itemId);
  const item = repositoryCatalog(repository)?.items?.find((candidate) => (
    candidate.itemId === sourceItemId
    || (candidate.sourceRepository === repository && String(candidate.sourceItemId) === sourceItemId)
  ));
  return item ? { folderId: item.folderId || "", folderPath: item.folderPath || "/" } : { folderId: "", folderPath: "/" };
}

function appendRepositoryOptions(select, rows, value, label, repository) {
  if (!repository || !repositoryCatalog(repository)) {
    rows.forEach((row) => {
      const option = document.createElement("option");
      option.value = value(row);
      option.textContent = label(row);
      select.appendChild(option);
    });
    return;
  }
  const groups = new Map();
  rows.forEach((row) => {
    const itemId = String(value(row));
    const path = repositoryPlacement(repository, itemId).folderPath;
    if (!groups.has(path)) groups.set(path, []);
    groups.get(path).push(row);
  });
  [...groups.entries()]
    .sort(([left], [right]) => left.localeCompare(right))
    .forEach(([path, groupRows]) => {
      const group = document.createElement("optgroup");
      group.label = path;
      groupRows.forEach((row) => {
        const option = document.createElement("option");
        option.value = value(row);
        option.textContent = label(row);
        group.appendChild(option);
      });
      select.appendChild(group);
    });
}

function renderSelectOptions(selectId, rows, value, label, repository = "", placeholder = "") {
  const select = $(selectId);
  const previous = select.value;
  select.innerHTML = "";
  if (placeholder) {
    const option = document.createElement("option");
    option.value = "";
    option.textContent = placeholder;
    select.appendChild(option);
  }
  appendRepositoryOptions(select, rows, value, label, repository);
  select.value = [...select.options].some((option) => option.value === previous)
    ? previous
    : "";
  if (repository) {
    select.dataset.repositoryHierarchy = repository;
    enhanceHierarchicalRepositorySelect(select);
  }
}

function hierarchicalOptionRows(select) {
  return [...select.options].map((option) => ({
    value: option.value,
    label: option.textContent || option.value,
    path: option.parentElement?.tagName === "OPTGROUP" ? (option.parentElement.label || "/") : "/",
    disabled: option.disabled,
  }));
}

function enhanceHierarchicalRepositorySelect(select) {
  if (!select) return;
  if (select.multiple) {
    enhanceHierarchicalMultiSelect(select);
    return;
  }
  document.querySelectorAll(".hierarchical-select-menu").forEach((candidate) => {
    if (candidate.__sourceSelect && !candidate.__sourceSelect.isConnected) candidate.remove();
  });
  select.classList.add("repository-native-select");
  let host = select.nextElementSibling?.classList.contains("hierarchical-select") ? select.nextElementSibling : null;
  if (!host) {
    host = document.createElement("div");
    host.className = "hierarchical-select";
    host.innerHTML = '<button class="hierarchical-select-trigger" type="button" aria-haspopup="tree" aria-expanded="false"></button>';
    select.insertAdjacentElement("afterend", host);
  }
  select.__hierarchicalMenu?.remove();
  const menu = document.createElement("div");
  menu.className = "hierarchical-select-menu";
  menu.hidden = true;
  menu.setAttribute("role", "tree");
  document.body.appendChild(menu);
  menu.__sourceSelect = select;
  select.__hierarchicalMenu = menu;
  const rows = hierarchicalOptionRows(select);
  const selected = rows.find((row) => row.value === select.value) || rows[0];
  const trigger = host.querySelector(".hierarchical-select-trigger");
  trigger.innerHTML = selected
    ? `<span>${escapeHtml(selected.label)}</span><small>${escapeHtml(selected.path)}</small><b aria-hidden="true">▾</b>`
    : '<span>No items</span><b aria-hidden="true">▾</b>';
  trigger.disabled = select.disabled || !rows.length;
  const root = { name: "/", path: "/", children: new Map(), items: [] };
  rows.forEach((row) => {
    const segments = row.path.split("/").filter(Boolean);
    let node = root;
    let currentPath = "";
    segments.forEach((segment) => {
      currentPath += `/${segment}`;
      if (!node.children.has(segment)) {
        node.children.set(segment, { name: segment, path: currentPath, children: new Map(), items: [] });
      }
      node = node.children.get(segment);
    });
    node.items.push(row);
  });
  const renderTreeNode = (node, parent, depth) => {
    const details = document.createElement("details");
    details.className = "hierarchical-folder";
    details.open = Boolean(selected?.path === node.path || selected?.path?.startsWith(`${node.path}/`));
    const summary = document.createElement("summary");
    summary.textContent = node.name;
    summary.style.setProperty("--hierarchy-depth", depth);
    details.appendChild(summary);
    node.items.forEach((row) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "hierarchical-item";
      button.classList.toggle("active", row.value === select.value);
      button.disabled = row.disabled;
      button.style.setProperty("--hierarchy-depth", depth + 1);
      button.innerHTML = `<span>${escapeHtml(row.label)}</span><small>${escapeHtml(row.path)}</small>`;
      button.addEventListener("click", () => {
        select.value = row.value;
        menu.hidden = true;
        trigger.setAttribute("aria-expanded", "false");
        select.dispatchEvent(new Event("change", { bubbles: true }));
        enhanceHierarchicalRepositorySelect(select);
      });
      details.appendChild(button);
    });
    [...node.children.values()]
      .sort((left, right) => left.name.localeCompare(right.name))
      .forEach((child) => renderTreeNode(child, details, depth + 1));
    parent.appendChild(details);
  };
  root.items.forEach((row) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "hierarchical-item";
    button.classList.toggle("active", row.value === select.value);
    button.disabled = row.disabled;
    button.innerHTML = `<span>${escapeHtml(row.label)}</span><small>/</small>`;
    button.addEventListener("click", () => {
      select.value = row.value;
      menu.hidden = true;
      trigger.setAttribute("aria-expanded", "false");
      select.dispatchEvent(new Event("change", { bubbles: true }));
      enhanceHierarchicalRepositorySelect(select);
    });
    menu.appendChild(button);
  });
  [...root.children.values()]
    .sort((left, right) => left.name.localeCompare(right.name))
    .forEach((child) => renderTreeNode(child, menu, 0));
  trigger.onclick = (event) => {
    event.stopPropagation();
    document.querySelectorAll(".hierarchical-select-menu").forEach((candidate) => {
      if (candidate !== menu) candidate.hidden = true;
    });
    menu.hidden = !menu.hidden;
    trigger.setAttribute("aria-expanded", String(!menu.hidden));
    if (!menu.hidden) {
      const rect = trigger.getBoundingClientRect();
      menu.style.left = `${Math.min(rect.left, window.innerWidth - Math.max(320, rect.width) - 12)}px`;
      menu.style.top = `${Math.min(rect.bottom + 4, window.innerHeight - Math.min(460, menu.scrollHeight) - 12)}px`;
      menu.style.width = `${Math.max(320, rect.width)}px`;
    }
  };
}

function enhanceHierarchicalMultiSelect(select) {
  select.classList.add("repository-native-select");
  let host = select.nextElementSibling?.classList.contains("hierarchical-select") ? select.nextElementSibling : null;
  if (!host) {
    host = document.createElement("div");
    host.className = "hierarchical-select";
    host.innerHTML = '<button class="hierarchical-select-trigger" type="button" aria-haspopup="tree" aria-expanded="false"></button>';
    select.insertAdjacentElement("afterend", host);
  }
  select.__hierarchicalMenu?.remove();
  const menu = document.createElement("div");
  menu.className = "hierarchical-select-menu";
  menu.hidden = true;
  menu.__sourceSelect = select;
  document.body.appendChild(menu);
  select.__hierarchicalMenu = menu;
  const rows = hierarchicalOptionRows(select);
  const root = { name: "/", path: "/", children: new Map(), items: [] };
  rows.forEach((row) => {
    const segments = row.path.split("/").filter(Boolean);
    let node = root;
    let currentPath = "";
    segments.forEach((segment) => {
      currentPath += `/${segment}`;
      if (!node.children.has(segment)) node.children.set(segment, { name: segment, path: currentPath, children: new Map(), items: [] });
      node = node.children.get(segment);
    });
    node.items.push(row);
  });
  const trigger = host.querySelector(".hierarchical-select-trigger");
  const updateTrigger = () => {
    const selected = [...select.selectedOptions];
    trigger.innerHTML = `<span>${selected.length ? `${selected.length} selected` : "Select items"}</span><small>${escapeHtml(selected.slice(0, 2).map((option) => option.textContent).join(", "))}</small><b>▾</b>`;
  };
  const appendItem = (row, parent, depth) => {
    const option = [...select.options].find((candidate) => candidate.value === row.value);
    const label = document.createElement("label");
    label.className = "hierarchical-multi-item";
    label.style.setProperty("--hierarchy-depth", depth);
    label.innerHTML = `<input type="checkbox" ${option?.selected ? "checked" : ""} ${row.disabled ? "disabled" : ""}/><span>${escapeHtml(row.label)}</span><small>${escapeHtml(row.path)}</small>`;
    label.querySelector("input").addEventListener("change", (event) => {
      if (option) option.selected = event.target.checked;
      select.dispatchEvent(new Event("change", { bubbles: true }));
      updateTrigger();
    });
    parent.appendChild(label);
  };
  const renderNode = (node, parent, depth) => {
    const details = document.createElement("details");
    details.className = "hierarchical-folder";
    details.open = true;
    const summary = document.createElement("summary");
    summary.textContent = node.name;
    summary.style.setProperty("--hierarchy-depth", depth);
    details.appendChild(summary);
    node.items.forEach((row) => appendItem(row, details, depth + 1));
    [...node.children.values()].sort((left, right) => left.name.localeCompare(right.name))
      .forEach((child) => renderNode(child, details, depth + 1));
    parent.appendChild(details);
  };
  root.items.forEach((row) => appendItem(row, menu, 0));
  [...root.children.values()].sort((left, right) => left.name.localeCompare(right.name))
    .forEach((child) => renderNode(child, menu, 0));
  updateTrigger();
  trigger.onclick = (event) => {
    event.stopPropagation();
    document.querySelectorAll(".hierarchical-select-menu").forEach((candidate) => {
      if (candidate !== menu) candidate.hidden = true;
    });
    menu.hidden = !menu.hidden;
    trigger.setAttribute("aria-expanded", String(!menu.hidden));
    if (!menu.hidden) {
      const rect = trigger.getBoundingClientRect();
      menu.style.left = `${Math.min(rect.left, window.innerWidth - Math.max(360, rect.width) - 12)}px`;
      menu.style.top = `${Math.min(rect.bottom + 4, window.innerHeight - Math.min(460, menu.scrollHeight) - 12)}px`;
      menu.style.width = `${Math.max(360, rect.width)}px`;
    }
  };
}

function refreshHierarchicalRepositorySelects() {
  document.querySelectorAll("select[data-repository-hierarchy]").forEach(enhanceHierarchicalRepositorySelect);
}

if (!window.__tradeHierarchicalSelectClickBound) {
  window.__tradeHierarchicalSelectClickBound = true;
  document.addEventListener("click", (event) => {
    if (event.target.closest(".hierarchical-select-menu, .hierarchical-select")) return;
    document.querySelectorAll(".hierarchical-select-menu").forEach((menu) => { menu.hidden = true; });
    document.querySelectorAll(".hierarchical-select-trigger").forEach((trigger) => trigger.setAttribute("aria-expanded", "false"));
  });
}

window.TradeRepositorySelect = { enhance: enhanceHierarchicalRepositorySelect };

async function loadRepositoryCatalog(repository, force = false) {
  const scope = repositoryScope(repository);
  if (!force && repositoryCatalog(scope)) return repositoryCatalog(scope);
  if (!repositoryCatalog(scope)) renderEmbeddedRepositoryLoading(scope);
  const response = await getJson(`/api/repositories?repository=${encodeURIComponent(scope)}`);
  state.repositoryCatalogs[scope] = response;
  return response;
}

function repositoryFolderById(repository, folderId) {
  return repositoryCatalog(repository)?.folders?.find((folder) => folder.folderId === folderId) || null;
}

function repositoryFolderOptions(repository, item = null) {
  const catalog = repositoryCatalog(repository);
  if (!catalog) return [];
  let folders = [...(catalog.folders || [])];
  if (repository === "modules" && item) {
    const fixedPath = `/${String(item.folderPath || "/").split("/").filter(Boolean)[0] || item.kind || ""}`;
    folders = folders.filter((folder) => folder.path === fixedPath || folder.path.startsWith(`${fixedPath}/`));
  }
  const fixedPlacement = repository === "modules";
  const root = fixedPlacement ? [] : [{ folderId: "", path: "/", name: "/", fixed: true }];
  return [...root, ...folders].sort((left, right) => left.path.localeCompare(right.path));
}

function setRepositoryError(message = "") {
  const node = $("repositoryError");
  if (!node) return;
  node.textContent = message;
  node.hidden = !message;
}

function repositoryItemSummary(repository, item) {
  if (MODULE_REPOSITORY_IDS.has(repository)) {
    const repositoryLabel = {
      modules: "Pipeline",
      "analysis-modules": "Analysis",
      "environment-modules": "Environment",
    }[repository];
    return [item.kind, item.version && `v${item.version}`, `${repositoryLabel} Module`]
      .filter(Boolean).join(" · ");
  }
  if (repository === "datasets") {
    return [item.status || "active", item.source?.type].filter(Boolean).join(" · ");
  }
  if (repository === "samplers") return `${item.type || "Sampler"} · v${item.version || "-"}`;
  if (repository === "pipelines") return `${item.status || "active"} · current v${item.currentVersion || "-"}`;
  if (repository === "environments" || repository === "analyses") {
    const nodeCount = Array.isArray(item.graph?.nodes) ? item.graph.nodes.length : 0;
    return `v${item.version || "-"} · ${nodeCount} Graph Module(s)`;
  }
  return `${item.status || "completed"} · ${item.metrics?.cycleCount ?? "-"} cycle(s)`;
}

function repositoryFolderContains(selectedFolder, itemPath) {
  if (selectedFolder === "*") return true;
  if (!selectedFolder) return itemPath === "/";
  const folder = repositoryFolderById(state.selectedRepository, selectedFolder);
  if (!folder) return itemPath === "/";
  return itemPath === folder.path || itemPath.startsWith(`${folder.path}/`);
}

function renderRepositoryFolderTree() {
  const repository = state.selectedRepository;
  const catalog = repositoryCatalog(repository);
  const tree = $("repositoryFolderTree");
  if (!tree || !catalog) return;
  tree.innerHTML = "";
  const makeButton = (label, folderId, depth, { fixed = false, count = 0 } = {}) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "repository-folder-button";
    button.classList.toggle("active", state.selectedRepositoryFolderId === folderId);
    button.style.setProperty("--folder-depth", depth);
    button.dataset.repositoryFolder = folderId;
    button.innerHTML = `<span class="repository-folder-icon" aria-hidden="true">${folderId === "*" ? "▦" : "▸"}</span><span>${escapeHtml(label)}</span>${fixed ? '<span class="repository-fixed-badge">fixed</span>' : ""}<strong>${count}</strong>`;
    tree.appendChild(button);
  };
  const countFor = (folder) => (catalog.items || []).filter((item) => (
    item.folderPath === folder.path || item.folderPath.startsWith(`${folder.path}/`)
  )).length;
  makeButton("All items", "*", 0, { count: catalog.total || 0 });
  if (repository !== "modules") {
    makeButton("/", "", 0, { fixed: true, count: (catalog.items || []).filter((item) => item.folderPath === "/").length });
  }
  const children = new Map();
  (catalog.folders || []).forEach((folder) => {
    const parentId = folder.parentId || "";
    if (!children.has(parentId)) children.set(parentId, []);
    children.get(parentId).push(folder);
  });
  children.forEach((folders) => folders.sort((left, right) => left.name.localeCompare(right.name)));
  const appendChildren = (parentId, depth) => {
    (children.get(parentId) || []).forEach((folder) => {
      makeButton(folder.name, folder.folderId, depth, { fixed: folder.fixed, count: countFor(folder) });
      appendChildren(folder.folderId, depth + 1);
    });
  };
  appendChildren("", 0);
  tree.querySelectorAll("[data-repository-folder]").forEach((button) => {
    button.addEventListener("click", () => {
      state.selectedRepositoryFolderId = button.dataset.repositoryFolder;
      renderRepositoryManager();
    });
  });
}

function renderRepositoryCards() {
  const repository = state.selectedRepository;
  const catalog = repositoryCatalog(repository);
  const grid = $("repositoryCardGrid");
  if (!grid || !catalog) return;
  const filter = ($("repositoryFilter")?.value || "").trim().toLowerCase();
  const repositoryItems = (catalog.items || []).filter((item) => (
    (repository !== "pipelines" || showInactivePipelines || item.status !== "inactive")
    && (repository !== "backtests" || showArchivedBacktests || item.status !== "archived")
  ));
  const items = repositoryItems.filter((item) => (
    repositoryFolderContains(state.selectedRepositoryFolderId, item.folderPath)
    && (!filter || JSON.stringify(item).toLowerCase().includes(filter))
  ));
  $("repositoryItemCount").textContent = `${items.length} item(s)`;
  grid.innerHTML = "";
  if (!items.length) {
    grid.innerHTML = '<div class="repository-empty muted">No items in this folder.</div>';
    return;
  }
  items.forEach((item) => {
    const card = document.createElement("article");
    card.className = "repository-card";
    const options = repositoryFolderOptions(repository, item).map((folder) => (
      `<option value="${escapeHtml(folder.folderId)}" ${folder.folderId === item.folderId ? "selected" : ""}>${escapeHtml(folder.path)}</option>`
    )).join("");
    card.innerHTML = `
      <div class="repository-card-head">
        <div><h3>${escapeHtml(item.label || item.itemId)}</h3><span>${escapeHtml(item.folderPath || "/")}</span></div>
        <span class="pill">${escapeHtml(repository.slice(0, -1) || repository)}</span>
      </div>
      <p>${escapeHtml(repositoryItemSummary(repository, item))}</p>
      <code title="${escapeHtml(item.itemId)}">${escapeHtml(item.itemId)}</code>
      <div class="repository-card-actions">
        <select data-repository-move-select="${escapeHtml(item.itemId)}" aria-label="Move ${escapeHtml(item.label || item.itemId)}">${options}</select>
        <button type="button" data-repository-move="${escapeHtml(item.itemId)}">Move</button>
        <button type="button" data-repository-open="${escapeHtml(item.itemId)}">Open</button>
      </div>`;
    grid.appendChild(card);
  });
  grid.querySelectorAll("[data-repository-move]").forEach((button) => {
    button.addEventListener("click", async () => {
      const itemId = button.dataset.repositoryMove;
      const select = grid.querySelector(`[data-repository-move-select="${CSS.escape(itemId)}"]`);
      button.disabled = true;
      try {
        const response = await postJson("/api/repository-folders", {
          action: "moveItem",
          repository,
          itemId,
          folderId: select?.value || "",
        });
        state.repositoryCatalogs[repository] = response.repository;
        setRepositoryError("");
        renderRepositoryManager();
      } catch (error) {
        setRepositoryError(error.message);
        button.disabled = false;
      }
    });
  });
  grid.querySelectorAll("[data-repository-open]").forEach((button) => {
    button.addEventListener("click", () => openRepositoryItem(repository, button.dataset.repositoryOpen));
  });
}

function renderRepositoryManager() {
  const catalog = repositoryCatalog(state.selectedRepository);
  if (!catalog) return;
  const selected = state.selectedRepositoryFolderId;
  if (selected !== "*" && selected !== "" && !repositoryFolderById(state.selectedRepository, selected)) {
    state.selectedRepositoryFolderId = "*";
  }
  $("repositoryTypeSelect").value = state.selectedRepository;
  const folder = repositoryFolderById(state.selectedRepository, state.selectedRepositoryFolderId);
  $("repositoryCurrentPath").textContent = state.selectedRepositoryFolderId === "*" ? "All items" : (folder?.path || "/");
  const mutableFolder = Boolean(folder && !folder.fixed);
  $("renameRepositoryFolderBtn").disabled = !mutableFolder;
  $("deleteRepositoryFolderBtn").disabled = !mutableFolder;
  renderRepositoryFolderTree();
  renderRepositoryCards();
}

async function loadRepositories(force = false) {
  await loadRepositoryCatalog(state.selectedRepository, force);
  renderRepositoryManager();
}

function scrollIntoViewAndWait(element, options = { behavior: "smooth", block: "start" }) {
  if (!element) return Promise.resolve();
  element.scrollIntoView(options);
  const scrollingElement = document.scrollingElement || document.documentElement;
  return new Promise((resolve) => {
    const startedAt = performance.now();
    let previousPosition = scrollingElement.scrollTop;
    let stableFrames = 0;
    const check = () => {
      const position = scrollingElement.scrollTop;
      stableFrames = Math.abs(position - previousPosition) < 0.5 ? stableFrames + 1 : 0;
      previousPosition = position;
      if ((stableFrames >= 5 && performance.now() - startedAt > 100) || performance.now() - startedAt > 2200) {
        resolve();
        return;
      }
      requestAnimationFrame(check);
    };
    requestAnimationFrame(check);
  });
}

async function openRepositoryItem(repository, itemId, openContext = {}) {
  const item = repositoryCatalog(repository)?.items?.find((candidate) => candidate.itemId === itemId);
  if (!item) return;
  const sourceRepository = item.sourceRepository || repository;
  const resourceType = item.resourceType || item.kind || "";
  if (MODULE_REPOSITORY_IDS.has(repository)) {
    if (item.builtin) {
      throw localUiError("Built-in Modules are read-only. Use Replace to create an editable Version.", "BUILTIN_MODULE_READ_ONLY");
    }
    await openModuleJupyter(item, repository);
  } else if (repositoryScope(repository) === "data") {
    await switchView("data");
    if (sourceRepository === "datasets") {
      if (item.status === "archived") throw new Error("Archived Datasets cannot create a Workspace.");
      openDatasetWorkspaceDialog([item], openContext.parentFolderId || "");
    } else if (sourceRepository === "workspaces") {
      await openWorkspaceJupyter(item.workspaceId || item.sourceItemId || item.itemId);
    } else if (sourceRepository === "scripts") {
      openDatasetProcessDialog([item], openContext.parentFolderId || "");
    } else if (sourceRepository === "samplers") {
      await openSamplerJupyter(item);
    }
  } else if (repository === "pipelines") {
    await openPipelineBuilder(item.pipelineId || item.itemId);
  } else if (repository === "environments") {
    await openEnvironmentBlueprint(item.versionKey || item.sourceItemId || item.itemId || "", { returnView: "environment" });
  } else if (repository === "analyses") {
    await openAnalysisBlueprint(item.versionKey || item.sourceItemId || item.itemId || "", { returnView: "analysis" });
  } else if (repositoryScope(repository) === "backtest" && sourceRepository === "environments") {
    await openEnvironmentBlueprint(item.sourceItemId || item.itemId || "", { returnView: "backtests" });
  } else if (repositoryScope(repository) === "backtest" && sourceRepository === "analyses") {
    await openAnalysisBlueprint(item.sourceItemId || item.itemId || "", { returnView: "backtests" });
  } else if (repositoryScope(repository) === "backtest" && (sourceRepository === "results" || resourceType === "Result")) {
    if (!item.visualizable || item.status === "archived") {
      throw localUiError("This Result is not available for visualization.", "RESULT_NOT_VISUALIZABLE");
    }
    await openBacktestResult(item.backtestId || item.sourceItemId || item.itemId);
  } else if (repositoryScope(repository) === "backtest" && sourceRepository === "backtests") {
    if (!item.visualizable || item.status === "archived") {
      throw localUiError("This Backtest does not have an available Result.", "RESULT_NOT_VISUALIZABLE");
    }
    await openBacktestResult(item.backtestId || item.sourceItemId || item.itemId);
  }
}

function openBacktestResult(backtestId) {
  if (!backtestId) throw localUiError("Result requires a Backtest ID.", "RESULT_BACKTEST_REQUIRED");
  state.resultBacktestId = backtestId;
  state.selectedBacktest = null;
  return switchView("results");
}

async function openWorkspaceJupyter(workspaceId) {
  const jupyterWindow = window.open("about:blank", "_blank");
  if (!jupyterWindow) throw localUiError("Allow pop-ups for this site to open JupyterLab.", "JUPYTER_POPUP_BLOCKED");
  jupyterWindow.document.title = "Starting JupyterLab";
  jupyterWindow.document.body.textContent = "Starting the Workspace JupyterLab session...";
  try {
    const result = await postJson(`/api/data/workspaces/${encodeURIComponent(workspaceId)}/jupyter`, {});
    jupyterWindow.opener = null;
    jupyterWindow.location.replace(result.url);
    return result;
  } catch (error) {
    jupyterWindow.close();
    throw error;
  }
}

function setDialogError(id, message = "") {
  const node = $(id);
  if (!node) return;
  node.textContent = message;
  node.hidden = !message;
}

function showModal(id) {
  const dialog = $(id);
  if (dialog?.open) dialog.close();
  if (typeof dialog?.showModal === "function") dialog.showModal();
}

function selectedIds(items, sourceRepository) {
  return (items || [])
    .filter((item) => (item.sourceRepository || "") === sourceRepository)
    .map((item) => item.sourceItemId || item.datasetId || item.workspaceId || item.itemId)
    .filter(Boolean);
}

async function assignDataResourceToFolder(sourceRepository, sourceItemId, folderId) {
  if (!folderId) return;
  await postJson("/api/repository-folders", {
    action: "moveItem",
    repository: "data",
    itemId: `${sourceRepository}::${sourceItemId}`,
    folderId,
  });
}

function selectMultipleValues(select, values) {
  const selected = new Set(values || []);
  [...select.options].forEach((option) => { option.selected = selected.has(option.value); });
}

function datasetWorkspaceCatalogRows() {
  const placements = new Map(
    (repositoryCatalog("data")?.items || [])
      .filter((item) => item.sourceRepository === "datasets")
      .map((item) => [item.datasetId, item]),
  );
  return state.datasets
    .filter((dataset) => dataset.status === "active")
    .map((dataset) => ({ ...dataset, folderPath: placements.get(dataset.datasetId)?.folderPath || "/" }));
}

function fuzzyDatasetMatch(query, dataset) {
  const needle = String(query || "").trim().toLocaleLowerCase();
  if (!needle) return 1;
  const haystack = [dataset.name, dataset.datasetId, dataset.source?.type, dataset.folderPath]
    .filter(Boolean).join(" ").toLocaleLowerCase();
  if (haystack.includes(needle)) return 1000 - haystack.indexOf(needle);
  let cursor = 0;
  let gap = 0;
  for (const character of needle) {
    const index = haystack.indexOf(character, cursor);
    if (index < 0) return -1;
    gap += index - cursor;
    cursor = index + 1;
  }
  return 500 - gap;
}

function toggleDatasetWorkspaceSelection(datasetId) {
  if (datasetWorkspaceSelection.has(datasetId)) datasetWorkspaceSelection.delete(datasetId);
  else datasetWorkspaceSelection.add(datasetId);
  renderDatasetWorkspacePicker(true);
}

function renderDatasetWorkspacePicker(showCandidates = !$("datasetWorkspaceCandidates")?.hidden) {
  const selectedHost = $("datasetWorkspaceSelected");
  const candidatesHost = $("datasetWorkspaceCandidates");
  if (!selectedHost || !candidatesHost) return;
  const rows = datasetWorkspaceCatalogRows();
  const rowsById = new Map(rows.map((dataset) => [dataset.datasetId, dataset]));
  for (const datasetId of [...datasetWorkspaceSelection]) {
    if (!rowsById.has(datasetId)) datasetWorkspaceSelection.delete(datasetId);
  }
  const selected = [...datasetWorkspaceSelection].map((datasetId) => rowsById.get(datasetId)).filter(Boolean);
  selectedHost.innerHTML = selected.length
    ? selected.map((dataset, index) => `
      <button type="button" class="dataset-picker-chip" data-workspace-dataset-remove="${forms.escapeHtml(dataset.datasetId)}" title="Remove ${forms.escapeHtml(dataset.name || dataset.datasetId)}">
        <span>${index + 1}</span><strong>${forms.escapeHtml(dataset.name || dataset.datasetId)}</strong><small>${forms.escapeHtml(dataset.datasetId)}</small><em aria-hidden="true">×</em>
      </button>
    `).join("")
    : '<div class="dataset-picker-empty">Search above and click a Dataset to add it.</div>';
  $("datasetWorkspaceSelectionCount").textContent = `${selected.length} selected`;
  selectedHost.querySelectorAll("[data-workspace-dataset-remove]").forEach((button) => {
    button.addEventListener("click", () => toggleDatasetWorkspaceSelection(button.dataset.workspaceDatasetRemove));
  });

  const query = $("datasetWorkspaceSearch").value;
  const matches = rows
    .map((dataset) => ({ dataset, score: fuzzyDatasetMatch(query, dataset) }))
    .filter((entry) => entry.score >= 0)
    .sort((left, right) => right.score - left.score || (left.dataset.name || "").localeCompare(right.dataset.name || ""))
    .slice(0, 80);
  candidatesHost.innerHTML = matches.length
    ? matches.map(({ dataset }) => `
      <button type="button" role="option" aria-selected="${datasetWorkspaceSelection.has(dataset.datasetId)}" data-workspace-dataset-add="${forms.escapeHtml(dataset.datasetId)}">
        <span><strong>${forms.escapeHtml(dataset.name || dataset.datasetId)}</strong><small>${forms.escapeHtml(dataset.datasetId)} · ${forms.escapeHtml(dataset.folderPath || "/")}</small></span>
        <em>${datasetWorkspaceSelection.has(dataset.datasetId) ? "Selected" : "Add"}</em>
      </button>
    `).join("")
    : '<div class="dataset-picker-empty">No matching Datasets.</div>';
  candidatesHost.querySelectorAll("[data-workspace-dataset-add]").forEach((button) => {
    button.addEventListener("click", () => {
      toggleDatasetWorkspaceSelection(button.dataset.workspaceDatasetAdd);
      $("datasetWorkspaceSearch").focus();
    });
  });
  candidatesHost.hidden = !showCandidates;
  $("datasetWorkspaceSearch").setAttribute("aria-expanded", String(showCandidates));
}

function openDatasetAddDialog(parentFolderId = "") {
  $("datasetAddDialog").dataset.parentFolderId = parentFolderId;
  setDialogError("datasetAddError", "");
  $("datasetAddDialogTitle").textContent = "Upload Dataset ZIP";
  showModal("datasetAddDialog");
}

function openDatasetWorkspaceDialog(items = [], parentFolderId = "") {
  const ids = selectedIds(items, "datasets");
  $("datasetWorkspaceDialog").dataset.parentFolderId = parentFolderId;
  const source = ids.length === 1 ? state.datasets.find((row) => row.datasetId === ids[0]) : null;
  $("datasetWorkspaceName").value = source ? `${source.name || source.datasetId} Workspace` : "";
  datasetWorkspaceSelection.clear();
  ids.forEach((datasetId) => datasetWorkspaceSelection.add(datasetId));
  $("datasetWorkspaceSearch").value = "";
  renderDatasetWorkspacePicker(false);
  setDialogError("datasetWorkspaceError", "");
  showModal("datasetWorkspaceDialog");
  requestAnimationFrame(() => $("datasetWorkspaceSearch").focus());
}

function openDatasetScriptDialog(parentFolderId = "", workspaceId = "") {
  $("datasetScriptDialog").dataset.parentFolderId = parentFolderId;
  $("datasetScriptForm").reset();
  showDatasetScriptMethodStep();
  const workspaceSelect = $("datasetScriptWorkspace");
  if (workspaceId) {
    if (![...workspaceSelect.options].some((option) => option.value === workspaceId)) {
      const workspace = state.datasetWorkspaces.find((row) => row.workspaceId === workspaceId);
      const option = document.createElement("option");
      option.value = workspaceId;
      option.textContent = workspace?.name || workspaceId;
      workspaceSelect.appendChild(option);
    }
    workspaceSelect.value = workspaceId;
    enhanceHierarchicalRepositorySelect(workspaceSelect);
    showDatasetScriptDetail("workspace");
  }
  showModal("datasetScriptDialog");
}

function showDatasetScriptMethodStep() {
  $("datasetScriptMode").value = "";
  $("datasetScriptDialogTitle").textContent = "Add Script";
  $("datasetScriptMethodStep").hidden = false;
  $("datasetScriptDetailStep").hidden = true;
  $("datasetScriptFileField").hidden = true;
  $("datasetScriptWorkspaceFields").hidden = true;
  $("datasetScriptDetailStep").querySelectorAll("input, select, textarea").forEach((field) => { field.disabled = true; });
  setDialogError("datasetScriptError", "");
}

function showDatasetScriptDetail(method) {
  const workspace = method === "workspace";
  $("datasetScriptMode").value = workspace ? "workspace" : "upload";
  $("datasetScriptDialogTitle").textContent = workspace ? "Choose Workspace Script" : "Upload Python Script";
  $("datasetScriptMethodStep").hidden = true;
  $("datasetScriptDetailStep").hidden = false;
  $("datasetScriptFileField").hidden = workspace;
  $("datasetScriptWorkspaceFields").hidden = !workspace;
  $("datasetScriptDetailStep").querySelectorAll("input, select, textarea").forEach((field) => { field.disabled = false; });
  $("datasetScriptFile").disabled = workspace;
  $("datasetScriptWorkspace").disabled = !workspace;
  $("datasetScriptWorkspacePath").disabled = !workspace;
  setDialogError("datasetScriptError", "");
  if (workspace) refreshDatasetScriptWorkspacePaths().catch((error) => setDialogError("datasetScriptError", error.message));
}

function currentProcessRecipe() {
  const value = $("datasetProcessScript").value;
  return state.datasetRecipes.find((recipe) => `${recipe.recipeId}::${recipe.version}` === value) || null;
}

function openDatasetProcessDialog(items = [], parentFolderId = "") {
  const datasetIds = selectedIds(items, "datasets");
  const script = (items || []).find((item) => item.sourceRepository === "scripts");
  $("datasetProcessDialog").dataset.parentFolderId = parentFolderId;
  if (script) $("datasetProcessScript").value = `${script.recipeId}::${script.version}`;
  selectMultipleValues($("datasetProcessSources"), datasetIds);
  $("datasetProcessOutputName").value = "";
  $("datasetProcessArguments").value = "";
  setDialogError("datasetProcessError", "");
  showModal("datasetProcessDialog");
}

function openDatasetReplaceDialog(item) {
  pendingDatasetReplace = item;
  $("datasetReplaceTarget").textContent = `${item.name || item.datasetId} · ${item.datasetId}`;
  $("datasetReplaceFile").value = "";
  setDialogError("datasetReplaceError", "");
  showModal("datasetReplaceDialog");
}

function openRepositoryResourceRenameDialog(repository, item) {
  pendingRepositoryRename = { repository, item };
  $("repositoryResourceRenameName").value = item.name || item.label || "";
  setDialogError("repositoryResourceRenameError", "");
  showModal("repositoryResourceRenameDialog");
  $("repositoryResourceRenameName").focus();
  $("repositoryResourceRenameName").select();
}

async function refreshDataFilesystemAfterMutation() {
  ["data", "datasets", "dataset-management"].forEach((key) => loadedViews.delete(key));
  await Promise.all([loadSummary(), loadData(true)]);
}

async function runRepositoryResourceAction(repository, action, item) {
  const sourceRepository = item?.sourceRepository || repository;
  if (MODULE_REPOSITORY_IDS.has(repository) && ["add", "publish", "edit"].includes(action)) {
    await startModuleLifecycleAction(action, item, repository);
    return;
  }
  if (repository === "pipelines" && action === "add-folder") {
    state.selectedRepository = "pipelines";
    state.selectedRepositoryFolderId = item?.parentFolderId || "";
    state.repositoryFolderSelections.pipelines = state.selectedRepositoryFolderId;
    openRepositoryFolderDialog("create");
    return;
  }
  if (repository === "pipelines" && action === "add-pipeline") {
    $("addPipelineBtn")?.click();
    return;
  }
  if (repository === "pipelines" && ["clone-pipeline", "disable-pipeline"].includes(action)) {
    if (!item?.itemId) throw new Error("Select a Pipeline first.");
    pipelineEditorState.pipelineId = item.pipelineId || item.itemId;
    syncPipelineLifecycleActionState();
    $(action === "clone-pipeline" ? "clonePipelineBtn" : "disablePipelineBtn")?.click();
    return;
  }
  if ((repository === "pipelines" || repository === "backtest") && action === "rename") {
    openRepositoryResourceRenameDialog(repository, item);
    return;
  }
  if (repository === "pipelines" && action === "toggle-inactive") {
    $("showInactivePipelinesBtn")?.click();
    return;
  }
  if (sourceRepository === "datasets" && action === "download") {
    return downloadDatasetArchive(item.datasetIds || [item.datasetId]);
  }
  if (sourceRepository === "datasets" && action === "archive") {
    await postJson(`/api/data/datasets/${encodeURIComponent(item.datasetId)}/archive`, {
      reason: "Archived from Dataset browser",
    });
    loadedViews.delete("data");
    loadedViews.delete("datasets");
    await loadData(true);
    return;
  }
  if (["backtests", "results"].includes(sourceRepository) && action === "archive") {
    await postJson(`/api/backtests/${encodeURIComponent(item.backtestId)}/archive`, {
      reason: "Archived from Backtest Results browser",
    });
    loadedViews.delete("backtests");
    await loadBacktests(true);
    return;
  }
  if (sourceRepository === "workspaces" && action === "jupyter") {
    return openWorkspaceJupyter(item.workspaceId);
  }
  if (sourceRepository === "scripts" && action === "use-script") {
    return openDatasetProcessDialog([item]);
  }
  if (sourceRepository === "samplers" && action === "publish") {
    return publishSamplerWorkspace(item);
  }
  if (repository === "data") {
    const items = item?.items || [];
    const parentFolderId = item?.parentFolderId || "";
    if (action === "add-dataset") return openDatasetAddDialog(parentFolderId);
    if (action === "add-script") {
      const contextWorkspace = items.find((entry) => (
        entry?.itemId === state.lastDataContextItemId && entry?.sourceRepository === "workspaces"
      ));
      const workspaces = items.filter((entry) => entry?.sourceRepository === "workspaces");
      const workspace = contextWorkspace || (workspaces.length === 1 ? workspaces[0] : null);
      return openDatasetScriptDialog(parentFolderId, workspace?.workspaceId || workspace?.sourceItemId || "");
    }
    if (action === "add-workspace") return openDatasetWorkspaceDialog(items, parentFolderId);
    if (action === "publish" && items.length === 1 && items[0]?.sourceRepository === "samplers") {
      return publishSamplerWorkspace(items[0]);
    }
    if (action === "process") return openDatasetProcessDialog(items, parentFolderId);
    if (action === "replace") return openDatasetReplaceDialog(items[0]);
    if (action === "rename") return openRepositoryResourceRenameDialog("data", items[0]);
    if (action === "archive") {
      const datasets = items.filter((entry) => entry.sourceRepository === "datasets");
      if (!window.confirm(`Archive ${items.length} selected resource${items.length === 1 ? "" : "s"}?`)) return;
      for (const dataset of datasets) {
        await postJson(`/api/data/datasets/${encodeURIComponent(dataset.datasetId)}/archive`, { reason: "Archived from Data Filesystem" });
      }
      return refreshDataFilesystemAfterMutation();
    }
    if (action === "delete") {
      if (!window.confirm(`Delete ${items.length} selected Workspace${items.length === 1 ? "" : "s"}?`)) return;
      for (const workspace of items) {
        const response = await authenticatedFetch(`/api/data/workspaces/${encodeURIComponent(workspace.workspaceId)}`, {
          method: "DELETE",
          headers: { Accept: "application/json", "X-CSRF-Token": authState.csrfToken },
        });
        const result = await response.json().catch(() => ({}));
        if (!response.ok || result.accepted === false) throw new Error(result.error || `Workspace delete returned ${response.status}`);
      }
      return refreshDataFilesystemAfterMutation();
    }
  }
  if (action === "download") throw new Error("Download is not supported for this repository resource.");
}

let repositoryFolderDialogMode = "create";

function openRepositoryFolderDialog(mode) {
  const repository = state.selectedRepository;
  const folder = repositoryFolderById(repository, state.selectedRepositoryFolderId);
  repositoryFolderDialogMode = mode;
  $("repositoryFolderDialogTitle").textContent = mode === "rename" ? "Rename Folder" : "New Folder";
  $("repositoryFolderName").value = mode === "rename" ? (folder?.name || "") : "";
  $("repositoryFolderParentField").hidden = mode === "rename";
  const parent = $("repositoryFolderParent");
  parent.innerHTML = "";
  repositoryFolderOptions(repository).forEach((candidate) => {
    const option = document.createElement("option");
    option.value = candidate.folderId;
    option.textContent = candidate.path;
    parent.appendChild(option);
  });
  const selectedParent = state.selectedRepositoryFolderId === "*"
    ? (repository === "modules" ? repositoryCatalog(repository)?.folders?.find((candidate) => candidate.fixed)?.folderId : "")
    : state.selectedRepositoryFolderId;
  if ([...parent.options].some((option) => option.value === selectedParent)) parent.value = selectedParent;
  $("repositoryFolderDialogError").hidden = true;
  $("repositoryFolderDialogError").textContent = "";
  $("repositoryFolderDialog").showModal();
  requestAnimationFrame(() => $("repositoryFolderName").focus());
}

const EMBEDDED_REPOSITORIES = {
  modules: "moduleRepositoryBrowser",
  "analysis-modules": "analysisModuleRepositoryBrowser",
  "environment-modules": "environmentModuleRepositoryBrowser",
  data: "dataRepositoryBrowser",
  pipelines: "pipelineRepositoryBrowser",
  environments: "environmentRepositoryBrowser",
  analyses: "analysisRepositoryBrowser",
  backtest: "backtestResourceBrowser",
  visualizers: "visualizerRepositoryBrowser",
};

function renderEmbeddedRepositoryLoading(repository) {
  const container = $(EMBEDDED_REPOSITORIES[repository]);
  if (!container || repositoryCatalog(repository) || !window.TradeResourceBrowser) return;
  window.TradeResourceBrowser.mount(container, { repository, loading: true });
}

function selectedEmbeddedFolder(repository) {
  return state.repositoryFolderSelections[repository] ?? "*";
}

function embeddedFolderContains(repository, folderId, itemPath) {
  if (folderId === "*") return true;
  if (!folderId) return itemPath === "/";
  const folder = repositoryFolderById(repository, folderId);
  if (!folder) return itemPath === "/";
  return itemPath === folder.path || itemPath.startsWith(`${folder.path}/`);
}

function repositoryCardExtraActions(repository, item) {
  if (repository === "backtests" && item.status !== "archived") {
    return `<button class="danger" data-archive-backtest="${escapeHtml(item.backtestId)}" type="button">Archive</button>`;
  }
  return "";
}

function bindEmbeddedRepositoryActions(repository, root) {
  root.querySelectorAll("[data-embedded-folder]").forEach((button) => {
    button.addEventListener("click", () => {
      state.repositoryFolderSelections[repository] = button.dataset.embeddedFolder;
      renderEmbeddedRepositoryBrowser(repository);
    });
  });
  root.querySelector("[data-embedded-filter]")?.addEventListener("input", (event) => {
    state.repositoryFilters[repository] = event.target.value;
    renderEmbeddedRepositoryBrowser(repository);
    requestAnimationFrame(() => {
      const input = $(EMBEDDED_REPOSITORIES[repository])?.querySelector("[data-embedded-filter]");
      input?.focus();
      input?.setSelectionRange(input.value.length, input.value.length);
    });
  });
  root.querySelector("[data-embedded-new-folder]")?.addEventListener("click", () => {
    state.selectedRepository = repository;
    state.selectedRepositoryFolderId = selectedEmbeddedFolder(repository);
    openRepositoryFolderDialog("create");
  });
  root.querySelector("[data-embedded-rename-folder]")?.addEventListener("click", () => {
    state.selectedRepository = repository;
    state.selectedRepositoryFolderId = selectedEmbeddedFolder(repository);
    openRepositoryFolderDialog("rename");
  });
  root.querySelector("[data-embedded-delete-folder]")?.addEventListener("click", async () => {
    const folder = repositoryFolderById(repository, selectedEmbeddedFolder(repository));
    if (!folder || folder.fixed || !window.confirm(`Delete empty folder ${folder.path}?`)) return;
    try {
      const response = await postJson("/api/repository-folders", { action: "delete", repository, folderId: folder.folderId });
      state.repositoryCatalogs[repository] = response.repository;
      state.repositoryFolderSelections[repository] = folder.parentId || (repository === "modules" ? "*" : "");
      renderEmbeddedRepositoryBrowser(repository);
      refreshHierarchicalRepositorySelects();
    } catch (error) {
      const node = root.querySelector("[data-embedded-error]");
      node.textContent = error.message;
      node.hidden = false;
    }
  });
  root.querySelectorAll("[data-repository-move]").forEach((button) => {
    button.addEventListener("click", async () => {
      const itemId = button.dataset.repositoryMove;
      const select = root.querySelector(`[data-repository-move-select="${CSS.escape(itemId)}"]`);
      button.disabled = true;
      try {
        const response = await postJson("/api/repository-folders", {
          action: "moveItem", repository, itemId, folderId: select?.value || "",
        });
        state.repositoryCatalogs[repository] = response.repository;
        renderEmbeddedRepositoryBrowser(repository);
        refreshHierarchicalRepositorySelects();
      } catch (error) {
        const node = root.querySelector("[data-embedded-error]");
        node.textContent = error.message;
        node.hidden = false;
        button.disabled = false;
      }
    });
  });
  root.querySelectorAll("[data-repository-open]").forEach((button) => {
    button.addEventListener("click", () => {
      openRepositoryItem(repository, button.dataset.repositoryOpen).catch((error) => setHealth(false, error.message));
    });
  });
  root.querySelectorAll("[data-archive-backtest]").forEach((button) => {
    button.addEventListener("click", () => runUiAction("Archiving", async () => {
      await postJson(`/api/backtests/${encodeURIComponent(button.dataset.archiveBacktest)}/archive`, {
        reason: "Archived from Backtest UI",
      });
      await loadBacktests(true);
    }));
  });
}

function renderEmbeddedRepositoryBrowser(repository) {
  const container = $(EMBEDDED_REPOSITORIES[repository]);
  const catalog = repositoryCatalog(repository);
  if (!container || !catalog || !window.TradeResourceBrowser) return;
  if (repository === "data" && !window.__tradeDataContextTargetCapture) {
    window.__tradeDataContextTargetCapture = true;
    window.addEventListener("contextmenu", (event) => {
      const card = event.target.closest("#dataRepositoryBrowser .file-item-container");
      const label = card?.getAttribute("title") || "";
      const record = label ? repositoryCatalog("data")?.items?.find((entry) => entry.label === label) : null;
      state.lastDataContextItemId = card?.dataset.tradeItemId || record?.itemId || "";
    }, true);
  }
  const filter = String(state.repositoryFilters[repository] || "").toLowerCase();
  const visibleItems = (catalog.items || []).filter((item) => (
    (repository !== "pipelines" || showInactivePipelines || item.status !== "inactive")
    && (repository !== "backtest" || showArchivedBacktests || item.status !== "archived")
    && (!filter || JSON.stringify(item).toLowerCase().includes(filter))
  ));
  const browserCatalog = { ...catalog, items: visibleItems, total: visibleItems.length };
  window.TradeResourceBrowser.mount(container, {
    repository,
    catalog: browserCatalog,
    readOnly: repository === "visualizers",
    initialPath: state.repositoryBrowserPaths?.[repository] || "",
    onFolderChange: (path) => {
      state.repositoryBrowserPaths ||= {};
      state.repositoryBrowserPaths[repository] = path;
      scheduleUiContextSync();
    },
    onMutation: async (action, payload) => {
      if (repository === "visualizers") throw new Error("Built-in Visualizers are read-only.");
      const response = await postJson("/api/repository-folders", { action, repository, ...payload });
      state.repositoryCatalogs[repository] = response.repository;
      renderEmbeddedRepositoryBrowser(repository);
      refreshHierarchicalRepositorySelects();
    },
    onRefresh: async () => {
      if (repository === "visualizers") {
        await loadVisualizers(true);
        return;
      }
      await loadRepositoryCatalog(repository, true);
      renderEmbeddedRepositoryBrowser(repository);
    },
    onOpen: (entry) => openRepositoryItem(repository, entry.itemId, entry),
    onSelectionChange: (entries) => {
      state.uiRepositorySelections[repository] = structuredClone(entries || []);
      if (MODULE_REPOSITORY_IDS.has(repository)) {
        const selected = entries.length === 1 && entries[0]?.moduleId ? entries[0] : null;
        state.selectedModuleRepositoryItem = selected;
        if (selected) selectModuleRepository(repository);
      }
      scheduleUiContextSync();
    },
    onResourceAction: (action, item) => runRepositoryResourceAction(repository, action, item),
    showInactive: repository === "pipelines" ? showInactivePipelines : false,
    showArchived: repository === "backtest" ? showArchivedBacktests : false,
  });
}

function renderAllEmbeddedRepositoryBrowsers(repository = "") {
  Object.keys(EMBEDDED_REPOSITORIES).forEach((candidate) => {
    if (!repository || candidate === repository) renderEmbeddedRepositoryBrowser(candidate);
  });
}

function sortedPipelines({ includeInactive = showInactivePipelines } = {}) {
  return Object.values(state.pipelines || {})
    .filter((pipeline) => includeInactive || pipeline.status !== "inactive")
    .sort((a, b) => (a.pipelineId || "").localeCompare(b.pipelineId || ""));
}

function selectedPipelineRecord() {
  return state.pipelines?.[pipelineEditorState.pipelineId] || null;
}

function validPipelineId(pipelineId, pipelines = sortedPipelines()) {
  return pipelines.some((pipeline) => pipeline.pipelineId === pipelineId) ? pipelineId : "";
}

function renderPipelineEditorSelector() {
  const pipelines = sortedPipelines();
  const select = $("pipelineSelect");
  if (!select) return;
  const selectedPipelineId = validPipelineId(pipelineEditorState.pipelineId, pipelines) || pipelines[0]?.pipelineId || "";
  pipelineEditorState.pipelineId = selectedPipelineId;
  select.innerHTML = "";
  if (!pipelines.length) {
    const option = document.createElement("option");
    option.value = "";
    option.textContent = "No Pipeline available";
    select.appendChild(option);
    select.disabled = true;
  } else {
    select.disabled = Boolean(pipelineBlueprintBusyMessage());
    appendRepositoryOptions(
      select,
      pipelines,
      (pipeline) => pipeline.pipelineId,
      (pipeline) => `${pipeline.name || pipeline.pipelineId}${pipeline.status === "inactive" ? " · Inactive" : ""}`,
      "pipelines",
    );
    select.value = selectedPipelineId;
  }
  const pipelineIdField = pipelineField("Id");
  if (pipelineIdField) pipelineIdField.value = selectedPipelineId;
  select.dataset.repositoryHierarchy = "pipelines";
  enhanceHierarchicalRepositorySelect(select);
  const showInactiveButton = $("showInactivePipelinesBtn");
  if (showInactiveButton) {
    showInactiveButton.textContent = showInactivePipelines ? "Hide Inactive" : "Show Inactive";
  }
  syncPipelineLifecycleActionState();
}

function renderPipelineVersionSelector() {
  const select = $("pipelineVersionSelect");
  if (!select) return;
  const versions = pipelineEditorState.versions || [];
  select.innerHTML = "";
  if (!versions.length) {
    const option = document.createElement("option");
    option.value = "";
    option.textContent = "No saved Version";
    select.appendChild(option);
    select.disabled = true;
    return;
  }
  const selected = pipelineEditorState.loadedVersion
    || versions.find((row) => row.current)?.version
    || versions[versions.length - 1].version;
  versions.slice().reverse().forEach((versionSummary) => {
    const option = document.createElement("option");
    option.value = versionSummary.version;
    option.textContent = `${versionSummary.current ? "Current · " : ""}v${versionSummary.version} · ${formatTime(versionSummary.createdAt)}`;
    option.title = versionSummary.contentDigest;
    option.selected = versionSummary.version === selected;
    select.appendChild(option);
  });
  select.disabled = Boolean(pipelineBlueprintBusyMessage());
}

function renderBacktestPipelineSelector() {
  const pipelines = sortedPipelines().filter((pipeline) => pipeline.status === "active");
  const activeIds = new Set(pipelines.map((pipeline) => pipeline.pipelineId));
  const versions = (state.pipelineVersions || []).filter((version) => activeIds.has(version.pipelineId));
  const select = $("backtestPipelineSelect");
  if (!select) return;
  const selectedKey = `${backtestEntryState.pipelineId}::${backtestEntryState.pipelineVersion}`;
  const availableKeys = new Set(versions.map((version) => `${version.pipelineId}::${version.version}`));
  const effectiveKey = availableKeys.has(selectedKey) ? selectedKey : "";
  const [selectedPipelineId = "", selectedPipelineVersion = ""] = effectiveKey.split("::");
  backtestEntryState.pipelineId = selectedPipelineId;
  backtestEntryState.pipelineVersion = selectedPipelineVersion;
  select.innerHTML = "";
  if (!versions.length) {
    const option = document.createElement("option");
    option.value = "";
    option.textContent = "No archived Pipeline Version available";
    select.appendChild(option);
    select.disabled = true;
    return;
  }
  select.disabled = false;
  const placeholder = document.createElement("option");
  placeholder.value = "";
  placeholder.textContent = "Select an archived Pipeline Version";
  select.appendChild(placeholder);
  versions.slice().reverse().forEach((version) => {
    const pipeline = state.pipelines[version.pipelineId] || {};
    const option = document.createElement("option");
    option.value = `${version.pipelineId}::${version.version}`;
    option.textContent = `${pipeline.name || version.pipelineId} · v${version.version}${version.current ? " · Current" : ""}`;
    select.appendChild(option);
  });
  select.value = effectiveKey;
}

function selectedBacktestDatasetEvidence() {
  const datasetId = $("backtestDataset")?.value || "";
  const dataset = (state.datasets || []).find((item) => item.datasetId === datasetId);
  if (!dataset) return null;
  const sealed = (state.datasetVersions || [])
    .filter((evidence) => evidence.datasetId === datasetId && evidence.status === "sealed")
    .sort((left, right) => String(right.createdAt || "").localeCompare(String(left.createdAt || "")));
  return sealed.find((evidence) => evidence.datasetVersionId === dataset.latestVersionId)
    || sealed[0]
    || null;
}

function datasetEvidenceSummary(evidence) {
  const capabilities = evidence?.capabilities;
  if (!capabilities || Array.isArray(capabilities) || typeof capabilities !== "object") {
    return "no declared capabilities";
  }
  const recordCount = capabilities.records?.descriptor?.recordCount;
  if (Number.isInteger(recordCount) && recordCount >= 0) return `${recordCount} records`;
  const count = Object.keys(capabilities).length;
  return count ? `${count} declared ${count === 1 ? "capability" : "capabilities"}` : "opaque container";
}

function datasetCatalogLabel(dataset) {
  return [dataset.name || "Untitled Dataset", dataset.source?.type]
    .filter(Boolean).join(" · ");
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
  $("moduleKindStatus").textContent = `${repositoryCatalog("modules")?.total || 0} module(s) · fixed top-level type folders`;
  renderEmbeddedRepositoryBrowser("modules");
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
      loadModules(true).catch((error) => setHealth(false, error.message));
    });
    menu.appendChild(button);
  });
}

function instancesByKind(kind) {
  return Object.entries(state.pipelineDraft?.instances || {})
    .map(([key, value]) => ({ key, instanceId: value.instanceId || key, ...value }))
    .filter((row) => (row.kind || "").toLowerCase() === kind.toLowerCase())
    .sort((a, b) => (a.instanceId || "").localeCompare(b.instanceId || ""));
}

function pipelineField(id) {
  return $(`pipeline${id}`);
}

const OBSERVATION_PATH_PATTERN = /^[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)*$/;

function normalizeObservationPathEntries(value) {
  const source = Array.isArray(value) ? value : String(value || "").split(/\r?\n/);
  return [...new Set(source
    .flatMap((path) => String(path || "").split(/\r?\n/))
    .map((path) => path.trim())
    .filter(Boolean))]
    .sort((left, right) => left.localeCompare(right));
}

function observationEditorEntries(fieldId) {
  const editor = $(`pipelineObservation${fieldId}Editor`);
  if (!editor) return [];
  try {
    return normalizeObservationPathEntries(JSON.parse(editor.dataset.entries || "[]"));
  } catch {
    return [];
  }
}

function setObservationEditorError(fieldId, message = "") {
  const error = $(`pipelineObservation${fieldId}Error`);
  if (!error) return;
  error.textContent = message;
  error.hidden = !message;
}

function observationPathError(value) {
  const path = String(value || "").trim();
  if (!path) return "A DataKey cannot be empty.";
  if (!OBSERVATION_PATH_PATTERN.test(path)) {
    return "Use dot-separated segments containing only letters, numbers, _ or -.";
  }
  const root = path.split(".", 1)[0];
  if (root === "last" || root === "decisionTime") {
    return `The reserved Engine root “${root}” cannot be selected.`;
  }
  return "";
}

function observationPathIsCovered(path, parent) {
  return path === parent || path.startsWith(`${parent}.`);
}

function observationInputCandidate(fieldId, entries) {
  return {
    whitelist: fieldId === "Whitelist" ? entries : observationEditorEntries("Whitelist"),
    blacklist: fieldId === "Blacklist" ? entries : observationEditorEntries("Blacklist"),
  };
}

function observationInputCandidateError(fieldId, entries) {
  const candidate = observationInputCandidate(fieldId, entries);
  const outside = candidate.blacklist.filter((path) => (
    !candidate.whitelist.some((allowed) => observationPathIsCovered(path, allowed))
  ));
  if (!outside.length) return "";
  if (fieldId === "Whitelist") {
    return `This change would leave Blacklist DataKey “${outside[0]}” outside the Whitelist.`;
  }
  return `Blacklist DataKey “${outside[0]}” must be inside a Whitelist path.`;
}

function renderObservationEditor(fieldId, values) {
  const editor = $(`pipelineObservation${fieldId}Editor`);
  const list = $(`pipelineObservation${fieldId}List`);
  const count = $(`pipelineObservation${fieldId}Count`);
  if (!editor || !list || !count) return;
  const entries = normalizeObservationPathEntries(values);
  editor.dataset.entries = JSON.stringify(entries);
  list.replaceChildren();
  entries.forEach((entry) => {
    const item = document.createElement("div");
    item.className = "observation-entry";
    item.setAttribute("role", "listitem");
    const label = document.createElement("button");
    label.type = "button";
    label.className = "observation-entry-label";
    label.dataset.editObservationEntry = entry;
    label.setAttribute("aria-label", `Edit ${entry}`);
    label.textContent = entry;
    label.title = `${entry} · click to edit`;
    label.dataset.defaultTitle = label.title;
    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "observation-entry-remove";
    remove.dataset.removeObservationEntry = entry;
    remove.setAttribute("aria-label", `Remove ${entry}`);
    remove.dataset.defaultTitle = `Remove ${entry}`;
    remove.textContent = "×";
    item.append(label, remove);
    list.appendChild(item);
  });
  count.textContent = String(entries.length);
  count.setAttribute("aria-label", `${entries.length} entries`);
  syncPipelineDraftFieldState();
}

function commitObservationEditor(fieldId, values) {
  renderObservationEditor(fieldId, values);
  state.pipelineDraft ||= clonePipelineDraft(pipelineEditorState.definition || {});
  state.pipelineDraft.config = {
    ...(state.pipelineDraft.config || {}),
    observationInput: pipelineObservationInputFromFields(),
  };
  setPipelineSaveError("");
  invalidateBacktestBuild("Pipeline Observation Input changed · Build again before running");
  syncPipelineSaveActionState();
}

function commitObservationEditorCandidate(fieldId, values) {
  const next = normalizeObservationPathEntries(values);
  const error = observationInputCandidateError(fieldId, next);
  if (error) {
    setObservationEditorError(fieldId, error);
    return false;
  }
  setObservationEditorError(fieldId, "");
  commitObservationEditor(fieldId, next);
  return true;
}

function showObservationEntryValidation(input, errorNode, message = "") {
  input.setAttribute("aria-invalid", message ? "true" : "false");
  errorNode.textContent = message;
  errorNode.hidden = !message;
}

function beginObservationEntryEdit(fieldId, originalEntry = null) {
  if (pipelineBlueprintBusyMessage()) return;
  const list = $(`pipelineObservation${fieldId}List`);
  if (!list) return;
  const entries = observationEditorEntries(fieldId);
  renderObservationEditor(fieldId, entries);
  let item = originalEntry === null
    ? null
    : [...list.querySelectorAll(".observation-entry")].find((node) => (
      node.querySelector("[data-edit-observation-entry]")?.dataset.editObservationEntry === originalEntry
    ));
  if (!item) {
    item = document.createElement("div");
    item.className = "observation-entry";
    item.setAttribute("role", "listitem");
    list.appendChild(item);
  }
  item.className = "observation-entry editing";
  item.replaceChildren();
  const input = document.createElement("input");
  input.type = "text";
  input.className = "observation-entry-edit";
  input.value = originalEntry || "";
  input.spellcheck = false;
  input.autocomplete = "off";
  input.setAttribute("aria-label", originalEntry === null ? `New ${fieldId} DataKey` : `Edit ${originalEntry}`);
  input.setAttribute("aria-invalid", "false");
  const error = document.createElement("span");
  error.className = "observation-entry-validation";
  error.setAttribute("role", "alert");
  error.hidden = true;
  item.append(input, error);
  setObservationEditorError(fieldId, "");

  let composing = false;
  let compositionJustEnded = false;
  input.addEventListener("compositionstart", () => { composing = true; });
  input.addEventListener("compositionend", () => {
    composing = false;
    compositionJustEnded = true;
    setTimeout(() => { compositionJustEnded = false; }, 0);
  });
  input.addEventListener("input", () => {
    showObservationEntryValidation(input, error);
    setObservationEditorError(fieldId, "");
  });
  input.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      event.preventDefault();
      renderObservationEditor(fieldId, entries);
      setObservationEditorError(fieldId, "");
      return;
    }
    if (
      event.key !== "Enter"
      || composing
      || compositionJustEnded
      || event.isComposing
      || event.keyCode === 229
    ) return;
    event.preventDefault();
    const value = input.value.trim();
    let message = observationPathError(value);
    const remaining = originalEntry === null
      ? entries
      : entries.filter((entry) => entry !== originalEntry);
    if (!message && remaining.includes(value)) {
      message = `DataKey “${value}” already exists in ${fieldId}.`;
    }
    const next = originalEntry === null
      ? [...entries, value]
      : entries.map((entry) => entry === originalEntry ? value : entry);
    if (!message) message = observationInputCandidateError(fieldId, normalizeObservationPathEntries(next));
    if (message) {
      showObservationEntryValidation(input, error, message);
      setObservationEditorError(fieldId, message);
      return;
    }
    if (originalEntry !== null && value === originalEntry) {
      renderObservationEditor(fieldId, entries);
      setObservationEditorError(fieldId, "");
      return;
    }
    commitObservationEditorCandidate(fieldId, next);
  });
  syncPipelineDraftFieldState();
  requestAnimationFrame(() => {
    input.focus();
    if (originalEntry !== null) input.select();
  });
}

let pendingObservationBatchFieldId = "";

function openObservationBatchDialog(fieldId) {
  if (pipelineBlueprintBusyMessage()) return;
  pendingObservationBatchFieldId = fieldId;
  $("pipelineObservationBatchTitle").textContent = `Batch import ${fieldId} DataKeys`;
  $("pipelineObservationBatchInput").value = "";
  setDialogError("pipelineObservationBatchError", "");
  showModal("pipelineObservationBatchDialog");
  requestAnimationFrame(() => $("pipelineObservationBatchInput")?.focus());
}

function observationBatchCandidate(fieldId, source) {
  const existing = observationEditorEntries(fieldId);
  const firstLineByValue = new Map(existing.map((value) => [value, 0]));
  const additions = [];
  const errors = [];
  String(source || "").split(/\r?\n/).forEach((raw, index) => {
    const lineNumber = index + 1;
    const value = raw.trim();
    if (!value) return;
    const formatError = observationPathError(value);
    if (formatError) {
      errors.push(`Line ${lineNumber}: ${formatError}`);
      return;
    }
    if (firstLineByValue.has(value)) {
      const firstLine = firstLineByValue.get(value);
      errors.push(firstLine
        ? `Line ${lineNumber}: DataKey “${value}” duplicates line ${firstLine}.`
        : `Line ${lineNumber}: DataKey “${value}” already exists in ${fieldId}.`);
      return;
    }
    firstLineByValue.set(value, lineNumber);
    additions.push(value);
  });
  if (!additions.length && !errors.length) errors.push("Enter at least one DataKey.");
  const entries = normalizeObservationPathEntries([...existing, ...additions]);
  const contractError = errors.length ? "" : observationInputCandidateError(fieldId, entries);
  if (contractError) errors.push(contractError);
  return { entries, errors };
}

function bindObservationEditor(fieldId) {
  const editor = $(`pipelineObservation${fieldId}Editor`);
  const addButton = $(`pipelineObservation${fieldId}AddBtn`);
  const batchButton = $(`pipelineObservation${fieldId}BatchBtn`);
  if (!editor || !addButton || !batchButton || editor.dataset.bound === "1") return;
  editor.dataset.bound = "1";
  addButton.dataset.defaultTitle = "Add one DataKey";
  batchButton.dataset.defaultTitle = `Batch import ${fieldId} DataKeys`;
  addButton.addEventListener("click", () => beginObservationEntryEdit(fieldId));
  batchButton.addEventListener("click", () => openObservationBatchDialog(fieldId));
  editor.addEventListener("click", (event) => {
    const remove = event.target.closest("[data-remove-observation-entry]");
    if (remove) {
      const next = observationEditorEntries(fieldId).filter(
        (entry) => entry !== remove.dataset.removeObservationEntry,
      );
      commitObservationEditorCandidate(fieldId, next);
      return;
    }
    const edit = event.target.closest("[data-edit-observation-entry]")
      || event.target.closest(".observation-entry")?.querySelector("[data-edit-observation-entry]");
    if (edit) beginObservationEntryEdit(fieldId, edit.dataset.editObservationEntry);
  });
}

function bindObservationBatchDialog() {
  const dialog = $("pipelineObservationBatchDialog");
  const form = $("pipelineObservationBatchForm");
  const cancel = $("cancelPipelineObservationBatchBtn");
  if (!dialog || !form || !cancel || dialog.dataset.bound === "1") return;
  dialog.dataset.bound = "1";
  cancel.addEventListener("click", () => dialog.close());
  dialog.addEventListener("close", () => {
    pendingObservationBatchFieldId = "";
    $("pipelineObservationBatchInput").value = "";
    setDialogError("pipelineObservationBatchError", "");
  });
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    if (pipelineBlueprintBusyMessage() || !pendingObservationBatchFieldId) return;
    const fieldId = pendingObservationBatchFieldId;
    const candidate = observationBatchCandidate(fieldId, $("pipelineObservationBatchInput").value);
    if (candidate.errors.length) {
      setDialogError("pipelineObservationBatchError", candidate.errors.join("\n"));
      return;
    }
    if (commitObservationEditorCandidate(fieldId, candidate.entries)) dialog.close();
  });
}

function pipelineObservationInputFromFields() {
  return {
    whitelist: observationEditorEntries("Whitelist"),
    blacklist: observationEditorEntries("Blacklist"),
  };
}

function moduleDefinitionsByKind(kind) {
  return Object.entries(state.pipelineModules || {})
    .map(([key, value]) => ({ key, ...value, folderPath: repositoryPlacement("modules", key).folderPath }))
    .filter((row) => row.kind === kind && row.status === "archived");
}

function schemaDefaults(schema = {}) {
  return forms.schemaDefaults(schema);
}

function defaultWireName(portName) {
  const suffix = Date.now().toString(36).slice(-5);
  return `${portName}_${suffix}`;
}

function defaultInputWire(portName) {
  return "";
}

function defaultPortInputs(module) {
  return Object.fromEntries(Object.keys(module?.ports?.inputs || {}).map((name) => [name, defaultInputWire(name)]));
}

function pipelineDataKeyOptions() {
  const observationInput = state.pipelineDraft?.config?.observationInput || {};
  const blacklist = observationInput.blacklist || [];
  const options = new Map(
    (observationInput.whitelist || [])
      .filter((path) => !blacklist.some((blocked) => path === blocked || path.startsWith(`${blocked}.`)))
      .map((path) => [path, {
        value: path,
        label: path,
        dataType: "Observation path",
      }]),
  );
  const outputContracts = {};
  Object.values(state.pipelineDraft?.instances || {}).forEach((instance) => {
    const definition = Object.values(state.pipelineModules || {}).find((item) => (
      item.kind === instance.kind
      && item.moduleId === instance.moduleId
      && String(item.version) === String(instance.version)
    ));
    Object.entries(instance.outputs || {}).forEach(([portName, dataPath]) => {
      if (!dataPath) return;
      outputContracts[dataPath] = definition?.ports?.outputs?.[portName]?.schema || {};
    });
  });
  Object.entries(window.TradeChartCore.expandSchemaPaths(outputContracts))
    .forEach(([value, schema]) => options.set(value, { value, label: value, schema }));
  return [...options.values()]
    .sort((left, right) => left.value.localeCompare(right.value));
}

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

function defaultPortOutputs(module) {
  const used = new Set(state.pipelineDraft?.config?.observationInput?.whitelist || []);
  Object.values(state.pipelineDraft?.instances || {}).forEach((instance) => {
    Object.values(instance?.outputs || {}).forEach((dataKey) => dataKey && used.add(dataKey));
  });
  const namespace = semanticDataKeySegment(module?.name || module?.kind);
  return Object.fromEntries(Object.keys(module?.ports?.outputs || {}).map((name) => {
    const base = `${namespace}.${semanticDataKeySegment(name, "output")}`;
    let candidate = base;
    let suffix = 2;
    while (used.has(candidate)) candidate = `${base}.${suffix++}`;
    used.add(candidate);
    return [name, candidate];
  }));
}

function draftInstanceIdSet() {
  const used = new Set(Object.keys(state.pipelineDraft?.instances || {}));
  const alphaGraph = state.pipelineDraft?.alphaGraph || alphaGraphObject();
  (alphaGraph?.nodes || []).forEach((instanceId) => used.add(instanceId));
  Object.values(state.pipelineDraft?.stages || {}).flat().forEach((instanceId) => instanceId && used.add(instanceId));
  return used;
}

function uniqueDraftInstanceId() {
  const base = opaqueClientId("inst");
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
  return parsePipelineAlphaGraphValue();
}

function alphaGraphNodeIds(source = null) {
  const graph = source?.alphaGraph || source || alphaGraphObject();
  return Array.isArray(graph?.nodes) ? graph.nodes.filter(Boolean) : [];
}

function syncSignalStageWithAlphaGraph(target) {
  if (!target) return target;
  target.stages ||= {};
  target.alphaGraph ||= { nodes: [], inputs: {}, outputs: {} };
  target.alphaGraph.inputs ||= {};
  target.alphaGraph.outputs ||= {};
  return target;
}

function syncPipelineAlphaGraphFieldFromDraft() {
  const field = pipelineField("AlphaGraph");
  if (!field || !state.pipelineDraft) return;
  field.value = JSON.stringify(state.pipelineDraft.alphaGraph || { nodes: [], inputs: {}, outputs: {} }, null, 2);
  setPipelineAlphaGraphError("");
}

function alphaGraphSnapshotFromDraft() {
  state.pipelineDraft ||= clonePipelineDraft(pipelineEditorState.definition || {});
  syncSignalStageWithAlphaGraph(state.pipelineDraft);
  return {
    instances: { ...(state.pipelineDraft.instances || {}) },
    alphaGraph: JSON.parse(JSON.stringify(state.pipelineDraft.alphaGraph || { nodes: [], inputs: {}, outputs: {} })),
  };
}

function syncAlphaGraphBuilderFromDraft({ recordHistory = false } = {}) {
  const root = alphaGraphBuilderRoot();
  if (!root?.__syncFromAlphaGraphSnapshot) return false;
  root.__syncFromAlphaGraphSnapshot(alphaGraphSnapshotFromDraft(), { recordHistory });
  return true;
}

function addSignalInstanceToAlphaGraph(instanceId, options = {}) {
  if (!instanceId) return false;
  const { render = true, syncBuilder = true } = options;
  state.pipelineDraft ||= clonePipelineDraft(pipelineEditorState.definition || {});
  state.pipelineDraft.alphaGraph ||= { nodes: [], inputs: {}, outputs: {} };
  const nodes = alphaGraphNodeIds(state.pipelineDraft.alphaGraph);
  const before = nodes.length;
  state.pipelineDraft.alphaGraph.nodes = [...new Set([...nodes, instanceId])];
  syncSignalStageWithAlphaGraph(state.pipelineDraft);
  syncPipelineAlphaGraphFieldFromDraft();
  if (syncBuilder) syncAlphaGraphBuilderFromDraft({ recordHistory: state.pipelineDraft.alphaGraph.nodes.length !== before });
  syncPipelineSaveActionState();
  renderBlueprintMeta();
  if (render) renderPipelineBuilder();
  return state.pipelineDraft.alphaGraph.nodes.length !== before;
}

function alphaGraphNodeEntries(instancesMap = draftInstances(), graph = alphaGraphObject()) {
  return alphaGraphNodeIds(graph)
    .map((instanceId) => {
      const instance = (instancesMap || {})[instanceId];
      if (!instance) return null;
      return { instanceId, instance };
    })
    .filter(Boolean);
}

function parsePipelineAlphaGraphValue({ reportError = true } = {}) {
  try {
    const parsed = JSON.parse(pipelineField("AlphaGraph").value || '{"nodes":[],"inputs":{},"outputs":{}}');
    if (reportError) setPipelineAlphaGraphError("");
    return parsed;
  } catch (error) {
    const message = error?.message || "Invalid Signal Graph";
    if (reportError) setPipelineAlphaGraphError(message);
    throw localUiError(message, "PIPELINE_ALPHA_GRAPH_PARSE");
  }
}

function clonePipelineDraft(definition = {}) {
  const stages = {};
  PIPELINE_MODULE_STAGES.forEach(({ stage }) => {
    stages[stage] = [...(definition.stages?.[stage] || [])];
  });
  return syncSignalStageWithAlphaGraph({
    stages,
    instances: { ...(definition.instances || {}) },
    alphaGraph: JSON.parse(JSON.stringify(definition.signalGraph || { nodes: [], inputs: {}, outputs: {} })),
    config: JSON.parse(JSON.stringify(definition.config || {
      observationInput: { whitelist: [], blacklist: [] },
    })),
    meta: {
      pipelineId: definition.pipelineId || pipelineEditorState.pipelineId,
      name: definition.name || "",
    },
  });
}

function pipelinePinnedInstanceIds(draft = {}) {
  const ids = new Set();
  PIPELINE_MODULE_STAGES.forEach(({ stage }) => {
    (draft?.stages?.[stage] || []).forEach((instanceId) => ids.add(instanceId));
  });
  return ids;
}

function sanitizePipelineDraft(draft, definition = {}) {
  const base = clonePipelineDraft(definition || {});
  const definitionGraph = definition.signalGraph || { nodes: [], inputs: {}, outputs: {} };
  const next = {
    ...base,
    ...(draft || {}),
    stages: { ...base.stages, ...((draft || {}).stages || {}) },
    config: JSON.parse(JSON.stringify((draft || {}).config || base.config)),
    instances: { ...((draft || {}).instances || {}) },
    alphaGraph: JSON.parse(JSON.stringify((draft || {}).alphaGraph || definitionGraph)),
    meta: {
      ...base.meta,
      ...((draft || {}).meta || {}),
    },
  };
  syncSignalStageWithAlphaGraph(next);
  return next;
}

function loadPipelineFormFromDefinition(options = {}) {
  const {
    preferDraft = true,
    discardDraft = false,
    sourceDefinition = null,
  } = options;
  document.querySelector("#alphaGraphBuilder")?.__flushPendingEmit?.();
  const definition = pipelineEditorState.definition || {};
  const draftSource = sourceDefinition || definition;
  const pipelineId = definition.pipelineId || pipelineEditorState.pipelineId;
  state.pipelineDraft = sourceDefinition
    ? sanitizePipelineDraft(clonePipelineDraft(draftSource), draftSource)
    : (preferDraft && !discardDraft && state.pipelineDraft?.meta?.pipelineId === pipelineId
      ? sanitizePipelineDraft(state.pipelineDraft, definition)
      : sanitizePipelineDraft(clonePipelineDraft(definition), definition));
  if (sourceDefinition) {
    state.pipelineDraft.meta = {
      pipelineId: sourceDefinition.pipelineId || definition.pipelineId || pipelineEditorState.pipelineId,
      name: sourceDefinition.name || sourceDefinition.pipelineId || "",
    };
  }
  const meta = state.pipelineDraft.meta || {};
  pipelineField("Id").value = meta.pipelineId || definition.pipelineId || pipelineEditorState.pipelineId;
  pipelineField("Name").value = meta.name || definition.name || "";
  const observationInput = state.pipelineDraft.config?.observationInput || { whitelist: [], blacklist: [] };
  renderObservationEditor("Whitelist", observationInput.whitelist || []);
  renderObservationEditor("Blacklist", observationInput.blacklist || []);
  pipelineField("AlphaGraph").value = JSON.stringify(state.pipelineDraft.alphaGraph || { nodes: [], inputs: {}, outputs: {} }, null, 2);
  setPipelineSaveError("");
  setPipelineAlphaGraphError("");
  syncPipelineSaveActionState();
  const shouldRenderAlphaBuilder = currentPipelineSection === "signal"
    || String(location.pathname || "").toLowerCase() === "/signal-blueprint";
  if (shouldRenderAlphaBuilder) {
    renderAlphaGraphBuilder({ flushBeforeCleanup: preferDraft });
  } else {
    unmountAlphaGraphBuilder({ flushPending: false });
  }
  renderBlueprintMeta();
  renderPipelineBuilder();
  if (String(location.pathname || "").toLowerCase() === "/signal-blueprint") {
    currentPipelineSection = "signal";
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
  const instanceId = uniqueDraftInstanceId();
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
  state.pipelineDraft ||= clonePipelineDraft(pipelineEditorState.definition || {});
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
  root.__moduleGraphCleanup?.({ flushPending: flushBeforeCleanup });
  const modules = moduleDefinitionsByKind("Signal")
    .filter((row) => Object.keys(row.ports?.inputs || {}).length || Object.keys(row.ports?.outputs || {}).length)
    .sort((a, b) => (a.moduleId || "").localeCompare(b.moduleId || ""));
  if (!modules.length) {
    root.innerHTML = '<div class="muted">No Signal graph modules available</div>';
    return;
  }
  syncPipelineAlphaGraphFieldFromDraft();
  const graph = alphaGraphObject();
  const blueprintImpl = window.ModuleGraphLiteGraph;
  blueprintImpl?.mount({
    root,
    modules,
    instances: draftInstances(),
    alphaGraph: graph,
    versions: pipelineEditorState.versions || [],
    loadedVersion: pipelineEditorState.loadedVersion,
    meta: {
      pipelineId: pipelineField("Id")?.value?.trim() || state.pipelineDraft?.meta?.pipelineId || pipelineEditorState.definition?.pipelineId || pipelineEditorState.pipelineId,
      name: pipelineField("Name")?.value?.trim() || state.pipelineDraft?.meta?.name || pipelineEditorState.definition?.name || "",
    },
    actions: {
      onValidate: () => postJson("/api/graphs/validate", {
        resourceType: "pipeline",
        draft: buildPipelinePayload(),
      }),
    },
    onChange(next) {
      state.pipelineDraft ||= clonePipelineDraft(pipelineEditorState.definition || {});
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
      state.pipelineDraft.alphaGraph = next.alphaGraph || { nodes: [], inputs: {}, outputs: {} };
      syncSignalStageWithAlphaGraph(state.pipelineDraft);
      pipelineField("AlphaGraph").value = JSON.stringify(next.alphaGraph || { nodes: [], inputs: {}, outputs: {} }, null, 2);
      setPipelineSaveError("");
      invalidateBacktestBuild("Pipeline modules changed · Build again before running");
      setPipelineAlphaGraphError("");
      syncPipelineSaveActionState();
      renderBlueprintMeta();
    },
    moduleKind: "Signal",
    graphLabel: "Signal Graph",
    contextLabel: "Pipeline · Signal",
    storageNamespace: "signal",
  });
}

function renderAnalysisBrowser() {
  const total = repositoryCatalog("analyses")?.total || 0;
  if ($("analysisRepositoryStatus")) {
    $("analysisRepositoryStatus").textContent = `${total} Analysis resource(s) · Latest Version shown · Open one to edit or switch Version`;
  }
  if ($("analysisModuleStatus")) {
    $("analysisModuleStatus").textContent = `${repositoryCatalog("analysis-modules")?.total || 0} module(s)`;
  }
  renderEmbeddedRepositoryBrowser("analyses");
  renderEmbeddedRepositoryBrowser("analysis-modules");
}

function syncAnalysisBlueprintRoute() {
  if (currentView !== "analysis" || currentAnalysisSection !== "blueprint") return;
  const target = pathForView("analysis");
  if (`${location.pathname}${location.search}` !== target) {
    history.replaceState({ viewId: "analysis", analysisKey: analysisEditorState.analysisKey }, "", target);
  }
  syncRouteChrome();
}

function openAnalysisBlueprint(analysisKey, { returnView = "analysis" } = {}) {
  analysisEditorState.analysisKey = analysisKey || "";
  analysisEditorState.returnView = returnView === "backtests" ? "backtests" : "analysis";
  currentAnalysisSection = "blueprint";
  return switchView("analysis");
}

function closeAnalysisBlueprint() {
  const returnView = analysisEditorState.returnView;
  currentAnalysisSection = "browser";
  analysisEditorState.returnView = "analysis";
  return switchView(returnView === "backtests" ? "backtests" : "analysis");
}

function renderAnalysisDetails() {
  if (currentAnalysisSection !== "blueprint") return;
  const [analysisId, version] = analysisEditorState.analysisKey.split("::");
  const analysis = state.analyses.find((row) => (
    row.analysisId === analysisId && String(row.version) === String(version)
  ));
  if (!analysis) {
    analysisEditorState.analysisKey = "";
    currentAnalysisSection = "browser";
    history.replaceState({ viewId: "analysis" }, "", pathForView("analysis"));
    renderAnalysisBrowser();
    syncRouteChrome();
    return;
  }
  analysisEditorState.analysisKey = `${analysis.analysisId}::${analysis.version}`;
  syncAnalysisBlueprintRoute();
  const key = analysisEditorState.analysisKey;
  const draft = analysisEditorState.draftsByAnalysis[key] ||= {
    analysisId: analysis.builtin ? "" : (analysis.analysisId || ""),
    name: analysis.builtin ? `${analysis.name || "Analysis"} Copy` : (analysis.name || analysis.analysisId || ""),
    instances: structuredClone(analysis.instances || {}),
    graph: structuredClone(analysis.graph || { nodes: [], inputs: {}, outputs: {} }),
  };
  const root = $("analysisGraphBuilder");
  root.__flushPendingEmit?.();
  root.__moduleGraphCleanup?.();
  const modules = Object.entries(state.analysisModules || {})
    .filter(([, definition]) => definition.status === "archived")
    .map(([moduleKey, definition]) => ({
      key: moduleKey,
      ...definition,
      folderPath: repositoryPlacement("analysis-modules", moduleKey).folderPath,
    }));
  const analysisVersions = state.analyses.filter((row) => row.analysisId === analysis.analysisId);
  const returnsToBacktests = analysisEditorState.returnView === "backtests";
  window.ModuleGraphLiteGraph?.mount({
    root,
    modules,
    moduleKind: "Analyzer",
    graphLabel: "Analysis Graph",
    contextLabel: "Analysis",
    backLabel: returnsToBacktests ? "Back to Backtest Entry" : "Back to Analyses",
    storageNamespace: "analysis",
    defaultInputSourceLabel: "Current Sample + prior Pipeline",
    inputSources: {
      currentPipeline: "Current completed Pipeline",
    },
    versions: analysisVersions,
    loadedVersion: analysis.version,
    instances: draft.instances,
    alphaGraph: draft.graph,
    meta: { contextId: key, name: analysis.name || analysis.analysisId || "Analysis" },
    resourceEditor: {
      title: "Analysis Details",
      description: "Identity and name for the next saved Version",
      fields: [
        {
          key: "analysisId",
          label: "Analysis ID",
          value: draft.analysisId,
          placeholder: "analysis-id",
          required: true,
        },
        {
          key: "name",
          label: "Name",
          value: draft.name,
          placeholder: "Analysis name",
          required: true,
        },
      ],
      contextName: (values) => values.name,
      onChange: (values) => {
        draft.analysisId = values.analysisId;
        draft.name = values.name;
      },
    },
    actions: {
      onBack: closeAnalysisBlueprint,
      onLoad: (nextVersion) => {
        analysisEditorState.analysisKey = `${analysis.analysisId}::${nextVersion}`;
        renderAnalysisDetails();
      },
      onValidate: () => postJson("/api/graphs/validate", {
        resourceType: "analysis",
        draft: {
          schemaVersion: 1,
          analysisId: draft.analysisId.trim(),
          name: draft.name.trim(),
          description: analysis.description || "",
          instances: draft.instances,
          graph: draft.graph,
        },
      }),
      onSave: async () => {
        const nextId = draft.analysisId.trim();
        const name = draft.name.trim();
        if (!nextId) throw new Error("Analysis ID is required");
        if (!name) throw new Error("Analysis name is required");
        const response = await postJson("/api/analyses", {
          schemaVersion: 1,
          analysisId: nextId,
          name,
          description: analysis.description || "",
          instances: draft.instances,
          graph: draft.graph,
        });
        loadedViews.delete("analyses");
        await Promise.all([
          loadAnalyses(true),
          loadRepositoryCatalog("analyses", true),
        ]);
        analysisEditorState.analysisKey = `${nextId}::${response.definition.version}`;
        backtestEntryState.analysisKey = analysisEditorState.analysisKey;
        renderAnalysisDetails();
        return response;
      },
    },
    onChange(next) {
      draft.instances = structuredClone(next.instances || {});
      draft.graph = structuredClone(next.alphaGraph || { nodes: [], inputs: {}, outputs: {} });
      invalidateBacktestBuild("Analysis modules changed · Build again before running");
    },
  });
}

function renderBlueprintMeta() {
  const node = $("blueprintMeta");
  if (!node) return;
  const definition = pipelineEditorState.definition || {};
  const pipelineId = pipelineField("Id")?.value?.trim() || definition.pipelineId || pipelineEditorState.pipelineId;
  const pipelineName = pipelineField("Name")?.value?.trim() || definition.name || pipelineId;
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
    { label: "Pipeline", value: pipelineName, meta: pipelineId },
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
  const instanceId = uniqueDraftInstanceId();
  const dialog = $("moduleLoadDialog");
  const instanceInput = $("moduleLoadInstanceId");
  $("moduleLoadDialogTitle").textContent = `Load ${kind}: ${module.name || "Untitled Module"}`;
  instanceInput.value = instanceId;
  setModuleLoadDialogError("");
  forms.renderSchemaFields($("moduleLoadConfigFields"), module.configSchema, schemaDefaults(module.configSchema));
  const inputDefinitions = Object.entries(module.ports?.inputs || {}).map(([name, spec]) => ({
    name,
    label: forms.humanizeName(name),
    type: "dataKey",
    description: `${JSON.stringify(spec?.schema)} / ${spec?.required === false ? "optional" : "required"}`,
  }));
  forms.renderParamFields(
    $("moduleLoadInputsFields"),
    inputDefinitions,
    defaultPortInputs(module),
    Object.fromEntries(Object.keys(module.ports?.inputs || {}).map((name) => [
      name,
      pipelineDataKeyOptions(),
    ])),
  );
  forms.renderPortFields($("moduleLoadOutputsFields"), module.ports?.outputs || {}, {}, defaultPortOutputs(module));
  dialog.oninput = (event) => {
    if (!event.target.closest(".dialog-form")) return;
    setModuleLoadDialogError("");
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
    inputs = forms.readParamFields(
      $("moduleLoadInputsFields"),
      Object.keys(module.ports?.inputs || {}).map((name) => ({ name, type: "dataKey" })),
    );
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
  };
  draftInstances()[payload.instanceId] = payload;
  setModuleLoadDialogError("");
  $("moduleLoadDialog").close();
  pendingModuleLoad = null;
  if (target.type === "stage") {
    loadStageInstance(target.stage, payload.instanceId);
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
  state.pipelineDraft ||= clonePipelineDraft(pipelineEditorState.definition || {});
  if (stage === "signal") {
    addSignalInstanceToAlphaGraph(instanceId);
    invalidateBacktestBuild("Pipeline modules changed · Build again before running");
    return;
  } else {
    const current = state.pipelineDraft.stages[stage] || [];
    state.pipelineDraft.stages[stage] = MULTI_STAGE.has(stage)
      ? [...new Set([...current, instanceId])]
      : [instanceId];
  }
  invalidateBacktestBuild("Pipeline modules changed · Build again before running");
  renderBlueprintMeta();
  renderPipelineBuilder();
}

function unloadStageInstance(stage, instanceId) {
  state.pipelineDraft ||= clonePipelineDraft(pipelineEditorState.definition || {});
  if (stage === "signal") {
    const instances = state.pipelineDraft.instances || {};
    const removedInstance = instances[instanceId];
    const removedWires = new Set(Object.values(removedInstance?.outputs || {}).filter(Boolean));
    state.pipelineDraft.alphaGraph ||= { nodes: [], inputs: {}, outputs: {} };
    state.pipelineDraft.alphaGraph.nodes = (state.pipelineDraft.alphaGraph.nodes || []).filter((value) => value !== instanceId);
    state.pipelineDraft.alphaGraph.outputs = Object.fromEntries(
      Object.entries(state.pipelineDraft.alphaGraph.outputs || {})
        .filter(([, boundary]) => !removedWires.has(boundary?.wire)),
    );
    syncSignalStageWithAlphaGraph(state.pipelineDraft);
  } else {
    state.pipelineDraft.stages[stage] = (state.pipelineDraft.stages[stage] || []).filter((value) => value !== instanceId);
  }
  if (state.pipelineDraft.instances?.[instanceId]) delete state.pipelineDraft.instances[instanceId];
  if (stage === "signal") {
    syncPipelineAlphaGraphFieldFromDraft();
    syncAlphaGraphBuilderFromDraft({ recordHistory: true });
  }
  invalidateBacktestBuild("Pipeline modules changed · Build again before running");
  renderBlueprintMeta();
  renderPipelineBuilder();
}

function graphDefaultPositions() {
  const positions = {
    "stage:universe": { x: 32, y: 120 },
    "stage:signal": { x: 282, y: 120 },
    "stage:target": { x: 532, y: 120 },
    "stage:constraint": { x: 782, y: 120 },
  };
  return Object.fromEntries(Object.entries(positions).map(([key, position]) => [
    key,
    {
      x: position.x + PIPELINE_CANVAS_ORIGIN.x,
      y: position.y + PIPELINE_CANVAS_ORIGIN.y,
    },
  ]));
}

function migratePipelineGraphSpace() {
  try {
    if (localStorage.getItem(GRAPH_SPACE_VERSION_KEY) === GRAPH_SPACE_VERSION) return;
    const stored = JSON.parse(localStorage.getItem(GRAPH_POSITIONS_KEY) || "{}");
    const migrated = Object.fromEntries(Object.entries(stored).map(([key, position]) => [
      key,
      {
        x: Number(position?.x || 0) + PIPELINE_CANVAS_ORIGIN.x,
        y: Number(position?.y || 0) + PIPELINE_CANVAS_ORIGIN.y,
      },
    ]));
    localStorage.setItem(GRAPH_POSITIONS_KEY, JSON.stringify(migrated));
    localStorage.setItem(GRAPH_SPACE_VERSION_KEY, GRAPH_SPACE_VERSION);
  } catch {
    try {
      localStorage.setItem(GRAPH_SPACE_VERSION_KEY, GRAPH_SPACE_VERSION);
    } catch {}
  }
}

function graphStoredPositions() {
  try {
    return JSON.parse(localStorage.getItem(GRAPH_POSITIONS_KEY) || "{}");
  } catch {
    return {};
  }
}

function graphPosition(nodeId) {
  migratePipelineGraphSpace();
  const defaults = graphDefaultPositions();
  const stored = graphStoredPositions();
  return stored[nodeId] || defaults[nodeId] || { x: 32, y: 32 };
}

function saveGraphPosition(nodeId, position) {
  const stored = graphStoredPositions();
  stored[nodeId] = position;
  localStorage.setItem(GRAPH_POSITIONS_KEY, JSON.stringify(stored));
}

function clampPipelineScale(scale) {
  return Math.min(PIPELINE_VIEWPORT_MAX_SCALE, Math.max(PIPELINE_VIEWPORT_MIN_SCALE, scale));
}

function applyPipelineViewport() {
  const viewport = state.pipelineViewport;
  const canvas = $("pipelineFlowCanvas");
  const label = $("pipelineZoomLabel");
  const fullscreenButton = $("pipelineFullscreenBtn");
  const infoTab = $("pipelineInfoTabBtn");
  if (canvas) {
    canvas.style.transform = `translate(${viewport.x}px, ${viewport.y}px) scale(${viewport.scale})`;
  }
  if (label) label.textContent = `${Math.round(viewport.scale * 100)}%`;
  if (fullscreenButton) {
    fullscreenButton.textContent = viewport.fullscreen ? "Exit Fullscreen" : "Fullscreen";
    fullscreenButton.setAttribute("aria-pressed", viewport.fullscreen ? "true" : "false");
  }
  if (infoTab) {
    infoTab.textContent = viewport.infoCollapsed ? "▼" : "▲";
    infoTab.setAttribute("aria-expanded", viewport.infoCollapsed ? "false" : "true");
    infoTab.title = viewport.infoCollapsed ? "Show builder controls" : "Hide builder controls";
  }
  document.body.classList.toggle("pipeline-builder-fullscreen", !!viewport.fullscreen);
  document.body.classList.toggle("pipeline-builder-info-collapsed", !!viewport.infoCollapsed);
}

function fitPipelineViewportToNodes() {
  const viewportEl = $("pipelineFlowViewport");
  const nodes = [...document.querySelectorAll("#pipelineStageGrid .flow-node")];
  if (!viewportEl || !nodes.length) {
    state.pipelineViewport.scale = 0.8;
    state.pipelineViewport.x = 40;
    state.pipelineViewport.y = 40;
    return;
  }
  const bounds = nodes.reduce((acc, node) => {
    const left = parseFloat(node.style.left || "0");
    const top = parseFloat(node.style.top || "0");
    const right = left + (node.offsetWidth || GRAPH_NODE_SIZE.width);
    const bottom = top + (node.offsetHeight || GRAPH_NODE_SIZE.height);
    return {
      left: Math.min(acc.left, left),
      top: Math.min(acc.top, top),
      right: Math.max(acc.right, right),
      bottom: Math.max(acc.bottom, bottom),
    };
  }, {
    left: Number.POSITIVE_INFINITY,
    top: Number.POSITIVE_INFINITY,
    right: Number.NEGATIVE_INFINITY,
    bottom: Number.NEGATIVE_INFINITY,
  });
  const rect = viewportEl.getBoundingClientRect();
  const graphWidth = Math.max(bounds.right - bounds.left, 1);
  const graphHeight = Math.max(bounds.bottom - bounds.top, 1);
  const scale = clampPipelineScale(Math.min(
    1,
    (rect.width - 96) / graphWidth,
    (rect.height - 96) / graphHeight,
  ));
  state.pipelineViewport.scale = scale;
  state.pipelineViewport.x = Math.round((rect.width - graphWidth * scale) / 2 - bounds.left * scale);
  state.pipelineViewport.y = Math.round((rect.height - graphHeight * scale) / 2 - bounds.top * scale);
}

function resetPipelineViewport() {
  fitPipelineViewportToNodes();
  applyPipelineViewport();
}

function togglePipelineFullscreen(force = null) {
  const nextFullscreen = force === null ? !state.pipelineViewport.fullscreen : !!force;
  state.pipelineViewport.fullscreen = nextFullscreen;
  state.pipelineViewport.infoCollapsed = nextFullscreen;
  applyPipelineViewport();
  requestAnimationFrame(() => {
    const board = $("pipelineStageGrid");
    if (board) drawGraphEdges(board);
    fitPipelineViewportToNodes();
    applyPipelineViewport();
  });
}

function togglePipelineInfoCollapsed(force = null) {
  state.pipelineViewport.infoCollapsed = force === null ? !state.pipelineViewport.infoCollapsed : !!force;
  applyPipelineViewport();
  requestAnimationFrame(() => {
    const board = $("pipelineStageGrid");
    if (board) drawGraphEdges(board);
  });
}

function arrangePipelineGraph() {
  const board = $("pipelineStageGrid");
  if (!board) return;
  migratePipelineGraphSpace();
  Object.entries(graphDefaultPositions()).forEach(([nodeId, position]) => {
    const node = board.querySelector(`[data-node-id="${nodeId}"]`);
    if (!node) return;
    node.style.left = `${position.x}px`;
    node.style.top = `${position.y}px`;
    saveGraphPosition(nodeId, position);
  });
  drawGraphEdges(board);
  fitPipelineViewportToNodes();
  applyPipelineViewport();
}

function zoomPipelineViewport(event) {
  const viewportEl = $("pipelineFlowViewport");
  if (!viewportEl) return;
  if (event.target.closest(".loaded-tags-scroll")) return;
  event.preventDefault();
  const viewport = state.pipelineViewport;
  const rect = viewportEl.getBoundingClientRect();
  const pointerX = event.clientX - rect.left;
  const pointerY = event.clientY - rect.top;
  const beforeX = (pointerX - viewport.x) / viewport.scale;
  const beforeY = (pointerY - viewport.y) / viewport.scale;
  const nextScale = clampPipelineScale(viewport.scale * Math.exp(-event.deltaY * 0.001));
  viewport.x = pointerX - beforeX * nextScale;
  viewport.y = pointerY - beforeY * nextScale;
  viewport.scale = nextScale;
  applyPipelineViewport();
}

function bindPipelineViewportControls() {
  const viewportEl = $("pipelineFlowViewport");
  if (!viewportEl || viewportEl.__pipelineViewportControlsBound) {
    applyPipelineViewport();
    return;
  }
  viewportEl.__pipelineViewportControlsBound = true;
  viewportEl.addEventListener("wheel", zoomPipelineViewport, { passive: false });
  viewportEl.addEventListener("pointerdown", (event) => {
    if (pipelineBlueprintBusyMessage() || event.button !== 0) return;
    if (event.target.closest(".flow-node, button, select, input, textarea")) return;
    event.preventDefault();
    const viewport = state.pipelineViewport;
    const start = { clientX: event.clientX, clientY: event.clientY, x: viewport.x, y: viewport.y };
    viewportEl.setPointerCapture(event.pointerId);
    viewportEl.classList.add("panning");
    const onMove = (moveEvent) => {
      viewport.x = start.x + moveEvent.clientX - start.clientX;
      viewport.y = start.y + moveEvent.clientY - start.clientY;
      applyPipelineViewport();
    };
    const onUp = () => {
      viewportEl.classList.remove("panning");
      viewportEl.removeEventListener("pointermove", onMove);
      viewportEl.removeEventListener("pointerup", onUp);
      viewportEl.removeEventListener("pointercancel", onUp);
    };
    viewportEl.addEventListener("pointermove", onMove);
    viewportEl.addEventListener("pointerup", onUp);
    viewportEl.addEventListener("pointercancel", onUp);
  });
  applyPipelineViewport();
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
    ["stage:universe", "stage:signal", "pipeline"],
    ["stage:signal", "stage:target", "pipeline"],
    ["stage:target", "stage:constraint", "pipeline"],
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
      const scale = state.pipelineViewport?.scale || 1;
      const next = {
        x: Math.min(PIPELINE_CANVAS_SIZE.width - GRAPH_NODE_SIZE.width, Math.max(12, left + (moveEvent.clientX - startX) / scale)),
        y: Math.min(PIPELINE_CANVAS_SIZE.height - GRAPH_NODE_SIZE.height, Math.max(12, top + (moveEvent.clientY - startY) / scale)),
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
  const definition = pipelineEditorState.definition || {};
  syncPipelineLoadActionState();
  syncPipelineBlueprintErrorState();
  if (!state.pipelineDraft) state.pipelineDraft = clonePipelineDraft(definition);
  const stages = state.pipelineDraft.stages || {};
  if (!pipelineField("Id").value) {
    pipelineField("Id").value = definition.pipelineId || pipelineEditorState.pipelineId;
    pipelineField("Name").value = definition.name || "";
    pipelineField("AlphaGraph").value = JSON.stringify(definition.signalGraph || { nodes: [], inputs: {}, outputs: {} }, null, 2);
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
    const helperText = available.length ? `${available.length} archived version(s)` : "No archived Module version";
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
      : `<span class="muted">${stage === "signal" ? "No Signal Modules in Graph" : "No loaded Module"}</span>`;
    const detailsButton = stage === "signal"
      ? '<button class="details-btn" data-open-alpha-details="signal" type="button">Details</button>'
      : "";
    const loadRow = stage === "signal" ? "" : `
      <div class="load-row">
        <select data-load-stage="${stage}" data-stage-kind="${kind}"><option value="">Select module</option></select>
        <button data-load-stage-button="${stage}" type="button">Load</button>
      </div>`;
    const helperRow = `<div class="muted" data-load-helper-stage="${stage}">${stage === "signal" ? pipelineSignalDetailsSummary() : helperText}</div>`;
    group.innerHTML = `
      <div class="component-head">
        <h3>${title}</h3>
        <div class="component-head-actions">
          <span>${stage === "signal" ? "Graph" : `${MULTI_STAGE.has(stage) ? "multi" : "single"} ${kind}`}</span>
          ${detailsButton}
        </div>
      </div>
      <div class="loaded-tags${stage === "signal" ? " loaded-tags-scroll" : ""}">${tags}</div>
      ${loadRow}
      ${helperRow}
    `;
    if (stage !== "signal") appendRepositoryOptions(
        group.querySelector(`[data-load-stage="${CSS.escape(stage)}"]`),
        available,
        (row) => row.key,
        (row) => `${row.moduleId} / ${row.version}`,
        "modules",
      );
    const stageSelect = group.querySelector(`[data-load-stage="${CSS.escape(stage)}"]`);
    if (stageSelect) {
      stageSelect.dataset.repositoryHierarchy = "modules";
      enhanceHierarchicalRepositorySelect(stageSelect);
    }
    placeGraphNode(grid, group, `stage:${stage}`);
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
      switchPipelineSection("signal");
    });
  });
  syncPipelineComposerEditorState();
  syncPipelineDraftFieldState();
  syncPipelineLoadActionState();
  syncPipelineSaveActionState();
  bindPipelineViewportControls();
  requestAnimationFrame(() => {
    if (!state.pipelineViewport.initialized) {
      fitPipelineViewportToNodes();
      state.pipelineViewport.initialized = true;
    }
    applyPipelineViewport();
    drawGraphEdges(grid);
  });

  if (currentPipelinePage === "browser") renderEmbeddedRepositoryBrowser("pipelines");
}

function buildPipelinePayload() {
  const pipelineId = pipelineField("Id").value.trim();
  if (!pipelineId) {
    const message = "Pipeline metadata is incomplete";
    setPipelineSaveError(message);
    throw localUiError(message, "PIPELINE_DEFINITION_VALIDATION");
  }
  const stages = {};
  PIPELINE_MODULE_STAGES.forEach(({ stage }) => {
    stages[stage] = [...(state.pipelineDraft?.stages?.[stage] || [])];
  });
  setPipelineSaveError("");
  return {
    pipelineId,
    name: pipelineField("Name").value.trim() || pipelineId,
    stages,
    instances: { ...(state.pipelineDraft?.instances || {}) },
    signalGraph: parsePipelineAlphaGraphValue(),
    config: { observationInput: pipelineObservationInputFromFields() },
  };
}

async function saveCurrentPipelineVersion({ redirect = false } = {}) {
  document.querySelector("#alphaGraphBuilder")?.__flushPendingEmit?.();
  const lifecycle = pipelineModuleLifecycleReport();
  if (lifecycle.errors.length) {
    throw localUiError(lifecycle.errors.join(" | "), "PIPELINE_MODULE_UNAVAILABLE");
  }
  if (lifecycle.warnings.length && !window.confirm(
    `Module Version warning:\n\n${lifecycle.warnings.join("\n")}\n\nSave this Pipeline Version anyway?`
  )) {
    throw localUiError("Save cancelled because Module Version warnings were not accepted.", "PIPELINE_MODULE_WARNING_CANCELLED");
  }
  const payload = buildPipelinePayload();
  const response = await postJson(
    `/api/pipelines/${encodeURIComponent(payload.pipelineId)}/versions`,
    payload,
  );
  pipelineEditorState.pipelineId = response.pipelineId || payload.pipelineId;
  pipelineField("Id").value = pipelineEditorState.pipelineId;
  loadedViews.delete("pipeline");
  if (redirect) {
    switchView("overview");
  } else {
    await loadPipeline(true);
    $("pipelineStatus").textContent = `Saved v${response.version}`;
    switchPipelineSection(currentPipelineSection);
  }
  return response;
}

async function loadSelectedPipelineVersion(version) {
  const pipelineId = pipelineEditorState.pipelineId;
  if (!pipelineId || !version) throw new Error("Select a Pipeline version");
  const versionSummary = (pipelineEditorState.versions || []).find((row) => row.version === version);
  if (!versionSummary) throw new Error(`Unknown Pipeline version '${version}'`);
  setPipelineBlueprintBusyState({ reloadInFlight: true });
  try {
    const sourceDefinition = versionSummary.current
      ? pipelineEditorState.definition
      : (await getJson(
        `/api/pipelines/${encodeURIComponent(pipelineId)}/versions/${encodeURIComponent(version)}`,
      )).definition;
    pipelineEditorState.loadedVersion = version;
    pipelineEditorState.loadedDefinition = structuredClone(sourceDefinition || null);
    renderPipelineVersionSelector();
    loadPipelineFormFromDefinition({
      preferDraft: false,
      discardDraft: true,
      sourceDefinition,
    });
    $("pipelineStatus").textContent = versionSummary.current
      ? `Loaded Current · v${versionSummary.version}`
      : `Loaded v${versionSummary.version} as draft`;
  } finally {
    setPipelineBlueprintBusyState({ reloadInFlight: false });
  }
}

window.__tradePipelineActions = {
  buildPayload: buildPipelinePayload,
  saveCurrentPipelineVersion,
  loadPipelineFormFromDefinition,
  loadPipelineVersion: loadSelectedPipelineVersion,
  backToPipeline: () => {
    currentPipelineSection = "composer";
    switchView("pipeline");
  },
};

["Id", "Name"].forEach((fieldId) => {
  pipelineField(fieldId)?.addEventListener("input", () => {
    if (pipelineField(fieldId)?.disabled) return;
    state.pipelineDraft ||= clonePipelineDraft(pipelineEditorState.definition || {});
    state.pipelineDraft.meta = {
      ...(state.pipelineDraft.meta || {}),
      pipelineId: pipelineField("Id").value.trim() || pipelineEditorState.definition?.pipelineId || pipelineEditorState.pipelineId,
      name: pipelineField("Name").value.trim(),
    };
    setPipelineSaveError("");
    syncPipelineSaveActionState();
    renderBlueprintMeta();
  });
});

["Whitelist", "Blacklist"].forEach(bindObservationEditor);
bindObservationBatchDialog();

function renderData() {
  renderEmbeddedRepositoryBrowser("data");
  const activeDatasets = state.datasets.filter((row) => row.status === "active");
  renderSelectOptions(
    "backtestDataset",
    activeDatasets,
    (row) => row.datasetId,
    (row) => datasetCatalogLabel(row),
    "datasets",
    "Select a Dataset",
  );
  ["datasetProcessSources"].forEach((id) => {
    const picker = $(id);
    if (!picker) return;
    const selected = new Set([...picker.selectedOptions].map((option) => option.value));
    picker.innerHTML = "";
    appendRepositoryOptions(
      picker,
      activeDatasets,
      (dataset) => dataset.datasetId,
      (dataset) => `${dataset.name || dataset.datasetId} · ${dataset.datasetId}`,
      "datasets",
    );
    [...picker.options].forEach((option) => { option.selected = selected.has(option.value); });
  });
  renderDatasetWorkspacePicker(false);
  renderSelectOptions(
    "datasetScriptWorkspace",
    state.datasetWorkspaces,
    (row) => row.workspaceId,
    (row) => row.name || row.workspaceId,
    "workspaces",
  );
  const processScript = $("datasetProcessScript");
  const previousScript = processScript.value;
  processScript.innerHTML = "";
  state.datasetRecipes.forEach((recipe) => {
    const option = document.createElement("option");
    option.value = `${recipe.recipeId}::${recipe.version}`;
    option.textContent = `${recipe.name || recipe.recipeId} @ ${recipe.version}`;
    option.selected = option.value === previousScript;
    processScript.appendChild(option);
  });
}

async function refreshDatasetScriptWorkspacePaths() {
  const workspaceId = $("datasetScriptWorkspace").value;
  const select = $("datasetScriptWorkspacePath");
  select.innerHTML = "";
  const statusOption = document.createElement("option");
  statusOption.value = "";
  statusOption.textContent = workspaceId ? "Loading Python scripts…" : "Select a Workspace first";
  select.appendChild(statusOption);
  select.disabled = true;
  if (!workspaceId) return;
  const result = await getJson(`/api/data/workspaces/${encodeURIComponent(workspaceId)}/scripts`);
  select.innerHTML = "";
  (result.scripts || []).forEach((script) => {
    const option = document.createElement("option");
    option.value = script.path;
    option.textContent = script.path;
    select.appendChild(option);
  });
  if (!(result.scripts || []).length) {
    statusOption.textContent = "No Python scripts in this Workspace";
    select.appendChild(statusOption);
    return;
  }
  select.disabled = false;
}

function renderBacktests() {
  renderBacktestPipelineSelector();
  renderSelectOptions(
    "backtestDataset",
    state.datasets.filter((row) => row.status === "active"),
    (row) => row.datasetId,
    (row) => datasetCatalogLabel(row),
    "datasets",
    "Select a Dataset",
  );
  renderSelectOptions(
    "backtestSampler",
    state.samplers,
    (row) => `${row.samplerId}::${row.version}`,
    (row) => `${row.name || row.samplerId} @ ${row.version}`,
    "samplers",
    "Select a Sampler Version",
  );
  renderSelectOptions(
    "backtestEnvironmentSelect",
    state.environments,
    (row) => `${row.environmentId}::${row.version}`,
    (row) => `${row.name || row.environmentId} @ ${row.version}`,
    "environments",
    "Select an Environment Version",
  );
  renderSelectOptions(
    "backtestAnalysisSelect",
    state.analyses,
    (row) => `${row.analysisId}::${row.version}`,
    (row) => `${row.name || row.analysisId} @ ${row.version}`,
    "analyses",
    "Select an Analysis Version",
  );
  renderEmbeddedRepositoryBrowser("backtest");
  $("showArchivedBacktestsBtn").textContent = showArchivedBacktests ? "Hide Archived" : "Show Archived";
  restoreBacktestControlsFromBuildCache();
  renderBacktestChain();
  renderBacktestSamplerParameters();
  renderBacktestEnvironmentParameters();
  renderBacktestJobs();
  syncBacktestRunState();
}

function backtestJobTitle(job) {
  const pipeline = state.pipelines?.[job.pipelineId];
  const dataset = (state.datasets || []).find((row) => row.datasetId === job.datasetId);
  return `${pipeline?.name || job.pipelineId || "Pipeline"} × ${dataset?.name || job.datasetId || "Dataset"}`;
}

function renderBacktestJobs() {
  const list = $("backtestJobList");
  if (!list) return;
  const jobs = state.backtestJobs || [];
  const active = jobs.filter((job) => ["queued", "running"].includes(job.status)).length;
  $("backtestJobCapacity").textContent = state.backtestJobMaxConcurrent
    ? `${active} active · ${state.backtestJobMaxConcurrent} concurrent slots`
    : `${active} active`;
  if (!jobs.length) {
    list.innerHTML = '<div class="backtest-job-empty">No Backtest jobs have been submitted.</div>';
    return;
  }
  list.innerHTML = jobs.map((job) => {
    const total = Number(job.totalCycles || 0);
    const completed = Number(job.completedCycles || 0);
    const percent = job.status === "completed"
      ? 100
      : (total > 0 ? Math.max(0, Math.min(100, (completed / total) * 100)) : 0);
    const progressValue = job.status === "completed" || total > 0
      ? ` value="${percent.toFixed(2)}"`
      : "";
    const phase = job.status === "queued" && job.queuePosition
      ? `Queued · position ${job.queuePosition}`
      : job.phase || job.status;
    const detail = total > 0
      ? `${completed.toLocaleString()} / ${total.toLocaleString()} cycles`
      : (job.status === "queued"
        ? "Waiting for an execution slot"
        : (job.phase === "counting"
          ? "Counting exact Sampler cycles"
          : (job.phase === "running"
            ? `Running · ${completed.toLocaleString()} cycles`
            : "Preparing Backtest runtime")));
    return `<article class="backtest-job" data-backtest-job="${escapeHtml(job.jobId)}">
      <div class="backtest-job-identity">
        <strong title="${escapeHtml(backtestJobTitle(job))}">${escapeHtml(backtestJobTitle(job))}</strong>
        <span class="muted" title="${escapeHtml(job.jobId)}">${escapeHtml(job.jobId)} · ${escapeHtml(formatTime(job.submittedAt))}</span>
      </div>
      <div class="backtest-job-progress-wrap">
        <progress class="backtest-job-progress" max="100"${progressValue} aria-label="${escapeHtml(detail)}"></progress>
        <span class="muted">${escapeHtml(detail)}</span>
      </div>
      <span class="backtest-job-status ${escapeHtml(job.status)}" title="${escapeHtml(phase)}">${escapeHtml(phase)}</span>
      ${job.error ? `<p class="backtest-job-error">${escapeHtml(job.error)}</p>` : ""}
    </article>`;
  }).join("");
}

function scheduleBacktestJobPoll(delay = 1000) {
  clearTimeout(backtestJobPollTimer);
  backtestJobPollTimer = null;
  const hasActiveJobs = (state.backtestJobs || []).some((job) => ["queued", "running"].includes(job.status));
  if (!hasActiveJobs || currentView !== "backtests" || currentBacktestSection !== "entry") return;
  backtestJobPollTimer = setTimeout(() => {
    refreshBacktestJobs().catch((error) => {
      setBacktestEntryError(error.message);
      scheduleBacktestJobPoll(2000);
    });
  }, delay);
}

async function refreshBacktestJobs() {
  const previous = new Map((state.backtestJobs || []).map((job) => [job.jobId, job.status]));
  const response = await getJson("/api/backtest-jobs?limit=50");
  state.backtestJobs = response.jobs || [];
  publishBacktestOperations(state.backtestJobs);
  state.backtestJobMaxConcurrent = Number(response.maxConcurrent || 0);
  renderBacktestJobs();
  const newlyFinished = state.backtestJobs.some((job) => (
    ["queued", "running"].includes(previous.get(job.jobId))
      && ["completed", "failed"].includes(job.status)
  ));
  if (newlyFinished) {
    loadedViews.delete("backtests");
    const [backtests] = await Promise.all([
      getJson(`/api/backtests?limit=50${showArchivedBacktests ? "&includeArchived=true" : ""}`),
      loadRepositoryCatalog("backtest", true),
    ]);
    state.backtests = backtests.backtests || [];
    state.totals.backtests = backtests.total ?? state.backtests.length;
    renderBacktests();
  }
  scheduleBacktestJobPoll();
}

function selectedBacktestSampler() {
  const [samplerId, version] = ($("backtestSampler")?.value || "").split("::");
  return (state.samplers || []).find((row) => row.samplerId === samplerId && String(row.version) === String(version));
}

function selectedBacktestEnvironment() {
  const [environmentId, version] = ($("backtestEnvironmentSelect")?.value || "").split("::");
  return (state.environments || []).find((row) => (
    row.environmentId === environmentId && String(row.version) === String(version)
  ));
}

function selectedBacktestAnalysis() {
  const [analysisId, version] = ($("backtestAnalysisSelect")?.value || "").split("::");
  return (state.analyses || []).find((row) => (
    row.analysisId === analysisId && String(row.version) === String(version)
  ));
}

function inferSamplerParameterSchema(config = {}) {
  const inferValue = (value, root = false) => {
    if (Array.isArray(value)) {
      return {
        type: "array",
        items: value.length ? inferValue(value[0]) : {},
        default: structuredClone(value),
      };
    }
    if (value && typeof value === "object") {
      const entries = Object.entries(value);
      if (!root && entries.length) {
        const children = entries.map(([, child]) => inferValue(child));
        const structural = children.map(({ default: _default, ...schema }) => schema);
        if (structural.every((schema) => JSON.stringify(schema) === JSON.stringify(structural[0]))) {
          return { type: "object", additionalProperties: structural[0], default: structuredClone(value) };
        }
      }
      return {
        type: "object",
        properties: Object.fromEntries(entries.map(([name, child]) => [name, inferValue(child)])),
        additionalProperties: false,
        ...(root ? {} : { default: structuredClone(value) }),
      };
    }
    const type = value === null ? "null" : (Number.isInteger(value) ? "integer" : typeof value);
    return { type, default: value };
  };
  return inferValue(config, true);
}

function selectedSamplerParameterSchema(sampler = selectedBacktestSampler()) {
  return sampler?.parameterSchema || inferSamplerParameterSchema(sampler?.config || {});
}

function backtestGraphNode(nodeId) {
  return document.querySelector(`#backtestChain [data-backtest-node="${nodeId}"]`);
}

function backtestGraphNodeBox(nodeId) {
  const node = backtestGraphNode(nodeId);
  if (!node) return null;
  return {
    left: node.offsetLeft,
    top: node.offsetTop,
    right: node.offsetLeft + node.offsetWidth,
    bottom: node.offsetTop + node.offsetHeight,
    centerX: node.offsetLeft + node.offsetWidth / 2,
    centerY: node.offsetTop + node.offsetHeight / 2,
  };
}

function setBacktestEdgePath(edgeId, path) {
  document.querySelector(`#backtestChain [data-backtest-edge="${edgeId}"]`)?.setAttribute("d", path);
}

function positionBacktestEdgeLabel(edgeId, x, y) {
  const label = document.querySelector(`#backtestChain [data-backtest-edge-label="${edgeId}"]`);
  if (!label) return;
  label.setAttribute("x", String(Math.round(x)));
  label.setAttribute("y", String(Math.round(y)));
}

function drawBacktestGraphEdges() {
  const boxes = {
    dataset: backtestGraphNodeBox("dataset"),
    sampler: backtestGraphNodeBox("sampler"),
    environment: backtestGraphNodeBox("environment"),
    pipeline: backtestGraphNodeBox("pipeline"),
    analyzer: backtestGraphNodeBox("analyzer"),
  };
  if (Object.values(boxes).some((box) => !box)) return;

  const connect = (edgeId, source, target, labelOffset = -12) => {
    const from = { x: source.right, y: source.centerY };
    const to = { x: target.left, y: target.centerY };
    const bend = Math.max(35, Math.abs(to.x - from.x) * 0.42);
    setBacktestEdgePath(
      edgeId,
      `M${from.x} ${from.y} C${from.x + bend} ${from.y} ${to.x - bend} ${to.y} ${to.x} ${to.y}`,
    );
    positionBacktestEdgeLabel(edgeId, (from.x + to.x) / 2, (from.y + to.y) / 2 + labelOffset);
  };

  connect("dataset-sampler", boxes.dataset, boxes.sampler);
  connect("sampler-environment", boxes.sampler, boxes.environment);
  connect("environment-pipeline", boxes.environment, boxes.pipeline);
  connect("sampler-analyzer", boxes.sampler, boxes.analyzer, 18);
  connect("pipeline-analyzer", boxes.pipeline, boxes.analyzer, 30);

  const from = { x: boxes.pipeline.right, y: boxes.pipeline.top + 34 };
  const to = { x: boxes.environment.centerX, y: boxes.environment.top };
  const top = Math.max(18, Math.min(from.y, to.y) - 72);
  const right = Math.min(BACKTEST_CANVAS_SIZE.width - 30, Math.max(from.x + 55, to.x + 130));
  setBacktestEdgePath(
    "pipeline-environment",
    `M${from.x} ${from.y} C${right} ${from.y} ${right} ${top} ${to.x} ${top} C${to.x} ${top} ${to.x} ${top + 28} ${to.x} ${to.y}`,
  );
  positionBacktestEdgeLabel("pipeline-environment", (right + to.x) / 2, top - 7);
}

function loadBacktestGraphPositions() {
  try {
    const stored = JSON.parse(localStorage.getItem(BACKTEST_GRAPH_POSITIONS_KEY) || "{}");
    document.querySelectorAll("#backtestChain [data-backtest-node]").forEach((node) => {
      const fallback = BACKTEST_GRAPH_DEFAULT_POSITIONS[node.dataset.backtestNode];
      const candidate = stored[node.dataset.backtestNode];
      const position = candidate && Number.isFinite(candidate.left) && Number.isFinite(candidate.top)
        ? candidate
        : fallback;
      if (!position) return;
      node.style.left = `${position.left}px`;
      node.style.top = `${position.top}px`;
    });
  } catch {}
}

function saveBacktestGraphPosition(node) {
  try {
    const stored = JSON.parse(localStorage.getItem(BACKTEST_GRAPH_POSITIONS_KEY) || "{}");
    stored[node.dataset.backtestNode] = { left: node.offsetLeft, top: node.offsetTop };
    localStorage.setItem(BACKTEST_GRAPH_POSITIONS_KEY, JSON.stringify(stored));
  } catch {}
}

function clampBacktestScale(scale) {
  return Math.min(BACKTEST_VIEWPORT_MAX_SCALE, Math.max(BACKTEST_VIEWPORT_MIN_SCALE, scale));
}

function applyBacktestViewport() {
  const viewport = state.backtestViewport;
  const canvas = document.querySelector("#backtestChain .backtest-graph-canvas");
  if (canvas) canvas.style.transform = `translate(${viewport.x}px, ${viewport.y}px) scale(${viewport.scale})`;
  if ($("backtestZoomLabel")) $("backtestZoomLabel").textContent = `${Math.round(viewport.scale * 100)}%`;
  if ($("backtestFullscreenBtn")) {
    $("backtestFullscreenBtn").textContent = viewport.fullscreen ? "Exit Fullscreen" : "Fullscreen";
    $("backtestFullscreenBtn").setAttribute("aria-pressed", viewport.fullscreen ? "true" : "false");
  }
  document.body.classList.toggle("backtest-builder-fullscreen", !!viewport.fullscreen);
}

function fitBacktestViewportToNodes() {
  const viewportEl = $("backtestChain");
  const nodes = [...document.querySelectorAll("#backtestChain [data-backtest-node]")];
  if (!viewportEl || !nodes.length || viewportEl.clientWidth < 1 || viewportEl.clientHeight < 1) return;
  const bounds = nodes.reduce((acc, node) => ({
    left: Math.min(acc.left, node.offsetLeft),
    top: Math.min(acc.top, node.offsetTop),
    right: Math.max(acc.right, node.offsetLeft + node.offsetWidth),
    bottom: Math.max(acc.bottom, node.offsetTop + node.offsetHeight),
  }), {
    left: Number.POSITIVE_INFINITY,
    top: Number.POSITIVE_INFINITY,
    right: Number.NEGATIVE_INFINITY,
    bottom: Number.NEGATIVE_INFINITY,
  });
  const graphWidth = Math.max(bounds.right - bounds.left, 1);
  const graphHeight = Math.max(bounds.bottom - bounds.top, 1);
  const scale = clampBacktestScale(Math.min(
    1,
    (viewportEl.clientWidth - 96) / graphWidth,
    (viewportEl.clientHeight - 96) / graphHeight,
  ));
  state.backtestViewport.scale = scale;
  state.backtestViewport.x = Math.round((viewportEl.clientWidth - graphWidth * scale) / 2 - bounds.left * scale);
  state.backtestViewport.y = Math.round((viewportEl.clientHeight - graphHeight * scale) / 2 - bounds.top * scale);
}

function resetBacktestViewport() {
  fitBacktestViewportToNodes();
  applyBacktestViewport();
}

function arrangeBacktestGraph() {
  Object.entries(BACKTEST_GRAPH_DEFAULT_POSITIONS).forEach(([nodeId, position]) => {
    const node = backtestGraphNode(nodeId);
    if (!node) return;
    node.style.left = `${position.left}px`;
    node.style.top = `${position.top}px`;
    saveBacktestGraphPosition(node);
  });
  drawBacktestGraphEdges();
  resetBacktestViewport();
}

function toggleBacktestFullscreen(force = null) {
  state.backtestViewport.fullscreen = force === null ? !state.backtestViewport.fullscreen : !!force;
  applyBacktestViewport();
  requestAnimationFrame(() => {
    drawBacktestGraphEdges();
    resetBacktestViewport();
  });
}

function zoomBacktestViewport(event) {
  event.preventDefault();
  const viewportEl = $("backtestChain");
  if (!viewportEl) return;
  const rect = viewportEl.getBoundingClientRect();
  const pointerX = event.clientX - rect.left;
  const pointerY = event.clientY - rect.top;
  const viewport = state.backtestViewport;
  const beforeX = (pointerX - viewport.x) / viewport.scale;
  const beforeY = (pointerY - viewport.y) / viewport.scale;
  const nextScale = clampBacktestScale(viewport.scale * Math.exp(-event.deltaY * 0.001));
  viewport.x = pointerX - beforeX * nextScale;
  viewport.y = pointerY - beforeY * nextScale;
  viewport.scale = nextScale;
  applyBacktestViewport();
}

function bindBacktestViewportControls() {
  const viewportEl = $("backtestChain");
  if (!viewportEl || viewportEl.dataset.viewportReady === "1") return;
  viewportEl.dataset.viewportReady = "1";
  viewportEl.addEventListener("wheel", zoomBacktestViewport, { passive: false });
  viewportEl.addEventListener("pointerdown", (event) => {
    if (event.button !== 0 || event.target.closest("[data-backtest-node], button, select, input, textarea, a")) return;
    event.preventDefault();
    const viewport = state.backtestViewport;
    const start = { clientX: event.clientX, clientY: event.clientY, x: viewport.x, y: viewport.y };
    viewportEl.setPointerCapture(event.pointerId);
    viewportEl.classList.add("panning");
    const move = (moveEvent) => {
      viewport.x = start.x + moveEvent.clientX - start.clientX;
      viewport.y = start.y + moveEvent.clientY - start.clientY;
      applyBacktestViewport();
    };
    const finish = () => {
      viewportEl.classList.remove("panning");
      viewportEl.removeEventListener("pointermove", move);
      viewportEl.removeEventListener("pointerup", finish);
      viewportEl.removeEventListener("pointercancel", finish);
    };
    viewportEl.addEventListener("pointermove", move);
    viewportEl.addEventListener("pointerup", finish);
    viewportEl.addEventListener("pointercancel", finish);
  });
}

function ensureBacktestViewportReady() {
  if (currentView !== "backtests") return;
  requestAnimationFrame(() => {
    if (!state.backtestViewport.initialized) {
      fitBacktestViewportToNodes();
      state.backtestViewport.initialized = true;
    }
    applyBacktestViewport();
    drawBacktestGraphEdges();
  });
}

function initializeBacktestGraph() {
  const canvas = document.querySelector("#backtestChain .backtest-graph-canvas");
  if (!canvas || canvas.dataset.dragReady === "1") return;
  canvas.dataset.dragReady = "1";
  loadBacktestGraphPositions();
  bindBacktestViewportControls();
  canvas.querySelectorAll("[data-backtest-node]").forEach((node) => {
    node.addEventListener("pointerdown", (event) => {
      if (event.button !== 0 || event.target.closest("button, select, input, textarea, a")) return;
      event.preventDefault();
      const startX = event.clientX;
      const startY = event.clientY;
      const startLeft = node.offsetLeft;
      const startTop = node.offsetTop;
      node.classList.add("dragging");
      node.setPointerCapture(event.pointerId);

      const move = (moveEvent) => {
        const scale = state.backtestViewport.scale || 1;
        const left = Math.max(8, Math.min(canvas.clientWidth - node.offsetWidth - 8, startLeft + (moveEvent.clientX - startX) / scale));
        const top = Math.max(8, Math.min(canvas.clientHeight - node.offsetHeight - 8, startTop + (moveEvent.clientY - startY) / scale));
        node.style.left = `${left}px`;
        node.style.top = `${top}px`;
        drawBacktestGraphEdges();
      };
      const finish = () => {
        node.classList.remove("dragging");
        saveBacktestGraphPosition(node);
        node.removeEventListener("pointermove", move);
        node.removeEventListener("pointerup", finish);
        node.removeEventListener("pointercancel", finish);
      };
      node.addEventListener("pointermove", move);
      node.addEventListener("pointerup", finish);
      node.addEventListener("pointercancel", finish);
    });
  });
  requestAnimationFrame(() => {
    drawBacktestGraphEdges();
    ensureBacktestViewportReady();
  });
  window.addEventListener("resize", drawBacktestGraphEdges);
}

function renderBacktestSamplerParameters({ reset = false } = {}) {
  const sampler = selectedBacktestSampler();
  const samplerKey = sampler ? `${sampler.samplerId}::${sampler.version}` : "";
  if (reset || backtestEntryState.samplerKey !== samplerKey) {
    backtestEntryState.samplerKey = samplerKey;
    backtestEntryState.samplerParameters = structuredClone(sampler?.config || {});
  }
  const parameters = backtestEntryState.samplerParameters || {};
  const mappingCount = parameters.mapping && typeof parameters.mapping === "object"
    ? Object.keys(parameters.mapping).length
    : 0;
  const settingCount = Object.keys(parameters).length;
  $("chainSamplerConfigMeta").textContent = mappingCount
    ? `${settingCount} settings · ${mappingCount} mappings`
    : `${settingCount} parameter${settingCount === 1 ? "" : "s"} configured`;
}

function setBacktestSamplerConfigError(message = "") {
  const error = $("backtestSamplerConfigError");
  error.textContent = message;
  error.hidden = !message;
}

function openBacktestSamplerConfig() {
  const sampler = selectedBacktestSampler();
  if (!sampler) return;
  renderBacktestSamplerParameters();
  $("backtestSamplerConfigTitle").textContent = `Configure ${sampler.name || sampler.samplerId}`;
  $("backtestSamplerConfigDescription").textContent = `${sampler.samplerId} @ ${sampler.version} · parameters apply to this Backtest Entry only`;
  forms.renderSchemaFields(
    $("backtestSamplerConfigFields"),
    selectedSamplerParameterSchema(sampler),
    structuredClone(backtestEntryState.samplerParameters),
  );
  setBacktestSamplerConfigError("");
  $("backtestSamplerConfigDialog").showModal();
}

function renderBacktestEnvironmentParameters({ reset = false } = {}) {
  const environment = selectedBacktestEnvironment();
  const environmentKey = environment ? `${environment.environmentId}::${environment.version}` : "";
  if (reset || backtestEntryState.environmentKey !== environmentKey) {
    backtestEntryState.environmentKey = environmentKey;
  }
  const moduleCount = environment?.graph?.nodes?.length || 0;
  const outputCount = Object.keys(environment?.graph?.outputs || {}).length;
  $("chainEnvironmentConfigMeta").textContent = `${moduleCount} graph nodes · ${outputCount} Pipeline outputs`;
}

function buildBacktestCompositionRequest() {
  const datasetId = $("backtestDataset")?.value || "";
  const dataset = (state.datasets || []).find((row) => row.datasetId === datasetId);
  const datasetEvidence = selectedBacktestDatasetEvidence();
  const sampler = selectedBacktestSampler();
  const environment = selectedBacktestEnvironment();
  const analysis = selectedBacktestAnalysis();
  const pipelineId = backtestEntryState.pipelineId;
  const pipelineVersion = backtestEntryState.pipelineVersion;
  if (!datasetId || !dataset || !datasetEvidence || !sampler || !environment || !analysis
      || !pipelineId || !pipelineVersion || !state.pipelines?.[pipelineId]) return null;
  return {
    pipeline: { pipelineId, version: pipelineVersion },
    datasetId,
    datasetVersionId: datasetEvidence.datasetVersionId,
    sampler: {
      samplerId: sampler.samplerId,
      version: sampler.version,
      parameters: structuredClone(backtestEntryState.samplerParameters || {}),
    },
    environment: {
      environmentId: environment.environmentId,
      version: environment.version,
    },
    analysis: {
      analysisId: analysis.analysisId,
      version: analysis.version,
    },
  };
}

function renderBacktestCompositionStatus() {
  const status = $("backtestCompositionStatus");
  if (!status) return;
  const validation = backtestEntryState.compositionValidation;
  const titles = {
    idle: "Complete configuration",
    build: "Build required",
    pending: "Checking configuration",
    valid: "Build complete",
    invalid: "Build failed",
    submitting: "Submitting Backtest",
    submitted: "Backtest queued",
  };
  status.textContent = backtestEntryState.compositionMessage;
  status.dataset.state = validation;
  status.classList.toggle("dialog-error", validation === "invalid");
  status.classList.toggle("muted", validation !== "invalid");
  $("backtestSubmitTitle").textContent = titles[validation] || titles.idle;
  $("backtestSubmitPanel").dataset.state = validation;
}

function setBacktestSubmissionPending(pending) {
  const active = Boolean(pending);
  backtestEntryState.submissionPending = active;
  const chain = $("backtestChain");
  if (chain) {
    chain.inert = active;
    if (active) chain.setAttribute("aria-busy", "true");
    else chain.removeAttribute("aria-busy");
  }
  syncBacktestRunState();
}

function backtestRequestFingerprint(request) {
  return request ? JSON.stringify(request) : "";
}

let backtestBuildExpiryTimer = 0;
let backtestBuildSelectionRestoreAttempted = false;
const BACKTEST_BUILD_CACHE_KEY = "trade.backtest.build.v1";

function readPersistedBacktestBuildCache() {
  try {
    const cached = JSON.parse(sessionStorage.getItem(BACKTEST_BUILD_CACHE_KEY) || "null");
    if (!cached
        || cached.owner !== (authState.user?.email || "")
        || typeof cached.requestDigest !== "string"
        || !cached.requestDigest
        || !cached.request
        || backtestRequestFingerprint(cached.request) !== cached.requestFingerprint
        || !Number.isFinite(cached.expiresAt)
        || cached.expiresAt <= Date.now()) return null;
    return cached;
  } catch {
    return null;
  }
}

function persistBacktestBuildCache() {
  if (!backtestEntryState.preparedRequestDigest
      || !backtestEntryState.preparedRequestFingerprint
      || backtestEntryState.preparedBuildExpiresAt <= Date.now()) return;
  try {
    sessionStorage.setItem(BACKTEST_BUILD_CACHE_KEY, JSON.stringify({
      owner: authState.user?.email || "",
      request: buildBacktestCompositionRequest(),
      requestDigest: backtestEntryState.preparedRequestDigest,
      requestFingerprint: backtestEntryState.preparedRequestFingerprint,
      expiresAt: backtestEntryState.preparedBuildExpiresAt,
    }));
  } catch {}
}

function clearPersistedBacktestBuildCache() {
  try {
    sessionStorage.removeItem(BACKTEST_BUILD_CACHE_KEY);
  } catch {}
}

function restoreBacktestBuildCache(request) {
  try {
    const cached = readPersistedBacktestBuildCache();
    if (!cached || cached.requestFingerprint !== backtestRequestFingerprint(request)) {
      clearPersistedBacktestBuildCache();
      return false;
    }
    backtestEntryState.preparedRequestDigest = cached.requestDigest;
    backtestEntryState.preparedRequestFingerprint = cached.requestFingerprint;
    backtestEntryState.preparedBuildExpiresAt = cached.expiresAt;
    backtestEntryState.compositionValidation = "valid";
    backtestEntryState.compositionMessage = "Cached Build restored · ready to run";
    scheduleBacktestBuildExpiry();
    return true;
  } catch {
    clearPersistedBacktestBuildCache();
    return false;
  }
}

function restoreBacktestControlsFromBuildCache() {
  if (backtestBuildSelectionRestoreAttempted) return false;
  backtestBuildSelectionRestoreAttempted = true;
  const cached = readPersistedBacktestBuildCache();
  const request = cached?.request;
  if (!request) {
    clearPersistedBacktestBuildCache();
    return false;
  }
  const values = {
    pipeline: `${request.pipeline?.pipelineId || ""}::${request.pipeline?.version || ""}`,
    dataset: request.datasetId || "",
    sampler: `${request.sampler?.samplerId || ""}::${request.sampler?.version || ""}`,
    environment: `${request.environment?.environmentId || ""}::${request.environment?.version || ""}`,
    analysis: `${request.analysis?.analysisId || ""}::${request.analysis?.version || ""}`,
  };
  const controls = {
    pipeline: $("backtestPipelineSelect"),
    dataset: $("backtestDataset"),
    sampler: $("backtestSampler"),
    environment: $("backtestEnvironmentSelect"),
    analysis: $("backtestAnalysisSelect"),
  };
  const available = Object.entries(controls).every(([key, control]) => (
    control && [...control.options].some((option) => option.value === values[key])
  ));
  if (!available) {
    clearPersistedBacktestBuildCache();
    return false;
  }
  Object.entries(controls).forEach(([key, control]) => {
    control.value = values[key];
  });
  backtestEntryState.pipelineId = request.pipeline.pipelineId;
  backtestEntryState.pipelineVersion = request.pipeline.version;
  backtestEntryState.samplerKey = values.sampler;
  backtestEntryState.samplerParameters = structuredClone(request.sampler.parameters || {});
  backtestEntryState.environmentKey = values.environment;
  backtestEntryState.analysisKey = values.analysis;
  return true;
}

function backtestCachedBuildMatches(request = buildBacktestCompositionRequest()) {
  return Boolean(request)
    && Boolean(backtestEntryState.preparedRequestDigest)
    && backtestEntryState.preparedRequestFingerprint === backtestRequestFingerprint(request)
    && backtestEntryState.preparedBuildExpiresAt > Date.now();
}

function backtestPreparedTokenIsUsable() {
  return Boolean(backtestEntryState.preparedSubmissionToken)
    && backtestEntryState.preparedTokenExpiresAt > Date.now();
}

function scheduleBacktestBuildExpiry() {
  window.clearTimeout(backtestBuildExpiryTimer);
  const delay = backtestEntryState.preparedBuildExpiresAt - Date.now();
  if (delay <= 0) return;
  const expireBuild = () => {
    if (backtestEntryState.submissionPending
        || backtestEntryState.compositionValidation === "pending") {
      backtestBuildExpiryTimer = window.setTimeout(expireBuild, 1000);
      return;
    }
    if (backtestEntryState.preparedBuildExpiresAt <= Date.now()) {
      invalidateBacktestBuild("Cached Build expired · Build again before running");
    }
  };
  backtestBuildExpiryTimer = window.setTimeout(
    expireBuild,
    Math.min(delay + 25, 2_147_000_000),
  );
}

function invalidateBacktestBuild(message = "") {
  window.clearTimeout(backtestBuildExpiryTimer);
  clearPersistedBacktestBuildCache();
  ++backtestEntryState.compositionSequence;
  backtestEntryState.preparedSubmissionToken = "";
  backtestEntryState.preparedRequestDigest = "";
  backtestEntryState.preparedRequestFingerprint = "";
  backtestEntryState.preparedTokenExpiresAt = 0;
  backtestEntryState.preparedBuildExpiresAt = 0;
  const request = buildBacktestCompositionRequest();
  if (!request) {
    backtestEntryState.compositionValidation = "idle";
    backtestEntryState.compositionMessage = "Select Pipeline, Dataset, Sampler, Environment, and Analysis";
  } else {
    backtestEntryState.compositionValidation = "build";
    backtestEntryState.compositionMessage = message || "Build this configuration before running";
  }
  renderBacktestCompositionStatus();
  syncBacktestRunState();
}

async function buildBacktestSubmission({ runAfterBuild = false } = {}) {
  const request = buildBacktestCompositionRequest();
  if (!request) {
    invalidateBacktestBuild();
    return;
  }
  const requestFingerprint = backtestRequestFingerprint(request);
  const previousBuild = {
    requestDigest: backtestEntryState.preparedRequestDigest,
    requestFingerprint: backtestEntryState.preparedRequestFingerprint,
    expiresAt: backtestEntryState.preparedBuildExpiresAt,
  };
  const sequence = ++backtestEntryState.compositionSequence;
  window.clearTimeout(backtestBuildExpiryTimer);
  backtestEntryState.preparedSubmissionToken = "";
  backtestEntryState.preparedTokenExpiresAt = 0;
  if (!runAfterBuild) {
    backtestEntryState.preparedRequestDigest = "";
    backtestEntryState.preparedRequestFingerprint = "";
    backtestEntryState.preparedBuildExpiresAt = 0;
  }
  backtestEntryState.compositionValidation = "pending";
  backtestEntryState.compositionMessage = runAfterBuild
    ? "Preparing a fresh Run from the cached Build…"
    : "Checking exact Backtest configuration…";
  renderBacktestCompositionStatus();
  syncBacktestRunState();
  try {
    const result = await postJson("/api/backtest-submissions/prepare", request);
    if (sequence !== backtestEntryState.compositionSequence) return;
    const currentFingerprint = backtestRequestFingerprint(buildBacktestCompositionRequest());
    if (currentFingerprint !== requestFingerprint) {
      invalidateBacktestBuild("Configuration changed while checking · Build again");
      return;
    }
    const prepared = result.valid && typeof result.preparedSubmissionToken === "string"
      && result.preparedSubmissionToken.length > 0;
    backtestEntryState.preparedSubmissionToken = prepared
      ? result.preparedSubmissionToken
      : "";
    backtestEntryState.preparedTokenExpiresAt = prepared
      ? Date.now() + Math.max(0, Number(result.expiresInSeconds) || 0) * 1000
      : 0;
    backtestEntryState.preparedRequestDigest = prepared
      ? (result.requestDigest || "")
      : "";
    backtestEntryState.preparedRequestFingerprint = prepared
      ? requestFingerprint
      : "";
    backtestEntryState.preparedBuildExpiresAt = prepared
      ? Date.now() + Math.max(
        0,
        Number(result.buildCacheExpiresInSeconds ?? result.expiresInSeconds) || 0,
      ) * 1000
      : 0;
    backtestEntryState.compositionValidation = prepared ? "valid" : "invalid";
    backtestEntryState.compositionMessage = prepared
      ? `${result.cacheHit ? "Cached Build reused" : "Configuration built"} · ready to run`
      : result.valid
        ? "Engine did not prepare this composition"
        : "Composition is invalid";
  } catch (error) {
    if (sequence !== backtestEntryState.compositionSequence) return;
    backtestEntryState.preparedSubmissionToken = "";
    backtestEntryState.preparedTokenExpiresAt = 0;
    const restoreCachedBuild = runAfterBuild
      && previousBuild.requestDigest
      && previousBuild.requestFingerprint === requestFingerprint
      && previousBuild.expiresAt > Date.now();
    backtestEntryState.preparedRequestDigest = restoreCachedBuild
      ? previousBuild.requestDigest
      : "";
    backtestEntryState.preparedRequestFingerprint = restoreCachedBuild
      ? previousBuild.requestFingerprint
      : "";
    backtestEntryState.preparedBuildExpiresAt = restoreCachedBuild
      ? previousBuild.expiresAt
      : 0;
    backtestEntryState.compositionValidation = restoreCachedBuild ? "valid" : "invalid";
    backtestEntryState.compositionMessage = restoreCachedBuild
      ? `Run preparation failed · cached Build retained · ${error?.message || "try again"}`
      : error?.message || "Composition is invalid";
  }
  renderBacktestCompositionStatus();
  syncBacktestRunState();
  if (backtestEntryState.compositionValidation === "valid") {
    persistBacktestBuildCache();
    scheduleBacktestBuildExpiry();
    if (runAfterBuild) await submitPreparedBacktest();
  }
}

function renderBacktestChain() {
  const dataset = (state.datasets || []).find((row) => row.datasetId === $("backtestDataset")?.value);
  const datasetEvidence = selectedBacktestDatasetEvidence();
  const sampler = selectedBacktestSampler();
  const environment = selectedBacktestEnvironment();
  const analysis = selectedBacktestAnalysis();
  const pipelineId = backtestEntryState.pipelineId;
  const pipelineVersion = backtestEntryState.pipelineVersion;
  const pipeline = state.pipelines?.[pipelineId] || {};
  $("chainDatasetMeta").textContent = !dataset
    ? "Select a Dataset"
    : datasetEvidence
      ? `${datasetEvidenceSummary(datasetEvidence)} · evidence locked automatically`
      : "No sealed evidence available";
  $("chainSamplerMeta").textContent = sampler
    ? `${sampler.type} · v${sampler.version}`
    : "DataKey mapping";
  const samplerOpenButton = $("chainSampler");
  const samplerReadOnly = !!sampler?.builtin;
  const samplerEditable = ["row-map", "python-script"].includes(sampler?.type);
  samplerOpenButton.disabled = !sampler || samplerReadOnly || !samplerEditable;
  samplerOpenButton.title = !sampler
    ? "No Sampler selected"
    : samplerReadOnly
      ? "Built-in Samplers are read-only"
      : !samplerEditable
        ? "This Sampler type cannot be edited"
        : "Open an isolated Jupyter edit Workspace";
  $("chainEnvironmentMeta").textContent = environment
    ? `${environment.environmentId} · v${environment.version}`
    : "Select an Environment";
  $("chainAnalysisMeta").textContent = analysis
    ? `${analysis.analysisId} · v${analysis.version}`
    : "Select an Analysis";
  $("chainPipelineMeta").textContent = pipelineId
    ? `${pipelineId} · v${pipelineVersion}`
    : "Select a Pipeline";
  requestAnimationFrame(drawBacktestGraphEdges);
  const request = buildBacktestCompositionRequest();
  if (backtestEntryState.compositionValidation === "idle" && request) {
    if (!restoreBacktestBuildCache(request)) {
      backtestEntryState.compositionValidation = "build";
      backtestEntryState.compositionMessage = "Build this configuration before running";
    }
    renderBacktestCompositionStatus();
  } else if (backtestEntryState.compositionValidation === "valid"
      && backtestEntryState.preparedRequestFingerprint !== backtestRequestFingerprint(request)) {
    invalidateBacktestBuild("Configuration changed · Build again before running");
  }
}

function syncBacktestRunState() {
  const button = $("runBacktestBtn");
  if (backtestEntryState.submissionPending) {
    button.disabled = true;
    button.title = "Backtest submission is in progress";
    button.textContent = "Submitting…";
    button.classList.add("button-loading");
    button.setAttribute("aria-busy", "true");
    return;
  }
  button.classList.remove("button-loading");
  button.removeAttribute("aria-busy");
  const datasetId = $("backtestDataset").value;
  const dataset = (state.datasets || []).find((row) => row.datasetId === datasetId);
  const datasetEvidence = selectedBacktestDatasetEvidence();
  let disabled = false;
  let title = "";
  let label = "Build";
  if (!backtestEntryState.pipelineId || !backtestEntryState.pipelineVersion
      || !state.pipelines?.[backtestEntryState.pipelineId]) {
    disabled = true;
    title = "No Pipeline selected";
  } else if (!datasetId) {
    disabled = true;
    title = "No dataset available";
  } else if (!datasetEvidence) {
    disabled = true;
    title = "Selected Dataset has no sealed evidence";
  } else if (!selectedBacktestSampler()) {
    disabled = true;
    title = "No Sampler available";
  } else if (!selectedBacktestEnvironment()) {
    disabled = true;
    title = "No Environment available";
  } else if (!selectedBacktestAnalysis()) {
    disabled = true;
    title = "No Analysis available";
  } else if (!backtestEntryState.samplerParameters
      || Array.isArray(backtestEntryState.samplerParameters)
      || typeof backtestEntryState.samplerParameters !== "object") {
    disabled = true;
    title = "Sampler parameters are invalid";
  } else if (backtestEntryState.compositionValidation === "pending") {
    disabled = true;
    title = "Engine is checking the configuration";
    label = "Checking…";
    button.classList.add("button-loading");
    button.setAttribute("aria-busy", "true");
  } else {
    const cachedBuild = backtestCachedBuildMatches();
    if (cachedBuild) {
      title = "Run the checked Backtest configuration";
      label = "Run Backtest";
    } else {
      title = backtestEntryState.compositionValidation === "invalid"
        ? backtestEntryState.compositionMessage
        : "Check and prepare this Backtest configuration";
      label = backtestEntryState.compositionValidation === "invalid"
        ? "Build again"
        : "Build";
    }
  }
  button.disabled = disabled;
  button.title = title;
  button.textContent = label;
}

function currentResultBacktestId() {
  return state.resultBacktestId || "";
}

function syncResultsActionState() {
  const backtestId = currentResultBacktestId();
  const addChartButton = $("addChartBtn");
  const saveSpecButton = $("saveVisualizationBtn");
  const hasBacktest = !!backtestId;
  setResultsActionError(hasBacktest ? "" : "Open a completed Result from the Backtest Browser.");
  if (addChartButton) {
    addChartButton.disabled = !hasBacktest;
    addChartButton.title = hasBacktest ? "" : "Open a Result from the Backtest Browser first";
  }
  if (!saveSpecButton) return;
  if (!hasBacktest) {
    saveSpecButton.disabled = true;
    saveSpecButton.title = "Open a Result from the Backtest Browser first";
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
  const target = $("manifestJson");
  if (!target) return;
  const pipelineId = pipelineEditorState.pipelineId;
  const pipeline = state.pipelines?.[pipelineId] || {};
  const meta = $("pipelineManifestMeta");
  if (meta) {
    const identity = pipeline.name || pipelineId;
    meta.textContent = identity === pipelineId ? pipelineId : `${identity} · ${pipelineId}`;
  }
  target.textContent = JSON.stringify(pipelineEditorState.manifest || {}, null, 2);
}

function normalizeVisualizationSpec(result, spec) {
  return window.TradeChartCore.normalizeVisualizationSpec(result, spec);
}

function renderResults() {
  const backtestId = currentResultBacktestId();
  const title = $("resultTitle");
  const meta = $("resultMeta");
  if (title) title.textContent = state.selectedBacktest?.name || "Result";
  if (meta) meta.textContent = backtestId ? `Backtest · ${backtestId}` : "";
  if (!backtestId) {
    clearResultCharts();
    $("metricStrip").innerHTML = "";
    $("chartArea").innerHTML = "";
    $("visualizationSpec").value = "";
    setVisualizationSpecError("");
    setResultsActionError("Open a completed Result from the Backtest Browser.");
    syncResultsActionState();
    return;
  }
  setResultsActionError("");
  if (!state.selectedBacktest || state.selectedBacktest.backtestId !== backtestId) {
    clearResultCharts();
    $("metricStrip").innerHTML = "";
    $("chartArea").innerHTML = '<div class="muted">Loading selected backtest</div>';
    syncResultsActionState();
    return;
  }
  const metrics = state.selectedBacktest.metrics || {};
  const findPerformance = (value) => {
    if (!value || typeof value !== "object") return null;
    if (["annualizedReturn", "sharpeRatio", "maxDrawdown"].some((key) => key in value)) {
      return value;
    }
    for (const child of Object.values(value)) {
      const match = findPerformance(child);
      if (match) return match;
    }
    return null;
  };
  const performance = findPerformance(metrics.analysis) || {};
  const chain = state.selectedBacktest.executionSummary || {};
  $("metricStrip").innerHTML = [
    ["Cycles", metrics.cycleCount ?? "-"],
    ["Annualized", formatPercent(performance.annualizedReturn)],
    ["Sharpe", typeof performance.sharpeRatio === "number" ? performance.sharpeRatio.toFixed(3) : "-"],
    ["Max Drawdown", formatPercent(performance.maxDrawdown)],
    ["Dataset", chain.dataset?.datasetId || state.selectedBacktest.datasetId || "-"],
    ["Sampler", chain.sampler ? `${chain.sampler.samplerId}@${chain.sampler.version}` : "-"],
    ["Status", state.selectedBacktest.status || "completed"],
  ].map(([label, value]) => `<div><span>${label}</span><strong>${value}</strong></div>`).join("");
  const spec = normalizeVisualizationSpec({ dataKeys: state.selectedBacktest.dataKeys || {} }, state.selectedBacktest.visualization || {});
  state.selectedBacktest.visualization = spec;
  syncResultTimezoneButton(spec);
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
  const owner = state.selectedBacktest;
  const backtestId = owner.backtestId;
  owner.paneResults ||= {};
  owner.loadingPanes ||= {};
  owner.loadedPanes ||= {};
  const requestKey = paneRequestKey(pane, spec);
  if (owner.loadingPanes[pane.id] === requestKey || owner.loadedPanes[pane.id] === requestKey) return;
  owner.loadingPanes[pane.id] = requestKey;
  try {
    const parsed = JSON.parse(requestKey);
    if (!parsed.paths.length) {
      if (state.selectedBacktest === owner && owner.loadingPanes[pane.id] === requestKey) {
        owner.paneResults[pane.id] = {};
        owner.loadedPanes[pane.id] = requestKey;
      }
      return;
    }
    const response = await postJson(`/api/backtests/${encodeURIComponent(backtestId)}/result`, {
      paths: parsed.paths,
      temporaryModules: pane.temporaryModules || [],
    });
    if (state.selectedBacktest !== owner || owner.loadingPanes[pane.id] !== requestKey) return;
    owner.paneResults[pane.id] = response.result || {};
    owner.loadedPanes[pane.id] = requestKey;
  } finally {
    if (owner.loadingPanes[pane.id] === requestKey) {
      delete owner.loadingPanes[pane.id];
    }
    if (state.selectedBacktest === owner) renderResults();
  }
}

function scheduleVisualizationSave(spec) {
  const backtestId = currentResultBacktestId();
  if (!backtestId) return;
  const saveSeq = ++visualizationSaveSeq;
  clearTimeout(visualizationSaveTimer);
  setHealth(false, "Saving visualization");
  visualizationSaveTimer = setTimeout(async () => {
    try {
      await postJson("/api/visualizations", {
        backtestId,
        visualizationId: `${backtestId}-current`,
        name: "current",
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
    const inputs = forms.readParamFields(
      document.querySelector(`[data-temp-inputs-fields="${paneIndex}"]`),
      Object.keys(tempModule.ports?.inputs || {}).map((name) => ({ name, type: "dataKey" })),
    );
    validateTemporaryModuleInputs(paneIndex, tempModule, inputs);
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
  try {
    validateVisualizerInputs(paneIndex, visualizerDefinition, params);
  } catch (error) {
    return { disabled: true, title: error?.message || "Invalid visualizer DataKey" };
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
    .map(([key, value]) => ({ key, ...value, folderPath: repositoryPlacement("modules", key).folderPath }))
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

function uniqueTemporaryModuleInstanceId(paneIndex, preferred = "", currentId = "") {
  const trimmedPreferred = String(preferred || "").trim();
  const base = trimmedPreferred || opaqueClientId("tmp");
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

function currentDataKeyOptions(paneIndex, requiredSchema = {}) {
  const pane = state.selectedBacktest.visualization.panes[paneIndex];
  const scoped = paneScopedSpec(state.selectedBacktest.visualization, pane);
  return window.TradeChartCore.chartLayerCatalog({ dataKeys: state.selectedBacktest.dataKeys || {} }, scoped)
    .filter((item) => window.TradeChartCore.schemasCompatible(item.dataSchema, requiredSchema || {}))
    .map((item) => ({ value: item.dataKey, label: item.dataKey, schema: item.dataSchema }));
}

function currentDataKeyDeclaration(paneIndex, dataKey) {
  const pane = state.selectedBacktest.visualization.panes[paneIndex];
  const scoped = paneScopedSpec(state.selectedBacktest.visualization, pane);
  return window.TradeChartCore.resolveDataKeyDeclaration(
    { dataKeys: state.selectedBacktest.dataKeys || {} },
    scoped,
    dataKey,
  );
}

function validateTemporaryModuleInputs(paneIndex, module, inputs) {
  for (const [portName, dataKey] of Object.entries(inputs || {})) {
    const declaration = currentDataKeyDeclaration(paneIndex, dataKey);
    const requiredSchema = module.ports?.inputs?.[portName]?.schema || {};
    if (!declaration || !window.TradeChartCore.schemasCompatible(
      declaration.schema,
      requiredSchema,
    )) {
      throw new Error(`Input ${forms.humanizeName(portName)} must reference a compatible DataKey`);
    }
  }
}

function validateVisualizerInputs(paneIndex, definition, params) {
  for (const [portName, port] of Object.entries(definition.inputPorts || {})) {
    const dataKey = params?.[portName];
    const declaration = currentDataKeyDeclaration(paneIndex, dataKey);
    if (!declaration || !window.TradeChartCore.schemasCompatible(
      declaration.schema,
      port.schema || {},
    )) {
      throw new Error(`${forms.humanizeName(portName)} must reference a compatible DataKey`);
    }
  }
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
  instanceInput.value = selectedItem?.instanceId || uniqueTemporaryModuleInstanceId(paneIndex);
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
      description: JSON.stringify(module.ports.inputs[name]?.schema || {}),
    })),
    selectedItem?.inputs || {},
    Object.fromEntries(Object.keys(module.ports?.inputs || {}).map((name) => [
      name,
      currentDataKeyOptions(paneIndex, module.ports.inputs[name]?.schema || {}),
    ])),
  );
  forms.renderParamFields(
    outputFields,
    Object.keys(module.ports?.outputs || {}).map((name) => ({
      name,
      label: forms.humanizeName(name),
      type: "string",
      description: JSON.stringify(module.ports.outputs[name]?.schema || {}),
      default: nextUniqueDataKey(`${semanticDataKeySegment(module.name || module.kind)}.${semanticDataKeySegment(name, "output")}`, paneIndex),
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
    validateTemporaryModuleInputs(paneIndex, module, inputs);
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
  const instanceId = uniqueTemporaryModuleInstanceId(paneIndex, rawInstanceId, selectedId || "");
  if (instanceId !== rawInstanceId) instanceInput.value = instanceId;
  const nextItem = window.TradeChartCore.createTemporaryModuleInstance(module, {
    instanceId, config, inputs, outputs,
  });
  pane.temporaryModules ||= [];
  setPaneControlError(paneIndex, "");
  if (selectedId) {
    pane.temporaryModules = window.TradeChartCore.upsertIdentity(
      pane.temporaryModules, selectedId, nextItem, "instanceId",
    );
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
    pane.temporaryModules = window.TradeChartCore.upsertIdentity(
      pane.temporaryModules, "", nextItem, "instanceId",
    );
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
  pane.visualizers ||= [];
  const selectedId = selectedVisualizerId(paneIndex);
  let nextItem;
  try {
    validateVisualizerInputs(paneIndex, definition, params);
    nextItem = window.TradeChartCore.createVisualizerInstance(definition, {
      id: uniquePaneVisualizerId(definition.id, paneIndex, selectedId || ""), params,
    });
  } catch (error) {
    setPaneControlError(paneIndex, error?.message || "Invalid visualizer parameters");
    return false;
  }
  setPaneControlError(paneIndex, "");
  if (selectedId) {
    pane.visualizers = window.TradeChartCore.upsertIdentity(
      pane.visualizers, selectedId, nextItem, "id",
    );
  } else {
    pane.visualizers = window.TradeChartCore.upsertIdentity(
      pane.visualizers, "", nextItem, "id",
    );
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

function currentResultTimeZone(spec = state.selectedBacktest?.visualization) {
  const timeZone = spec?.timeZone || "UTC";
  return { timeZone, label: timeZone };
}

function syncResultTimezoneButton(spec = state.selectedBacktest?.visualization) {
  const button = $("resultTimezoneBtn");
  if (!button) return;
  const zone = currentResultTimeZone(spec);
  button.textContent = `TZ: ${zone.label}`;
  button.title = zone.timeZone;
}

function persistVisualizationView(spec) {
  if (!state.selectedBacktest) return;
  state.selectedBacktest.visualization = spec;
  $("visualizationSpec").value = JSON.stringify(spec, null, 2);
  setVisualizationSpecError("");
  syncResultsActionState();
  scheduleVisualizationSave(spec);
}

function chartEntryForPane(paneIndex) {
  return state.resultCharts.find((entry) => entry.paneIndex === paneIndex) || null;
}

function setChartViewError(paneIndex, message = "") {
  const node = document.querySelector(`[data-chart-view-error="${paneIndex}"]`);
  if (!node) return;
  node.textContent = message;
  node.hidden = !message;
}

function applyChartTimeRange(paneIndex) {
  const entry = chartEntryForPane(paneIndex);
  const pane = state.selectedBacktest?.visualization?.panes?.[paneIndex];
  if (!entry || !pane) return false;
  const options = { timeZone: currentResultTimeZone().timeZone, showTime: entry.timeInfo.showTime };
  const startInput = entry.rangeStartInput;
  const endInput = entry.rangeEndInput;
  const parsedStart = window.TradeChartCore.parseRangeInput(startInput?.value, options);
  const parsedEnd = window.TradeChartCore.parseRangeInput(endInput?.value, options);
  if (Number.isNaN(parsedStart) || Number.isNaN(parsedEnd)) {
    setChartViewError(paneIndex, `Use ${entry.timeInfo.showTime ? "a valid local date and time" : "YYYY-MM-DD"}.`);
    return false;
  }
  const start = parsedStart ?? entry.timeInfo.start;
  const end = parsedEnd ?? entry.timeInfo.end;
  if (start == null || end == null) {
    setChartViewError(paneIndex, "This chart has no time range to display.");
    return false;
  }
  if (start >= end) {
    setChartViewError(paneIndex, "Start must be before End.");
    return false;
  }
  if (end < entry.timeInfo.start || start > entry.timeInfo.end) {
    setChartViewError(paneIndex, "The selected range does not overlap this chart's data.");
    return false;
  }
  const boundedStart = Math.max(start, entry.timeInfo.start);
  const boundedEnd = Math.min(end, entry.timeInfo.end);
  entry.chart.timeScale().setVisibleRange({ from: boundedStart, to: boundedEnd });
  pane.view ||= {};
  pane.view.start = parsedStart == null ? null : (entry.timeInfo.showTime ? new Date(parsedStart * 1000).toISOString() : startInput.value);
  pane.view.end = parsedEnd == null ? null : (entry.timeInfo.showTime ? new Date(parsedEnd * 1000).toISOString() : endInput.value);
  setChartViewError(paneIndex, "");
  persistVisualizationView(state.selectedBacktest.visualization);
  return true;
}

function fitChartPane(paneIndex) {
  const entry = chartEntryForPane(paneIndex);
  const pane = state.selectedBacktest?.visualization?.panes?.[paneIndex];
  if (!entry || !pane) return;
  pane.view ||= {};
  pane.view.start = null;
  pane.view.end = null;
  if (entry.rangeStartInput) entry.rangeStartInput.value = "";
  if (entry.rangeEndInput) entry.rangeEndInput.value = "";
  entry.chart.timeScale().fitContent();
  setChartViewError(paneIndex, "");
  persistVisualizationView(state.selectedBacktest.visualization);
}

function toggleChartLogScale(paneIndex) {
  const entry = chartEntryForPane(paneIndex);
  const pane = state.selectedBacktest?.visualization?.panes?.[paneIndex];
  if (!entry || !pane) return;
  pane.view ||= {};
  pane.view.logScale = !pane.view.logScale;
  entry.chart.priceScale("right").applyOptions({ mode: window.TradeChartCore.priceScaleMode(pane.view.logScale) });
  entry.logButton?.classList.toggle("active", !!pane.view.logScale);
  if (entry.logButton) entry.logButton.textContent = pane.view.logScale ? "Log" : "Linear";
  persistVisualizationView(state.selectedBacktest.visualization);
}

function toggleChartControlsCollapsed(paneIndex) {
  const entry = chartEntryForPane(paneIndex);
  const pane = state.selectedBacktest?.visualization?.panes?.[paneIndex];
  if (!entry || !pane) return;
  pane.view ||= {};
  pane.view.controlsCollapsed = !pane.view.controlsCollapsed;
  if (entry.controls) entry.controls.hidden = !!pane.view.controlsCollapsed;
  if (entry.controlsButton) entry.controlsButton.textContent = pane.view.controlsCollapsed ? "Show Config" : "Hide Config";
  persistVisualizationView(state.selectedBacktest.visualization);
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
    view: { start: null, end: null, logScale: false, controlsCollapsed: false },
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
      </select>
      <input data-temp-instance="${paneIndex}" type="hidden" />
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
  const temporaryModuleSelect = control.querySelector(`[data-temp-module-select="${paneIndex}"]`);
  appendRepositoryOptions(
    temporaryModuleSelect,
    resultModuleDefinitions(),
    (row) => row.key,
    (row) => `${row.kind} / ${row.moduleId} / ${row.version}`,
    "modules",
  );
  if (selectedTempKey) temporaryModuleSelect.value = selectedTempKey;
  temporaryModuleSelect.dataset.repositoryHierarchy = "modules";
  enhanceHierarchicalRepositorySelect(temporaryModuleSelect);
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

function clearResultCharts() {
  state.resultCharts.forEach(({ chart, observer, cleanups = [] }) => {
    observer?.disconnect();
    cleanups.forEach((cleanup) => cleanup?.());
    chart?.remove?.();
  });
  state.resultCharts = [];
}

function drawVisualization(spec) {
  const area = $("chartArea");
  clearResultCharts();
  area.innerHTML = "";
  area.dataset.backtestId = state.selectedBacktest?.backtestId || "";
  const library = window.LightweightCharts;
  if (!library?.createChart) {
    const missing = document.createElement("div");
    missing.className = "muted";
    missing.textContent = "Chart library failed to load.";
    area.appendChild(missing);
    return;
  }
  const panes = spec.panes?.length ? spec.panes : [];
  panes.forEach((pane, paneIndex) => {
    pane.view ||= { start: null, end: null, logScale: false, controlsCollapsed: false };
    const panel = document.createElement("div");
    panel.className = "chart-panel";
    panel.classList.toggle("collapsed", !!pane.collapsed);
    const title = document.createElement("div");
    title.className = "chart-title";
    title.innerHTML = `
      <span>${escapeHtml(pane.title || pane.id || "Chart")}</span>
      <div class="chart-actions">
        <button class="inline-action" data-toggle-chart-controls="${paneIndex}" type="button">${pane.view.controlsCollapsed ? "Show Config" : "Hide Config"}</button>
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
    controls.hidden = !!pane.view.controlsCollapsed;
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
    const timeInfo = window.TradeChartCore.paneTimeInfo(result, pane, scoped);
    const zone = currentResultTimeZone(spec);
    const viewToolbar = document.createElement("div");
    viewToolbar.className = "chart-view-toolbar";
    const inputType = timeInfo.showTime ? "datetime-local" : "date";
    const rangeOptions = { timeZone: zone.timeZone, showTime: timeInfo.showTime };
    const storedStart = pane.view.start ? window.TradeChartCore.chartTime(pane.view.start) : null;
    const storedEnd = pane.view.end ? window.TradeChartCore.chartTime(pane.view.end) : null;
    viewToolbar.innerHTML = `
      <label><span>Start</span><input type="${inputType}" data-chart-start="${paneIndex}" value="${escapeHtml(window.TradeChartCore.formatRangeInput(storedStart, rangeOptions))}" /></label>
      <label><span>End</span><input type="${inputType}" data-chart-end="${paneIndex}" value="${escapeHtml(window.TradeChartCore.formatRangeInput(storedEnd, rangeOptions))}" /></label>
      <button type="button" data-apply-chart-range="${paneIndex}">Apply</button>
      <button type="button" data-fit-chart="${paneIndex}">Fit</button>
      <button type="button" class="${pane.view.logScale ? "active" : ""}" data-toggle-chart-log="${paneIndex}">${pane.view.logScale ? "Log" : "Linear"}</button>
      <span class="muted">${escapeHtml(zone.label)} · ${timeInfo.showTime ? "intraday" : "date"}</span>
      <span class="chart-view-error" data-chart-view-error="${paneIndex}" hidden></span>
    `;
    panel.appendChild(viewToolbar);
    const container = document.createElement("div");
    container.className = "tv-chart";
    container.style.height = "460px";
    panel.appendChild(container);
    const chart = window.TradeChartCore.createFinancialChart(container, {
      timeZone: zone.timeZone,
      showTime: timeInfo.showTime,
      logScale: !!pane.view.logScale,
    });
    const chartContext = window.TradeChartCore.drawFinancialPane(library, chart, result, pane, scoped);
    const savedStart = timeInfo.start == null
      ? null
      : (typeof storedStart === "number" && Number.isFinite(storedStart) ? Math.max(storedStart, timeInfo.start) : timeInfo.start);
    const savedEnd = timeInfo.end == null
      ? null
      : (typeof storedEnd === "number" && Number.isFinite(storedEnd) ? Math.min(storedEnd, timeInfo.end) : timeInfo.end);
    if (pane.view.start || pane.view.end) {
      if (savedStart != null && savedEnd != null && savedStart < savedEnd) {
        chart.timeScale().setVisibleRange({ from: savedStart, to: savedEnd });
      } else {
        chart.timeScale().fitContent();
        setChartViewError(paneIndex, "Saved range is invalid for this chart.");
      }
    } else {
      chart.timeScale().fitContent();
    }
    const observer = new ResizeObserver(() => {
      if (container.isConnected) chart.applyOptions({ width: container.clientWidth });
    });
    observer.observe(container);
    const entry = {
      paneIndex,
      chart,
      observer,
      cleanups: chartContext?.cleanups || [],
      controls,
      controlsButton: title.querySelector(`[data-toggle-chart-controls="${paneIndex}"]`),
      rangeStartInput: viewToolbar.querySelector(`[data-chart-start="${paneIndex}"]`),
      rangeEndInput: viewToolbar.querySelector(`[data-chart-end="${paneIndex}"]`),
      logButton: viewToolbar.querySelector(`[data-toggle-chart-log="${paneIndex}"]`),
      timeInfo,
    };
    state.resultCharts.push(entry);
    viewToolbar.querySelector(`[data-apply-chart-range="${paneIndex}"]`)?.addEventListener("click", () => applyChartTimeRange(paneIndex));
    viewToolbar.querySelector(`[data-fit-chart="${paneIndex}"]`)?.addEventListener("click", () => fitChartPane(paneIndex));
    entry.logButton?.addEventListener("click", () => toggleChartLogScale(paneIndex));
    entry.controlsButton?.addEventListener("click", () => toggleChartControlsCollapsed(paneIndex));
  });
  area.querySelectorAll("[data-open-chart]").forEach((button) => {
    button.addEventListener("click", () => {
      const backtestId = currentResultBacktestId();
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
}

function renderOverview() {
  renderSummary();
}

function environmentGraphKey(environment) {
  return environment?.environmentId ? `${environment.environmentId}::${environment.version}` : "";
}

function renderEnvironmentBrowser() {
  const total = repositoryCatalog("environments")?.total || 0;
  if ($("environmentRepositoryStatus")) {
    $("environmentRepositoryStatus").textContent = `${total} Environment resource(s) · Latest Version shown · Open one to edit or switch Version`;
  }
  if ($("environmentModuleStatus")) {
    $("environmentModuleStatus").textContent = `${repositoryCatalog("environment-modules")?.total || 0} module(s)`;
  }
  renderEmbeddedRepositoryBrowser("environments");
  renderEmbeddedRepositoryBrowser("environment-modules");
}

function syncEnvironmentBlueprintRoute() {
  if (currentView !== "environment" || currentEnvironmentSection !== "blueprint") return;
  const target = pathForView("environment");
  if (`${location.pathname}${location.search}` !== target) {
    history.replaceState({ viewId: "environment", environmentKey: environmentEditorState.environmentKey }, "", target);
  }
  syncRouteChrome();
}

function openEnvironmentBlueprint(environmentKey, { returnView = "environment" } = {}) {
  environmentEditorState.environmentKey = environmentKey || "";
  environmentEditorState.returnView = returnView === "backtests" ? "backtests" : "environment";
  currentEnvironmentSection = "blueprint";
  return switchView("environment");
}

function closeEnvironmentBlueprint() {
  const returnView = environmentEditorState.returnView;
  currentEnvironmentSection = "browser";
  environmentEditorState.returnView = "environment";
  return switchView(returnView === "backtests" ? "backtests" : "environment");
}

function renderEnvironmentDetails() {
  if (currentEnvironmentSection !== "blueprint") return;
  const [environmentId, version] = environmentEditorState.environmentKey.split("::");
  const environment = state.environments.find((row) => (
    row.environmentId === environmentId && String(row.version) === String(version)
  ));
  if (!environment) {
    environmentEditorState.environmentKey = "";
    currentEnvironmentSection = "browser";
    history.replaceState({ viewId: "environment" }, "", pathForView("environment"));
    renderEnvironmentBrowser();
    syncRouteChrome();
    return;
  }
  environmentEditorState.environmentKey = `${environment.environmentId}::${environment.version}`;
  syncEnvironmentBlueprintRoute();
  const key = environmentEditorState.environmentKey;
  const draft = environmentEditorState.draftsByEnvironment[key] ||= {
    environmentId: environment.builtin ? "" : (environment.environmentId || ""),
    name: environment.builtin ? `${environment.name || "Environment"} Copy` : (environment.name || environment.environmentId || ""),
    instances: structuredClone(environment.instances || {}),
    graph: structuredClone(environment.graph || { nodes: [], inputs: {}, outputs: {} }),
  };
  const returnsToBacktests = environmentEditorState.returnView === "backtests";
  const backLabel = returnsToBacktests ? "Back to Backtest Entry" : "Back to Environments";
  const root = $("environmentGraphBuilder");
  root?.__flushPendingEmit?.();
  root?.__moduleGraphCleanup?.();
  const modules = Object.entries(state.environmentModules || {})
    .filter(([, definition]) => definition.status === "archived")
    .map(([moduleKey, definition]) => ({
      key: moduleKey,
      ...definition,
      folderPath: repositoryPlacement("environment-modules", moduleKey).folderPath,
    }));
  const environmentVersions = state.environments.filter((row) => row.environmentId === environment.environmentId);
  window.ModuleGraphLiteGraph?.mount({
    root,
    modules,
    moduleKind: "Environment",
    graphLabel: "Environment Graph",
    contextLabel: "Environment",
    backLabel,
    storageNamespace: "environment",
    versions: environmentVersions,
    loadedVersion: environment.version,
    instances: draft.instances,
    alphaGraph: draft.graph,
    meta: { contextId: key, name: environment.name || environment.environmentId || "Environment" },
    resourceEditor: {
      title: "Environment Details",
      description: "Identity and name for the next saved Version",
      fields: [
        {
          key: "environmentId",
          label: "Environment ID",
          value: draft.environmentId,
          placeholder: "environment-id",
          required: true,
        },
        {
          key: "name",
          label: "Name",
          value: draft.name,
          placeholder: "Environment name",
          required: true,
        },
      ],
      contextName: (values) => values.name,
      onChange: (values) => {
        draft.environmentId = values.environmentId;
        draft.name = values.name;
      },
    },
    actions: {
      onBack: closeEnvironmentBlueprint,
      onLoad: (nextVersion) => {
        environmentEditorState.environmentKey = `${environment.environmentId}::${nextVersion}`;
        renderEnvironmentDetails();
      },
      onValidate: () => postJson("/api/graphs/validate", {
        resourceType: "environment",
        draft: {
          schemaVersion: 2,
          environmentId: draft.environmentId.trim(),
          name: draft.name.trim(),
          description: environment.description || "",
          instances: draft.instances,
          graph: draft.graph,
        },
      }),
      onSave: async () => {
        const environmentId = draft.environmentId.trim();
        const name = draft.name.trim();
        if (!environmentId) throw new Error("Environment ID is required");
        if (!name) throw new Error("Environment name is required");
        const response = await postJson("/api/environments", {
          schemaVersion: 2,
          environmentId,
          name,
          description: environment.description || "",
          instances: draft.instances,
          graph: draft.graph,
        });
        loadedViews.delete("environments");
        await Promise.all([
          loadEnvironments(true),
          loadRepositoryCatalog("environments", true),
        ]);
        environmentEditorState.environmentKey = `${environmentId}::${response.definition.version}`;
        backtestEntryState.environmentKey = environmentEditorState.environmentKey;
        renderEnvironmentDetails();
        return response;
      },
    },
    onChange(next) {
      draft.instances = structuredClone(next.instances || {});
      draft.graph = structuredClone(next.alphaGraph || { nodes: [], inputs: {}, outputs: {} });
      invalidateBacktestBuild("Environment modules changed · Build again before running");
    },
  });
}

async function loadSummary() {
  const summary = await getJson("/api/summary");
  state.summary = summary;
}

async function loadPipelines(force = false) {
  const [pipelines] = await Promise.all([
    getJson("/api/pipelines"),
    loadRepositoryCatalog("pipelines", force),
  ]);
  state.pipelines = pipelines.pipelines || {};
  state.pipelineVersions = pipelines.versions || [];
}

async function loadPipelineSelection(pipelineId) {
  if (!pipelineId) {
    pipelineEditorState.manifest = null;
    pipelineEditorState.definition = null;
    pipelineEditorState.versions = [];
    pipelineEditorState.loadedVersion = "";
    pipelineEditorState.loadedDefinition = null;
    renderPipelineVersionSelector();
    return;
  }
  const current = await getJson(`/api/pipelines/${encodeURIComponent(pipelineId)}`);
  if (current.pipelineId !== pipelineId) throw new Error(`Pipeline response mismatch for '${pipelineId}'`);
  pipelineEditorState.manifest = current.manifest;
  pipelineEditorState.definition = current.definition || null;
  pipelineEditorState.versions = current.versions || [];
  pipelineEditorState.loadedVersion = current.versions?.find((row) => row.current)?.version || "";
  pipelineEditorState.loadedDefinition = structuredClone(current.definition || null);
  renderPipelineVersionSelector();
}

async function loadModules(force = false) {
  if (!force && loadedViews.has("modules") && repositoryCatalog("modules")) {
    renderModules();
    return;
  }
  const [modules] = await Promise.all([
    getJson("/api/modules?limit=500"),
    loadRepositoryCatalog("modules", force),
  ]);
  state.pipelineModules = modules.modules || {};
  state.totals.modules = modules.total ?? Object.keys(state.pipelineModules).length;
  loadedViews.add("modules");
  renderModules();
}

async function loadDatasets(force = false) {
  if (!force && loadedViews.has("datasets")) return;
  const datasets = await getJson("/api/data/datasets?limit=50");
  state.datasets = datasets.datasets || [];
  state.datasetVersions = datasets.versions || [];
  state.totals.datasets = datasets.total ?? state.datasets.length;
  loadedViews.add("datasets");
}

async function loadSamplers(force = false) {
  if (!force && loadedViews.has("samplers")) return;
  const samplers = await getJson("/api/data/samplers");
  state.samplers = samplers.samplers || [];
  loadedViews.add("samplers");
}

async function loadEnvironments(force = false) {
  if (!force && loadedViews.has("environments")) return;
  const response = await getJson("/api/environments");
  state.environments = response.environments || [];
  loadedViews.add("environments");
}

async function loadAnalyses(force = false) {
  if (!force && loadedViews.has("analyses")) return;
  const response = await getJson("/api/analyses");
  state.analyses = response.analyses || [];
  loadedViews.add("analyses");
}

async function loadEnvironmentView(force = false) {
  const [environmentModules] = await Promise.all([
    getJson("/api/environment-modules?limit=500"),
    loadEnvironments(force),
    loadRepositoryCatalog("environments", force),
    loadRepositoryCatalog("environment-modules", force),
  ]);
  state.environmentModules = environmentModules.modules || {};
  if (currentEnvironmentSection !== "blueprint") {
    renderEnvironmentBrowser();
    return;
  }
  renderEnvironmentDetails();
}

async function loadAnalysisView(force = false) {
  const [analysisModules] = await Promise.all([
    getJson("/api/analysis-modules?limit=500"),
    loadAnalyses(force),
    loadRepositoryCatalog("analysis-modules", force),
    loadRepositoryCatalog("analyses", force),
  ]);
  state.analysisModules = analysisModules.modules || {};
  if (currentAnalysisSection !== "blueprint") {
    renderAnalysisBrowser();
    return;
  }
  renderAnalysisDetails();
}

async function loadVisualizers(force = false) {
  if (!force && loadedViews.has("visualizers") && state.visualizers.length) {
    renderEmbeddedRepositoryBrowser("visualizers");
    return;
  }
  if (!repositoryCatalog("visualizers")) renderEmbeddedRepositoryLoading("visualizers");
  const response = await getJson("/api/visualizers");
  state.visualizers = response.visualizers || [];
  state.repositoryCatalogs.visualizers = {
    repository: "visualizers",
    schemaVersion: 1,
    folders: [],
    assignments: {},
    items: state.visualizers.map((definition) => ({
      ...definition,
      itemId: definition.id,
      label: definition.label || definition.id,
      resourceType: "Visualizer",
      sourceRepository: "visualizers",
      status: "active",
      builtin: true,
      folderId: "",
      folderPath: "/",
    })),
    total: state.visualizers.length,
  };
  window.TradeChartCore?.setVisualizerDefinitions(state.visualizers);
  loadedViews.add("visualizers");
  renderEmbeddedRepositoryBrowser("visualizers");
}

async function loadDatasetManagement(force = false) {
  if (!force && loadedViews.has("dataset-management")) return;
  const [workspaces, recipes, builds] = await Promise.all([
    getJson("/api/data/workspaces"),
    getJson("/api/data/recipes"),
    getJson("/api/data/builds"),
  ]);
  state.datasetWorkspaces = workspaces.workspaces || [];
  state.datasetRecipes = recipes.recipes || [];
  state.datasetBuildJobs = builds.jobs || [];
  loadedViews.add("dataset-management");
}

async function loadData(force = false) {
  if (!force && loadedViews.has("data")) return;
  await Promise.all([
    loadDatasets(force),
    loadSamplers(force),
    loadDatasetManagement(force),
    loadRepositoryCatalog("data", force),
  ]);
  loadedViews.add("data");
  renderData();
}

async function loadBacktests(force = false) {
  if (!force && loadedViews.has("backtests")) return;
  await Promise.all([loadPipelines(force), loadDatasets(force), loadSamplers(force), loadEnvironments(force), loadAnalyses(force)]);
  const [backtests, jobs, visualizers] = await Promise.all([
    getJson(`/api/backtests?limit=50${showArchivedBacktests ? "&includeArchived=true" : ""}`),
    getJson("/api/backtest-jobs?limit=50"),
    getJson("/api/visualizers"),
    loadRepositoryCatalog("backtest", force),
  ]);
  window.TradeChartCore?.setVisualizerDefinitions(visualizers.visualizers || []);
  state.backtests = backtests.backtests || [];
  state.totals.backtests = backtests.total ?? state.backtests.length;
  state.backtestJobs = jobs.jobs || [];
  publishBacktestOperations(state.backtestJobs);
  state.backtestJobMaxConcurrent = Number(jobs.maxConcurrent || 0);
  loadedViews.add("backtests");
  renderBacktests();
  scheduleBacktestJobPoll();
}

async function loadPipeline(force = false) {
  const requestedPipelineId = new URLSearchParams(location.search).get("pipelineId") || "";
  if (currentPipelinePage !== "builder") {
    if (`${location.pathname}${location.search}` !== "/pipeline") {
      history.replaceState({ viewId: "pipeline" }, "", "/pipeline");
    }
    if (!force && loadedViews.has("pipeline-browser") && repositoryCatalog("pipelines")) {
      renderEmbeddedRepositoryBrowser("pipelines");
      return;
    }
    await loadPipelines(force);
    loadedViews.add("pipeline-browser");
    renderEmbeddedRepositoryBrowser("pipelines");
    return;
  }

  const selectedPipelineId = requestedPipelineId || pipelineEditorState.pipelineId;
  if (!selectedPipelineId) {
    await closePipelineBuilder({ replace: true });
    return;
  }
  if (!force && loadedViews.has("pipeline")
      && selectedPipelineId === pipelineEditorState.pipelineId
      && pipelineEditorState.definition) return;
  state.pipelineViewport.initialized = false;
  const [modules] = await Promise.all([
    getJson("/api/modules?limit=500"),
    loadPipelines(force),
    loadRepositoryCatalog("modules", force),
  ]);
  if (!state.pipelines?.[selectedPipelineId]) {
    throw new Error(`Unknown Pipeline '${selectedPipelineId}'`);
  }
  pipelineEditorState.pipelineId = selectedPipelineId;
  renderPipelineEditorSelector();
  if (currentView === "pipeline") {
    history.replaceState(
      { viewId: "pipeline", pipelineId: pipelineEditorState.pipelineId },
      "",
      pathForView("pipeline"),
    );
  }
  state.pipelineModules = modules.modules || {};
  await loadPipelineSelection(pipelineEditorState.pipelineId);
  loadedViews.add("pipeline");
  loadPipelineFormFromDefinition();
  if (currentPipelineSection === "manifest") renderManifest();
}

async function loadResults(force = false) {
  if (!state.resultBacktestId) {
    renderResults();
    return;
  }
  if (force || !Object.keys(state.resultModules || {}).length) {
    const [modules, visualizers] = await Promise.all([
      getJson("/api/modules?limit=500"),
      getJson("/api/visualizers"),
      loadRepositoryCatalog("modules", force),
    ]);
    state.resultModules = modules.modules || {};
    window.TradeChartCore?.setVisualizerDefinitions(visualizers.visualizers || []);
  }
  window.TradeChartCore?.setTemporaryModuleDefinitions(state.resultModules);
  await refreshSelectedBacktest();
}

async function refreshOverview() {
  serviceRuntimeState = { ...serviceRuntimeState, loading: true };
  renderOverview();
  try {
    await loadSummary();
    serviceRuntimeState = {
      active: state.summary?.status === "ok",
      serviceTime: state.summary?.serviceTime || null,
      loading: false,
    };
    renderOverview();
    setHealth(true, "Online");
  } catch (error) {
    serviceRuntimeState = { ...serviceRuntimeState, active: false, loading: false };
    renderOverview();
    throw error;
  }
}

async function ensureViewData(viewId, force = false) {
  if (viewId === "overview") {
    await refreshOverview();
  } else if (viewId === "pipeline") {
    await loadPipeline(force);
  } else if (viewId === "environment") {
    await loadEnvironmentView(force);
  } else if (viewId === "analysis") {
    await loadAnalysisView(force);
  } else if (viewId === "visualizers") {
    await loadVisualizers(force);
  } else if (viewId === "modules") {
    await loadModules(force);
  } else if (viewId === "data") {
    await loadData(force);
  } else if (viewId === "mining-kline") {
    await miningKLine?.load(force);
  } else if (viewId === "backtests") {
    await loadBacktests(force);
  } else if (viewId === "results") {
    await loadResults(force);
  } else if (viewId === "history") {
    renderHistory();
  }
}

async function refreshSelectedBacktest() {
  const backtestId = state.resultBacktestId;
  if (!backtestId) return;
  const selectionSeq = ++resultSelectionSeq;
  ++visualizationSaveSeq;
  clearTimeout(visualizationSaveTimer);
  visualizationSaveTimer = null;
  state.selectedBacktest = null;
  clearResultCharts();
  $("metricStrip").innerHTML = "";
  $("chartArea").dataset.backtestId = "";
  $("chartArea").innerHTML = '<div class="muted">Loading selected backtest</div>';
  const selected = await getJson(`/api/backtests/${encodeURIComponent(backtestId)}/view`);
  if (selectionSeq !== resultSelectionSeq || state.resultBacktestId !== backtestId) return;
  selected.paneResults = {};
  selected.loadingPanes = {};
  selected.loadedPanes = {};
  state.selectedBacktest = selected;
  renderResults();
}

document.querySelectorAll(".nav-btn").forEach((button) => {
  button.addEventListener("click", () => {
    if (button.disabled) return;
    if (!button.dataset.view) return;
    if (button.dataset.view === "pipeline") {
      unmountAlphaGraphBuilder({ flushPending: true });
      currentPipelinePage = "browser";
      currentPipelineSection = "composer";
    }
    if (button.dataset.view === "environment") {
      currentEnvironmentSection = "browser";
      environmentEditorState.returnView = "environment";
    }
    if (button.dataset.view === "analysis") {
      currentAnalysisSection = "browser";
      analysisEditorState.returnView = "analysis";
    }
    if (button.dataset.view === "backtests") {
      currentBacktestSection = "entry";
    }
    switchView(button.dataset.view);
  });
});

$("agentNavLink")?.addEventListener("click", (event) => {
  const link = event.currentTarget;
  const returnTo = `${location.pathname}${location.search}`;
  link.href = `/agent?returnTo=${encodeURIComponent(returnTo === "/agent" ? "/" : returnTo)}`;
});

$("cancelRepositoryFolderBtn")?.addEventListener("click", () => $("repositoryFolderDialog").close());
$("repositoryFolderForm")?.addEventListener("submit", async (event) => {
  event.preventDefault();
  const repository = state.selectedRepository;
  const folder = repositoryFolderById(repository, state.selectedRepositoryFolderId);
  const payload = repositoryFolderDialogMode === "rename"
    ? { action: "rename", repository, folderId: folder?.folderId, name: $("repositoryFolderName").value.trim() }
    : { action: "create", repository, parentId: $("repositoryFolderParent").value, name: $("repositoryFolderName").value.trim() };
  $("confirmRepositoryFolderBtn").disabled = true;
  try {
    const response = await postJson("/api/repository-folders", payload);
    state.repositoryCatalogs[repository] = response.repository;
    if (repositoryFolderDialogMode === "create") {
      state.selectedRepositoryFolderId = response.result.folderId;
      state.repositoryFolderSelections[repository] = response.result.folderId;
    }
    $("repositoryFolderDialog").close();
    renderEmbeddedRepositoryBrowser(repository);
    refreshHierarchicalRepositorySelects();
  } catch (error) {
    $("repositoryFolderDialogError").textContent = error.message;
    $("repositoryFolderDialogError").hidden = false;
  } finally {
    $("confirmRepositoryFolderBtn").disabled = false;
  }
});
window.addEventListener("popstate", () => {
  switchView(normalizedViewFromPath(location.pathname), { push: false });
});

window.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && !$("accountMenuPanel")?.hidden) {
    setAccountMenuOpen(false);
    $("accountMenuBtn")?.focus();
  }
  if (event.key === "Escape" && state.pipelineViewport.fullscreen) {
    togglePipelineFullscreen(false);
  }
  if (event.key === "Escape" && state.backtestViewport.fullscreen) {
    toggleBacktestFullscreen(false);
  }
});

document.addEventListener("click", (event) => {
  if (!event.target.closest("#accountMenu")) setAccountMenuOpen(false);
  scheduleUiContextSync();
});

document.addEventListener("change", scheduleUiContextSync);
document.addEventListener("input", scheduleUiContextSync);

$("pipelineSelect")?.addEventListener("change", async (event) => {
  if ($("pipelineSelect")?.disabled) return;
  unmountAlphaGraphBuilder({ flushPending: true });
  const pipelineId = event.target.value || "";
  if (!pipelineId) {
    setPipelineLoadError("Select a Pipeline");
    return;
  }
  pipelineEditorState.pipelineId = pipelineId;
  pipelineEditorState.definition = null;
  pipelineEditorState.manifest = null;
  pipelineEditorState.versions = [];
  pipelineEditorState.loadedVersion = "";
  pipelineEditorState.loadedDefinition = null;
  pipelineField("Id").value = pipelineId;
  state.pipelineDraft = null;
  loadedViews.delete("pipeline");
  history.replaceState({ viewId: "pipeline", pipelineId }, "", pathForView("pipeline"));
  setPipelineLoadError("");
  setPipelineBlueprintBusyState({ reloadInFlight: true });
  try {
    await loadPipeline(true);
    renderManifest();
  } catch (error) {
    setPipelineLoadError(error.message);
    setHealth(false, error.message);
  } finally {
    setPipelineBlueprintBusyState({ reloadInFlight: false });
  }
});
$("backToPipelineListBtn")?.addEventListener("click", () => {
  if ($("backToPipelineListBtn")?.disabled) return;
  closePipelineBuilder().catch((error) => setHealth(false, error.message));
});
$("addPipelineBtn")?.addEventListener("click", () => {
  if ($("addPipelineBtn")?.disabled) return;
  $("createPipelineName").value = "";
  setCreatePipelineError("");
  $("createPipelineDialog").showModal();
  requestAnimationFrame(() => $("createPipelineName").focus());
});
$("showInactivePipelinesBtn")?.addEventListener("click", async () => {
  showInactivePipelines = !showInactivePipelines;
  loadedViews.delete("pipeline");
  await loadPipeline(true);
});
$("clonePipelineBtn")?.addEventListener("click", () => {
  const pipeline = selectedPipelineRecord();
  if (!pipeline || $("clonePipelineBtn")?.disabled) return;
  $("clonePipelineSource").textContent = `Source: ${pipeline.name || pipeline.pipelineId}`;
  $("clonePipelineName").value = `${pipeline.name || pipeline.pipelineId} Copy`;
  setClonePipelineError("");
  $("clonePipelineDialog").showModal();
  requestAnimationFrame(() => $("clonePipelineName").select());
});
$("cancelClonePipelineBtn")?.addEventListener("click", () => {
  setClonePipelineError("");
  $("clonePipelineDialog").close();
});
$("clonePipelineName")?.addEventListener("input", () => setClonePipelineError(""));
$("clonePipelineForm")?.addEventListener("submit", async (event) => {
  event.preventDefault();
  const sourcePipelineId = pipelineEditorState.pipelineId;
  const name = $("clonePipelineName").value.trim();
  if (!sourcePipelineId || !name) {
    setClonePipelineError("Source Pipeline and new name are required.");
    return;
  }
  const confirm = $("confirmClonePipelineBtn");
  confirm.disabled = true;
  setClonePipelineError("");
  try {
    const response = await postJson(`/api/pipelines/${encodeURIComponent(sourcePipelineId)}/clone`, { name });
    $("clonePipelineDialog").close();
    loadedViews.delete("pipeline-browser");
    delete state.repositoryCatalogs.pipelines;
    await openPipelineBuilder(response.pipelineId);
  } catch (error) {
    setClonePipelineError(error?.message || "Unable to clone Pipeline.");
  } finally {
    confirm.disabled = false;
  }
});
$("disablePipelineBtn")?.addEventListener("click", () => {
  const pipeline = selectedPipelineRecord();
  if (!pipeline || $("disablePipelineBtn")?.disabled) return;
  $("disablePipelineText").textContent = `Disable ${pipeline.name || pipeline.pipelineId}? Archived versions remain immutable and available for inspection.`;
  $("disablePipelineReason").value = "";
  setArchivePipelineError("");
  $("disablePipelineDialog").showModal();
});
$("cancelArchivePipelineBtn")?.addEventListener("click", () => {
  setArchivePipelineError("");
  $("disablePipelineDialog").close();
});
$("disablePipelineForm")?.addEventListener("submit", async (event) => {
  event.preventDefault();
  const pipelineId = pipelineEditorState.pipelineId;
  if (!pipelineId) return;
  const confirm = $("confirmArchivePipelineBtn");
  confirm.disabled = true;
  setArchivePipelineError("");
  try {
    await postJson(`/api/pipelines/${encodeURIComponent(pipelineId)}/disable`, {
      reason: $("disablePipelineReason").value.trim(),
    });
    $("disablePipelineDialog").close();
    pipelineEditorState.pipelineId = "";
    pipelineEditorState.definition = null;
    pipelineEditorState.manifest = null;
    pipelineEditorState.versions = [];
    pipelineEditorState.loadedVersion = "";
    pipelineEditorState.loadedDefinition = null;
    state.pipelineDraft = null;
    loadedViews.delete("pipeline");
    loadedViews.delete("pipeline-browser");
    delete state.repositoryCatalogs.pipelines;
    await closePipelineBuilder({ replace: true });
  } catch (error) {
    setArchivePipelineError(error?.message || "Unable to archive Pipeline.");
  } finally {
    confirm.disabled = false;
  }
});
$("cancelCreatePipelineBtn")?.addEventListener("click", () => {
  setCreatePipelineError("");
  $("createPipelineDialog").close();
});
$("moduleUploadFiles")?.addEventListener("change", async (event) => {
  setModuleLifecycleError("moduleUploadError", "");
  const files = [...event.target.files];
  const manifestFile = files.find((file) => file.name === "module.json");
  if (!manifestFile) {
    const python = files.find((file) => file.name.endsWith(".py"));
    if (python?.name === "module.py") $("moduleUploadEntry").value = "module.py";
    return;
  }
  try {
    const definition = JSON.parse(await manifestFile.text());
    moduleUploadManifest = definition;
    setModuleUploadDefinition({ ...definition, moduleId: "", version: "" });
  } catch (error) {
    moduleUploadManifest = null;
    setModuleLifecycleError("moduleUploadError", `module.json is invalid: ${error.message}`);
  }
});
$("cancelModuleUploadBtn")?.addEventListener("click", () => $("moduleUploadDialog").close());
$("moduleUploadForm")?.addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = $("confirmModuleUploadBtn");
  button.disabled = true;
  setModuleLifecycleError("moduleUploadError", "");
  $("moduleUploadImpact").hidden = true;
  try {
    const entryFile = $("moduleUploadEntry").value.trim();
    const files = await moduleFilesPayload($("moduleUploadFiles").files);
    if (!files.some((file) => file.path === entryFile || file.path.endsWith(`/${entryFile}`))) {
      throw localUiError(`Entry file '${entryFile}' is not included in the selected local files`, "MODULE_ENTRY_MISSING");
    }
    const manifest = moduleUploadManifest || {};
    const kind = $("moduleUploadKind").value;
    const payload = {
      kind,
      moduleId: $("moduleUploadId").value.trim(),
      name: $("moduleUploadName").value.trim(),
      activationMode: manifest.activationMode || "PythonModule",
      parameters: manifest.activationMode === "ProcessRunner"
        ? manifest.parameters
        : {},
      configSchema: parseModuleJsonField("moduleUploadConfigSchema", "Config schema"),
      ports: {
        inputs: parseModuleJsonField("moduleUploadInputs", "Input ports"),
        outputs: parseModuleJsonField("moduleUploadOutputs", "Output ports"),
      },
      description: $("moduleUploadDescription").value.trim(),
      files,
    };
    const repositoryEndpoint = `/api/${state.selectedModuleRepository}`;
    await postJson(repositoryEndpoint, payload);
    $("moduleUploadDialog").close();
    await refreshModulesAfterLifecycle();
  } catch (error) {
    setModuleLifecycleError("moduleUploadError", error.message);
  } finally {
    button.disabled = false;
  }
});
$("createPipelineName")?.addEventListener("input", () => setCreatePipelineError(""));
$("createPipelineForm")?.addEventListener("submit", async (event) => {
  event.preventDefault();
  const name = $("createPipelineName").value.trim();
  if (!name) {
    setCreatePipelineError("Name is required.");
    return;
  }
  const confirm = $("confirmCreatePipelineBtn");
  confirm.disabled = true;
  setCreatePipelineError("");
  try {
    const response = await postJson("/api/pipelines", { name });
    $("createPipelineDialog").close();
    loadedViews.delete("pipeline-browser");
    delete state.repositoryCatalogs.pipelines;
    await openPipelineBuilder(response.pipelineId);
    renderManifest();
  } catch (error) {
    setCreatePipelineError(error?.message || "Unable to add Pipeline.");
  } finally {
    confirm.disabled = false;
  }
});
$("loadPipelineBtn").addEventListener("click", async () => {
  if ($("loadPipelineBtn")?.disabled) return;
  try {
    await loadSelectedPipelineVersion($("pipelineVersionSelect").value);
  } catch (error) {
    setPipelineLoadError(error?.message || "Unable to load Version");
  }
});
$("pipelineVersionSelect")?.addEventListener("change", syncPipelineLoadActionState);
$("pipelineFullscreenBtn")?.addEventListener("click", () => togglePipelineFullscreen());
$("pipelineArrangeBtn")?.addEventListener("click", arrangePipelineGraph);
$("pipelineFitBtn")?.addEventListener("click", resetPipelineViewport);
$("pipelineInfoTabBtn")?.addEventListener("click", () => togglePipelineInfoCollapsed());
document.querySelectorAll(".pipeline-subnav-btn").forEach((button) => {
  button.addEventListener("click", () => {
    if (button.disabled) return;
    switchPipelineSection(button.dataset.pipelineSection);
  });
});
$("savePipelineVersionBtn").addEventListener("click", () => {
  if ($("savePipelineVersionBtn")?.disabled) return;
  runUiAction("Saving Pipeline Version", async () => {
    setPipelineBlueprintBusyState({ saveInFlight: true });
    try {
      await saveCurrentPipelineVersion();
    } finally {
      setPipelineBlueprintBusyState({ saveInFlight: false });
    }
  });
});
$("datasetAddForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  setDialogError("datasetAddError", "");
  const button = $("confirmDatasetAddBtn");
  button.disabled = true;
  try {
    const file = $("uploadZip").files[0];
    if (!file || !$("uploadDatasetName").value.trim()) throw new Error("Dataset name and ZIP file are required.");
    const query = new URLSearchParams({ name: $("uploadDatasetName").value.trim(), filename: file.name });
    const response = await authenticatedFetch(`/api/data/upload?${query}`, {
      method: "POST",
      headers: { "Content-Type": "application/zip", Accept: "application/json", "X-CSRF-Token": authState.csrfToken },
      body: file,
    });
    const result = await response.json().catch(() => ({}));
    if (!response.ok || result.accepted === false) throw new Error(result.error || `Upload returned ${response.status}`);
    const dataset = result.dataset;
    await assignDataResourceToFolder("datasets", dataset.datasetId, $("datasetAddDialog").dataset.parentFolderId);
    $("datasetAddDialog").close();
    await refreshDataFilesystemAfterMutation();
  } catch (error) {
    setDialogError("datasetAddError", error.message);
  } finally {
    button.disabled = false;
  }
});
$("datasetWorkspaceForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  setDialogError("datasetWorkspaceError", "");
  const button = $("createDatasetWorkspaceBtn");
  button.disabled = true;
  try {
    const selected = [...datasetWorkspaceSelection];
    if (!selected.length) throw new Error("Select at least one source Dataset.");
    const result = await postJson("/api/data/workspaces", {
      name: $("datasetWorkspaceName").value.trim(),
      sources: selected.map((datasetId, index) => ({ datasetId, alias: `dataset${index + 1}` })),
    });
    await assignDataResourceToFolder("workspaces", result.workspace.workspaceId, $("datasetWorkspaceDialog").dataset.parentFolderId);
    $("datasetWorkspaceDialog").close();
    await refreshDataFilesystemAfterMutation();
  } catch (error) {
    setDialogError("datasetWorkspaceError", error.message);
  } finally {
    button.disabled = false;
  }
});
$("datasetWorkspaceSearch").addEventListener("focus", () => renderDatasetWorkspacePicker(true));
$("datasetWorkspaceSearch").addEventListener("input", () => renderDatasetWorkspacePicker(true));
$("datasetWorkspaceSearch").addEventListener("keydown", (event) => {
  if (event.key === "Escape") renderDatasetWorkspacePicker(false);
  if (event.key !== "Enter") return;
  const first = $("datasetWorkspaceCandidates").querySelector("[data-workspace-dataset-add]");
  if (!first) return;
  event.preventDefault();
  toggleDatasetWorkspaceSelection(first.dataset.workspaceDatasetAdd);
});
document.addEventListener("pointerdown", (event) => {
  if (!$("datasetWorkspaceDialog")?.open) return;
  if (event.target.closest(".dataset-collection-picker")) return;
  renderDatasetWorkspacePicker(false);
});
document.querySelectorAll("[data-dataset-script-method]").forEach((button) => {
  button.addEventListener("click", () => showDatasetScriptDetail(button.dataset.datasetScriptMethod));
});
$("backDatasetScriptBtn").addEventListener("click", showDatasetScriptMethodStep);
$("cancelDatasetScriptMethodBtn").addEventListener("click", () => $("datasetScriptDialog").close());
$("datasetScriptWorkspace").addEventListener("change", () => refreshDatasetScriptWorkspacePaths().catch((error) => setDialogError("datasetScriptError", error.message)));
$("datasetScriptForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  setDialogError("datasetScriptError", "");
  const button = $("confirmDatasetScriptBtn");
  button.disabled = true;
  try {
    const mode = $("datasetScriptMode").value;
    let scriptText = "";
    if (mode === "upload") {
      const file = $("datasetScriptFile").files[0];
      if (!file || !file.name.toLowerCase().endsWith(".py")) throw new Error("Select a .py file.");
      scriptText = await file.text();
    } else if (mode === "workspace") {
      const workspaceId = $("datasetScriptWorkspace").value;
      const path = $("datasetScriptWorkspacePath").value;
      if (!workspaceId || !path) throw new Error("Select a Workspace and Python script.");
      const response = await getJson(`/api/data/workspaces/${encodeURIComponent(workspaceId)}/script?path=${encodeURIComponent(path)}`);
      scriptText = response.scriptText;
    } else throw new Error("Choose a Script source.");
    const result = await postJson("/api/data/recipes", {
      name: $("datasetScriptName").value.trim(),
      scriptText,
    });
    await assignDataResourceToFolder("scripts", `${result.recipe.recipeId}::${result.recipe.version}`, $("datasetScriptDialog").dataset.parentFolderId);
    $("datasetScriptDialog").close();
    await refreshDataFilesystemAfterMutation();
  } catch (error) {
    setDialogError("datasetScriptError", error.message);
  } finally {
    button.disabled = false;
  }
});
$("datasetProcessForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  setDialogError("datasetProcessError", "");
  const button = $("confirmDatasetProcessBtn");
  button.disabled = true;
  try {
    const recipe = currentProcessRecipe();
    const datasetIds = [...$("datasetProcessSources").selectedOptions].map((option) => option.value);
    if (!recipe || !datasetIds.length) throw new Error("Select one Script and at least one Dataset.");
    const result = await postJson("/api/data/process", {
      recipeId: recipe.recipeId, recipeVersion: recipe.version, datasetIds,
      arguments: $("datasetProcessArguments").value,
      outputDatasetName: $("datasetProcessOutputName").value.trim(),
    });
    await assignDataResourceToFolder("datasets", result.dataset.datasetId, $("datasetProcessDialog").dataset.parentFolderId);
    $("datasetProcessDialog").close();
    await refreshDataFilesystemAfterMutation();
  } catch (error) {
    setDialogError("datasetProcessError", error.message);
  } finally {
    button.disabled = false;
  }
});
$("datasetReplaceForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const file = $("datasetReplaceFile").files[0];
  if (!pendingDatasetReplace || !file) return;
  setDialogError("datasetReplaceError", "");
  const button = $("confirmDatasetReplaceBtn");
  button.disabled = true;
  try {
    const query = new URLSearchParams({ filename: file.name, name: pendingDatasetReplace.name || "" });
    const response = await authenticatedFetch(`/api/data/datasets/${encodeURIComponent(pendingDatasetReplace.datasetId)}/replace?${query}`, {
      method: "POST",
      headers: { "Content-Type": "application/zip", Accept: "application/json", "X-CSRF-Token": authState.csrfToken }, body: file,
    });
    const result = await response.json().catch(() => ({}));
    if (!response.ok || result.accepted === false) throw new Error(result.error || `Replace returned ${response.status}`);
    $("datasetReplaceDialog").close();
    await refreshDataFilesystemAfterMutation();
  } catch (error) {
    setDialogError("datasetReplaceError", error.message);
  } finally {
    button.disabled = false;
  }
});
$("repositoryResourceRenameForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!pendingRepositoryRename) return;
  setDialogError("repositoryResourceRenameError", "");
  try {
    const item = pendingRepositoryRename.item;
    await postJson("/api/repository-resources", {
      action: "rename", repository: pendingRepositoryRename.repository,
      itemId: item.itemId, name: $("repositoryResourceRenameName").value.trim(),
    });
    $("repositoryResourceRenameDialog").close();
    if (pendingRepositoryRename.repository === "data") await refreshDataFilesystemAfterMutation();
    else if (pendingRepositoryRename.repository === "pipelines") {
      loadedViews.delete("pipeline");
      await loadPipeline(true);
    }
    else await loadBacktests(true);
  } catch (error) {
    setDialogError("repositoryResourceRenameError", error.message);
  }
});
["datasetAdd", "datasetWorkspace", "datasetScript", "datasetProcess", "datasetReplace", "repositoryResourceRename"].forEach((name) => {
  $(`cancel${name[0].toUpperCase()}${name.slice(1)}Btn`)?.addEventListener("click", () => $(`${name}Dialog`)?.close());
});
async function submitPreparedBacktest() {
  await runUiAction("Submitting", async () => {
    const datasetId = $("backtestDataset").value;
    if (!datasetId) {
      const message = "Dataset is required";
      setBacktestEntryError(message);
      throw localUiError(message, "BACKTEST_ENTRY_VALIDATION");
    }
    const datasetEvidence = selectedBacktestDatasetEvidence();
    if (!datasetEvidence) {
      const message = "Selected Dataset has no sealed evidence";
      setBacktestEntryError(message);
      throw localUiError(message, "BACKTEST_ENTRY_VALIDATION");
    }
    setBacktestEntryError("");
    const pipelineId = backtestEntryState.pipelineId;
    if (!pipelineId || !state.pipelines?.[pipelineId]) {
      const message = "Pipeline is required";
      setBacktestEntryError(message);
      throw localUiError(message, "BACKTEST_ENTRY_VALIDATION");
    }
    const request = buildBacktestCompositionRequest();
    if (!request) {
      const message = "Backtest composition is incomplete";
      setBacktestEntryError(message);
      throw localUiError(message, "BACKTEST_ENTRY_VALIDATION");
    }
    const preparedSubmissionToken = backtestEntryState.preparedSubmissionToken;
    const requestFingerprint = backtestRequestFingerprint(request);
    if (backtestEntryState.compositionValidation !== "valid"
        || !preparedSubmissionToken
        || !backtestEntryState.preparedRequestDigest
        || backtestEntryState.preparedRequestFingerprint !== requestFingerprint
        || !backtestPreparedTokenIsUsable()) {
      invalidateBacktestBuild("Configuration changed · Build again before running");
      const message = "This configuration must be built again";
      setBacktestEntryError(message);
      throw localUiError(message, "BACKTEST_ENTRY_VALIDATION");
    }
    ++backtestEntryState.compositionSequence;
    backtestEntryState.preparedSubmissionToken = "";
    backtestEntryState.preparedTokenExpiresAt = 0;
    backtestEntryState.compositionValidation = "submitting";
    backtestEntryState.compositionMessage = "Submitting prepared Backtest…";
    renderBacktestCompositionStatus();
    setBacktestSubmissionPending(true);
    $("backtestStatus").textContent = "Submitting Backtest…";
    try {
      const response = await postJson("/api/backtests", {
        request,
        preparedSubmissionToken,
      });
      if (response.job) {
        state.backtestJobs = [
          response.job,
          ...(state.backtestJobs || []).filter((job) => job.jobId !== response.job.jobId),
        ];
        publishBacktestOperations([response.job]);
      }
      $("backtestStatus").textContent = response.job?.jobId
        ? `Submitted ${response.job.jobId}`
        : "Backtest submitted";
      backtestEntryState.compositionValidation = "submitted";
      backtestEntryState.compositionMessage = response.job?.jobId
        ? `Backtest queued · ${response.job.jobId} · Build remains cached`
        : "Backtest queued · Build remains cached";
      renderBacktestCompositionStatus();
      renderBacktestJobs();
      scheduleBacktestJobPoll(250);
    } catch (error) {
      backtestEntryState.preparedSubmissionToken = "";
      backtestEntryState.preparedTokenExpiresAt = 0;
      backtestEntryState.compositionValidation = backtestCachedBuildMatches(request)
        ? "valid"
        : "build";
      backtestEntryState.compositionMessage = backtestCachedBuildMatches(request)
        ? "Submission failed · cached Build is still available"
        : "Submission failed · Build again before retrying";
      renderBacktestCompositionStatus();
      throw error;
    } finally {
      setBacktestSubmissionPending(false);
    }
  });
}

$("runBacktestBtn").addEventListener("click", () => {
  if (backtestEntryState.submissionPending
      || backtestEntryState.compositionValidation === "pending") return;
  const requestFingerprint = backtestRequestFingerprint(buildBacktestCompositionRequest());
  const cachedBuild = backtestCachedBuildMatches();
  if (cachedBuild) {
    if (backtestPreparedTokenIsUsable()
        && backtestEntryState.preparedRequestFingerprint === requestFingerprint) {
      submitPreparedBacktest();
    } else {
      buildBacktestSubmission({ runAfterBuild: true });
    }
    return;
  }
  buildBacktestSubmission();
});
$("resultTimezoneBtn")?.addEventListener("click", () => {
  const spec = state.selectedBacktest?.visualization;
  if (!spec) return;
  const current = currentResultTimeZone(spec);
  const candidate = window.prompt("IANA time zone", current.timeZone);
  if (candidate === null) return;
  const timeZone = candidate.trim();
  try {
    new Intl.DateTimeFormat("en-US", { timeZone }).format(0);
  } catch {
    setVisualizationSpecError("Enter a valid IANA time zone, for example UTC or Europe/London.");
    return;
  }
  spec.timeZone = timeZone;
  persistVisualizationView(spec);
  syncResultTimezoneButton(spec);
  drawVisualization(spec);
});
$("chainDataset").addEventListener("click", () => {
  switchView("data");
  requestAnimationFrame(() => $("dataRepositoryBrowser")?.scrollIntoView({ behavior: "smooth", block: "center" }));
});
$("chainSampler").addEventListener("click", () => {
  const sampler = selectedBacktestSampler();
  if (!sampler) return;
  openSamplerJupyter(sampler).catch((error) => setHealth(false, error.message));
});
$("chainPipeline").addEventListener("click", () => {
  const pipelineId = backtestEntryState.pipelineId;
  if (!pipelineId) return;
  openPipelineBuilder(pipelineId).catch((error) => setHealth(false, error.message));
});
$("chainEnvironmentDetails")?.addEventListener("click", () => {
  const environment = selectedBacktestEnvironment();
  const environmentKey = environment
    ? `${environment.environmentId}::${environment.version}`
    : "";
  openEnvironmentBlueprint(environmentKey, { returnView: "backtests" });
});
$("chainAnalysisDetails")?.addEventListener("click", () => {
  const analysis = selectedBacktestAnalysis();
  const analysisKey = analysis
    ? `${analysis.analysisId}::${analysis.version}`
    : "";
  openAnalysisBlueprint(analysisKey, { returnView: "backtests" });
});
$("backFromResultBtn")?.addEventListener("click", () => {
  state.resultBacktestId = "";
  state.selectedBacktest = null;
  switchView("backtests");
});
$("backtestArrangeBtn")?.addEventListener("click", arrangeBacktestGraph);
$("backtestFitBtn")?.addEventListener("click", resetBacktestViewport);
$("backtestFullscreenBtn")?.addEventListener("click", () => toggleBacktestFullscreen());
$("addChartBtn").addEventListener("click", addChartPane);
$("pipelineAlphaGraph").addEventListener("input", () => {
  if ($("pipelineAlphaGraph")?.disabled) return;
  syncPipelineAlphaGraphInputState();
});
$("uploadDatasetName").addEventListener("input", () => {
  setDataUploadError("");
  syncDataUploadActionState();
});
$("uploadZip").addEventListener("change", async () => {
  await validateSelectedUploadZipFile();
});
$("backtestDataset").addEventListener("change", () => {
  invalidateBacktestBuild("Dataset changed · Build again before running");
  renderBacktestChain();
});
$("backtestPipelineSelect")?.addEventListener("change", (event) => {
  const [pipelineId = "", version = ""] = (event.target.value || "").split("::");
  backtestEntryState.pipelineId = pipelineId;
  backtestEntryState.pipelineVersion = version;
  invalidateBacktestBuild("Pipeline changed · Build again before running");
  renderBacktestChain();
});
$("backtestSampler").addEventListener("change", () => {
  renderBacktestSamplerParameters();
  invalidateBacktestBuild("Sampler changed · Build again before running");
  renderBacktestChain();
});
$("backtestEnvironmentSelect")?.addEventListener("change", () => {
  renderBacktestEnvironmentParameters();
  invalidateBacktestBuild("Environment changed · Build again before running");
  renderBacktestChain();
});
$("backtestAnalysisSelect")?.addEventListener("change", () => {
  const analysis = selectedBacktestAnalysis();
  backtestEntryState.analysisKey = analysis ? `${analysis.analysisId}::${analysis.version}` : "";
  invalidateBacktestBuild("Analysis changed · Build again before running");
  renderBacktestChain();
});
$("configureBacktestEnvironment")?.addEventListener("click", () => {
  const environment = selectedBacktestEnvironment();
  environmentEditorState.environmentKey = environment
    ? `${environment.environmentId}::${environment.version}`
    : "";
  switchBacktestSection("environment");
});
$("configureBacktestSampler")?.addEventListener("click", openBacktestSamplerConfig);
$("cancelBacktestSamplerConfigBtn")?.addEventListener("click", () => {
  $("backtestSamplerConfigDialog").close();
});
$("applyBacktestSamplerConfigBtn")?.addEventListener("click", () => {
  try {
    backtestEntryState.samplerParameters = forms.readSchemaFields(
      $("backtestSamplerConfigFields"),
      selectedSamplerParameterSchema(),
    );
    setBacktestSamplerConfigError("");
    $("backtestSamplerConfigDialog").close();
    renderBacktestSamplerParameters();
    invalidateBacktestBuild("Sampler configuration changed · Build again before running");
    renderBacktestChain();
  } catch (error) {
    setBacktestSamplerConfigError(error?.message || "Invalid Sampler parameters");
  }
});
$("showArchivedBacktestsBtn")?.addEventListener("click", () => {
  showArchivedBacktests = !showArchivedBacktests;
  loadedViews.delete("backtests");
  loadBacktests(true).catch((error) => setHealth(false, error.message));
});
$("visualizationSpec").addEventListener("input", syncVisualizationSpecInputState);
$("saveVisualizationBtn").addEventListener("click", () => {
  runUiAction("Saving", async () => {
    const backtestId = currentResultBacktestId();
    if (!backtestId) {
      setVisualizationSpecError("Open a Result from the Backtest Browser before saving.");
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

$("accountMenuBtn").addEventListener("click", () => {
  setAccountMenuOpen($("accountMenuPanel").hidden);
});
$("accountBtn").addEventListener("click", () => {
  setAccountMenuOpen(false);
  clearPasswordFields();
  setAccountError("");
  $("accountDialog").showModal();
});
$("cancelAccountBtn").addEventListener("click", () => {
  clearPasswordFields();
  setAccountError("");
  $("accountDialog").close();
});
$("passwordChangeForm").addEventListener("submit", (event) => {
  event.preventDefault();
  runUiAction("Changing password", async () => {
    const currentPassword = $("currentPassword").value;
    const newPassword = $("newPassword").value;
    if (newPassword !== $("confirmNewPassword").value) {
      setAccountError("New password confirmation does not match.");
      return;
    }
    setAccountError("");
    try {
      await postJson("/api/account/password", { currentPassword, newPassword });
      clearPasswordFields();
      $("accountDialog").close();
    } catch (error) {
      clearPasswordFields();
      setAccountError(error?.message || "Password change failed.");
      throw error;
    }
  });
});
$("logoutBtn").addEventListener("click", () => {
  setAccountMenuOpen(false);
  runUiAction("Signing out", async () => {
    try {
      await postJson("/auth/logout", {});
    } finally {
      clearPersistedBacktestBuildCache();
      authState = { user: null, csrfToken: "", expiresAt: 0 };
      window.__tradeAuth = authState;
      location.replace("/login");
    }
  });
});

async function initializeAuthenticatedApplication() {
  await loadBrowserSession();
  await window.TradeUiSync?.start();
  initializeBacktestGraph();
  currentView = normalizedViewFromPath(location.pathname);
  await switchView(currentView, { push: false });
  if (currentView !== "overview") await refreshOverview();
  scheduleUiContextSync();
}

initializeAuthenticatedApplication().catch((error) => setHealth(false, error.message));
